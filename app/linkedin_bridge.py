"""Hand a LinkedIn list to a third-party tool (Waalaxy, Dux-Soup, PhantomBuster...) and read its results back.

Nothing here talks to LinkedIn. The export is a CSV the tool imports (profile link, name, company, title and the
messages from the LinkedIn sequence, already filled in); the import reads the tool's result file and marks matching leads
as accepted or replied through the same functions as the manual buttons. Automating LinkedIn can breach LinkedIn's terms
and put the account at risk: that is the owner's decision and risk, ideally on a separate account.
"""
import csv
import io
import re
from typing import Optional
from urllib.parse import urlsplit

from . import linkedin_seq as li
from .hygiene import safe_http_url
from .sequences import clean_first_name

CLOSED = ("unsubscribed", "disqualified")
MESSAGE_STEPS = (2, 3, 4, 5)              # step 4 (case study) is skipped until a real link is set
MESSAGE_COLUMNS = ["message1", "message2", "message3", "message4", "message5"]
TOOL_COLUMNS = ["firstName", "lastName", "companyName", "title", "profileUrl", *MESSAGE_COLUMNS]
MAX_RESULT_ROWS = 5000
MAX_RESULT_CHARS = 5_000_000
MAX_UNMATCHED_SHOWN = 50
URL_COLUMNS = ("profileurl", "linkedinurl", "linkedinprofileurl", "linkedinprofile", "profile", "url", "linkedin", "publicprofileurl")
STATUS_COLUMNS = ("status", "connectionstatus", "state", "result", "outcome", "campaignstatus", "stage")
ACCEPTED = ("connected", "accepted", "connectionaccepted", "firstdegree", "1stdegree", "message", "messaged", "messagesent")
REPLIED = ("replied", "reply", "responded", "response", "hasreplied", "answered")
PENDING = ("invited", "pending", "connectionsent", "requested", "invitationsent", "sent", "queued", "notconnected")


def _names(contact_name: str) -> tuple:
    first = clean_first_name(contact_name)
    words = [w for w in str(contact_name or "").split() if w]
    if not first:
        return "", ""
    at = next((i for i, w in enumerate(words) if w.strip(".,;:").title() == first), 0)
    return first, " ".join(words[at + 1:])


def profile_key(url) -> str:
    """linkedin.com/in/<name> in lower case, whatever the scheme, www, query, fragment or trailing slash. '' when not a profile."""
    link = safe_http_url(str(url or "").strip())
    if not link:
        return ""
    parts = urlsplit(link)
    host = (parts.hostname or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return ""
    path = re.sub(r"/+", "/", parts.path).rstrip("/").lower()
    return f"linkedin.com{path}" if path.startswith("/in/") and len(path) > 4 else ""


def tool_rows(store, params: Optional[dict] = None) -> list:
    """One row per open lead with a LinkedIn profile link, with the sequence messages written for that person."""
    params = params or {}
    settings = store.read("settings")
    suppressed = set(store.read("suppressed"))
    raw = params.get("ids")
    wanted = {str(i).strip() for i in (raw if isinstance(raw, list) else str(raw or "").split(",")) if str(i).strip()}
    campaign = str(params.get("campaign_id") or "").strip()
    steps = [s for s in li.STEPS if s["n"] == 1 or (s["n"] in MESSAGE_STEPS and (not s.get("needs") or settings.get(s["needs"])))]
    rows = []
    for lead in store.read("leads"):
        link = safe_http_url(lead.get("linkedin_url"))
        if (not link or lead.get("stage") in CLOSED or lead.get("email") in suppressed or (wanted and lead["id"] not in wanted)
                or (campaign and lead.get("campaign_id") != campaign)):
            continue
        first, last = _names(lead.get("contact_name"))
        row = {"firstName": first, "lastName": last, "companyName": lead.get("company") or lead.get("domain") or "",
               "title": lead.get("title") or "", "profileUrl": link, **{c: "" for c in MESSAGE_COLUMNS}}
        for column, step in zip(MESSAGE_COLUMNS, steps):
            row[column] = li.render(step, lead, settings)
        rows.append(row)
    return rows


# ---------- reading the tool's results ----------
def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _pick(row: dict, names: tuple) -> str:
    by_key = {_key(k): v for k, v in row.items() if k}
    return next((str(by_key[n]).strip() for n in names if by_key.get(n) not in (None, "")), "")


def parse_results(text: str) -> list:
    if len(text) > MAX_RESULT_CHARS:
        raise ValueError("That file is too large. Send the results in parts of a few thousand rows.")
    text = text.lstrip("﻿")
    if not text.strip():
        raise ValueError("The file is empty.")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.DictReader(io.StringIO(text), dialect=dialect)][:MAX_RESULT_ROWS + 1]
    if len(rows) > MAX_RESULT_ROWS:
        raise ValueError(f"At most {MAX_RESULT_ROWS} rows at a time.")
    if not rows:
        raise ValueError("No rows found.")
    headers = {_key(h) for h in rows[0] if h}
    if not headers & set(URL_COLUMNS):
        raise ValueError("No profile link column found. The file needs a column such as profileUrl or linkedin_url.")
    if not headers & set(STATUS_COLUMNS):
        raise ValueError("No status column found. The file needs a column such as status (connected, accepted, replied).")
    return rows


def classify(status: str) -> str:
    """'accepted', 'replied', 'pending' or 'unknown' for a tool's status wording."""
    word = _key(status)
    if word in REPLIED:
        return "replied"
    if word in ACCEPTED or word.startswith(("connected", "accepted")):
        return "accepted"
    if word in PENDING:
        return "pending"
    return "unknown"


def import_results(store, text: str) -> dict:
    """Mark matching leads accepted or replied. Match is by LinkedIn profile link; nothing is created or deleted."""
    rows = parse_results(text)
    by_profile = {}
    for lead in store.read("leads"):
        key = profile_key(lead.get("linkedin_url"))
        if key:
            by_profile.setdefault(key, lead["id"])
    out = {"rows": len(rows), "matched": 0, "accepted": 0, "replied": 0, "already_up_to_date": 0, "not_on_sequence": 0,
           "pending_or_unknown": 0, "unmatched": 0, "unmatched_links": [], "problems": []}
    for row in rows:
        key = profile_key(_pick(row, URL_COLUMNS))
        if not key:
            out["unmatched"] += 1
            continue
        lead_id = by_profile.get(key)
        if lead_id is None:
            out["unmatched"] += 1
            if len(out["unmatched_links"]) < MAX_UNMATCHED_SHOWN:
                out["unmatched_links"].append("https://" + key)
            continue
        out["matched"] += 1
        kind = classify(_pick(row, STATUS_COLUMNS))
        lead = next(l for l in store.read("leads") if l["id"] == lead_id)
        state = lead.get("linkedin_seq") or {}
        if kind in ("pending", "unknown"):
            out["pending_or_unknown"] += 1
        elif kind == "replied":
            if state.get("replied_at"):
                out["already_up_to_date"] += 1
            else:
                li.mark_replied(store, lead_id)
                out["replied"] += 1
        elif not state:
            out["not_on_sequence"] += 1
        elif state.get("accepted_at") or state.get("status") in ("stopped", "finished"):
            out["already_up_to_date"] += 1
        else:
            li.mark_accepted(store, lead_id)
            out["accepted"] += 1
    if out["not_on_sequence"]:
        out["problems"].append(f"{out['not_on_sequence']} matched lead(s) are not on the LinkedIn sequence here, so their "
                               "acceptance was not recorded. Start them in LinkedIn outreach first if you want the follow-ups scheduled.")
    if out["pending_or_unknown"]:
        out["problems"].append(f"{out['pending_or_unknown']} row(s) had a status that is not accepted or replied (still pending, "
                               "or wording this app does not know), so they were left as they are.")
    return out
