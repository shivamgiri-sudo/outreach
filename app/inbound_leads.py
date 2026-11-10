"""The inbound capture engine: one lead from an ad, a form or a webhook goes through the same steps whatever sent it.

normalise -> refuse if suppressed or erased -> skip a replay -> find an existing lead (never drop a duplicate) ->
merge or create -> record only the consent the person actually gave -> a "Call now" task and a notification -> log.
It never sends an email or a WhatsApp message; sending stays under the existing rules and practice mode.
"""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from . import capture_connectors as connectors
from .activities import add_task, caller_name
from .capture_catalogue import BY_ID
from .hygiene import safe_http_url
from .lead_merge import CLOSED_STAGES, LeadIndex, lead_domain, merge_record
from .lead_model import build_lead, event
from .places import clean_city, clean_country
from .privacy import is_blocked, record_consent
from .reminders import notify
from .source_names import add_touch, label_of
from .standardise import best_phone, clean_company, clean_email, clean_person_name, clean_title, parse_phones

LOG_CAP = 500
SEEN_CAP = 5000
TEST_COMPANY = "ZZ Test capture"
HASH_WINDOW = timedelta(minutes=10)         # the same details twice within this are one submission (a double click)
KEY_WINDOW = timedelta(hours=24)            # an Idempotency-Key header is remembered this long


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def normalise(fields: dict) -> dict:
    """Hygiene on the raw fields: names, company, e-mail, phone (+91 form), city, website."""
    email, email_problem = clean_email(fields.get("email"))
    numbers = parse_phones(fields.get("phone"))["numbers"]
    phone, phone_type = best_phone(numbers)
    raw_site = (fields.get("website") or "").strip()
    site = safe_http_url(raw_site if "//" in raw_site else f"https://{raw_site}") if raw_site and " " not in raw_site and "." in raw_site else ""
    company, legal = clean_company(fields.get("company"))
    return {"email": email or "", "email_bad": bool(fields.get("email")) and not email, "phone": phone or "",
            "phone_type": phone_type or "", "phones": [n for n, _ in numbers],
            "phone_bad": bool(fields.get("phone")) and not phone,
            "contact_name": clean_person_name(fields.get("name")), "title": clean_title(fields.get("title")),
            "company": company, "company_legal": legal, "city": clean_city(fields.get("city")),
            "country": clean_country(fields.get("country")), "website": site,
            "message": (fields.get("message") or "")[:1000]}


def _hash(connector_id: str, rec: dict, parsed: dict) -> str:
    material = json.dumps([connector_id, rec["email"], rec["phone"], rec["contact_name"].lower(), rec["company"].lower(),
                           (parsed["attribution"].get("form_id") or parsed["attribution"].get("form_name") or ""), rec["message"],
                           parsed.get("sales") or ""],               # a sheet row whose sales columns changed is not a double click
                          ensure_ascii=False)
    return "h:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _seen_lookup(store, key: str, window: timedelta, now: datetime):
    for row in store.read("capture_seen"):
        if row.get("key") == key:
            if window is None or datetime.fromisoformat(row["at"]) > now - window:
                return row
    return None


def _remember(store, key: str, lead_id: str, now: datetime) -> None:
    row = {"key": key, "at": _iso(now), "lead_id": lead_id}
    store.update("capture_seen", lambda rows: [*rows[-(SEEN_CAP - 1):], row])


def log(store, connector: dict, outcome: str, **info) -> dict:
    """Append one row to the capture log (last 500). Holds ids and a short reason, never names, e-mails or phones."""
    row = {"id": uuid.uuid4().hex[:10], "at": _iso(now_utc()), "connector_id": connector["id"], "connector": connector.get("name", ""),
           "type": connector.get("type"), "source_id": BY_ID.get(connector.get("type"), {}).get("source_id", ""),
           "outcome": outcome, "lead_id": info.get("lead_id"), "platform_ids": info.get("platform_ids") or {},
           "error": (info.get("error") or "")[:200], "is_test": bool(info.get("is_test"))}
    store.update("capture_log", lambda rows: [*rows[-(LOG_CAP - 1):], row])
    connectors.bump(store, connector["id"], outcome)
    return row


def _consent_channels(rec: dict, answers: dict, whatsapp_first: bool, closed: bool, default: bool = True) -> list:
    """Which channels the person actually agreed to. A "no" anywhere wins; WhatsApp needs its own yes or a first message.
    With default=False (rows of a sheet nobody vouched for) only an explicit yes counts."""
    if closed or answers.get("general") is False:
        return []
    if whatsapp_first:
        return ["whatsapp"] if rec["phone"] and answers.get("whatsapp") is not False else []     # they only wrote to us on WhatsApp
    out = []
    yes = lambda key: answers.get(key) is True or (default and answers.get(key) is not False)
    if rec["email"] and yes("email"):
        out.append("email")
    if rec["phone"] and yes("call"):
        out.append("call")
    if rec["phone"] and answers.get("whatsapp") is not False and (answers.get("whatsapp") is True or whatsapp_first):
        out.append("whatsapp")
    return out


def _find_flow(store, flow_id: str):
    try:
        return next((f for f in store.read("flows") if f.get("id") == flow_id and f.get("status") == "active"), None)
    except KeyError:
        return None


def capture(store, connector: dict, parsed: dict, *, detail: str = "", idem_key: str = "", is_test: bool = False,
            whatsapp_first: bool = False, source_id: str = "", now: datetime = None, consent_default: bool = True) -> dict:
    """Run one lead through the engine. Returns {"outcome": new|merged|refused|replay|error, "lead_id", "http", ...}."""
    now = now or now_utc()
    spec = BY_ID.get(connector["type"], {})
    sid = source_id or spec.get("source_id", "other_inbound")
    label = label_of(sid)
    is_test = is_test or parsed.get("is_test", False)
    rec = normalise(parsed["fields"])
    ids = {k: v for k, v in (parsed.get("ids") or {}).items() if v}
    platform_id = parsed.get("platform_id") or ""
    attribution = {**parsed["attribution"], "captured_at": _iso(now)}
    if spec.get("mode") == "meta":
        attribution.setdefault("platform", "meta")
    if not (rec["email"] or rec["phone"] or rec["website"]):
        reason = "No usable e-mail or phone number in this lead."
        row = log(store, connector, "error", error=reason, platform_ids=ids, is_test=is_test)
        return {"outcome": "error", "http": 422, "error": reason, "log_id": row["id"]}
    if is_blocked(store, rec["email"] or None, rec["phone"] or None):
        log(store, connector, "refused", error="refused: suppressed/erased", platform_ids=ids, is_test=is_test)
        return {"outcome": "refused", "http": 200, "reason": "suppressed/erased"}
    keys = []
    if idem_key:
        keys.append((f"{connector['id']}:k:{idem_key}"[:200], KEY_WINDOW))
    if platform_id:
        keys.append((f"{connector['id']}:p:{platform_id}"[:200], None))
    if not is_test:
        keys.append((f"{connector['id']}:{_hash(connector['id'], rec, parsed)}", HASH_WINDOW))
        for key, window in keys:
            seen = _seen_lookup(store, key, window, now)
            if seen:
                log(store, connector, "replay", lead_id=seen.get("lead_id"), platform_ids=ids)
                return {"outcome": "replay", "http": 200, "lead_id": seen.get("lead_id"), "duplicate": True}
    detail = (detail or attribution.get("form_name") or attribution.get("campaign_name") or "")[:200]
    at = _iso(now)
    result = {}
    if connector.get("account_id") and not is_test:                  # a client account's connector counts as imports
        from . import account_limits
        try:
            account_limits.check(store, connector["account_id"], "imports_per_day", 1)
        except account_limits.LimitReached as exc:
            log(store, connector, "error", error=exc.message, platform_ids=ids, is_test=is_test)
            return {"outcome": "error", "http": 429, "error": exc.message}

    def apply(leads: list) -> list:
        index = LeadIndex(leads)
        row = {**rec, "emails": [rec["email"]] if rec["email"] else [], "phones": rec["phones"] or ([rec["phone"]] if rec["phone"] else []),
               "domain": lead_domain({"website": rec["website"], "email": rec["email"]}) or None}
        hit = index.match(row)
        if hit is not None and is_test and not leads[hit[0]].get("is_test"):
            hit = None                           # a test lead never merges into a real person
        about = f" ({detail})" if detail else ""
        if hit is not None:
            i, who, why = hit
            before = leads[i]
            merged, info = merge_record(before, row, who, sid, "", at, index.keys, touch=False)
            if info["changed"] and merged["timeline"] and merged["timeline"][-1].get("kind") == "merged":
                merged = {**merged, "timeline": merged["timeline"][:-1]}      # the "Inbound from ..." event below says it better
            lead = {**merged, "timeline": list(merged.get("timeline") or [])}
            result.update(kind="merged", closed=lead.get("stage") in CLOSED_STAGES or bool(lead.get("consent_withdrawn_at")),
                          stage=lead.get("stage"), matched_by=why)
            lead["timeline"].append(event("inbound", f"Inbound from {label}{about}; matched by {why}"))
            add_touch(lead, sid, detail, attribution, at, every=True)
            leads[i] = lead
        else:
            lead = build_lead({"company": TEST_COMPANY if is_test else rec["company"], "contact_name": rec["contact_name"],
                               "title": rec["title"], "email": rec["email"], "phone": rec["phone"] if rec["phone"].startswith("+91") else "",
                               "website": rec["website"], "country": rec["country"]}, sid, allow_company_key=True)
            if lead is None:
                result.update(kind="error")
                return leads
            lead["timeline"] = [event("created", f"Inbound from {label}{about}")]
            if rec["city"]:
                lead["city"] = rec["city"]
            if len(rec["phones"]) > 1 or (rec["phones"] and not lead["phone"]):
                lead["phones"] = rec["phones"]
            lead["sources"] = [{"id": sid, "at": at, "detail": detail, "attribution": attribution}]
            if lead["dedupe_key"] in index.keys:
                lead["dedupe_key"] = f"{lead['dedupe_key']}#{lead['id']}"
            leads.append(lead)
            result.update(kind="new", closed=False, stage="new")
        lead = leads[i] if hit is not None else leads[-1]
        lead["inbound"] = True
        _sales(lead, parsed.get("sales"), result["kind"] == "new", sid, at)
        lead.setdefault("first_inbound_at", at)
        if not lead.get("attribution"):
            lead["attribution"] = attribution
        if connector.get("default_tags"):
            lead["tags"] = list(dict.fromkeys([*(lead.get("tags") or []), *connector["default_tags"]]))
        if is_test:
            lead["is_test"] = True
            lead["tags"] = list(dict.fromkeys([*(lead.get("tags") or []), "test"]))
            lead["capture_connector_id"] = connector["id"]
        if rec["message"]:
            lead["timeline"].append(event("note", f"They wrote: {rec['message'][:500]}"))
        if parsed.get("custom"):
            lead["custom_fields"] = {**(lead.get("custom_fields") or {}), **parsed["custom"]}
        if rec["email_bad"] and not rec["email"]:
            lead["timeline"].append(event("note", "The e-mail they typed did not look valid, so it was not saved."))
        channels = [] if is_test else _consent_channels(rec, parsed.get("consent") or {}, whatsapp_first, result["closed"], consent_default)
        if channels:
            proof = f"{label}: {detail or connector.get('name', '')}"[:200]
            given = dict(lead.get("inbound_consent") or {})
            for ch in channels:
                given[ch] = {"text": f"Submitted {proof}" if ch != "whatsapp" or not whatsapp_first else f"Messaged us first on WhatsApp ({proof})",
                             "at": at, "source": f"inbound:{sid}"}
            lead["inbound_consent"] = given
            if "whatsapp" in channels and not lead.get("consent"):
                lead["consent"] = {"text": given["whatsapp"]["text"], "at": at, "source": f"inbound:{sid}", "channel": "whatsapp"}
            lead["timeline"].append(event("consent", f"Agreed to be contacted by {', '.join(channels)} through {label}"))
        if whatsapp_first and "whatsapp" in channels:
            lead["whatsapp_last_inbound_at"] = at
        result.update(lead_id=lead["id"], channels=channels, has_phone=bool(lead.get("phone")), company=lead.get("company"))
        return leads

    store.update("leads", apply)
    if result.get("kind") not in ("new", "merged"):
        reason = "The lead could not be saved (no usable contact detail)."
        log(store, connector, "error", error=reason, platform_ids=ids, is_test=is_test)
        return {"outcome": "error", "http": 422, "error": reason}
    lead_id = result["lead_id"]
    if result["kind"] == "new" and not is_test:                     # no human uploader: the assignment rules pick the owner
        try:
            from .upload_owner import assign_connector_lead
            assign_connector_lead(store, lead_id)
        except Exception:                                           # assignment must never lose a captured lead
            pass
    for ch in result["channels"]:
        proof = f"{label}: {detail or connector.get('name', '')}"[:200]
        text = f"Messaged us first on WhatsApp ({proof})" if ch == "whatsapp" and whatsapp_first else f"Submitted {proof}"
        record_consent(store, lead_id, "given", ch, text, f"inbound:{sid}")
    if not is_test:
        for key, _ in keys:
            _remember(store, key, lead_id, now)
    task = None
    if not is_test and connector.get("call_task", True):
        task = _speed_to_lead(store, connector, lead_id, label, detail, result, now)
    flow = None
    if not is_test and connector.get("enroll_flow_id") and result["kind"] == "new" and not result["closed"]:
        flow = _enroll(store, connector["enroll_flow_id"], lead_id, now)
    log(store, connector, result["kind"], lead_id=lead_id, platform_ids=ids, is_test=is_test)
    return {"outcome": result["kind"], "http": 200, "lead_id": lead_id, "new": result["kind"] == "new",
            "merged": result["kind"] == "merged", "matched_by": result.get("matched_by"), "consent": result["channels"],
            "task_id": (task or {}).get("id"), "flow": flow, "is_test": is_test}


def _sales(lead: dict, incoming: dict, is_new: bool, sid: str, at: str) -> None:
    """Sales funnel: a new inbound lead enters at Inquiry with its Lead Source (Meta Ads ...); a sheet row's sales columns
    update the record without overwriting stage dates (app/sales_import.py)."""
    if is_new and not lead.get("sales"):
        from .sales_view import SALES_SOURCE
        lead["sales"] = {"source": SALES_SOURCE.get(sid) or label_of(sid), "dates": {"inquiry": at}}
    if not incoming:
        return
    from . import sales_import, sales_model
    when = sales_model.parse_dt(at)
    if incoming.get("sales"):
        new, _warnings, changed = sales_import.apply_incoming(lead.get("sales"), incoming["sales"], sales_model.parse_dt(lead.get("created_at")) or when, when, "sheet")
        if changed:
            lead["sales"] = new
    if incoming.get("owner") and not lead.get("owner"):
        lead.update(owner=incoming["owner"], owner_at=at)


def _speed_to_lead(store, connector: dict, lead_id: str, label: str, detail: str, result: dict, now: datetime) -> dict:
    owner = connector.get("default_owner") or caller_name(store)
    about = f" ({detail})" if detail else ""
    if result["closed"]:
        title, text = (f"Check before calling: {label} lead had opted out",
                       f"This person contacted us through {label}{about} but had unsubscribed or withdrawn consent earlier. "
                       "Look at the lead before contacting them.")
    elif result["has_phone"]:
        title, text = f"Call now: inbound lead from {label}", f"New inbound lead from {label}{about}. Call as soon as you can; leads called within minutes convert far better."
    else:
        title, text = (f"Reply now: inbound lead from {label} (no phone)",
                       f"New inbound lead from {label}{about} with no phone number. Reply by e-mail by hand.")
    task = add_task(store, lead_id, "call" if result["has_phone"] and not result["closed"] else "follow_up", text, due_at=now, owner=owner,
                    priority="high", title=title, source="inbound", dedupe_open=True)
    notify(store, owner, title, text, lead_id=lead_id, task_id=task.get("id"), kind="inbound")
    return task


def _enroll(store, flow_id: str, lead_id: str, now: datetime):
    from .flow_engine import FlowError, enroll
    flow = _find_flow(store, flow_id)
    if flow is None:
        return {"enrolled": False, "reason": "That flow is not active."}
    try:
        enroll(store, flow, lead_id, now, why="inbound capture", via="import")
        return {"enrolled": True}
    except (LookupError, FlowError) as exc:
        return {"enrolled": False, "reason": str(exc)}


def capture_error(store, connector: dict, message: str, ids: dict = None) -> None:
    log(store, connector, "error", error=message, platform_ids=ids or {})


def remove_test_leads(store, connector_id: str = "") -> int:
    """Delete the labelled test leads (and the tasks and notifications raised for them)."""
    gone = {}

    def apply(leads: list) -> list:
        keep = []
        for lead in leads:
            if lead.get("is_test") and (not connector_id or lead.get("capture_connector_id") == connector_id):
                gone[lead["id"]] = True
            else:
                keep.append(lead)
        return keep
    store.update("leads", apply)
    for key in ("tasks", "notifications"):
        store.update(key, lambda rows: [r for r in rows if r.get("lead_id") not in gone])
    return len(gone)


def capture_whatsapp(store, msg: dict):
    """An unknown number messaged us first on WhatsApp: make it a lead when a WhatsApp click-to-chat connector is on.
    Returns the lead id, or None (no connector, an opt-out or erasure request, or a number that cannot be a lead)."""
    from .privacy import asks_for_erasure
    from .replies import classify_reply, strip_quoted
    connector = next((c for c in connectors.all_connectors(store) if c.get("type") == "whatsapp_click" and c.get("enabled", True)), None)
    text = msg.get("text") or ""
    if connector is None or not msg.get("from_phone") or asks_for_erasure(text) or classify_reply(strip_quoted(text) or text.strip()) == "unsubscribe":
        return None
    parsed = {"fields": {"phone": msg["from_phone"], "name": msg.get("from_name") or "", "message": ""}, "custom": {},
              "attribution": {"platform": "whatsapp"}, "ids": {}, "consent": {},
              "platform_id": (msg.get("message_id") or msg.get("raw_id") or "")[:100]}
    result = capture(store, connector, parsed, detail="WhatsApp message", whatsapp_first=True)
    return result.get("lead_id") if result["outcome"] in ("new", "merged") else None
