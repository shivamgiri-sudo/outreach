"""Templates per workspace: who may change them, which ones a request sees, and which one a lead uses.

Templates (email sequences, snippets, flows, the LinkedIn steps + personas and the LinkedIn connection notes) belong to
a workspace. A row's `workspace` (missing or empty = "main") says whose it is; the LinkedIn sequence and notes of a
workspace other than main are kept in settings "workspace_templates" = {ws_id: {linkedin_sequence,
linkedin_connect_variants, default_sequence}} (main keeps its old settings keys, so nothing changes for it).

The rule:
  * an owner (and the super admin) may change any workspace's templates;
  * a workspace admin (role "admin") may change only the templates of its own workspaces, never main's;
  * callers and viewers only view and use them (the one legacy exception: an unlimited caller keeps making and
    changing its own snippets in main, as before).
A workspace login sees its own workspaces' templates plus main's (read-only, with "Copy into my workspace").

Which template a lead uses: its own workspace's; when that workspace has none of its own, main's (and the built-in
ones when main has none either).
"""
from typing import Optional

from fastapi import HTTPException, Request

from . import workspaces as wsm

MAIN = wsm.MAIN
KEY = "workspace_templates"

# Routes a workspace admin may call although they are owner-only for everyone else; each route checks that the
# template (or the workspace it is made for) is one of the admin's own (require_edit below).
ADMIN_TEMPLATE_ROUTES = {
    ("POST", "/api/sequences"), ("PUT", "/api/sequences/{seq_id}"), ("POST", "/api/sequences/{seq_id}/duplicate"),
    ("POST", "/api/sequences/{seq_id}/archive"), ("POST", "/api/sequences/{seq_id}/make-default"),
    ("PUT", "/api/linkedin/sequence"), ("POST", "/api/linkedin/sequence/reset"), ("POST", "/api/linkedin/sequence/copy"),
    ("POST", "/api/flows"), ("POST", "/api/flows/import"), ("POST", "/api/flows/templates/{key}"),
    ("PUT", "/api/flows/{flow_id}"), ("DELETE", "/api/flows/{flow_id}"), ("POST", "/api/flows/{flow_id}/validate"),
    ("POST", "/api/flows/{flow_id}/activate"), ("POST", "/api/flows/{flow_id}/pause"), ("POST", "/api/flows/{flow_id}/enroll"),
    ("POST", "/api/flows/{flow_id}/simulate"),
}


def tpl_ws(row: Optional[dict]) -> str:
    return (row or {}).get("workspace") or MAIN


def user_of(request: Optional[Request]) -> dict:
    from .routes_auth import LOCAL_OWNER
    return (getattr(getattr(request, "state", None), "user", None) if request is not None else None) or LOCAL_OWNER


def is_owner(user: Optional[dict]) -> bool:
    return (user or {}).get("role") == "owner"          # the super admin is always an owner login


def admin_spaces(user: Optional[dict]) -> set:
    user = user or {}
    return set(user.get("workspaces") or []) if user.get("role") == "admin" else set()


def can_edit(user: Optional[dict], ws: str) -> bool:
    if is_owner(user):
        return True
    return ws != MAIN and ws in admin_spaces(user)


def refusal(user: Optional[dict], ws: str) -> str:
    if (user or {}).get("role") == "admin":
        if ws == MAIN:
            return ("Main's templates are shared and read-only for workspace admins. "
                    "Use \"Copy into my workspace\" to make your own copy.")
        return "You can change only your own workspace's templates."
    return "Only an admin of this workspace or an owner can change its templates."


def require_edit(user: Optional[dict], ws: str) -> None:
    if not can_edit(user, ws):
        raise HTTPException(403, refusal(user, ws))


def _main_shared() -> set:
    """Main's templates are shared read-only inside Mas Callnet only; another account never sees them."""
    from . import accounts
    return {MAIN} if accounts.current() in (None, accounts.MCN) else set()


def readable_spaces() -> Optional[set]:
    """Workspaces whose templates this request lists (main too for Mas Callnet), or None for every workspace."""
    selected, allowed = wsm._SCOPE.get()
    if selected is None and allowed is None:
        return None
    return {selected, *_main_shared()} if selected else {*allowed, *_main_shared()}


def readable(row: dict) -> bool:
    spaces = readable_spaces()
    return spaces is None or tpl_ws(row) in spaces


def filter_readable(rows) -> list:
    return [r for r in rows or [] if isinstance(r, dict) and readable(r)]


def known(store, ws: str) -> bool:
    return any(w["id"] == ws for w in wsm.all_workspaces(store))


def name_of(store, ws: str) -> str:
    return next((w["name"] for w in wsm.all_workspaces(store) if w["id"] == ws), ws)


def target_ws(store, user: Optional[dict], wanted: Optional[str] = None) -> str:
    """The workspace a new template (or the LinkedIn sequence shown / saved) is for: the one asked for, else the
    workspace picked in the menu, else the login's own first workspace, else main."""
    wanted = str(wanted or "").strip()[:40]
    selected, allowed = wsm._SCOPE.get()
    if wanted and wanted != "all":
        if not known(store, wanted):
            raise HTTPException(400, "Pick a workspace from the list.")
        if allowed is not None and wanted not in allowed and (wanted != MAIN or not _main_shared()):
            raise HTTPException(403, refusal(user, wanted))
        return wanted
    if selected:
        return selected
    mine = sorted(admin_spaces(user)) or sorted(allowed or [])
    return mine[0] if mine else MAIN


def flow_takes(flow: Optional[dict], lead: Optional[dict]) -> bool:
    """A workspace's flow takes (and acts on) only its own leads; a main flow takes any lead, as before."""
    ws = tpl_ws(flow)
    return ws == MAIN or wsm.ws_of(lead or {}) == ws


# ---------------------------------------------------------------- per-workspace settings (LinkedIn, default sequence)
def own(settings: dict, ws: str) -> dict:
    found = ((settings or {}).get(KEY) or {}).get(ws)
    return found if isinstance(found, dict) else {}


def put_own(settings: dict, ws: str, changes: dict) -> dict:
    """Settings with `changes` merged into this workspace's own templates (a None value removes the key)."""
    all_ws = dict((settings or {}).get(KEY) or {})
    mine = {**own(settings, ws), **changes}
    mine = {k: v for k, v in mine.items() if v is not None}
    if mine:
        all_ws[ws] = mine
    else:
        all_ws.pop(ws, None)
    return {**settings, KEY: all_ws}


class Overlaid(dict):
    """Settings as one workspace sees them: its own LinkedIn sequence / notes in place of main's. Read-only use."""
    base: dict = None
    ws: str = MAIN


def _client_ws(settings: dict, ws: str) -> bool:
    """A workspace of another account (not Mas Callnet): it never falls back to main's templates."""
    from . import accounts
    return ws != MAIN and accounts.account_of_ws(settings, ws) != accounts.MCN


def linkedin_ws(settings: dict, ws: str) -> str:
    """The workspace whose LinkedIn steps a lead of `ws` follows: its own when it has them, else main (a client
    account's workspace: its own, else the built-in steps)."""
    base = getattr(settings, "base", None) or settings
    return ws if ws != MAIN and (own(base, ws).get("linkedin_sequence") or _client_ws(base, ws)) else MAIN


def settings_for(settings: dict, ws: str) -> dict:
    base = getattr(settings, "base", None) or settings
    ws = ws or MAIN
    if ws == MAIN:
        return base
    if isinstance(settings, Overlaid) and settings.ws == ws:
        return settings
    mine = own(base, ws)
    if not mine.get("linkedin_sequence") and not mine.get("linkedin_connect_variants"):
        if _client_ws(base, ws):              # built-in steps and notes, never Mas Callnet's
            out = Overlaid({k: v for k, v in base.items() if k not in ("linkedin_sequence", "linkedin_connect_variants")})
            out.base, out.ws = base, ws
            return out
        return base
    out = Overlaid(base)
    out.base, out.ws = base, ws
    if mine.get("linkedin_sequence"):
        out["linkedin_sequence"] = mine["linkedin_sequence"]
        out["linkedin_connect_variants"] = mine.get("linkedin_connect_variants") or {}
    else:
        out["linkedin_connect_variants"] = mine["linkedin_connect_variants"]
    return out


def for_lead(settings: dict, lead: Optional[dict]) -> dict:
    return settings_for(settings, wsm.ws_of(lead or {}))


def default_sequence(settings: dict, ws: str) -> str:
    """The sequence a lead of `ws` starts when none is picked: its workspace's default, else main's, else built-in."""
    from .sequence_library import DEFAULT_ID
    base = getattr(settings, "base", None) or settings
    if _client_ws(base, ws):
        return own(base, ws).get("default_sequence") or DEFAULT_ID
    return own(base, ws).get("default_sequence") or own(base, MAIN).get("default_sequence") or DEFAULT_ID


# ---------------------------------------------------------------- change log
def record(store, request_or_user, action: str, ws: str, target: str, detail: str = "", changes: Optional[list] = None) -> None:
    """One change-log row for a template write: who, which workspace, what."""
    from . import audit
    user = user_of(request_or_user) if hasattr(request_or_user, "state") else request_or_user
    extra = {"workspace": ws, **({"changes": changes} if changes else {})}
    label = target if ws == MAIN else f"{target} [{name_of(store, ws)}]"      # main rows read as before
    audit.record(store, user, action, label, detail, extra)


def record_diff(store, request_or_user, action: str, ws: str, target: str, before: dict, after: dict) -> None:
    from . import audit_settings
    changes = audit_settings.diff(before, after)
    if changes:
        record(store, request_or_user, action, ws, target, audit_settings.describe(changes), changes)
