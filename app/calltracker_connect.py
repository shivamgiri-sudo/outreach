"""Connect a DialHalo workspace in one click (PKCE-style), disconnect it, and ask DialHalo which workspace a key is for.

Contract with DialHalo:
  1. start(): a pending link {link id, random state, random verifier (64 chars), challenge = base64url(sha256(verifier))
     without padding, Outreach workspace, who started it, created_at, expires_at = +10 minutes} is kept in ct_pending
     {state: row} (the verifier is sealed at rest by app/secretbox.py). The browser then opens
     {DialHalo}/connect/outreach?state&challenge&callback&receiver&name&client=mcn_outreach in the SAME tab (the Outreach
     login token lives in sessionStorage, which a new tab would not have).
     callback = {public base}/api/calltracker/connect/callback; receiver = {public base}/api/calltracker/hook/<link id>.
     Both use the public base address (Settings, or OUTREACH_BASE_URL), so they share scheme + host + port, which DialHalo
     checks. DialHalo on the same server reaches the receiver through the public https address (Caddy).
  2. DialHalo sends the browser to callback?code&state (or ?error=access_denied&state). The callback route has no login
     (it is a browser navigation, and the API token is a bearer header): it only redirects to the SPA,
     /#integrations/dialhalo/connected?tab=calltracker&dh_state&dh_code, and changes nothing. The SPA then calls
     finish() through the signed-in API. finish() takes the pending row by state (single use, only for the person who
     started it, refused after 10 minutes), and server to server POST {DialHalo}/api/connect/exchange {code, verifier}
     -> {apiKey, webhookSecret, webhookId, workspace {id, name, slug}, connectionId}. The link is saved (secrets sealed
     like every link), tested, and the person sees "Connected to <workspace>".
  3. disconnect(): DELETE {DialHalo}/api/v1/connect/self with the link's key (best effort; 404 = an older DialHalo: the
     key must be revoked in DialHalo by hand), then the link is removed here.
  4. whoami(): GET {DialHalo}/api/v1/connect/self -> {workspace, connectionId, scopes, webhook, scope, member}; 404 on an
     older DialHalo. scope "member" = a personal connection (only that member's calls, member {id, name, email}); a
     missing scope is "workspace". Kept on the link as dh_scope / dh_member at finish and on every Test / refresh.
     Reconnecting replaces the link to the same DialHalo workspace with the same scope (and, for personal links, the
     same member); personal links of different members live side by side.

OUTREACH_DIALHALO_URL: the DialHalo address the Connect form starts with (until someone uses another one).
OUTREACH_DIALHALO_INTERNAL_URL (optional): when the browser address is OUTREACH_DIALHALO_URL, Outreach talks to DialHalo
server to server on this address instead (e.g. http://127.0.0.1:8081, which must be allowed in OUTREACH_ALLOW_PRIVATE_HOSTS).
"""
import base64
import hashlib
import os
import secrets
import uuid
from datetime import timedelta
from typing import Optional
from urllib.parse import urlencode

PENDING_MINUTES = 10
PENDING_MAX = 50
DEFAULT_DIALHALO = "https://calls.137-23-39-80.sslip.io"
CALLBACK_PATH = "/api/calltracker/connect/callback"
SPA_DONE = "/#integrations/dialhalo/connected"


class ConnectError(ValueError):
    """Shown to the person as it is (plain words, no secrets)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _ct():
    from . import calltracker
    return calltracker


def challenge_of(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")


def new_verifier() -> str:
    return secrets.token_urlsafe(48)[:64]          # 64 chars of [A-Za-z0-9_-]: inside the 43-128 the contract allows


def default_base(store) -> str:
    return (store.read("settings").get("dialhalo_last_url") or os.environ.get("OUTREACH_DIALHALO_URL") or DEFAULT_DIALHALO).rstrip("/")


def server_base(public: str) -> str:
    """The address Outreach uses server to server for this DialHalo (see OUTREACH_DIALHALO_INTERNAL_URL)."""
    internal = os.environ.get("OUTREACH_DIALHALO_INTERNAL_URL", "").strip().rstrip("/")
    configured = os.environ.get("OUTREACH_DIALHALO_URL", "").strip().rstrip("/")
    return _ct().check_base_url(internal) if internal and configured and public == configured else public


def _who(user: dict) -> str:
    return str(user.get("email") or user.get("sub") or user.get("name") or "").lower()


def start(store, ws: str, user: dict, dialhalo_url: str, public_base: str, ws_name: str) -> dict:
    """Make a pending link and the DialHalo address to open. Raises ConnectError."""
    ct = _ct()
    try:
        public = ct.check_public_url(dialhalo_url) or ""
        if not public:
            raise ValueError("Enter the DialHalo address, for example https://calls.example.com")
        ct.check_base_url(public)
        server = server_base(public)
    except ValueError as exc:
        raise ConnectError(str(exc)) from None
    if not public_base.startswith(("https://", "http://")):
        raise ConnectError("Set this app's public address first (Setup → Public address), so DialHalo can send people back here.")
    now = ct.now_utc()
    verifier, state, link_id = new_verifier(), secrets.token_urlsafe(32), uuid.uuid4().hex[:20]
    row = {"state": state, "link_id": link_id, "verifier": verifier, "challenge": challenge_of(verifier), "workspace": ws,
           "dialhalo_url": public, "server_url": server, "by": _who(user), "by_name": user.get("name") or user.get("email") or "",
           "created_at": ct.iso(now), "expires_at": ct.iso(now + timedelta(minutes=PENDING_MINUTES))}

    def apply(rows: dict) -> dict:
        live = {k: v for k, v in (rows or {}).items() if (ct.parse(v.get("expires_at")) or now) > now}
        live = dict(sorted(live.items(), key=lambda kv: kv[1].get("created_at", ""))[-(PENDING_MAX - 1):])
        return {**live, state: row}
    store.update("ct_pending", apply)
    store.update("settings", lambda s: {**s, "dialhalo_last_url": public})
    callback = f"{public_base}{CALLBACK_PATH}"
    from .routes_calltracker import HOOK_PATH
    receiver = f"{public_base}{HOOK_PATH}{link_id}"
    query = urlencode({"state": state, "challenge": row["challenge"], "callback": callback, "receiver": receiver,
                       "name": (ws_name or ws)[:80], "client": ct.SOURCE})
    return {"url": f"{public}/connect/outreach?{query}", "link_id": link_id, "expires_at": row["expires_at"],
            "callback": callback, "receiver": receiver}


def spa_redirect(code: str, state: str, error: str) -> str:
    """Where the callback sends the browser: the SPA, which finishes through the signed-in API."""
    clean = lambda v: str(v or "")[:512]
    params = {"tab": "calltracker", "dh_state": clean(state)}
    params.update({"dh_error": clean(error)[:60]} if error or not code else {"dh_code": clean(code)})
    return f"{SPA_DONE}?{urlencode(params)}"


def take_pending(store, state: str, user: dict) -> dict:
    """The pending row for `state`, removed (single use). Only the person who started it may take it."""
    ct = _ct()
    state = str(state or "")[:200]
    out = {}

    def apply(rows: dict) -> dict:
        row = (rows or {}).get(state)
        if not row:
            return rows
        if row.get("by") != _who(user):
            out["other"] = True
            return rows
        out["row"] = row
        return {k: v for k, v in rows.items() if k != state}
    if state:
        store.update("ct_pending", apply)
    if out.get("other"):
        raise ConnectError("Only the person who started connecting can finish it. Start again from your own login.", 403)
    row = out.get("row")
    if not row:
        raise ConnectError("This connect link was already used or is unknown. Press Connect a DialHalo workspace again.")
    if (ct.parse(row.get("expires_at")) or ct.now_utc()) <= ct.now_utc():
        raise ConnectError(f"Connecting took longer than {PENDING_MINUTES} minutes, so it was stopped. Press Connect a DialHalo workspace again.")
    return row


ERRORS = {"access_denied": "DialHalo was not allowed to connect (someone pressed Cancel or Deny there). Nothing was changed."}


def finish(store, row: dict, code: str, error: str, who: str) -> dict:
    """Exchange the code, save the link, test it. Returns {"link", "message", "replaced"}. Raises ConnectError."""
    ct = _ct()
    from . import calltracker_links as links
    if error or not code:
        raise ConnectError(ERRORS.get(str(error), f"DialHalo did not connect ({str(error or 'no code')[:60]}). Nothing was changed."))
    try:
        data = ct.ct_request({"base_url": row["server_url"]}, "POST", "/api/connect/exchange", {"code": str(code)[:512], "verifier": row["verifier"]})
    except ct.CallTrackerError as exc:
        if exc.status in (400, 401, 403, 404, 410):
            raise ConnectError("DialHalo did not accept the connection (the code was used, expired or does not match). "
                               f"Press Connect a DialHalo workspace again. ({exc})") from None
        raise ConnectError(f"DialHalo could not finish connecting: {exc} Try again in a minute.") from None
    data = data if isinstance(data, dict) else {}
    key, secret = str(data.get("apiKey") or ""), str(data.get("webhookSecret") or "")
    if not key or len(key) > 300 or any(ch.isspace() for ch in key) or len(secret) > 300 or any(ch.isspace() for ch in secret):
        raise ConnectError("DialHalo answered without a usable API key. Nothing was saved; try again or connect manually.")
    wsinfo = data.get("workspace") if isinstance(data.get("workspace"), dict) else {}
    dh = {"id": str(wsinfo.get("id") or "")[:60], "name": str(wsinfo.get("name") or "")[:120], "slug": str(wsinfo.get("slug") or "")[:80]}
    try:                                               # personal or whole workspace (an older DialHalo: whole workspace)
        scope = scope_of(whoami({"base_url": row["server_url"], "api_key": key}))
    except Exception:
        scope = {"dh_scope": "workspace", "dh_member": None}
    urls = {row["dialhalo_url"], row["server_url"], os.environ.get("OUTREACH_DIALHALO_INTERNAL_URL", "").strip().rstrip("/")} - {""}
    here = [l for l in links.links_of(store, row["workspace"]) if {(l.get("public_url") or "").rstrip("/"), (l.get("base_url") or "").rstrip("/")} & urls]
    for old in here:                                   # a manual / migrated link may not know its DialHalo workspace yet: ask
        if not (old.get("dh_workspace") or {}).get("id"):
            fill_workspace(store, old)
    same = [l for l in (links.get(store, x["id"]) for x in here) if l and dh["id"] and (l.get("dh_workspace") or {}).get("id") == dh["id"]
            and same_scope(l, scope)]
    keep = {k: same[0][k] for k in ("lead_sync", "mapping", "enabled") if same and k in same[0]}
    link = links.save(store, row["workspace"], None, {"base_url": row["server_url"], "public_url": row["dialhalo_url"], "api_key": key,
                                                      "webhook_secret": secret or None, "via": "connect", "dh_workspace": dh,
                                                      "dh_connection_id": str(data.get("connectionId") or "")[:80],
                                                      "dh_webhook_id": str(data.get("webhookId") or "")[:80], **keep,
                                                      "dh_scope": scope["dh_scope"], "dh_member": scope["dh_member"] or {}}, who, new_id=row["link_id"])
    for old in same:                                   # connecting the same DialHalo workspace again replaces the old link
        if old.get("primary"):
            links.set_primary(store, link["id"])
        disconnect(store, old)
    message = f"Connected to {dh['name'] or 'DialHalo'}." + (
        f" Personal connection: only {(scope['dh_member'] or {}).get('name') or 'one member'}'s calls." if scope["dh_scope"] == "member" else "")
    test = run_test(store, links.get(store, link["id"]))
    if not test["ok"]:
        message += f" The first test failed: {test['message']}"
    elif same:
        message += " The earlier link to the same DialHalo workspace was replaced."
    return {"link": links.get(store, link["id"]), "message": message, "replaced": len(same), "test": test}


def receiver_of(store, link: dict) -> str:
    """The receiver address DialHalo has for this link (its webhook URL): the public base + /api/calltracker/hook/<id>."""
    from .growth_core import public_base_url
    from .routes_calltracker import HOOK_PATH
    return f"{public_base_url(store, os.environ.get('OUTREACH_BASE_URL', 'http://localhost:8000'))}{HOOK_PATH}{link['id']}"


def scope_of(me: Optional[dict]) -> dict:
    """{"dh_scope", "dh_member"} from a connect/self answer. Missing or unknown scope = "workspace"."""
    me = me if isinstance(me, dict) else {}
    member = me.get("member") if isinstance(me.get("member"), dict) else None
    if me.get("scope") != "member" or not member:
        return {"dh_scope": "workspace", "dh_member": None}
    return {"dh_scope": "member", "dh_member": {"id": str(member.get("id") or "")[:60], "name": str(member.get("name") or "")[:120],
                                                 "email": str(member.get("email") or "").strip().lower()[:190]}}


def same_scope(link: dict, scope: dict) -> bool:
    """A reconnect replaces only the link with the same scope (and the same member, for a personal link)."""
    if (link.get("dh_scope") or "workspace") != scope["dh_scope"]:
        return False
    if scope["dh_scope"] != "member":
        return True
    mine, theirs = link.get("dh_member") or {}, scope["dh_member"] or {}
    return bool(mine.get("id")) and str(mine.get("id")) == str(theirs.get("id"))


def _scope_changes(me: Optional[dict]) -> dict:
    s = scope_of(me)
    return {"dh_scope": s["dh_scope"], "dh_member": s["dh_member"] or {}}


def _workspace_of(me: Optional[dict]) -> Optional[dict]:
    w = (me or {}).get("workspace")
    if not isinstance(w, dict) or not w.get("id"):
        return None
    return {"id": str(w.get("id") or "")[:60], "name": str(w.get("name") or "")[:120], "slug": str(w.get("slug") or "")[:80]}


def fill_workspace(store, link: dict) -> Optional[dict]:
    """Ask DialHalo which workspace this link's key belongs to (GET connect/self) and keep it on the link. Best effort:
    an older DialHalo (404), a refused key or DialHalo being down leave the link as it is. Returns the workspace or None."""
    from . import calltracker_links as links
    if not links.is_active(link):
        return None
    try:
        me = whoami(link)
        dh = _workspace_of(me)
    except Exception:                                  # never fail a connect or the start-up over this
        return None
    if dh:
        links.save(store, link["workspace"], link["id"], {"dh_workspace": dh, **_scope_changes(me)}, link.get("updated_by") or "")
    return dh


def fill_missing_workspaces(store) -> int:
    """Start-up: links made by hand or migrated from the single connection learn their DialHalo workspace, so a later
    one-click connect to the same workspace replaces them instead of adding a second link. Never raises."""
    from . import calltracker_links as links
    n = 0
    try:
        for link in links.all_links(store):
            if not (link.get("dh_workspace") or {}).get("id") and fill_workspace(store, link):
                n += 1
    except Exception as exc:
        print(f"calltracker workspace names not filled: {type(exc).__name__}")
    return n


def whoami(link: dict) -> Optional[dict]:
    """GET /api/v1/connect/self: which DialHalo workspace this key belongs to. None on an older DialHalo (404)."""
    ct = _ct()
    try:
        data = ct.ct_request(link, "GET", "/api/v1/connect/self")
    except ct.CallTrackerError as exc:
        if exc.status == 404:
            return None
        raise
    return data if isinstance(data, dict) else None


def run_test(store, link: dict) -> dict:
    """Which DialHalo workspace (connect/self, when DialHalo has it), the members (people pairing) and the do-not-call
    list. Saves last_test, the members cache and the workspace name. Never raises."""
    ct = _ct()
    from . import calltracker_links as links
    try:
        me = whoami(link)
        members = ct.ct_request(link, "GET", "/api/v1/members")
        if scope_of(me)["dh_scope"] != "member":       # a personal key may not read the do-not-call list
            ct.ct_request(link, "GET", "/api/v1/dnc?limit=1")
    except ct.CallTrackerError as exc:
        links.note(store, link["id"], "last_test", False, str(exc))
        if exc.status in (401, 403):
            links.note_auth(store, link["id"], True, str(exc))
        return {"ok": False, "message": str(exc)}
    if _workspace_of(me):
        links.save(store, link["workspace"], link["id"], {"dh_workspace": _workspace_of(me), **_scope_changes(me)}, link.get("updated_by") or "")
    link = links.get(store, link["id"]) or link
    all_members = links.save_members(store, link["id"], members if isinstance(members, list) else [])
    active = [m for m in all_members if m.get("active", 1)]
    preview = ct.owner_preview(store, link["workspace"], active, all_members)
    name = ((me or {}).get("workspace") or {}).get("name") or (link.get("dh_workspace") or {}).get("name")
    where = f"DialHalo workspace {name}" if name else f"DialHalo at {link.get('public_url') or link.get('base_url')}"
    if links.is_personal(link):
        where += f" (personal: only {(link.get('dh_member') or {}).get('name') or 'one member'}'s calls)"
    msg = (f"Connected to {where}. {len(active)} DialHalo member(s); {sum(p['matched'] for p in preview)} of {len(preview)} Outreach people match by email.")
    if me is None:
        msg += " (This DialHalo does not say which workspace a key is for; update it to see the name.)"
    if not link.get("webhook_secret"):
        msg += " Add the webhook secret so calls come back."
    links.note(store, link["id"], "last_test", True, msg)
    links.note_auth(store, link["id"], False)
    unmatched = [{"name": p["name"], "email": p["email"], "reason": p["reason"]} for p in preview if not p["matched"]]
    return {"ok": True, "message": msg, "members": len(active), "owners": preview, "unmatched": unmatched, "workspace": name or ""}


def disconnect(store, link: dict) -> str:
    """Tell DialHalo (best effort), then remove the link here. Returns the sentence for the person.
    DELETE /api/v1/connect/self turns the key off; the body {"receiver": <this link's receiver URL>} also makes DialHalo
    delete that workspace's webhooks to exactly that URL (a hand-made webhook of a manual link would otherwise keep
    retrying a receiver that no longer exists)."""
    ct = _ct()
    from . import calltracker_links as links
    receiver = receiver_of(store, link)
    note = "DialHalo was told: it turned the key off and removed its webhook to this link."
    if link.get("api_key") and link.get("base_url"):
        try:
            ct.ct_request(link, "DELETE", "/api/v1/connect/self", {"receiver": receiver})
        except ct.CallTrackerError as exc:
            hook = f" Also delete the webhook to {receiver} in DialHalo Settings → Webhooks."
            if exc.status == 404:
                note = "This DialHalo is older and cannot be told: revoke the key in DialHalo (Settings → Integrations → API keys)." + hook
            elif exc.status in (401, 403):
                note = "DialHalo no longer accepts this key (it was already revoked there)." + hook
            else:
                note = f"DialHalo could not be told ({exc}) Revoke the key in DialHalo (Settings → Integrations → API keys)." + hook
    links.delete(store, link["id"])
    return note
