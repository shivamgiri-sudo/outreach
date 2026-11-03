"""Sheet sync: pull new rows from a published Google Sheet or CSV link into the lead list.

The link is fetched through the guarded client (public https addresses only, redirects re-checked, size and time
limits). Columns are matched with the same header mapper as the importer. A row is identified by a hash of its
cleaned values, remembered on the connector, so running the sync twice never adds a row twice.
"""
import hashlib
import json
import re

from . import capture_connectors as connectors
from .channels.netguard import BlockedAddress, guarded_request
from .inbound_fields import extract
from .inbound_leads import capture, log
from .lead_import import quick_rows

MAX_BYTES = 5_000_000
MAX_NEW_ROWS = 500
MAX_HASHES = 20_000
SHEETS = re.compile(r"^https://docs\.google\.com/spreadsheets/d/(?!e/)([A-Za-z0-9_-]+)(?:/[a-z]+)?(?:[?#].*)?$")


class SheetError(RuntimeError):
    pass


def csv_url(url: str) -> str:
    """A Google Sheets edit link becomes its CSV export link; publish-to-web and plain CSV links are used as they are."""
    text = (url or "").strip()
    match = SHEETS.match(text)
    if match:
        gid = re.search(r"gid=(\d+)", text)
        return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")
    return text


def default_fetch(url: str) -> str:
    try:
        resp = guarded_request(url, "GET", {"Accept": "text/csv,*/*"}, timeout=20, max_bytes=MAX_BYTES + 1, max_redirects=3)
    except BlockedAddress as exc:
        raise SheetError("That address points to a private or internal network, which is not allowed.") from exc
    except ValueError as exc:
        raise SheetError(str(exc)) from exc
    except OSError as exc:
        raise SheetError("The sheet could not be fetched. Check that it is published to the web.") from exc
    if resp.status != 200:
        raise SheetError(f"The sheet answered with error {resp.status}. Check that it is published to the web.")
    if len(resp.body) > MAX_BYTES:
        raise SheetError("The sheet is larger than 5 MB.")
    return resp.body.decode("utf-8-sig", "replace")


def row_hash(row: dict) -> str:
    cleaned = sorted((k, " ".join(str(v).lower().split())) for k, v in row.items() if str(v).strip())
    return hashlib.sha1(json.dumps(cleaned, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]


def sync(store, connector: dict, fetcher=None) -> dict:
    """Fetch the sheet and capture every row not seen before. Safe to run as often as you like."""
    fetcher = fetcher or default_fetch
    url = csv_url(connector.get("sheet_url") or "")
    if not url:
        raise SheetError("Save the sheet link first.")
    try:
        text = fetcher(url)
        rows = quick_rows(text) if text.strip() else []
    except ValueError as exc:
        raise SheetError(str(exc)) from exc
    seen = list(connector.get("row_hashes") or [])
    known = set(seen)
    stats = {"rows": len(rows), "new": 0, "merged": 0, "refused": 0, "replay": 0, "skipped": 0, "errors": 0, "already_seen": 0}
    fresh = []
    for row in rows:
        h = row_hash(row)
        if h in known:
            stats["already_seen"] += 1
            continue
        if len(fresh) >= MAX_NEW_ROWS:
            break
        known.add(h)
        fresh.append((h, row))
    done = []
    for h, row in fresh:
        parsed = extract(row)
        result = capture(store, connector, parsed, detail="Google Sheet", consent_default=bool(connector.get("assume_consent")))
        key = {"new": "new", "merged": "merged", "refused": "refused", "replay": "replay"}.get(result["outcome"])
        if key:
            stats[key] += 1
        else:
            stats["skipped" if result.get("http") == 422 else "errors"] += 1
        done.append(h)
    seen = (seen + done)[-MAX_HASHES:]
    connectors.update_connector(store, connector["id"], lambda c: {**c, "row_hashes": seen, "last_sync_at": connectors.now_iso()})
    return stats
