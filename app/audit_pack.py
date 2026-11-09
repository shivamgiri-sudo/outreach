"""Audit pack: one zip of CSVs for one lead or a date range (owner only).

Files: outbox.csv (emails; an `origin` dict is flattened to origin_* columns), whatsapp.csv, linkedin.csv (LinkedIn
timeline events and agent log lines), change_log.csv, consent.csv, login_history.csv (when that collection exists),
verify.json (the hash-chain check) and README.txt. Rows follow the workspace scope: only leads the caller can see;
rows that belong to no lead (settings changes, logins) are included only when no workspace is selected.
"""
import csv
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

from . import accounts, audit_chain, consent_view, workspaces

IST = timezone(timedelta(hours=5, minutes=30))
MAX_ROWS = 50000


def _day(value) -> str:
    try:
        at = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return ""
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(IST).date().isoformat()


def _cell(value) -> str:
    text = value if isinstance(value, str) else ("" if value is None else json.dumps(value, ensure_ascii=False, default=str))
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text      # no formulas when opened in a spreadsheet


def flatten(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if key == "origin" and isinstance(value, dict):
            out.update({f"origin_{k}": v for k, v in value.items()})
        else:
            out[key] = value
    return out


def to_csv(rows: list, first: tuple = ()) -> str:
    rows = [flatten(r) for r in rows[:MAX_ROWS]]
    fields = list(dict.fromkeys([*first, *(k for r in rows for k in r)])) or list(first) or ["(no rows)"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(fields)
    for r in rows:
        writer.writerow([_cell(r.get(f)) for f in fields])
    return "﻿" + buf.getvalue()


def _optional(store, key: str):
    try:
        return store.read(key)
    except KeyError:
        return None


def build(store, lead_id: str = "", date_from: str = "", date_to: str = "") -> tuple:
    """(zip bytes, summary {file: row count}). Raises LookupError for a lead outside the caller's scope."""
    leads = {l["id"]: l for l in store.read("leads")}
    visible = {i for i, l in leads.items() if workspaces.visible(l)}
    if lead_id and lead_id not in visible:
        raise LookupError("Lead not found")
    unscoped = workspaces._SCOPE.get() == (None, None)

    def in_range(at) -> bool:
        day = _day(at)
        return (not date_from or (day and day >= date_from)) and (not date_to or (day and day <= date_to))

    def keep(row: dict, at_key: str = "at", lead_key: str = "lead_id") -> bool:
        owner = row.get(lead_key)
        if lead_id:
            return owner == lead_id and in_range(row.get(at_key))
        if owner:
            if owner not in visible and not (unscoped and owner not in leads):
                return False
        elif not unscoped and not (row.get("account_id") and row.get("account_id") == accounts.current()
                                   and not accounts.current_is_client()):
            return False                   # a row tied to no lead: only unscoped, or an owner's picked account's own rows
        return in_range(row.get(at_key))

    company = lambda r: (leads.get(r.get("lead_id")) or {}).get("company") or ""
    outbox = [{**r, "company": company(r)} for r in store.read("outbox") if keep(r, "sent_at")]
    whatsapp = [{**r, "company": company(r)} for r in store.read("whatsapp_outbox") if keep(r, "sent_at")]
    linkedin = []
    for lid in ([lead_id] if lead_id else sorted(visible)):
        for t in leads[lid].get("timeline") or []:
            if t.get("kind") == "linkedin" and in_range(t.get("at")):
                linkedin.append({"at": t.get("at"), "lead_id": lid, "company": leads[lid].get("company") or "", "what": t.get("text"),
                                 "by": t.get("by") or "", "where": "lead timeline"})
    from .linkedin_agent import SETTINGS_LOG
    for r in store.read("settings").get(SETTINGS_LOG) or []:
        if r.get("lead_id") and keep(r):
            linkedin.append({"at": r.get("at"), "lead_id": r.get("lead_id"), "company": company(r), "what": f"{r.get('kind')}: {r.get('note')}",
                             "by": r.get("account") or "LinkedIn agent", "where": "agent log"})
    linkedin.sort(key=lambda r: str(r.get("at") or ""))
    change_rows = [r for r in store.read("audit") if not audit_chain.is_anchor(r) and keep(r)]
    consent = []
    for lid in ([lead_id] if lead_id else sorted(visible)):
        for e in consent_view.events_for(store, leads[lid]):
            if in_range(e.get("at")):
                consent.append({"lead_id": lid, "company": leads[lid].get("company") or "", **e})
    files = {"outbox.csv": to_csv(outbox, ("sent_at", "lead_id", "company", "to", "subject", "status", "by", "ai_drafted", "ai_approved_by")),
             "whatsapp.csv": to_csv(whatsapp, ("sent_at", "lead_id", "company", "to", "template", "status", "by", "ai_drafted")),
             "linkedin.csv": to_csv(linkedin, ("at", "lead_id", "company", "what", "by", "where")),
             "change_log.csv": to_csv(change_rows, ("seq", "at", "by", "by_email", "action", "target", "detail", "lead_id", "prev_hash", "hash")),
             "consent.csv": to_csv(consent, ("at", "lead_id", "company", "event", "channel", "source_kind", "source", "by", "text", "from"))}
    counts = {"outbox.csv": len(outbox), "whatsapp.csv": len(whatsapp), "linkedin.csv": len(linkedin),
              "change_log.csv": len(change_rows), "consent.csv": len(consent)}
    logins = _optional(store, "login_history")
    if isinstance(logins, list) and unscoped and not lead_id:
        rows = [r for r in logins if isinstance(r, dict) and in_range(r.get("at") or r.get("time"))]
        files["login_history.csv"] = to_csv(rows)
        counts["login_history.csv"] = len(rows)
    verify = audit_chain.verify(store.read("audit"))
    files["verify.json"] = json.dumps(verify, ensure_ascii=False, indent=1)
    scope = f"lead {lead_id}" if lead_id else "all leads you can see" if unscoped else "the selected workspace"
    files["README.txt"] = (f"Audit pack made {datetime.now(IST).strftime('%d %b %Y %H:%M')} IST for {scope}"
                           f"{f', from {date_from}' if date_from else ''}{f' to {date_to}' if date_to else ''} (IST days).\n"
                           f"Rows: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + "\n"
                           f"Change log check: {verify['message']} {verify.get('note') or ''}\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue(), {**counts, "verify_ok": verify["ok"]}
