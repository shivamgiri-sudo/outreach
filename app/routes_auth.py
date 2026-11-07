"""Sign in / out, who am I, team logins (owner only), and the role check every route goes through.

Roles:
  owner   everything
  caller  works leads, calls, meetings and tasks; cannot change settings, integrations, campaign rules or live
          switches, run or resume sending, or download CSV exports
  viewer  read only: any request that changes something is refused (except signing out)
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import auth
from .hygiene import normalise_email
from .web_security import client_ip

LOCAL_OWNER = {"sub": "local", "email": "", "name": "This computer", "role": "owner", "claims": None}
ANYONE = {("POST", "/api/logout"), ("POST", "/api/me/password"),
          ("POST", "/api/linkedin/sequence/preview")}     # a preview changes nothing, so viewers may see it
OWNER_ROUTES = {
    ("PUT", "/api/settings"), ("POST", "/api/settings/live-sending"), ("PUT", "/api/offer"), ("PUT", "/api/team"), ("POST", "/api/workspaces"), ("DELETE", "/api/workspaces/{ws_id}"), ("POST", "/api/sequence/{index}/send-preview"),
    ("GET", "/api/channels/webhooks"), ("GET", "/api/flows/{flow_id}/webhook"), ("PUT", "/api/icp"), ("POST", "/api/signals/run"), ("POST", "/api/signals/watch"), ("DELETE", "/api/signals/watch/{watch_id}"), ("POST", "/api/icp/score"), ("GET", "/api/oauth/{provider}/start"),
    ("POST", "/api/sequences/run"), ("POST", "/api/sending/resume"), ("POST", "/api/sequences/resume-failed"),
    ("POST", "/api/practice/clear"), ("GET", "/api/enrich/apollo-export"), ("GET", "/api/change-log.csv"),   # CSV downloads: owners only
    ("GET", "/api/leads/{lead_id}/export"), ("POST", "/api/leads/{lead_id}/erase"),
    ("POST", "/api/find-leads"),          # spends provider credits
    ("POST", "/api/sequences"), ("PUT", "/api/sequences/{seq_id}"), ("POST", "/api/sequences/{seq_id}/duplicate"),       # sequence library
    ("POST", "/api/sequences/{seq_id}/archive"), ("POST", "/api/sequences/{seq_id}/steps/{index}/send-preview"),
    ("GET", "/api/scoring"), ("PUT", "/api/scoring"),
    ("PUT", "/api/linkedin/sequence"), ("POST", "/api/linkedin/sequence/reset"), ("PUT", "/api/lead-fields"),   # industry setup
    ("POST", "/api/presets/{preset_id}/apply"),
    ("POST", "/api/prospects/export"), ("POST", "/api/prospects/import"), ("POST", "/api/prospects/suppress"),       # prospect DB
    ("GET", "/api/prospects/suppressions"), ("POST", "/api/prospects/unsuppress"),
    ("DELETE", "/api/prospects/people/{person_id}"), ("PATCH", "/api/prospects/people/{person_id}"), ("POST", "/api/prospects/sync-crm"),
    ("PUT", "/api/prospects/settings"), ("POST", "/api/prospects/enrich"), ("GET", "/api/prospects/spend"), ("PUT", "/api/prospects/spend"),
    ("PUT", "/api/insights/source-spend"), ("GET", "/api/inbound/log"), ("POST", "/api/inbound/{connector_id}/test"), ("POST", "/api/inbound/{connector_id}/sync"),
}
OWNER_PREFIXES = ("/api/users", "/api/compliance", "/api/backup", "/api/export/", "/api/capture", "/api/hiring-engine", "/api/linkedin-agent", "/api/linkedin-cloud", "/api/audit", "/api/system")    # any method (exports: no CSV for callers)
OWNER_WRITE_PREFIXES = ("/api/integrations", "/api/channels", "/api/workflows", "/api/flows", "/api/brand", "/api/labels", "/api/onboarding")   # anything but GET
READ_METHODS = ("GET", "HEAD", "OPTIONS")


class LoginBody(BaseModel):
    email: str = ""
    password: str = ""


class UserBody(BaseModel):
    email: str
    name: str = ""
    role: str = "caller"
    password: str
    workspaces: list = []          # ids; empty = every workspace. Set at creation so a client's login never starts open.


class OwnPasswordBody(BaseModel):
    current: str = ""
    new: str = ""


class UserChange(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    password: Optional[str] = None
    can_edit_contacts: Optional[bool] = None
    workspaces: Optional[List[str]] = None


def needed_role(method: str, path: str) -> str:
    """'owner', 'caller' (any write) or 'viewer' (read) for a route template such as /api/leads/{lead_id}/erase."""
    if (method, path) in ANYONE:
        return "viewer"
    if (method, path) in OWNER_ROUTES or path.startswith(OWNER_PREFIXES):
        return "owner"
    if path in ("/api/extension/pair", "/api/extension/tokens") or path.startswith("/api/extension/tokens/"):
        return "caller"                       # viewers cannot create, list or revoke pairing tokens
    if method in READ_METHODS:
        return "viewer"
    return "owner" if path.startswith(OWNER_WRITE_PREFIXES) else "caller"


def allowed(role: str, needed: str) -> bool:
    rank = {"viewer": 0, "caller": 1, "admin": 1, "owner": 2}
    return rank.get(role, -1) >= rank[needed]


def role_guard(request: Request) -> None:
    """Global dependency. The login middleware puts the signed-in user on request.state; public routes have none."""
    user = getattr(request.state, "user", None)
    if user is None:
        return
    route = request.scope.get("route")
    path = getattr(route, "path", request.url.path)
    needed = needed_role(request.method, path)
    if user.get("role") == "admin" and (path == "/api/users" or path.startswith("/api/users/")):
        return                                # workspace admins manage their own workspaces' logins (checked in the routes)
    if not allowed(user.get("role", ""), needed):
        what = "make changes" if user.get("role") == "viewer" else "do this"
        raise HTTPException(403, f"Your role ({user.get('role')}) cannot {what}. Ask the owner.")


def make_router(store, admin_password: str, limiter: auth.LoginLimiter) -> APIRouter:
    router = APIRouter()

    def owner_left(remaining: list) -> bool:
        """Without the admin password, at least one owner login must remain, or nobody could manage the app."""
        return bool(admin_password) or any(u.get("role") == "owner" for u in remaining)

    @router.get("/api/auth/status")
    def auth_status():
        return {"auth_required": auth.auth_on(store, admin_password)}

    @router.post("/api/login")
    def login(body: LoginBody, request: Request):
        if not auth.auth_on(store, admin_password):
            return {"token": ""}
        caller = client_ip(request)
        if limiter.locked(caller):
            raise HTTPException(429, "Too many wrong passwords. Wait a minute and try again.")
        if len(body.password) > auth.MAX_PASSWORD_CHARS or len(body.email) > 320:
            raise HTTPException(400, "That is too long.")
        principal = None
        if body.email.strip():
            user = auth.find_user(store, body.email)
            if auth.check_password((user or {}).get("password_hash", ""), body.password) and user:
                principal = auth.subject_principal(store, admin_password, "user:" + user["email"])
        elif admin_password and auth.password_matches(admin_password, body.password):
            principal = auth.subject_principal(store, admin_password, auth.ADMIN_SUBJECT)
        if principal is None:
            limiter.record_failure(caller)
            raise HTTPException(401, "Wrong email or password" if body.email.strip() else "Wrong password")
        limiter.reset(caller)
        return {"token": auth.issue_token(store, principal), "role": principal["role"], "name": principal["name"]}

    @router.post("/api/logout")
    def logout(request: Request):
        user = getattr(request.state, "user", None) or {}
        if user.get("claims"):
            auth.revoke(store, user["claims"])
        return {"ok": True}

    @router.get("/api/me")
    def me(request: Request):
        user = getattr(request.state, "user", None) or LOCAL_OWNER
        return {"name": user["name"], "email": user["email"], "role": user["role"], "can_edit_contacts": auth.can_edit_contacts(user), "workspaces": user.get("workspaces") or [],
                "super_admin": auth.is_super_admin(user),
                "auth_required": auth.auth_on(store, admin_password)}

    @router.post("/api/me/password")
    def change_own_password(body: OwnPasswordBody, request: Request):
        """Anyone with their own login changes their own password (the current one is asked again). Every session of
        theirs, this one included, is signed out, because tokens carry a password fingerprint."""
        user = getattr(request.state, "user", None) or {}
        if not str(user.get("sub", "")).startswith("user:"):
            raise HTTPException(400, "This sign-in uses the server admin password; change OUTREACH_ADMIN_PASSWORD on the server instead.")
        stored = auth.find_user(store, user.get("email", ""))
        caller = client_ip(request)
        if limiter.locked(caller):
            raise HTTPException(429, "Too many wrong passwords. Wait a minute and try again.")
        if not stored or not auth.check_password(stored.get("password_hash", ""), body.current[:auth.MAX_PASSWORD_CHARS]):
            limiter.record_failure(caller)
            raise HTTPException(400, "Your current password is not right.")
        limiter.reset(caller)
        check_password(body.new)
        if body.new == body.current:
            raise HTTPException(400, "Pick a new password that is different from the current one.")
        changes = {"password_hash": auth.hash_password(body.new), "password_changed_at": auth.now_iso()}
        store.update("settings", lambda s: {**s, "users": [{**u, **changes} if isinstance(u, dict) and u.get("email") == stored["email"] else u
                                                           for u in s.get("users") or []]})
        from . import audit
        audit.record(store, user, "user.change", stored["email"], "changed own password")
        return {"ok": True}

    # ---------- team logins (owner only; the role guard enforces it) ----------
    def check_role(role: str) -> None:
        if role not in auth.ROLES:
            raise HTTPException(400, "Role must be owner, admin, caller or viewer.")

    ADMIN_GIVES = ("admin", "caller", "viewer")

    def admin_spaces(request: Request) -> Optional[set]:
        """The workspaces a workspace admin manages, or None when the person is an owner (manages everyone)."""
        who = actor(request)
        return set(who.get("workspaces") or []) if who.get("role") == "admin" else None

    def manageable(request: Request, target: dict) -> bool:
        mine = admin_spaces(request)
        if mine is None:
            return True
        spaces = set(target.get("workspaces") or [])
        return target.get("role") in ADMIN_GIVES and bool(spaces) and spaces <= mine

    def clean_spaces(request: Request, role: str, spaces: list) -> list:
        """Workspaces for a login: they must exist; a workspace admin gives only its own (its own by default); an
        admin login always has at least one."""
        from . import workspaces as ws_mod
        known = {w["id"] for w in ws_mod.all_workspaces(store)}
        spaces = list(dict.fromkeys(str(w)[:40] for w in spaces or []))[:50]
        if any(w not in known for w in spaces):
            raise HTTPException(400, "Pick workspaces from the list.")
        mine = admin_spaces(request)
        if mine is not None:
            if role not in ADMIN_GIVES:
                raise HTTPException(403, "A workspace admin can add admins, callers and viewers only.")
            spaces = spaces or sorted(mine)
            if not set(spaces) <= mine:
                raise HTTPException(403, "You can give access only to your own workspaces.")
        if role == "admin" and not spaces:
            raise HTTPException(400, "A workspace admin needs at least one workspace.")
        return spaces if role != "owner" else []

    def check_password(password: str) -> None:
        problem = auth.password_problem(password)
        if problem:
            raise HTTPException(400, problem)

    @router.get("/api/users")
    def list_users(request: Request):
        return [auth.public_user(u) for u in auth.users(store) if manageable(request, u)]

    def actor(request: Request) -> dict:
        return getattr(request.state, "user", None) or LOCAL_OWNER

    def guard_owner_change(request: Request, target: dict, new_role: Optional[str] = None, removing: bool = False) -> None:
        """Only the super admin manages owner logins; the super admin's own login cannot be removed or demoted here."""
        who = actor(request)
        target_super = normalise_email(target.get("email")) in auth.super_admins()
        if target_super and removing:
            raise HTTPException(403, "The super admin login cannot be removed.")
        if target_super and new_role is not None and new_role != "owner":
            raise HTTPException(403, "The super admin stays an owner.")
        if target_super and not auth.is_super_admin(who):
            raise HTTPException(403, "Only the super admin can change the super admin login.")
        touches_owner = target.get("role") == "owner" or new_role == "owner"
        if touches_owner and not auth.is_super_admin(who):
            raise HTTPException(403, "Only the super admin can add, change or remove owner logins.")

    @router.post("/api/users")
    def add_user(body: UserBody, request: Request):
        email = normalise_email(body.email)
        if not email:
            raise HTTPException(400, "Enter a valid email address.")
        check_role(body.role)
        check_password(body.password)
        if auth.find_user(store, email):
            raise HTTPException(409, "That person already has a login.")
        if auth.users(store):                      # the very first login (setting the app up) is always allowed
            guard_owner_change(request, {"email": email, "role": body.role})
        if not owner_left([*auth.users(store), {"role": body.role}]):
            raise HTTPException(400, "The first login must be an owner (no admin password is set).")
        spaces = clean_spaces(request, body.role, body.workspaces)
        user = {"email": email, "name": body.name.strip()[:80] or email, "role": body.role,
                "password_hash": auth.hash_password(body.password), "created_at": auth.now_iso(),
                **({"workspaces": spaces} if spaces else {})}
        store.update("settings", lambda s: {**s, "users": [*(s.get("users") or []), user]})
        from . import audit
        audit.record(store, actor(request), "user.add", email, f"role {body.role}")
        return auth.public_user(user)

    @router.put("/api/users/{email}")
    def change_user(email: str, body: UserChange, request: Request):
        user = auth.find_user(store, email)
        if not user or not manageable(request, user):
            raise HTTPException(404, "No login with that email.")
        guard_owner_change(request, user, body.role)
        changes = {}
        if body.name is not None:
            changes["name"] = body.name.strip()[:80] or user["email"]
        if body.role is not None:
            check_role(body.role)
            changes["role"] = body.role
        role_after = changes.get("role", user.get("role"))
        if body.workspaces is not None or "role" in changes:
            spaces = clean_spaces(request, role_after, body.workspaces if body.workspaces is not None else user.get("workspaces") or [])
            if body.workspaces is not None or spaces != (user.get("workspaces") or []):
                changes["workspaces"] = spaces
        if body.can_edit_contacts is not None:
            changes["can_edit_contacts"] = bool(body.can_edit_contacts)
        if body.password is not None:
            check_password(body.password)
            changes.update(password_hash=auth.hash_password(body.password), password_changed_at=auth.now_iso())
        updated = {**user, **changes}
        if not owner_left([updated if u["email"] == user["email"] else u for u in auth.users(store)]):
            raise HTTPException(400, "Keep at least one owner login (no admin password is set).")
        store.update("settings", lambda s: {**s, "users": [updated if isinstance(u, dict) and u.get("email") == user["email"] else u
                                                           for u in s.get("users") or []]})
        from . import audit
        what = [f"role {user.get('role')} -> {changes['role']}" if "role" in changes and changes["role"] != user.get("role") else "",
                "password changed" if "password_hash" in changes else "",
                f"workspaces: {', '.join(changes['workspaces']) or 'all'}" if "workspaces" in changes else "",
                f"may edit contacts: {'yes' if changes['can_edit_contacts'] else 'no'}" if "can_edit_contacts" in changes else "",
                "name changed" if "name" in changes and changes["name"] != user.get("name") else ""]
        audit.record(store, actor(request), "user.change", user["email"], "; ".join(w for w in what if w) or "no change")
        return auth.public_user(updated)

    @router.delete("/api/users/{email}")
    def remove_user(email: str, request: Request):
        user = auth.find_user(store, email)
        if not user or not manageable(request, user):
            raise HTTPException(404, "No login with that email.")
        guard_owner_change(request, user, removing=True)
        remaining = [u for u in auth.users(store) if u["email"] != user["email"]]
        if not owner_left(remaining):
            raise HTTPException(400, "Keep at least one owner login (no admin password is set).")
        store.update("settings", lambda s: {**s, "users": [u for u in s.get("users") or []
                                                           if not (isinstance(u, dict) and u.get("email") == user["email"])]})
        from . import audit
        audit.record(store, actor(request), "user.remove", user["email"], f"was {user.get('role')}")
        return {"ok": True}

    return router
