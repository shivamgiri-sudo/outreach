"""Meta Lead Ads pull mode: read new leads from Meta's Graph API on a timer, with no Meta app, webhook or app secret.

Only a Page access token with leads_retrieval (and the form ids, or a page id to find the forms) is needed. Every
10 minutes (and on "Pull now") each form's leads are read with GET /{form_id}/leads, page by page, newer than the
form's cursor (the newest created_time already handled, minus a small overlap). The first run reads everything Meta
still keeps (90 days). Each lead goes through meta_lead and the normal capture engine, so a pulled lead is the same
as one the webhook brought in; the Meta lead id stops a lead being added twice, whichever way it came.

The token travels only in the request address (as Meta documents), through the injectable guarded transport. It is
never logged, stored in a message or returned: Meta's own `paging.next` links (which carry the token) are not used.
"""
import json
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from . import capture_connectors as connectors
from .inbound_leads import capture, capture_error
from .inbound_platforms import DEFAULT_GRAPH_VERSION, GRAPH_HOST, PlatformError, graph_get, meta_lead

PULL_EVERY = timedelta(minutes=10)
OVERLAP = timedelta(minutes=5)                 # a lead Meta stamps a moment late is still read; the lead id stops a repeat
MAX_BACKOFF = timedelta(hours=6)
PAGE_SIZE = 100
MAX_PAGES = 100                                # per form and run: 10,000 leads
MAX_NEW_PER_RUN = 250                          # captures per run; the rest follow on the next run (the cursor keeps the place)
MAX_SEEN = 20_000
MAX_FORMS = 50
FULL_FIELDS = ("id,created_time,ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,form_id,platform,field_data")
BASIC_FIELDS = "id,created_time,ad_id,adset_id,campaign_id,form_id,platform,field_data"     # a token without ads_read
_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


class PullBusy(RuntimeError):
    pass


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text))
    except ValueError:
        return None


def meta_time(text):
    """Meta's created_time ("2026-11-03T10:00:00+0000") as an aware datetime, or None."""
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(str(text or ""), fmt)
        except ValueError:
            continue
    return _parse(text)


def _lock(connector_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(connector_id, threading.Lock())


def _version(connector: dict) -> str:
    version = str(connector.get("graph_version") or "")
    return version if version and version.replace("v", "").replace(".", "").isdigit() else DEFAULT_GRAPH_VERSION


def _url(connector: dict, path: str, params: dict, token: str) -> str:
    return f"{GRAPH_HOST}/{quote(_version(connector))}/{path}?{urlencode({**params, 'access_token': token})}"


def safe_message(exc: Exception, token: str) -> str:
    """A message fit for the card and the log: the platform's own wording, never the token or an address."""
    text = str(exc) if isinstance(exc, PlatformError) else "Meta could not be asked (unexpected problem)."
    if token:
        text = text.replace(token, "[token]")
    return text.replace(GRAPH_HOST, "Meta")[:200]


def _pages(transport, connector: dict, path: str, params: dict, token: str):
    """Yield each page's `data` list, following the `after` cursor (never Meta's `next` link, which holds the token).
    Yields (rows, complete) where complete is False when the page cap stopped the walk."""
    after = None
    for n in range(MAX_PAGES):
        query = {**params, **({"after": after} if after else {})}
        body = transport(_url(connector, path, query, token))
        body = body if isinstance(body, dict) else {}
        rows = [r for r in body.get("data") or [] if isinstance(r, dict)] if isinstance(body.get("data"), list) else []
        paging = body.get("paging") if isinstance(body.get("paging"), dict) else {}
        cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
        after = cursors.get("after")
        more = bool(paging.get("next")) and bool(after) and bool(rows)
        if not more:
            yield rows, True
            return
        if n == MAX_PAGES - 1:
            yield rows, False                  # stopped by the page cap: the cursor is not moved past what was not read
            return
        yield rows, True


def discover_forms(transport, connector: dict, token: str) -> list:
    """The page's lead forms: GET /{page_id}/leadgen_forms. Deleted forms are left out."""
    page_id = connector.get("page_id") or ""
    if not page_id:
        raise PlatformError("Add the form ids, or the page id so the forms can be found.")
    forms = []
    for rows, _ in _pages(transport, connector, f"{quote(page_id)}/leadgen_forms", {"fields": "id,name,status", "limit": PAGE_SIZE}, token):
        for row in rows:
            fid = str(row.get("id") or "")
            if fid.isdigit() and str(row.get("status") or "").upper() != "DELETED":
                forms.append({"id": fid, "name": str(row.get("name") or "")[:120]})
    return forms[:MAX_FORMS]


def _fetch_form(transport, connector: dict, form_id: str, since, token: str, state: dict) -> tuple:
    """All leads of one form newer than `since` (unix seconds or None): (rows, complete)."""
    params = {"fields": FULL_FIELDS if state.get("fields") != "basic" else BASIC_FIELDS, "limit": PAGE_SIZE}
    if since:
        params["filtering"] = json.dumps([{"field": "time_created", "operator": "GREATER_THAN", "value": int(since)}],
                                         separators=(",", ":"))

    def walk(query):
        out, complete = [], True
        for rows, done in _pages(transport, connector, f"{quote(form_id)}/leads", query, token):
            out.extend(rows)
            complete = done
        return out, complete
    try:
        return walk(params)
    except PlatformError as exc:
        # A token without ads_read may not read ad and campaign names: ask again for the ids only, and remember that.
        if params["fields"] == FULL_FIELDS and getattr(exc, "code", None) in (100, 10, *range(200, 300)):
            state["fields"] = "basic"
            return walk({**params, "fields": BASIC_FIELDS})
        raise


def parsed_lead(row: dict, connector: dict) -> dict:
    """One pulled lead, through the same mapping as a webhook notification (meta_lead), so both give the same lead."""
    event = {"leadgen_id": str(row.get("id") or ""), "form_id": row.get("form_id"), "ad_id": row.get("ad_id"),
             "adgroup_id": row.get("adset_id"), "created_time": row.get("created_time"), "page_id": connector.get("page_id") or None}
    event = {k: v for k, v in event.items() if v}
    data = {**event, **{k: v for k, v in row.items() if k not in ("id", "platform")}}
    if not data.get("field_data"):
        raise PlatformError("Meta sent a lead without its answers.")
    parsed = meta_lead(data, "", "", None)        # the row already has its answers: nothing is fetched here
    parsed["ids"] = {k: str(event[k])[:100] for k in ("leadgen_id", "form_id", "page_id", "ad_id", "adgroup_id") if event.get(k)}
    if row.get("platform"):
        parsed["custom"] = {**parsed.get("custom", {}), "meta_platform": str(row["platform"])[:20]}   # fb or ig
    return parsed


def _set_state(store, connector_id: str, state: dict, seen: list = None) -> None:
    def change(c: dict) -> dict:
        out = {**c, "pull_state": state}
        if seen is not None:
            out["pull_seen"] = seen[-MAX_SEEN:]
        return out
    connectors.update_connector(store, connector_id, change)


def due(connector: dict, now: datetime) -> bool:
    if not connector.get("enabled", True) or not connectors.pull_configured(connector):
        return False
    state = connector.get("pull_state") or {}
    nxt = _parse(state.get("next_pull_at"))
    return nxt is None or nxt <= now


def pull(store, connector: dict, transport=None, now: datetime = None, manual: bool = False) -> dict:
    """One pull of one Meta connector. Returns the run's numbers. Raises PullBusy when a pull of it is running already;
    Graph problems are recorded on the connector (safe message, back-off) and returned, never raised."""
    lock = _lock(connector["id"])
    if not lock.acquire(blocking=False):
        raise PullBusy("A pull of this connector is running already. Try again in a minute.")
    try:
        return _pull(store, connectors.get(store, connector["id"]) or connector, transport or graph_get, now or now_utc(), manual)
    finally:
        lock.release()


def _pull(store, connector: dict, transport, now: datetime, manual: bool) -> dict:
    token = (connector.get("tokens") or {}).get("page_access_token") or ""
    state = dict(connector.get("pull_state") or {})
    cursors = dict(state.get("cursors") or {})
    seen = list(connector.get("pull_seen") or [])
    known = set(seen)
    stats = {"fetched": 0, "new": 0, "merged": 0, "replay": 0, "refused": 0, "errors": 0, "already_seen": 0, "pending": 0}
    error = None
    try:
        if not token:
            raise PlatformError("Paste the page access token first.")
        form_ids = list(connector.get("form_ids") or [])
        if not form_ids:
            state["forms"] = discover_forms(transport, connector, token)
            form_ids = [f["id"] for f in state["forms"]]
            if not form_ids:
                raise PlatformError("Meta shows no lead forms on that page. Check the page id, or add the form ids.")
        else:
            names = {f.get("id"): f.get("name", "") for f in state.get("forms") or []}
            state["forms"] = [{"id": f, "name": names.get(f, "")} for f in form_ids]
        budget = MAX_NEW_PER_RUN
        for form_id in form_ids:
            cur = dict(cursors.get(form_id) or {})
            since = cur.get("since")
            rows, complete = _fetch_form(transport, connector, form_id, (since - OVERLAP.total_seconds()) if since else None, token, state)
            stats["fetched"] += len(rows)
            rows.sort(key=lambda r: (meta_time(r.get("created_time")) or now).timestamp())
            newest, stopped = since, False
            for row in rows:
                lead_id = str(row.get("id") or "")
                stamp = (meta_time(row.get("created_time")) or now).timestamp()
                if not lead_id.isdigit():
                    stats["errors"] += 1
                    continue
                if lead_id in known:
                    stats["already_seen"] += 1
                    if not stopped:
                        newest = max(newest or 0, stamp)
                    continue
                if budget <= 0:
                    stopped = True
                    stats["pending"] += 1
                    continue
                try:
                    parsed = parsed_lead(row, connector)
                except PlatformError:
                    stats["errors"] += 1            # a row without answers; Meta may fill it later, so it is not marked seen
                    continue
                attribution = parsed["attribution"]
                detail = attribution.get("form_name") or (f"form {attribution['form_id']}" if attribution.get("form_id") else "")
                result = capture(store, connector, parsed, detail=detail)
                outcome = result.get("outcome")
                stats[outcome if outcome in stats else "errors"] += 1
                budget -= 1
                known.add(lead_id)
                seen.append(lead_id)
                if not stopped:
                    newest = max(newest or 0, stamp)
            if complete and newest:
                cur["since"] = newest       # everything up to here is handled (a capped run stops the cursor at the last one done)
            cursors[form_id] = cur
            _set_state(store, connector["id"], {**state, "cursors": cursors}, seen)
    except PlatformError as exc:
        error = safe_message(exc, token)
    except Exception as exc:                 # a pull must never break the timer; the card shows the problem
        error = safe_message(exc, token)
    total = stats["new"] + stats["merged"]
    state.update(cursors=cursors, last_pull_at=_iso(now), last_fetched=stats["fetched"], last_count=stats["new"],
                 total_pulled=int(state.get("total_pulled") or 0) + total, pending=stats["pending"],
                 forms_found=len(state.get("forms") or []))
    if error:
        fails = int(state.get("fail_count") or 0) + 1
        wait = min(PULL_EVERY * (2 ** fails), MAX_BACKOFF)
        state.update(last_error=error, last_error_at=_iso(now), fail_count=fails, next_pull_at=_iso(now + wait))
        capture_error(store, connector, f"Meta pull: {error}")
    else:
        state.update(last_error=None, fail_count=0,
                     next_pull_at=_iso(now + (timedelta(minutes=1) if stats["pending"] else PULL_EVERY)))
    _set_state(store, connector["id"], state, seen)
    return {**stats, "ok": error is None, "error": error, "manual": manual, "next_pull_at": state["next_pull_at"]}


def run_meta_pulls(store, transport=None, now: datetime = None) -> int:
    """The background tick (every minute): pull every switched-on Meta connector that is due. One failing connector does
    not stop the others. Returns how many were pulled."""
    now = now or now_utc()
    done = 0
    for connector in connectors.all_connectors(store):
        if due(connector, now):
            try:
                pull(store, connector, transport, now)
                done += 1
            except PullBusy:
                continue
    return done
