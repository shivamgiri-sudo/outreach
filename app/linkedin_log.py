"""The LinkedIn agent log (settings["linkedin_agent_log"]): one list for every account.

Sends (connect / message) are the audit trail and feed the caps, so they are kept long (MAX_SENDS, never pushed out by
a burst of lookups or errors); everything else is a short "what it did lately" tail (MAX_LOG).
"""
MAX_LOG = 400
MAX_SENDS = 4000
SEND_KINDS = ("connect", "message")


def append(rows: list, row: dict) -> list:
    """rows + row, trimmed: at most MAX_SENDS send rows and MAX_LOG other rows (the newest of each, order kept)."""
    rows = [*rows, row]
    sends = [i for i, r in enumerate(rows) if r.get("kind") in SEND_KINDS]
    other = [i for i, r in enumerate(rows) if r.get("kind") not in SEND_KINDS]
    keep = set(sends[-MAX_SENDS:]) | set(other[-MAX_LOG:])
    return rows if len(keep) == len(rows) else [r for i, r in enumerate(rows) if i in keep]


def merge_into_recent(rows: list, row: dict, window_iso: str) -> tuple:
    """One real send = one row, whichever call reports it first. If a row for the same lead + kind + account (and the same
    step, when both name one) was written since `window_iso`, fill it in with `row`'s facts instead of adding a second.
    Returns (rows, merged_row, created, previous_row_or_None)."""
    for i in range(len(rows) - 1, max(-1, len(rows) - 80), -1):
        r = rows[i]
        if (r.get("lead_id") == row.get("lead_id") and r.get("kind") == row.get("kind") and r.get("account", "") == row.get("account", "")
                and r.get("at", "") >= window_iso and (not r.get("step") or not row.get("step") or r["step"] == row["step"])):
            merged = {**r, **{k: v for k, v in row.items() if k != "at" and v not in ("", None)}}
            if r.get("mismatch"):
                merged["mismatch"] = True
            return [*rows[:i], merged, *rows[i + 1:]], merged, False, r
    return append(rows, row), row, True, None
