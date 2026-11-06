"""Downloads: every list in the app as a CSV (opens cleanly in Excel) or as JSON, with the same filters as its page.

- Every CSV cell goes through `csv_safe`, so a value such as =HYPERLINK(...) can't run as a formula in Excel or Sheets.
- CSV files start with a UTF-8 byte-order mark, so Excel shows Indian names and the ₹ sign correctly.
- Each timestamp comes twice: `<field>_ist` (readable, India time) and `<field>` (the raw ISO value, for sorting and tools).
- Secrets are never columns: connection keys, the server secret, webhook secrets, OAuth tokens, demo and referral codes.
"""
import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone

from . import linkedin_bridge, linkedin_seq, source_names
from .activities import ensure_task_ids, lead_activity, now_utc, task_buckets
from .channels.meetings import all_meetings, parse_time
from . import hiring_engine
from .hygiene import csv_safe
from .hygiene_report import duplicate_clusters
from .routes_insights import build_insights

IST = timezone(timedelta(hours=5, minutes=30))
BOM = "﻿"
MAX_IDS = 20000
ROWS_PER_CHUNK = 500
# Lists with people's personal data are owner-only; callers keep their working lists (DPDP: least access).
TEAM_DATASETS = {"tasks", "activity", "linkedin_queue", "meetings"}
OWNER_ONLY = {"leads", "hiring_sheet", "linkedin_tool", "outbox", "inbox", "whatsapp", "transactional", "campaign_leads", "campaign_runs",
              "duplicates", "insights", "suppressed", "lists"}
STATUS_LABEL = {"dry_run": "practice", "provider_error": "provider problem"}
STATUS_FILTER = {"sent": ("sent",), "practice": ("dry_run",), "failed": ("failed", "provider_error")}
TASK_BUCKETS = ("overdue", "today", "upcoming", "no_date", "done")


class ExportError(Exception):
    """A request that can't be answered: `status` is the HTTP code, the message says why in plain words."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def cols(*names) -> list:
    """Column names; a name starting with @ is a timestamp and becomes `<name>_ist` (readable) plus `<name>` (raw ISO)."""
    out = []
    for name in names:
        out += [f"{name[1:]}_ist", name[1:]] if name.startswith("@") else [name]
    return out


def ist_text(value) -> str:
    """'03 Nov 2026, 04:00 PM' in India time for an ISO timestamp ('' when empty; the value itself when unreadable)."""
    if not value:
        return ""
    try:
        when = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    return when.astimezone(IST).strftime("%d %b %Y, %I:%M %p")


def ist_date(now: datetime = None) -> str:
    return (now or now_utc()).astimezone(IST).strftime("%Y-%m-%d")


def _flag(params: dict, key: str) -> bool:
    return str(params.get(key) or "").strip().lower() in ("1", "true", "yes", "on")


def _text(params: dict, key: str) -> str:
    return str(params.get(key) or "").strip()


def _choice(params: dict, key: str, allowed) -> str:
    value = _text(params, key).lower()
    if value and value not in allowed:
        raise ExportError(400, f"{key} must be one of: {', '.join(allowed)}")
    return value


def _companies(store) -> dict:
    return {l["id"]: l.get("company") or l.get("domain") or "" for l in store.read("leads")}


# ---------------------------------------------------------------- leads
LEAD_PLAIN = ("id", "company", "contact_name", "title", "seniority", "department", "email", "phone", "website", "domain",
              "linkedin_url", "city", "state", "country", "sector", "company_size", "open_roles", "role", "source",
              "campaign_id")
LEAD_COLUMNS = cols(*LEAD_PLAIN, "tags", "stage", "stage_name", "score", "band", "consent", "@consent_at", "consent_channel",
                    "consent_proof", "sequence_status", "emails_sent", "@last_activity_at", "owner", "linkedin_status",
                    "replies", "@created_at")


def _sequence_status(seq: dict) -> str:
    if not seq:
        return "not started"
    if seq.get("stopped"):
        return "stopped: " + str(seq["stopped"]).replace("_", " ")
    if seq.get("in_flight"):
        return "sending now"
    if seq.get("blocked"):
        return "waiting: " + str(seq["blocked"])
    return "active"


def _last_activity(lead: dict) -> str:
    stamps = [str(e.get("at") or "") for e in lead.get("timeline") or [] if isinstance(e, dict)]
    return max([lead.get("last_activity_at") or "", *stamps]) or lead.get("created_at") or ""


def _task_owners(store) -> dict:
    """Lead id -> owner of its next open task (used when the lead itself has no owner)."""
    owners = {}
    open_tasks = [t for t in store.read("tasks") if t.get("status", "open") == "open" and not t.get("done") and t.get("owner")]
    for t in sorted(open_tasks, key=lambda t: t.get("due_at") or "9999"):
        owners.setdefault(t.get("lead_id"), t["owner"])
    return owners


def _lead_row(lead: dict, owners: dict, stage_names: dict = None) -> dict:
    """One export row; `stage_names` are the owner's words for stages (Setup → Brand & offer), next to the fixed id."""
    from .brand import DEFAULT_STAGE_LABELS
    names = {**DEFAULT_STAGE_LABELS, **(stage_names or {})}
    consent = lead.get("consent") if isinstance(lead.get("consent"), dict) else {}
    seq = lead.get("sequence") or {}
    return {**{k: lead.get(k) for k in LEAD_PLAIN}, "tags": lead.get("tags") or [], "stage": lead.get("stage"),
            "stage_name": names.get(lead.get("stage"), lead.get("stage") or ""), "score": lead.get("score"), "band": lead.get("band"), "consent": bool(consent), "consent_at": consent.get("at"),
            "consent_channel": consent.get("channel"), "consent_proof": consent.get("text"),
            "sequence_status": _sequence_status(seq), "emails_sent": len(seq.get("sent_days") or []),
            "last_activity_at": _last_activity(lead), "owner": lead.get("owner") or owners.get(lead["id"]) or "",
            "linkedin_status": str((lead.get("linkedin_seq") or {}).get("status") or "").replace("_", " "),
            "replies": (lead.get("engagement") or {}).get("replies", 0), "created_at": lead.get("created_at")}


def _ids(params: dict) -> set:
    raw = params.get("ids")
    items = raw if isinstance(raw, list) else str(raw or "").split(",")
    ids = {str(i).strip() for i in items if str(i).strip()}
    if len(ids) > MAX_IDS:
        raise ExportError(400, f"Pick at most {MAX_IDS} leads at a time.")
    return ids


def _lead_matches(lead: dict, params: dict, ids: set) -> bool:
    q = _text(params, "q").lower()
    source = _text(params, "source").lower()
    text = " ".join(str(lead.get(k) or "") for k in ("company", "domain", "contact_name", "email", "phone", "role")).lower()
    lead_source = str(lead.get("source") or "").lower()
    wanted = {part.strip().lower() for part in source.split(",") if part.strip()}
    lead_sid = str(lead.get("source_id") or source_names.canonical(lead.get("source"))[0]).lower()
    inbound = _text(params, "inbound").lower() in ("1", "true", "yes")
    return ((not ids or lead["id"] in ids) and (not q or q in text)
            and (not inbound or bool(lead.get("inbound")))
            and (not _text(params, "stage") or lead.get("stage") == _text(params, "stage"))
            and (not _text(params, "band") or lead.get("band") == _text(params, "band"))
            and (not wanted or bool(wanted & {lead_source, lead_source.split(":")[0], lead_sid}))
            and (not _text(params, "campaign_id") or lead.get("campaign_id") == _text(params, "campaign_id"))
            and (not _text(params, "tag") or _text(params, "tag").lower() in [str(t).lower() for t in lead.get("tags") or []]))


def leads_rows(store, params: dict, present) -> list:
    ids, owners = _ids(params), _task_owners(store)
    shown = sorted((present(l) for l in store.read("leads")), key=lambda l: -(l.get("score") or 0))
    from .brand import stage_labels
    names = stage_labels(store)
    return [_lead_row(l, owners, names) for l in shown if _lead_matches(l, params, ids)]


def _campaign(store, params: dict) -> dict:
    campaign_id = _text(params, "campaign_id")
    if not campaign_id:
        raise ExportError(400, "Say which campaign: campaign_id is required.")
    campaign = next((c for c in store.read("campaigns") if c.get("id") == campaign_id), None)
    if campaign is None:
        raise ExportError(404, "Campaign not found")
    return campaign


def campaign_leads_rows(store, params: dict, present) -> list:
    campaign = _campaign(store, params)
    checks = {l["id"]: l.get("campaign_check") or {} for l in store.read("leads")}
    rows = leads_rows(store, {"campaign_id": campaign["id"]}, present)
    return [{**r, "ready_for_email": bool(checks.get(r["id"], {}).get("qualified")),
             "not_ready_because": checks.get(r["id"], {}).get("reasons") or []} for r in rows]


def campaign_runs_rows(store, params: dict, present) -> list:
    campaign = _campaign(store, params)
    rows = []
    for run in reversed(campaign.get("runs") or []):
        check = run.get("verification") or {}
        rows.append({**run, "campaign": campaign.get("name"), "lead_count": len(run.get("lead_ids") or []),
                     **{f"verified_{k}": check.get(k, 0) for k in ("valid", "unverified", "invalid", "risky")},
                     "problems": [f"{e.get('source')}: {e.get('message')}" for e in run.get("errors") or [] if isinstance(e, dict)]})
    return rows


# ---------------------------------------------------------------- messages
def outbox_rows(store, params: dict, present) -> list:
    wanted = _choice(params, "status", (*STATUS_FILTER, "delivered"))
    lead_id, names = _text(params, "lead_id"), _companies(store)
    rows = []
    for m in reversed(store.read("outbox")):
        if (wanted == "delivered" and not m.get("delivered_at")) or (wanted in STATUS_FILTER and m.get("status") not in STATUS_FILTER[wanted]):
            continue
        if lead_id and m.get("lead_id") != lead_id:
            continue
        rows.append({**{k: m.get(k) for k in ("sent_at", "lead_id", "to", "step", "variant", "subject", "error", "body")},
                     "company": names.get(m.get("lead_id"), ""), "status": STATUS_LABEL.get(m.get("status"), m.get("status")),
                     "delivered": bool(m.get("delivered_at")), "delivered_at": m.get("delivered_at")})
    return rows


def inbox_rows(store, params: dict, present) -> list:
    matched = _choice(params, "matched", ("yes", "no"))
    channel, label, lead_id, names = _text(params, "channel"), _text(params, "label"), _text(params, "lead_id"), _companies(store)
    rows = []
    for m in reversed(store.read("inbox")):
        if (matched and bool(m.get("lead_id")) != (matched == "yes")) or (channel and m.get("channel") != channel) \
                or (label and m.get("label") != label) or (lead_id and m.get("lead_id") != lead_id):
            continue
        rows.append({**{k: m.get(k) for k in ("received_at", "channel", "from", "subject", "text", "label", "lead_id",
                                               "matched_by", "provider")},
                     "matched": bool(m.get("lead_id")), "company": names.get(m.get("lead_id"), "")})
    return rows


def whatsapp_rows(store, params: dict, present) -> list:
    wanted = _choice(params, "status", tuple(STATUS_FILTER))
    lead_id, names = _text(params, "lead_id"), _companies(store)
    return [{**{k: m.get(k) for k in ("sent_at", "lead_id", "to", "template", "body", "error")},
             "company": names.get(m.get("lead_id"), ""), "status": STATUS_LABEL.get(m.get("status"), m.get("status"))}
            for m in reversed(store.read("whatsapp_outbox"))
            if (not wanted or m.get("status") in STATUS_FILTER[wanted]) and (not lead_id or m.get("lead_id") == lead_id)]


def transactional_rows(store, params: dict, present) -> list:
    wanted = _choice(params, "status", tuple(STATUS_FILTER))
    kind, names = _text(params, "kind"), _companies(store)
    return [{**{k: m.get(k) for k in ("at", "kind", "to", "subject", "body", "error", "lead_id")},
             "company": names.get(m.get("lead_id"), ""), "status": STATUS_LABEL.get(m.get("status"), m.get("status"))}
            for m in reversed(store.read("transactional_outbox"))
            if (not wanted or m.get("status") in STATUS_FILTER[wanted]) and (not kind or m.get("kind") == kind)]


# ---------------------------------------------------------------- tasks and activity
def tasks_rows(store, params: dict, present) -> list:
    """The task board's rows (same owner filter, same hiding of automatic tasks for unsubscribed leads), every done task."""
    owner, status = _text(params, "owner"), _choice(params, "status", ("open", "done"))
    bucket = _choice(params, "bucket", TASK_BUCKETS)
    ensure_task_ids(store)
    leads = {l["id"]: l for l in store.read("leads")}
    rows = []
    for t in store.read("tasks"):
        lead = leads.get(t.get("lead_id")) or {}
        if (owner and t.get("owner") != owner) or (lead.get("stage") == "unsubscribed" and t.get("source") not in ("person", "activity")
                                                    and t.get("kind") != "review"):
            continue
        rows.append({**t, "company": lead.get("company") or lead.get("domain") or "", "lead_stage": lead.get("stage")})
    buckets = {t["id"]: name for name, items in task_buckets(rows, now_utc()).items() for t in items}
    for t in rows:
        t["bucket"] = "done" if t.get("status") == "done" else buckets.get(t["id"], "")
    rows = [t for t in rows if (not status or t.get("status") == status) and (not bucket or t["bucket"] == bucket)]
    return sorted(rows, key=lambda t: (t.get("due_at") or "9999", t.get("created_at") or ""))


def activity_rows(store, params: dict, present) -> list:
    lead_id = _text(params, "lead_id")
    if not lead_id:
        raise ExportError(400, "Say which lead: lead_id is required for the activity download.")
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise ExportError(404, "Lead not found")
    company = lead.get("company") or lead.get("domain") or ""
    return [{**item, "lead_id": lead_id, "company": company, "direction": "" if item.get("direction") == "none" else item.get("direction")}
            for item in lead_activity(store, lead)]


# ---------------------------------------------------------------- other pages
def _when(params: dict, key: str):
    raw = _text(params, key)
    if not raw:
        return None
    try:
        return parse_time(raw)
    except ValueError:
        raise ExportError(400, f"'{key}' must be a date-time like 2026-11-05T10:00:00+05:30") from None


def meetings_rows(store, params: dict, present) -> list:
    status = _text(params, "status")
    return [m for m in all_meetings(store, _when(params, "from"), _when(params, "to")) if not status or m.get("status") == status]


def duplicates_rows(store, params: dict, present) -> list:
    rows = []
    for n, cluster in enumerate(duplicate_clusters(store.read("leads")), start=1):
        for lead in cluster["leads"]:
            rows.append({**lead, "group": n, "group_size": len(cluster["lead_ids"]), "why": cluster["why"],
                         "suggested_keep": lead["id"] == cluster["suggested_keep"], "lead_id": lead["id"]})
    return rows


def insights_rows(store, params: dict, present) -> list:
    data = build_insights(store, present)
    rows = [{"section": s, "item": "", "metric": k, "value": v} for s in ("totals", "outreach") for k, v in data[s].items()]
    rows += [{"section": "by_source", "item": r["source"], "metric": k, "value": r[k]}
             for r in data["by_source"] for k in ("leads", "replied", "interested", "customers")]
    rows += [{"section": "by_sector", "item": r["sector"], "metric": "leads", "value": r["leads"]} for r in data["by_sector"]]
    rows += [{"section": "by_band", "item": band, "metric": "leads", "value": n} for band, n in data["by_band"].items()]
    rows += [{"section": "top_leads", "item": r["company"], "metric": k, "value": r[k]} for r in data["top_leads"] for k in ("score", "stage")]
    return rows


def lists_rows(store, params: dict, present) -> list:
    """Every lead list in the caller's workspace scope with its numbers and rates."""
    from . import lists as lists_mod, workspaces
    return [{"name": r.get("name"), "created_by": r.get("created_by"), "created_at": r.get("created_at"), **lists_mod.stats(store, r)}
            for r in reversed(workspaces.filter_rows(store.read("lists")))]


def linkedin_rows(store, params: dict, present) -> list:
    q = linkedin_seq.queue(store)
    return ([{**d, "section": "to send today"} for d in q["due"]]
            + [{**w, "section": "waiting for acceptance"} for w in q["waiting_for_acceptance"]]
            + [{**s, "section": "suggested"} for s in q["suggestions"]])


def linkedin_tool_rows(store, params: dict, present) -> list:
    """People with a LinkedIn link and their sequence messages, in the columns third-party LinkedIn tools import."""
    return linkedin_bridge.tool_rows(store, params)


def suppressed_rows(store, params: dict, present) -> list:
    by_email = {}
    for lead in store.read("leads"):
        if lead.get("email"):
            by_email.setdefault(lead["email"], lead)
    rows = []
    for value in store.read("suppressed"):
        lead = by_email.get(value) or {}
        hashed = bool(re.fullmatch(r"[0-9a-f]{64}", str(value)))
        rows.append({"value": value, "type": "sha256 hash" if hashed else "email", "lead_id": lead.get("id"),
                     "company": lead.get("company") or lead.get("domain") or ""})
    return rows


# dataset: (builder, columns, filters people can pass)
DATASETS = {
    "leads": (leads_rows, LEAD_COLUMNS, ("stage", "band", "source", "inbound", "campaign_id", "tag", "q", "ids")),
    "outbox": (outbox_rows, cols("@sent_at", "lead_id", "company", "to", "step", "variant", "subject", "status", "delivered",
                                 "@delivered_at", "error"), ("status", "lead_id", "include_body")),
    "inbox": (inbox_rows, cols("@received_at", "channel", "from", "subject", "text", "label", "matched", "lead_id", "company",
                               "matched_by", "provider"), ("matched", "channel", "label", "lead_id")),
    "whatsapp": (whatsapp_rows, cols("@sent_at", "lead_id", "company", "to", "template", "body", "status", "error"), ("status", "lead_id")),
    "transactional": (transactional_rows, cols("@at", "kind", "to", "subject", "body", "status", "error", "lead_id", "company"),
                      ("kind", "status")),
    "tasks": (tasks_rows, cols("id", "title", "text", "kind", "priority", "owner", "status", "bucket", "@due_at", "@created_at",
                               "@done_at", "done_note", "lead_id", "company", "lead_stage", "source"), ("owner", "status", "bucket")),
    "activity": (activity_rows, cols("@at", "lead_id", "company", "type", "direction", "title", "body", "status", "detail", "to",
                                     "@due_at", "owner", "@start", "@end", "@delivered_at", "@done_at", "error"), ("lead_id",)),
    "campaign_leads": (campaign_leads_rows, [*LEAD_COLUMNS, "ready_for_email", "not_ready_because"], ("campaign_id",)),
    "campaign_runs": (campaign_runs_rows, cols("campaign", "id", "@started_at", "@finished_at", "trigger", "dry_run", "discovered",
                                               "new", "merged", "attached", "duplicates", "suppressed_skipped", "over_cap",
                                               "skipped_no_contact", "enriched_emails", "enriched_phones", "verified_valid",
                                               "verified_unverified", "verified_invalid", "verified_risky", "qualified", "enrolled",
                                               "credits_used_estimate", "lead_count", "problems"), ("campaign_id",)),
    "meetings": (meetings_rows, cols("@start", "@end", "title", "provider", "status", "join_url", "attendee_email", "lead_id",
                                     "company", "contact_name", "@created_at", "id"), ("from", "to", "status")),
    "duplicates": (duplicates_rows, cols("group", "group_size", "why", "suggested_keep", "lead_id", "company", "contact_name", "title",
                                         "email", "phones", "domain", "city", "stage", "source", "@created_at", "events", "emailed",
                                         "has_consent", "other_contacts"), ()),
    "insights": (insights_rows, ["section", "item", "metric", "value"], ()),
    "linkedin_queue": (linkedin_rows, cols("section", "lead_id", "company", "name", "title", "linkedin_url", "step", "persona",
                                           "@due_at", "@since", "message", "task_id"), ()),
    "linkedin_tool": (linkedin_tool_rows, linkedin_bridge.TOOL_COLUMNS, ("ids", "campaign_id")),
    "hiring_sheet": (lambda store, params, present: hiring_engine.sheet_rows(store), hiring_engine.SHEET_COLUMNS, ()),
    "suppressed": (suppressed_rows, ["value", "type", "lead_id", "company"], ()),
    "lists": (lists_rows, cols("name", "created_by", "@created_at", "members", "companies", "with_email", "email_started", "emailed",
                               "opened", "open_rate", "replied", "reply_rate", "linkedin_started", "linkedin_sent", "linkedin_accepted",
                               "accept_rate", "interested"), ()),
}


def catalogue() -> list:
    return [{"dataset": name, "columns": columns, "filters": list(filters), "owner_only": name in OWNER_ONLY}
            for name, (_, columns, filters) in DATASETS.items()]


def build(store, dataset: str, params: dict, present) -> tuple:
    """(columns, rows) for one dataset; each row holds exactly the columns, `_ist` columns filled in."""
    if dataset not in DATASETS:
        raise ExportError(404, f"Unknown download '{dataset}'. Choose one of: {', '.join(DATASETS)}")
    builder, columns, _ = DATASETS[dataset]
    if dataset == "outbox" and _flag(params, "include_body"):
        columns = [*columns, "body"]
    rows = builder(store, params, present)
    out = []
    for row in rows:
        out.append({c: (ist_text(row.get(c[:-4])) if c.endswith("_ist") and c not in row else row.get(c)) for c in columns})
    return columns, out


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return "; ".join(_cell(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def csv_chunks(columns: list, rows: list):
    """The CSV text in pieces: byte-order mark and header first, then a few hundred rows at a time."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([csv_safe(c) for c in columns])
    yield BOM + buffer.getvalue()
    for start in range(0, len(rows), ROWS_PER_CHUNK):
        buffer.seek(0)
        buffer.truncate()
        for row in rows[start:start + ROWS_PER_CHUNK]:
            writer.writerow([csv_safe(_cell(row[c])) for c in columns])
        yield buffer.getvalue()


def filename(dataset: str, params: dict, extension: str, now: datetime = None) -> str:
    """outreach-leads-2026-11-03.csv; per-lead and per-campaign files carry that id too."""
    which = _text(params, "lead_id") if dataset == "activity" else _text(params, "campaign_id") if dataset.startswith("campaign_") else ""
    which = re.sub(r"[^A-Za-z0-9_-]", "", which)[:40]
    return f"outreach-{dataset.replace('_', '-')}{'-' + which if which else ''}-{ist_date(now)}.{extension}"
