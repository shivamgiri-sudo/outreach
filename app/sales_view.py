"""Sales funnel, part 4: read a stored lead as the funnel sees it, with read-time defaults and automatic dates.

No migration: a lead without lead["sales"] is "Inquiry / Raw Lead" created when the lead was created. Dates that were
never set are filled from what the app already knows, lazily and only for counting:
  MQL            first sign of engagement: a person's reply, an inbound lead form (Meta, website, sheet ...), a connected call,
                 a meeting booked
  SAL            first human outbound contact (a call, e-mail, LinkedIn or WhatsApp a person logged or sent)
  First response the same first human outbound contact (speed to lead)
An explicit date (typed, imported or set by a status change) always wins.
"""
from collections import defaultdict
from typing import Optional

from .replies import is_person_reply
from .sales_calc import FLead
from .sales_model import (DATE_KEYS, ENGAGEMENTS, FITS, INQUIRY, RECYCLED, STAGES, WON, clean_status, money, parse_dt, stage_index)
from .source_names import canonical, label_of

HUMAN_KINDS = ("call", "email", "linkedin", "whatsapp", "sms")
CONNECTED = ("connected", "interested", "not_interested", "meeting_booked", "call_back")
# How the funnel names a lead's source (the spec's list); anything else shows the app's own label.
SALES_SOURCE = {"meta_ads": "Meta Ads", "website_form": "SEO / Website", "linkedin_leadgen": "LinkedIn", "linkedin_manual": "LinkedIn",
                "sales_navigator": "LinkedIn", "referral": "Referral"}


def auto_index(activities: list) -> dict:
    """lead_id -> {"human": first human outbound contact (datetime), "engaged": first connected / inbound activity}."""
    out = defaultdict(dict)
    for a in activities:
        lid, kind = a.get("lead_id"), a.get("type")
        at = parse_dt(a.get("at"))
        if not lid or at is None:
            continue
        slot = out[lid]
        if kind in HUMAN_KINDS and (a.get("direction") or "out") == "out":
            if "human" not in slot or at < slot["human"]:
                slot["human"] = at
        if (a.get("direction") == "in") or (kind == "call" and a.get("outcome") in CONNECTED):
            if "engaged" not in slot or at < slot["engaged"]:
                slot["engaged"] = at
    return out


def _timeline_signals(lead: dict) -> tuple:
    """(first human outbound, first engagement, last activity) from the lead's own timeline and fields."""
    human = engaged = last = None
    for e in lead.get("timeline") or []:
        at = parse_dt(e.get("at"))
        if at is None:
            continue
        if last is None or at > last:
            last = at
        kind = e.get("kind")
        if kind in HUMAN_KINDS and e.get("by") and (human is None or at < human):
            human = at
        if (is_person_reply(e) or kind in ("inbound", "meeting")) and (engaged is None or at < engaged):
            engaged = at
    first_inbound = parse_dt(lead.get("first_inbound_at"))
    if first_inbound and (engaged is None or first_inbound < engaged):
        engaged = first_inbound
    return human, engaged, last


def source_of(lead: dict) -> str:
    s = lead.get("sales") or {}
    if s.get("source"):
        return str(s["source"])
    if lead.get("source_detail"):
        return str(lead["source_detail"])
    sid = lead.get("source_id") or canonical(lead.get("source"))[0]
    return SALES_SOURCE.get(sid) or (label_of(sid) if sid and sid != "unknown" else "") or "Not set"


def sales_of(lead: dict) -> dict:
    s = lead.get("sales")
    return s if isinstance(s, dict) else {}


def effective_status(lead: dict) -> str:
    return clean_status(sales_of(lead).get("status")) or INQUIRY


def funnel_lead(lead: dict, auto: Optional[dict] = None) -> FLead:
    """The lead as the funnel reads it. `auto` is auto_index(...) for the activities (optional)."""
    s = sales_of(lead)
    sd = s.get("dates") or {}
    created = parse_dt(sd.get("inquiry")) or parse_dt(lead.get("created_at"))
    human, engaged, last = _timeline_signals(lead)
    a = (auto or {}).get(lead.get("id")) or {}
    if a.get("human") and (human is None or a["human"] < human):
        human = a["human"]
    if a.get("engaged") and (engaged is None or a["engaged"] < engaged):
        engaged = a["engaged"]
    status = clean_status(s.get("status")) or INQUIRY
    has_record = bool(s.get("status"))
    dates = {}
    for key in DATE_KEYS[1:7]:                                 # mql ... negotiation
        dates[key] = parse_dt(sd.get(key))
    closed = parse_dt(sd.get("closed"))
    dates["won"] = closed if status == WON else None          # the Closed Date means "won" only on a won lead
    # Automatic dates only fill gaps and only before the lead has an explicit sales stage of its own.
    if not dates["mql"] and engaged:
        dates["mql"] = engaged
    if not dates["sal"] and human:
        dates["sal"] = human
        if not dates["mql"]:
            dates["mql"] = human                               # a human contact implies the lead was qualified in
    first_response = parse_dt(s.get("first_response")) or human
    if not has_record:                                         # shown stage follows the automatic dates until someone sets one
        status = STAGES[2]["status"] if dates["sal"] else STAGES[1]["status"] if dates["mql"] else INQUIRY
    last_activity = parse_dt(s.get("last_activity"))
    if last and (last_activity is None or last > last_activity):
        last_activity = last
    fit = str(s.get("fit") or "").strip().upper()[:1]
    eng = str(s.get("engagement") or "").strip().capitalize()
    st = stage_index(status)
    prob = s.get("prob") if s.get("prob") is not None else None
    return FLead(
        id=lead.get("id") or "", created=created, company=lead.get("company") or lead.get("contact_name") or "",
        contact=lead.get("contact_name") or "", industry=str(s.get("industry") or lead.get("sector") or ""), service=str(s.get("service") or ""),
        source=source_of(lead), owner=str(lead.get("owner") or ""), fit=fit if fit in FITS else "", engagement=eng if eng in ENGAGEMENTS else "",
        status=status, first_response=first_response, dates=dates, closed=closed, recycled=parse_dt(sd.get("recycled")),
        lost_stage=str(s.get("lost_stage") or ""), lost_reason=str(s.get("lost_reason") or ""), est=money(s.get("est_value")),
        prob=prob if prob is not None else (0 if status == RECYCLED else 0), last_activity=last_activity,
        closed_value=money(s.get("closed_value")), tenure=money(s.get("tenure")), tcv=money(s.get("tcv")) or (
            money(s.get("closed_value")) * (money(s.get("tenure")) or 12) if status == WON and money(s.get("closed_value")) else 0),
        backfilled=[x.strip() for x in str(s.get("backfilled") if isinstance(s.get("backfilled"), str) else ",".join(s.get("backfilled") or [])).split(",") if x.strip()],
    )
