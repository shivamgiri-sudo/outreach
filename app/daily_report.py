"""Reports: what was done each day and by whom, from facts already stored (logged activities, sent emails, WhatsApp
messages, lead timelines, finished tasks). One activity log row per action says when (IST), who, which lead, what,
the outcome and exactly where the contact happened (which number, email address or LinkedIn profile).
Only leads in the request's workspace scope count.
"""
import csv
import io
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Optional

from . import workspaces
from .analytics_range import IST, _in, _stage_target, _t
from .replies import is_person_reply
from .source_stats import real

LOG_LIMIT = 5000
CONNECTED = {"connected", "interested", "not_interested", "meeting_booked", "call_back"}
COUNTERS = ("leads_added", "calls", "calls_connected", "interested", "not_interested", "follow_ups", "bad_numbers", "emails_sent",
            "whatsapp_sent", "linkedin", "replies", "meetings", "notes", "stage_changes", "tasks_done", "opens", "clicks")
SYSTEM = "System"


def _where(kind: str, lead: dict, row: dict) -> str:
    """Where the contact happened: the number, address or profile used (as saved then, else the lead's current one)."""
    if row.get("to"):
        return str(row["to"])
    if kind in ("call", "whatsapp", "sms"):
        return lead.get("phone") or ""
    if kind == "email":
        return lead.get("email") or ""
    if kind == "linkedin":
        return lead.get("linkedin_url") or ""
    return ""


def _row(at, who, lead, kind, what, outcome="", where="", notes="") -> dict:
    return {"at": at.isoformat(timespec="seconds"), "day": at.astimezone(IST).date().isoformat(),
            "time_ist": at.astimezone(IST).strftime("%H:%M"), "who": who or SYSTEM, "lead_id": lead.get("id"),
            "company": lead.get("company") or "", "contact": lead.get("contact_name") or "", "kind": kind, "what": what,
            "outcome": outcome or "", "where": where or "", "notes": (notes or "")[:300]}


def activity_log(store, rng: dict) -> list:
    leads = {l["id"]: l for l in real(workspaces.filter_rows(store.read("leads")))}
    inside = lambda at: _in(at, rng["start"], rng["end"])
    rows = []
    for a in store.read("activities"):
        lead, at = leads.get(a.get("lead_id")), _t(a.get("at"))
        if lead and at and inside(at):
            kind = a.get("type") or "other"
            what = {"call": "Call", "meeting": "Meeting", "note": "Note", "email": "Email (logged)", "whatsapp": "WhatsApp (logged)",
                    "sms": "SMS (logged)", "linkedin": "LinkedIn (logged)"}.get(kind, kind.title())
            rows.append(_row(at, a.get("by"), lead, kind, what, a.get("outcome"), _where(kind, lead, a), a.get("body")))
    for m in store.read("outbox"):
        lead, at = leads.get(m.get("lead_id")), _t(m.get("sent_at"))
        if lead and at and inside(at) and m.get("status") == "sent":
            rows.append(_row(at, m.get("by") or "Sequence", lead, "email_sent", "Email sent", m.get("step") or "", _where("email", lead, m), m.get("subject")))
    for m in store.read("whatsapp_outbox"):
        lead, at = leads.get(m.get("lead_id")), _t(m.get("sent_at") or m.get("at") or m.get("created_at"))
        if lead and at and inside(at):
            rows.append(_row(at, m.get("by"), lead, "whatsapp_sent", "WhatsApp sent", m.get("status") or m.get("mode") or "", _where("whatsapp", lead, m), m.get("template")))
    for lead in leads.values():
        created = _t(lead.get("created_at"))
        if created and inside(created):
            rows.append(_row(created, next((e.get("by") for e in lead.get("timeline") or [] if e.get("kind") in ("created", "imported")), None),
                             lead, "lead_added", "Lead added", lead.get("source") or ""))
        for e in lead.get("timeline") or []:
            at = _t(e.get("at"))
            if not at or not inside(at):
                continue
            if is_person_reply(e):
                rows.append(_row(at, "", lead, "reply", "Reply received", e.get("label") or e.get("kind") or "", e.get("from") or e.get("channel") or "", e.get("text")))
            elif e.get("kind") in ("email_open", "email_click"):
                rows.append(_row(at, "", lead, e["kind"], "Email opened" if e["kind"] == "email_open" else "Link clicked", "",
                                 lead.get("email") or "", e.get("text")))
            elif e.get("kind") == "stage":
                rows.append(_row(at, e.get("by"), lead, "stage", "Stage changed", _stage_target(e) or "", "", e.get("text")))
            elif e.get("kind") == "linkedin" and e.get("by"):
                rows.append(_row(at, e.get("by"), lead, "linkedin", "LinkedIn step", "", lead.get("linkedin_url") or "", e.get("text")))
    for t in store.read("tasks"):
        lead, at = leads.get(t.get("lead_id")), _t(t.get("done_at"))
        if lead and at and inside(at) and (t.get("status") == "done" or t.get("done")):
            rows.append(_row(at, t.get("done_by") or t.get("owner"), lead, "task_done", "Task done", t.get("title") or t.get("kind") or "", "", t.get("text")))
    return sorted(rows, key=lambda r: r["at"], reverse=True)


def _count(rows: list) -> Counter:
    """Counts for a set of log rows. Interested / not interested count each lead once (a call and the stage move it
    caused are one decision)."""
    c = Counter()
    yes, no = set(), set()
    for r in rows:
        if (r["kind"] == "call" and r["outcome"] in ("interested", "meeting_booked")) or (r["kind"] == "stage" and r["outcome"] == "interested"):
            yes.add(r["lead_id"])
        if (r["kind"] == "call" and r["outcome"] == "not_interested") or (r["kind"] == "stage" and r["outcome"] == "disqualified"):
            no.add(r["lead_id"])
        k, o = r["kind"], r["outcome"]
        if k == "lead_added":
            c["leads_added"] += 1
        elif k == "call":
            c["calls"] += 1
            c["calls_connected"] += o in CONNECTED
            c["follow_ups"] += o == "call_back"
            c["bad_numbers"] += o in ("wrong_number", "inactive_number")
            c["meetings"] += o == "meeting_booked"
        elif k == "email_sent" or k == "email":
            c["emails_sent"] += 1
        elif k in ("whatsapp_sent", "whatsapp"):
            c["whatsapp_sent"] += 1
        elif k == "linkedin":
            c["linkedin"] += 1
        elif k == "reply":
            c["replies"] += 1
        elif k == "meeting":
            c["meetings"] += 1
        elif k == "note":
            c["notes"] += 1
        elif k == "stage":
            c["stage_changes"] += 1
        elif k == "task_done":
            c["tasks_done"] += 1
        elif k == "email_open":
            c["opens"] += 1
        elif k == "email_click":
            c["clicks"] += 1
    c["interested"], c["not_interested"] = len(yes), len(no)
    return c


def build(store, rng: dict, who: str = "", kind: str = "") -> dict:
    rows = activity_log(store, rng)
    people = sorted({r["who"] for r in rows})
    if who:
        rows = [r for r in rows if r["who"] == who]
    shown = [r for r in rows if not kind or r["kind"] == kind]
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r)
    first, last = rng["start"].astimezone(IST).date(), (rng["end"] - timedelta(seconds=1)).astimezone(IST).date()
    days, d = [], last
    while d >= first and len(days) < 400:
        key = d.isoformat()
        days.append({"day": key, **{k: _count(by_day.get(key, [])).get(k, 0) for k in COUNTERS}})
        d -= timedelta(days=1)
    per_person = defaultdict(list)
    for r in rows:
        per_person[r["who"]].append(r)
    team = sorted(({"who": p, **{k: _count(v).get(k, 0) for k in COUNTERS}, "actions": len(v)} for p, v in per_person.items()),
                  key=lambda x: -x["actions"])
    total = _count(rows)
    return {"counters": list(COUNTERS), "totals": {k: total.get(k, 0) for k in COUNTERS}, "days": days, "team": team,
            "people": people, "log": shown[:LOG_LIMIT], "log_total": len(shown)}


def csv_text(rows: list) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    cols = ("day", "time_ist", "who", "company", "contact", "what", "outcome", "where", "notes")
    w.writerow(["Date", "Time (IST)", "By", "Company", "Contact", "Action", "Outcome", "Number / email / profile used", "Notes"])
    safe = lambda v: "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v
    for r in rows:
        w.writerow([safe(r.get(c, "")) for c in cols])
    return "﻿" + out.getvalue()
