"""Campaign outcomes: what each campaign's leads did after it found them (sent, opened, replied, interested, meetings,
customers, with rates of the leads emailed), and a paged, filtered list of one campaign's leads."""
from collections import defaultdict
from typing import Optional

from . import workspaces
from .analytics_range import milestones

SENT = ("sent",)
MAX_PAGE_SIZE = 200


def _pct(part, whole) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _outcome(leads: list, emails: dict) -> dict:
    emailed = [l for l in leads if emails.get(l["id"])]
    marks = {l["id"]: milestones(l) for l in leads}
    opened = sum(1 for l in emailed if ((l.get("engagement") or {}).get("opens") or 0) > 0)
    replied = sum(1 for l in leads if ((l.get("engagement") or {}).get("replies") or 0) > 0 or marks[l["id"]]["replied"])
    reach = lambda step: sum(1 for l in leads if marks[l["id"]][step])
    out = {"leads": len(leads), "emails_sent": sum(emails.get(l["id"], 0) for l in leads), "leads_emailed": len(emailed),
           "opened": opened, "replied": replied, "interested": reach("interested"), "meetings": reach("meeting"), "customers": reach("customer"),
           "unsubscribed": sum(1 for l in leads if l.get("stage") == "unsubscribed")}
    base = len(emailed)
    out["rates"] = {"open": _pct(opened, base), "reply": _pct(replied, base), "interested": _pct(out["interested"], base),
                    "meeting": _pct(out["meetings"], base)}
    return out


def _email_counts(store) -> dict:
    counts = defaultdict(int)
    for m in store.read("outbox"):
        if m.get("status") in SENT and m.get("lead_id"):
            counts[m["lead_id"]] += 1
    return counts


def outcomes(store, campaign_ids: Optional[list] = None) -> dict:
    """{campaign_id: outcome} for the leads in the caller's workspace scope."""
    by_campaign = defaultdict(list)
    for l in workspaces.filter_rows(store.read("leads")):
        if l.get("campaign_id"):
            by_campaign[l["campaign_id"]].append(l)
    emails = _email_counts(store)
    ids = campaign_ids if campaign_ids is not None else [c["id"] for c in store.read("campaigns")]
    return {cid: _outcome(by_campaign.get(cid, []), emails) for cid in ids}


def _matches(lead: dict, stage: str, q: str, state: str) -> bool:
    if stage and lead.get("stage") != stage:
        return False
    if q and q not in " ".join(str(lead.get(k) or "") for k in ("company", "contact_name", "email", "phone", "domain", "title")).lower():
        return False
    eng = lead.get("engagement") or {}
    if state == "in_sequence" and not lead.get("sequence"):
        return False
    if state == "not_started" and lead.get("sequence"):
        return False
    if state == "replied" and not (eng.get("replies") or 0):
        return False
    if state == "ready" and not (lead.get("campaign_check") or {}).get("qualified"):
        return False
    return True


def leads_page(store, campaign_id: str, present, page: int = 1, size: int = 25, stage: str = "", q: str = "", state: str = "",
               sort: str = "score") -> dict:
    size = max(1, min(int(size or 25), MAX_PAGE_SIZE))
    page = max(1, int(page or 1))
    q = " ".join(str(q or "").lower().split())[:80]
    leads = [l for l in workspaces.filter_rows(store.read("leads")) if l.get("campaign_id") == campaign_id]
    stages = sorted({l.get("stage") for l in leads if l.get("stage")})
    shown = [present(l) for l in leads if _matches(l, stage, q, state)]
    if sort == "company":
        shown.sort(key=lambda l: (l.get("company") or l.get("domain") or "").lower())
    elif sort == "recent":
        shown.sort(key=lambda l: l.get("created_at") or "", reverse=True)
    else:
        shown.sort(key=lambda l: -l["score"])
    start = (page - 1) * size
    return {"items": shown[start:start + size], "total": len(shown), "all": len(leads), "page": page, "size": size,
            "pages": max(1, -(-len(shown) // size)), "stages": stages}
