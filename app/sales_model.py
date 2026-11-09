"""Sales funnel, part 1: the stages, dates and the rules that stamp them.

Every lead carries an optional `sales` record next to its outreach stage (new / verified / contacted ...), which this
module never touches:

    lead["sales"] = {status, dates{inquiry,mql,sal,sql,meeting,proposal,negotiation,closed,recycled}, first_response,
                     lost_stage, lost_reason, est_value, closed_value, tenure, tcv, prob, backfilled[], history[],
                     industry, service, source, fit, engagement, last_activity}

Nothing is migrated: a lead without the record reads as "Inquiry / Raw Lead" created when the lead was created. A stage
counts as reached when its date is not blank; dates are never cleared. Dates are stored as UTC ISO text; text dates from
files ("yyyy/mm/dd hh:mm") are India time (IST).
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

# The 8 forward stages, in order: key, status label, short name.
STAGES = (
    {"key": "inquiry", "status": "Inquiry / Raw Lead", "short": "Inquiry"},
    {"key": "mql", "status": "Marketing Qualified Lead (MQL)", "short": "MQL"},
    {"key": "sal", "status": "Sales Accepted Lead (SAL)", "short": "SAL"},
    {"key": "sql", "status": "Sales Qualified Lead (SQL)", "short": "SQL"},
    {"key": "meeting", "status": "Discovery Meeting Held", "short": "Meeting"},
    {"key": "proposal", "status": "Proposal Submitted", "short": "Proposal"},
    {"key": "negotiation", "status": "Negotiation / Commercials", "short": "Negotiation"},
    {"key": "won", "status": "Closed Won", "short": "Won"},
)
LOST = "Closed Lost"
RECYCLED = "Recycled / Nurture"
INQUIRY = STAGES[0]["status"]
WON = "Closed Won"
STATUSES = tuple(s["status"] for s in STAGES) + (LOST, RECYCLED)
LOST_REASONS = ("Price", "No Budget", "Chose Competitor", "No Response", "Not ICP", "Timing", "Other")
FITS = ("A", "B", "C")
ENGAGEMENTS = ("Hot", "Warm", "Cold")
# Probability % per stage: Inquiry / MQL / SAL were not given in the spec, so 0 / 5 / 10 are proposed defaults.
PROBABILITY = {"inquiry": 0, "mql": 5, "sal": 10, "sql": 20, "meeting": 35, "proposal": 50, "negotiation": 75, "won": 100}
DEFAULTS = {"sla_hours": 24, "stalled_days": 7, "tenure_months": 12}
# Dates a lead record keeps (the "won" stage uses `closed`, only while the status is Closed Won).
DATE_KEYS = ("inquiry", "mql", "sal", "sql", "meeting", "proposal", "negotiation", "closed", "recycled")
STAGE_DATE_KEY = {"won": "closed"}
MAX_HISTORY = 200
MAX_MONEY = 10 ** 10
SHORT_NAMES = {  # what people type or import for a status
    "inquiry": INQUIRY, "raw lead": INQUIRY, "lead": INQUIRY, "new": INQUIRY, "mql": STAGES[1]["status"], "sal": STAGES[2]["status"],
    "sql": STAGES[3]["status"], "meeting": STAGES[4]["status"], "meeting held": STAGES[4]["status"], "discovery meeting": STAGES[4]["status"],
    "discovery": STAGES[4]["status"], "proposal": STAGES[5]["status"], "negotiation": STAGES[6]["status"], "commercials": STAGES[6]["status"],
    "won": WON, "closed won": WON, "win": WON, "lost": LOST, "closed lost": LOST, "recycled": RECYCLED, "nurture": RECYCLED,
}


def stage_index(status: str) -> int:
    for i, s in enumerate(STAGES):
        if s["status"] == status:
            return i
    return -1


def clean_status(raw) -> str:
    """The exact status label for what was typed or imported ('' when it is none of ours)."""
    text = " ".join(str(raw or "").split())
    if text in STATUSES:
        return text
    key = re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()
    key = " ".join(key.split())
    if key in SHORT_NAMES:
        return SHORT_NAMES[key]
    for status in STATUSES:                                  # "marketing qualified lead", "sales qualified lead (sql)" ...
        plain = " ".join(re.sub(r"[^a-z0-9 ]+", " ", status.lower()).split())
        if key and (key == plain or (len(key) >= 8 and plain.startswith(key + " "))):
            return status
    return ""


# ---------------------------------------------------------------- dates
_ISO_TZ = re.compile(r"[T ]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})$")
_TEXT = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?")
_DMY = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?:[ T](\d{1,2}):(\d{2}))?")


def parse_dt(value) -> Optional[datetime]:
    """An aware datetime from a datetime, ISO text with an offset, or text like 'yyyy/mm/dd hh:mm' (IST wall clock)."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=IST)
    text = str(value).strip()
    if not text:
        return None
    try:
        if _ISO_TZ.search(text):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        m = _TEXT.match(text)
        if m:
            y, mo, d, h, mi = (int(x) if x else 0 for x in m.groups())
            return datetime(y, mo, d, h, mi, tzinfo=IST)
        m = _DMY.match(text)
        if m:
            d, mo, y, h, mi = (int(x) if x else 0 for x in m.groups())
            return datetime(y, mo, d, h, mi, tzinfo=IST)
    except ValueError:
        return None
    return None


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def fmt_ist(dt: datetime) -> str:
    """'yyyy/mm/dd hh:mm' in India time."""
    return dt.astimezone(IST).strftime("%Y/%m/%d %H:%M")


def ist_day(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%Y-%m-%d")


def day_start(ymd: str) -> datetime:
    return datetime.fromisoformat(ymd + "T00:00:00").replace(tzinfo=IST)


def day_end(ymd: str) -> datetime:
    return datetime.fromisoformat(ymd + "T23:59:59.999000").replace(tzinfo=IST)


# ---------------------------------------------------------------- small helpers on the record
def money(value) -> float:
    """A non-negative amount from a number or text like '1,50,000' or '₹ 2.5L'; 0 when it is not one."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        out = float(value)
    else:
        text = str(value or "").lower().replace(",", "").replace("₹", "").replace("rs.", "").replace("rs", "").strip()
        mult = 1
        for suffix, factor in (("cr", 10 ** 7), ("crore", 10 ** 7), ("lakh", 10 ** 5), ("lac", 10 ** 5), ("l", 10 ** 5), ("k", 1000)):
            if text.endswith(suffix):
                text, mult = text[: -len(suffix)].strip(), factor
                break
        try:
            out = float(text) * mult
        except ValueError:
            return 0
    if out != out or out < 0 or out > MAX_MONEY:
        return 0
    return int(out) if out == int(out) else round(out, 2)


def _blank(value) -> bool:
    return value in (None, "", [], {})


def _list(value) -> list:
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    return [str(x).strip() for x in (value or []) if str(x).strip()]


# ---------------------------------------------------------------- the status-change rules
def apply_status(sales: dict, new_status: str, old_status: str, now: datetime) -> dict:
    """Check a status change against the rules and work out every field to write.

    `sales` is the record as it will be (values the person just typed already in it). Returns {"ok": False, "error"}
    (nothing is changed) or {"ok": True, "set": {...}} where set["dates"] holds only the dates to add. Existing stage
    dates are never overwritten; skipped stages get the same time and are marked back-filled.
    """
    s = sales or {}
    dates = s.get("dates") or {}
    blank = lambda key: _blank(dates.get(key))
    stamp = iso(now)
    idx = stage_index(new_status)
    out, new_dates = {}, {}
    if new_status not in STATUSES:
        return {"ok": False, "error": "Pick a Lead Status from the list."}
    if new_status == "Proposal Submitted" and blank("meeting"):
        return {"ok": False, "error": "Proposal Submitted needs a Meeting Held Date first (Proposal Meets Only rule)."}
    if idx >= stage_index("Sales Qualified Lead (SQL)") and not money(s.get("est_value")) > 0:
        return {"ok": False, "error": "Fill Est. Monthly Value (₹) first: it is required from SQL onwards."}
    if new_status == LOST and (s.get("lost_reason") or "") not in LOST_REASONS:
        return {"ok": False, "error": "Pick a Lost Reason first: it is required to mark a lead Closed Lost."}
    if new_status == WON:
        if not money(s.get("closed_value")) > 0:
            return {"ok": False, "error": "Fill Closed Monthly Value (₹) (more than 0) before marking Closed Won."}
        tenure = DEFAULTS["tenure_months"] if _blank(s.get("tenure")) else money(s.get("tenure"))
        if not tenure > 0:
            return {"ok": False, "error": "Contract Tenure (Months) must be more than 0 for Closed Won."}
        if _blank(s.get("tenure")):
            out["tenure"] = DEFAULTS["tenure_months"]
        out["tcv"] = money(s.get("closed_value")) * tenure
    filled = _list(s.get("backfilled"))
    if idx > 0:
        for i in range(1, idx + 1):                          # stamp this stage and back-fill skipped ones (never overwrite)
            key = STAGE_DATE_KEY.get(STAGES[i]["key"], STAGES[i]["key"])
            if blank(key):
                new_dates[key] = stamp
                if i < idx and STAGES[i]["short"] not in filled:
                    filled.append(STAGES[i]["short"])
    if new_status == LOST:
        if blank("closed"):
            new_dates["closed"] = stamp
        at = stage_index(old_status)
        if at < 0:                                           # from Recycled / unknown: the furthest stage reached
            for j in range(len(STAGES) - 1, 0, -1):
                if not blank(STAGE_DATE_KEY.get(STAGES[j]["key"], STAGES[j]["key"])) and STAGES[j]["key"] != "won":
                    at = j
                    break
        out["lost_stage"] = STAGES[max(at, 0)]["status"]
    if new_status == WON and not blank("closed") and old_status == LOST:
        new_dates["closed"] = stamp                          # a lost deal won later: the Closed Date becomes the won date
    if new_status == RECYCLED and blank("recycled"):
        new_dates["recycled"] = stamp                        # later cycles are in the history
    if _blank(s.get("first_response")) and new_status != INQUIRY:
        out["first_response"] = stamp
    key = STAGES[idx]["key"] if idx >= 0 else None
    out["prob"] = 0 if new_status in (LOST, RECYCLED) else PROBABILITY.get(key, 0)
    out["last_activity"] = stamp
    out["backfilled"] = filled
    out["dates"] = new_dates
    return {"ok": True, "set": out}


def recalc(sales: dict, created: Optional[datetime], now: datetime) -> dict:
    """Migration / 'Recalculate stage dates': fill blank stage dates up to the current status with the Created Date
    (the real history is missing) and mark them back-filled. Returns the fields to write (same shape as apply_status)."""
    s = sales or {}
    dates = s.get("dates") or {}
    blank = lambda key: _blank(dates.get(key))
    status = clean_status(s.get("status")) or INQUIRY
    idx = stage_index(status)
    stamp = iso(created or now)
    out, new_dates, filled = {}, {}, _list(s.get("backfilled"))
    if blank("inquiry"):
        new_dates["inquiry"] = stamp
    for i in range(1, idx + 1):
        key = STAGE_DATE_KEY.get(STAGES[i]["key"], STAGES[i]["key"])
        if blank(key):
            new_dates[key] = stamp
            if STAGES[i]["short"] not in filled:
                filled.append(STAGES[i]["short"])
    if status == LOST and blank("closed"):
        new_dates["closed"] = stamp
        filled.append("Closed")
    if status == RECYCLED and blank("recycled"):
        new_dates["recycled"] = stamp
    if status != INQUIRY and _blank(s.get("first_response")):
        out["first_response"] = stamp
    out["prob"] = 0 if status in (LOST, RECYCLED) else PROBABILITY.get(STAGES[idx]["key"], 0)
    if status == WON and money(s.get("closed_value")) > 0:
        out["tcv"] = money(s.get("closed_value")) * (money(s.get("tenure")) or DEFAULTS["tenure_months"])
    if _blank(s.get("last_activity")):
        out["last_activity"] = stamp
    out["backfilled"] = filled
    out["dates"] = new_dates
    return out


def merge_set(sales: dict, changes: dict, status: Optional[str] = None) -> dict:
    """A new record with `changes` (the `set` of apply_status / recalc) folded in; dates merge, never replace."""
    out = {**(sales or {}), **{k: v for k, v in changes.items() if k != "dates"}}
    out["dates"] = {**((sales or {}).get("dates") or {}), **(changes.get("dates") or {})}
    if status:
        out["status"] = status
    return out


def history_row(by: str, old: str, new: str, now: datetime) -> dict:
    return {"at": iso(now), "by": (by or "")[:80], "from": old, "to": new}


def stored_status(sales: dict) -> str:
    return clean_status((sales or {}).get("status")) or INQUIRY
