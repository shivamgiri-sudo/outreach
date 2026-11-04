"""Sign in / out, who am I, team logins (owner only), and the role check every route goes through.

Roles:
  owner   everything
  caller  works leads, calls, meetings and tasks; cannot change settings, integrations, campaign rules or live
          switches, run or resume sending, or download CSV exports
  viewer  read only: any request that changes something is refused (except signing out)
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import auth
from .hygiene import normalise_email
from .web_security import client_ip

LOCAL_OWNER = {"sub": "local", "email": "", "name": "This computer", "role": "owner", "claims": None}
ANYONE = {("POST", "/api/logout")}
OWNER_ROUTES = {
    ("PUT", "/api/settings"), ("PUT", "/api/offer"), ("PUT", "/api/team"),
    ("GET", "/api/channels/webhooks"), ("GET", "/api/oauth/{provider}/start"),
    ("POST", "/api/sequences/run"), ("POST", "/api/sending/resume"), ("POST", "/api/sequences/resume-failed"),
    ("POST", "/api/practice/clear"), ("GET", "/api/enrich/apollo-export"),
    ("GET", "/api/leads/{lead_id}/export"), ("POST", "/api/leads/{lead_id}/erase"),
    ("POST", "/api/find-leads"),          # spends provider credits
    ("PUT", "/api/insights/source-spend"), ("GET", "/api/inbound/log"), ("POST", "/api/inbound/{connector_id}/test"), ("POST", "/api/inbound/{connector_id}/sync"),
}
OWNER_PREFIXES = ("/api/users", "/api/compliance", "/api/backup", "/api/export/", "/api/capture", "/api/hiring-engine", "/api/linkedin-agent")    # any method (exports: no CSV for callers)
OWNER_WRITE_PREFIXES = ("/api/integrations", "/api/channels", "/api/workflows", "/api/flows")   # anything but GET
READ_METHODS = ("GET", "HEAD", "OPTIONS")


class LoginBody(BaseModel):
    email: str = ""
    password: str = ""


class UserBody(BaseModel):
    email: str
    name: str = ""
    role: str = "caller"
    password: str


class UserChange(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    password: Optional[str] = None


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
    rank = {"viewer": 0, "caller": 1, "owner": 2}
    return rank.get(role, -1) >= rank[needed]


def role_guard(request: Request) -> None:
    """Global dependency. The login middleware puts the signed-in user on request.state; public routes have none."""
    user = getattr(request.state, "user", None)
    if user is None:
        return
    route = request.scope.get("route")
    needed = needed_role(request.method, getattr(route, "path", request.url.path))
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
        return {"name": user["name"], "email": user["email"], "role": user["role"],
                "auth_required": auth.auth_on(store, admin_password)}

    # ---------- team logins (owner only; the role guard enforces it) ----------
    def check_role(role: str) -> None:
        if role not in auth.ROLES:
            raise HTTPException(400, "Role must be owner, caller or viewer.")

    def check_password(password: str) -> None:
        problem = auth.password_problem(password)
        if problem:
            raise HTTPException(400, problem)

    @router.get("/api/users")
    def list_users():
        return [auth.public_user(u) for u in auth.users(store)]

    @router.post("/api/users")
    def add_user(body: UserBody):
        email = normalise_email(body.email)
        if not email:
            raise HTTPException(400, "Enter a valid email address.")
        check_role(body.role)
        check_password(body.password)
        if auth.find_user(store, email):
            raise HTTPException(409, "That person already has a login.")
        if not owner_left([*auth.users(store), {"role": body.role}]):
            raise HTTPException(400, "The first login must be an owner (no admin password is set).")
        user = {"email": email, "name": body.name.strip()[:80] or email, "role": body.role,
                "password_hash": auth.hash_password(body.password), "created_at": auth.now_iso()}
        store.update("settings", lambda s: {**s, "users": [*(s.get("users") or []), user]})
        return auth.public_user(user)

    @router.put("/api/users/{email}")
    def change_user(email: str, body: UserChange):
        user = auth.find_user(store, email)
        if not user:
            raise HTTPException(404, "No login with that email.")
        changes = {}
        if body.name is not None:
            changes["name"] = body.name.strip()[:80] or user["email"]
        if body.role is not None:
            check_role(body.role)
            changes["role"] = body.role
        if body.password is not None:
            check_password(body.password)
            changes.update(password_hash=auth.hash_password(body.password), password_changed_at=auth.now_iso())
        updated = {**user, **changes}
        if not owner_left([updated if u["email"] == user["email"] else u for u in auth.users(store)]):
            raise HTTPException(400, "Keep at least one owner login (no admin password is set).")
        store.update("settings", lambda s: {**s, "users": [updated if isinstance(u, dict) and u.get("email") == user["email"] else u
                                                           for u in s.get("users") or []]})
        return auth.public_user(updated)

    @router.delete("/api/users/{email}")
    def remove_user(email: str):
        user = auth.find_user(store, email)
        if not user:
            raise HTTPException(404, "No login with that email.")
        remaining = [u for u in auth.users(store) if u["email"] != user["email"]]
        if not owner_left(remaining):
            raise HTTPException(400, "Keep at least one owner login (no admin password is set).")
        store.update("settings", lambda s: {**s, "users": [u for u in s.get("users") or []
                                                           if not (isinstance(u, dict) and u.get("email") == user["email"])]})
        return {"ok": True}

    return router
