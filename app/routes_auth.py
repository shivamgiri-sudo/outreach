"""Sign in / out, who am I, team logins (owner only), and the role check every route goes through.

Roles:
  owner   everything
  admin   workspace admin: a caller who also manages the caller and viewer logins of its own workspaces
  caller  works leads, calls, meetings and tasks: only the writes listed in CALLER_WRITES (every other write, including
          any route added later, needs an owner); cannot change settings, integrations, campaigns or live switches,
          run or resume sending, merge or clean data, spend provider credits, or download CSV exports
  viewer  read only: any request that changes something is refused (except signing out)
  admin   a workspace admin: works like a caller in its own workspaces, manages their logins and changes their
          templates (app/ws_templates.py; never main's or another workspace's)
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import auth
from .hygiene import normalise_email
from .web_security import client_ip
from .ws_templates import ADMIN_TEMPLATE_ROUTES

LOCAL_OWNER = {"sub": "local", "email": "", "name": "This computer", "role": "owner", "claims": None}
ANYONE = {("POST", "/api/logout"), ("POST", "/api/me/password"),
          ("POST", "/api/me/two-step/start"), ("POST", "/api/me/two-step/confirm"), ("POST", "/api/me/two-step/off"),
          ("POST", "/api/me/two-step/recovery"),       # each person's own two-step login
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
    ("PUT", "/api/linkedin/owner-accounts"), ("PUT", "/api/linkedin/sequence"), ("POST", "/api/linkedin/sequence/reset"), ("PUT", "/api/lead-fields"),   # industry setup
    ("POST", "/api/presets/{preset_id}/apply"),
    ("POST", "/api/prospects/export"), ("POST", "/api/prospects/import"), ("POST", "/api/prospects/suppress"),       # prospect DB
    ("GET", "/api/prospects/suppressions"), ("POST", "/api/prospects/unsuppress"),
    ("DELETE", "/api/prospects/people/{person_id}"), ("PATCH", "/api/prospects/people/{person_id}"), ("POST", "/api/prospects/sync-crm"),
    ("PUT", "/api/prospects/settings"), ("POST", "/api/prospects/enrich"), ("GET", "/api/prospects/spend"), ("PUT", "/api/prospects/spend"),
    ("GET", "/api/security/settings"), ("PUT", "/api/security/settings"), ("POST", "/api/security/two-step/{email}/reset"),
    ("PUT", "/api/insights/source-spend"), ("GET", "/api/inbound/log"), ("POST", "/api/inbound/{connector_id}/test"), ("POST", "/api/inbound/{connector_id}/sync"),
}
OWNER_PREFIXES = ("/api/users", "/api/compliance", "/api/backup", "/api/export/", "/api/capture", "/api/hiring-engine", "/api/linkedin-agent", "/api/linkedin-cloud", "/api/audit", "/api/system")    # any method (exports: no CSV for callers)
OWNER_WRITE_PREFIXES = ("/api/integrations", "/api/channels", "/api/workflows", "/api/flows", "/api/brand", "/api/labels", "/api/onboarding")   # anything but GET
READ_METHODS = ("GET", "HEAD", "OPTIONS")

# ---------- SECURITY: writes a caller (and a workspace admin) may make. Every other write needs an owner. ----------
# Deny by default: a new write route is owner-only until it is reviewed and listed here. Kept to a caller's daily work
# (leads, calls, notes, tasks, meetings, replies, one-off emails / WhatsApp, LinkedIn marks, imports, lists, views),
# plus routes that run without a login (their own secret or token is the check) and the paired-tool routes.
CALLER_WRITES = {
    # no login needed (sign-in, password reset, webhooks, tracking, the office runner swapping its one-time code)
    ("POST", "/api/login"), ("POST", "/api/login/code"), ("POST", "/api/login/setup/start"), ("POST", "/api/login/setup/confirm"),
    ("POST", "/api/password/forgot"), ("POST", "/api/password/reset"), ("POST", "/api/linkedin-runner/pair"),
    ("POST", "/api/webhooks/{channel}/{provider}"), ("POST", "/api/inbound/{connector_id}"), ("POST", "/api/inbound/google/{connector_id}"),
    ("POST", "/api/inbound/meta/{connector_id}"), ("POST", "/api/activation/event"), ("POST", "/u/{token}"), ("POST", "/demo/{token}/opened"),
    # paired tools: the Chrome assistant / LinkedIn agent token, the office runner, MCP (each limited to its own paths)
    ("POST", "/api/extension/pair"), ("DELETE", "/api/extension/tokens/{token_id}"), ("POST", "/api/extension/save-profile"),
    ("POST", "/api/extension/inbox"), ("POST", "/api/extension/agent/event"), ("POST", "/api/extension/cloud/state"),
    ("POST", "/api/extension/leads/{lead_id}/sent"), ("POST", "/api/extension/leads/{lead_id}/accepted"),
    ("POST", "/api/extension/leads/{lead_id}/replied"), ("POST", "/api/extension/leads/{lead_id}/profile"),
    ("POST", "/api/extension/leads/{lead_id}/claim"), ("POST", "/api/linkedin-runner/heartbeat"), ("POST", "/api/mcp"),
    # leads: add, import, edit, work
    ("POST", "/api/leads/import"), ("POST", "/api/leads/import-sheet"), ("POST", "/api/leads/bulk"), ("PATCH", "/api/leads/{lead_id}"),
    ("PATCH", "/api/leads/{lead_id}/custom"), ("POST", "/api/leads/{lead_id}/stage"), ("POST", "/api/leads/{lead_id}/tags"),
    ("POST", "/api/leads/{lead_id}/owner"), ("POST", "/api/leads/{lead_id}/note"), ("POST", "/api/leads/{lead_id}/activity"),
    ("POST", "/api/leads/{lead_id}/call-outcome"), ("POST", "/api/leads/{lead_id}/consent"), ("POST", "/api/leads/{lead_id}/reply"),
    ("POST", "/api/leads/{lead_id}/draft-reply"), ("POST", "/api/leads/{lead_id}/meetings"), ("POST", "/api/leads/{lead_id}/whatsapp"),
    ("POST", "/api/leads/{lead_id}/email-compose"), ("POST", "/api/leads/{lead_id}/email-now"), ("POST", "/api/leads/{lead_id}/enroll"),
    ("POST", "/api/leads/{lead_id}/sequence/resume"), ("POST", "/api/leads/{lead_id}/sequence/clear-stuck"),
    ("POST", "/api/leads/{lead_id}/contact-points"), ("POST", "/api/leads/{lead_id}/contact-points/primary"),
    ("POST", "/api/leads/{lead_id}/contact-points/remove"), ("POST", "/api/leads/{lead_id}/people"),
    ("PUT", "/api/leads/{lead_id}/people/{person_id}"), ("DELETE", "/api/leads/{lead_id}/people/{person_id}"),
    ("POST", "/api/leads/{lead_id}/company-health"), ("POST", "/api/leads/{lead_id}/demo-link"), ("POST", "/api/leads/{lead_id}/pilot"),
    ("POST", "/api/leads/{lead_id}/referral"), ("PUT", "/api/leads/{lead_id}/linkedin-account"),
    ("POST", "/api/leads/{lead_id}/linkedin/start"), ("POST", "/api/leads/{lead_id}/linkedin/stop"), ("POST", "/api/leads/{lead_id}/linkedin/sent"),
    ("POST", "/api/leads/{lead_id}/linkedin/accepted"), ("POST", "/api/leads/{lead_id}/linkedin/replied"),
    ("POST", "/api/leads/{lead_id}/linkedin/branch"), ("POST", "/api/linkedin/assign"), ("POST", "/api/linkedin/results"),
    ("POST", "/api/import/preview"), ("POST", "/api/import/excel"), ("POST", "/api/import/commit"), ("POST", "/api/import/{import_id}/undo"),
    ("POST", "/api/scrape/extract"), ("POST", "/api/jobs/ingest"), ("POST", "/api/prospects/add-to-leads"),
    ("POST", "/api/signals/items/{item_id}/lead"), ("POST", "/api/signals/items/{item_id}/status"),
    # tasks, meetings, replies, notifications, the rep's own day, lists and saved views
    ("POST", "/api/tasks"), ("POST", "/api/tasks/done"), ("PUT", "/api/tasks/{task_id}"), ("POST", "/api/tasks/{task_id}/done"),
    ("POST", "/api/meetings/{meeting_id}/cancel"), ("POST", "/api/meetings/{meeting_id}/done"), ("POST", "/api/reply-inbox/action"),
    ("POST", "/api/notifications/read"), ("PUT", "/api/my-day/targets"), ("POST", "/api/intelligence/remind-stale"),
    ("POST", "/api/lists"), ("DELETE", "/api/lists/{list_id}"), ("POST", "/api/lists/{list_id}/members"),
    ("POST", "/api/lists/{list_id}/start-email"), ("POST", "/api/lists/{list_id}/start-linkedin"),
    ("POST", "/api/lead-views"), ("DELETE", "/api/lead-views/{view_id}"),
    # writing help and shared snippets, stories, partners, call script, reports by email, estimates and searches (no spend)
    ("POST", "/api/prompts/generate"), ("POST", "/api/sequences/preview-draft"), ("POST", "/api/stories"), ("POST", "/api/partners"), ("POST", "/api/partners/{partner_id}/status"),
    ("PUT", "/api/call-script"), ("DELETE", "/api/call-script"), ("POST", "/api/reports/email"), ("POST", "/api/find-leads/estimate"),
    ("POST", "/api/prospects/enrich/estimate"), ("POST", "/api/prospects/people/search"), ("POST", "/api/prospects/saved-searches"),
    ("DELETE", "/api/prospects/saved-searches/{search_id}"),
}
# Templates (snippets, LinkedIn connection notes) are edited only by owners (any workspace) and workspace admins (their
# own workspace; the template routes / workspace guard check which). Never by callers.
ADMIN_WRITES = {
    # workspace management of their own workspaces (app/routes_workspaces.py checks the workspace is theirs)
    ("PUT", "/api/workspaces/{ws_id}"), ("POST", "/api/workspaces/{ws_id}/members"), ("DELETE", "/api/workspaces/{ws_id}/members/{email}"),
    ("POST", "/api/workspaces"),          # a workspace admin's new workspace is created as "requested" (an owner approves it)
    ("POST", "/api/snippets"), ("PUT", "/api/snippets/{snippet_id}"), ("DELETE", "/api/snippets/{snippet_id}"),
    ("PUT", "/api/linkedin/variants"), ("POST", "/api/linkedin/variants/generate"), ("POST", "/api/linkedin/variants/score"),
    # a client account's own WhatsApp provider and alert settings (app/routes_account_usage.py checks it is their own account)
    ("PUT", "/api/account/whatsapp"), ("DELETE", "/api/account/whatsapp"), ("PUT", "/api/account/alerts"),
}
# LinkedIn agent per client account (app/linkedin_scope.py): an account's admins run their own account's agent, slots and
# office computer (the routes cut to the admin's account, 404 for another's); callers / viewers only read its status.
LINKEDIN_OWNER_ONLY = {("PUT", "/api/linkedin-cloud/where"), ("PUT", "/api/linkedin-agent/kill")}


def linkedin_account_ok(role: str, method: str, path: str) -> bool:
    if (method, path) in LINKEDIN_OWNER_ONLY or not path.startswith(("/api/linkedin-agent", "/api/linkedin-cloud")):
        return False
    return role == "admin" or (role in ("caller", "viewer") and (method, path) == ("GET", "/api/linkedin-agent"))
# ---------- end SECURITY block ----------


class LoginBody(BaseModel):
    email: str = ""
    password: str = ""


class UserBody(BaseModel):
    email: str
    name: str = ""
    role: str = "caller"
    password: str
    workspaces: list = []          # ids; empty = every workspace. Set at creation so a client's login never starts open.
    all_workspaces: bool = False   # the explicit "every workspace" choice (owners only); needed once there is more than one
    account_id: str = ""           # the client company (app/accounts.py); empty = the adder's own (owners: Mas Callnet)


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
    if path.startswith(OWNER_WRITE_PREFIXES):
        return "owner"
    return "caller" if (method, path) in CALLER_WRITES else "owner"        # SECURITY: writes default to owner


def allowed(role: str, needed: str) -> bool:
    rank = {"viewer": 0, "caller": 1, "admin": 1, "owner": 2}
    return rank.get(role, -1) >= rank[needed]


def role_guard(request: Request) -> None:
    """Global dependency. The login middleware puts the signed-in user on request.state; public routes have none."""
    user = getattr(request.state, "user", None)
    if user is None:
        return
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path is None:                          # SECURITY: no matched route template means no reviewed decision: refuse
        raise HTTPException(403, "This request is not allowed.")
    needed = needed_role(request.method, path)
    if user.get("role") == "admin" and (path == "/api/users" or path.startswith("/api/users/")):
        return                                # workspace admins manage their own workspaces' logins (checked in the routes)
    if user.get("role") == "admin" and (request.method, path) in (ADMIN_TEMPLATE_ROUTES | ADMIN_WRITES):
        return                                # SECURITY: workspace admins edit their own workspace's templates (checked in the routes)
    if user.get("kind") not in ("extension", "runner") and linkedin_account_ok(user.get("role", ""), request.method, path):
        return                                # SECURITY: an account's own LinkedIn agent (the routes check the account)
    if not allowed(user.get("role", ""), needed):
        what = "make changes" if user.get("role") == "viewer" else "do this"
        raise HTTPException(403, f"Your role ({user.get('role')}) cannot {what}. Ask the owner.")


def make_router(store, admin_password: str, limiter: auth.LoginLimiter, admin_login: str = "any") -> APIRouter:
    """admin_login: when the server admin password sign-in works (app/super_admin.py: off | local | any)."""
    from . import super_admin
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
        from . import login_log
        from .routes_security import after_password
        who = body.email.strip()[:320] or login_log.ADMIN_LOGIN
        caller = client_ip(request)
        if limiter.locked(caller):
            login_log.record(store, request, "login", who, "failure", "blocked: too many wrong attempts from this address")
            raise HTTPException(429, "Too many wrong passwords. Wait a minute and try again.")
        if len(body.password) > auth.MAX_PASSWORD_CHARS or len(body.email) > 320:
            login_log.record(store, request, "login", who, "failure", "email or password too long")
            raise HTTPException(400, "That is too long.")
        principal, user = None, None
        if not body.email.strip() and admin_password and not super_admin.admin_login_allowed(request, admin_login):
            login_log.record(store, request, "login", who, "failure", f"admin password sign-in not allowed here ({admin_login})")
            raise HTTPException(403, super_admin.admin_login_refusal(admin_login))
        if body.email.strip():
            user = auth.find_user(store, body.email)
            if auth.check_password((user or {}).get("password_hash", ""), body.password) and user:
                principal = auth.subject_principal(store, admin_password, "user:" + user["email"])
                from . import accounts
                if principal is None and accounts.is_client(user):
                    row = accounts.find(store, accounts.of_user(user)) or {}
                    login_log.record(store, request, "login", who, "failure", f"account {row.get('status', 'unknown')}")
                    raise HTTPException(403, "Your company's account is not active yet (or is suspended). Ask the platform admin.")
        elif admin_password and auth.password_matches(admin_password, body.password):
            principal = auth.subject_principal(store, admin_password, auth.ADMIN_SUBJECT)
        if principal is None:
            limiter.record_failure(caller)
            reason = ("no login with this email" if body.email.strip() and not user else
                      "wrong password" if body.email.strip() or admin_password else "no admin password is set")
            login_log.record(store, request, "login", who, "failure", reason)
            raise HTTPException(401, "Wrong email or password" if body.email.strip() else "Wrong password")
        second = after_password(store, user) if user else None
        if second:                          # the limiter is reset only once the code is right too
            login_log.record(store, request, "login", who, "pending",
                             "password right; waiting for the two-step code" if second["two_step"] == "code"
                             else "password right; two-step setup required")
            return {**second, "name": principal["name"]}
        limiter.reset(caller)
        login_log.record(store, request, "login", who, "success")
        if principal["sub"] == auth.ADMIN_SUBJECT:
            super_admin.admin_login_used(store, request)
        return {"token": auth.issue_token(store, principal), "role": principal["role"], "name": principal["name"]}

    @router.post("/api/logout")
    def logout(request: Request):
        user = getattr(request.state, "user", None) or {}
        if user.get("claims"):
            auth.revoke(store, user["claims"])
            from . import login_log
            login_log.record(store, request, "logout", user.get("email") or login_log.ADMIN_LOGIN, "success")
        return {"ok": True}

    @router.get("/api/me")
    def me(request: Request):
        user = getattr(request.state, "user", None) or LOCAL_OWNER
        from . import accounts
        acct = accounts.find(store, accounts.of_user(user)) or {}
        return {"name": user["name"], "email": user["email"], "role": user["role"], "can_edit_contacts": auth.can_edit_contacts(user), "workspaces": user.get("workspaces") or [],
                "account_id": accounts.of_user(user), "account_name": acct.get("name") or "", "client": accounts.is_client(user),
                "no_workspace": bool(user.get("no_workspace")) and user["role"] != "owner", "super_admin": auth.is_super_admin(user),
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
        from . import login_log
        if not stored or not auth.check_password(stored.get("password_hash", ""), body.current[:auth.MAX_PASSWORD_CHARS]):
            limiter.record_failure(caller)
            login_log.record(store, request, "password_change", user.get("email", ""), "failure", "current password not right")
            raise HTTPException(400, "Your current password is not right.")
        limiter.reset(caller)
        check_password(body.new)
        if body.new == body.current:
            raise HTTPException(400, "Pick a new password that is different from the current one.")
        changes = {"password_hash": auth.hash_password(body.new), "password_changed_at": auth.now_iso()}
        store.update("settings", lambda s: {**s, "users": [{**u, **changes} if isinstance(u, dict) and u.get("email") == stored["email"] else u
                                                           for u in s.get("users") or []]})
        login_log.record(store, request, "password_change", stored["email"], "success")
        from . import audit
        audit.record(store, user, "user.change", stored["email"], "changed own password")
        return {"ok": True}

    # ---------- team logins (owner only; the role guard enforces it) ----------
    def check_role(role: str) -> None:
        if role not in auth.ROLES:
            raise HTTPException(400, "Role must be owner, admin, caller or viewer.")

    ADMIN_GIVES = ("caller", "viewer")             # SECURITY: only owners add, change or remove workspace admin logins
    ADMIN_SEES = ("admin", "caller", "viewer")     # an admin still sees its own workspaces' admins (read only)

    def admin_spaces(request: Request) -> Optional[set]:
        """The workspaces a workspace admin manages, or None when the person is an owner (manages everyone)."""
        who = actor(request)
        return set(who.get("workspaces") or []) if who.get("role") == "admin" else None

    def manageable(request: Request, target: dict) -> bool:
        from . import accounts
        mine = admin_spaces(request)
        if mine is None:
            return True
        if accounts.of_user(target) != accounts.of_user(actor(request)):
            return False                       # SECURITY: never another account's logins
        spaces = set(target.get("workspaces") or [])
        return target.get("role") in ADMIN_SEES and bool(spaces) and spaces <= mine

    def guard_admin_change(request: Request, target: dict) -> None:
        """SECURITY: a workspace admin may change or remove callers and viewers only, never another admin (nor itself)."""
        if admin_spaces(request) is not None and target.get("role") not in ADMIN_GIVES:
            raise HTTPException(403, "Only an owner can change or remove a workspace admin login.")

    def clean_spaces(request: Request, role: str, spaces: list, account_id: str = "mcn") -> list:
        """Workspaces for a login: they must exist and belong to the login's account; a workspace admin gives only its
        own (its own by default); an admin login, and every client login, always has at least one."""
        from . import workspaces as ws_mod
        known = {w["id"] for w in ws_mod.all_workspaces(store) if (w.get("account_id") or "mcn") == account_id}
        spaces = list(dict.fromkeys(str(w)[:40] for w in spaces or []))[:50]
        if any(w not in known for w in spaces):
            raise HTTPException(400, "Pick workspaces from the list.")
        mine = admin_spaces(request)
        if mine is not None:
            if role not in ADMIN_GIVES:
                raise HTTPException(403, "A workspace admin can add callers and viewers only.")
            spaces = spaces or sorted(mine)
            if not set(spaces) <= mine:
                raise HTTPException(403, "You can give access only to your own workspaces.")
        if role == "admin" and not spaces:
            raise HTTPException(400, "A workspace admin needs at least one workspace.")
        if account_id != "mcn" and not spaces:
            raise HTTPException(400, "A client login needs at least one of its company's workspaces.")
        return spaces if role != "owner" else []

    def account_for_new(request: Request, wanted: str) -> str:
        """The account a new login joins: a workspace admin's own (always); an owner picks any (default Mas Callnet)."""
        from . import accounts
        who = actor(request)
        if admin_spaces(request) is not None:
            if wanted and wanted != accounts.of_user(who):
                raise HTTPException(403, "You can add logins to your own company only.")
            return accounts.of_user(who)
        wanted = wanted or accounts.MCN
        if accounts.find(store, wanted) is None:
            raise HTTPException(400, "Pick an account from the list.")
        return wanted

    def check_password(password: str) -> None:
        problem = auth.password_problem(password)
        if problem:
            raise HTTPException(400, problem)

    @router.get("/api/users")
    def list_users(request: Request):
        from . import two_step
        last = {}
        try:
            history = store.read("login_history")
        except KeyError:
            history = []
        for row in history:
            if row.get("event") == "login" and row.get("result") == "success" and row.get("email"):
                last[row["email"]] = row.get("at")
        from . import accounts
        picked = accounts.current() if admin_spaces(request) is None else None     # an owner's account picker
        return [{**auth.public_user(u), "two_step_on": two_step.is_on(u), "last_sign_in": last.get(u["email"]),
                 "no_workspace": bool(u.get("no_workspace")) and u.get("role") != "owner"}
                for u in auth.users(store) if manageable(request, u) and (picked is None or accounts.of_user(u) == picked)]

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
        if body.all_workspaces and admin_spaces(request) is not None:
            raise HTTPException(403, "Only an owner can give access to every workspace.")
        account = account_for_new(request, body.account_id.strip()[:40])
        if account != "mcn" and body.role == "owner":
            raise HTTPException(400, "A client account's login cannot be an owner. Use admin, caller or viewer.")
        if account != "mcn" and body.all_workspaces:
            raise HTTPException(400, "Pick the client's workspaces for this login.")
        spaces = clean_spaces(request, body.role, body.workspaces, account)
        from . import accounts, workspaces as ws_mod
        from . import account_limits
        account_limits.check_count(store, account, "seats")          # 403: "Your account reached its logins limit of N; ..."
        account_spaces = [w for w in ws_mod.all_workspaces(store) if (w.get("account_id") or "mcn") == account]
        if body.role != "owner" and not spaces and not body.all_workspaces and len(account_spaces) > 1:
            raise HTTPException(400, "Choose the workspaces this login may see, or All workspaces.")
        user = {"email": email, "name": body.name.strip()[:80] or email, "role": body.role,
                "password_hash": auth.hash_password(body.password), "created_at": auth.now_iso(), "account_id": account,
                **({"workspaces": spaces} if spaces else {})}
        store.update("settings", lambda s: {**s, "users": [*(s.get("users") or []), user]})
        from . import audit
        audit.record(store, actor(request), "user.add", email, f"role {body.role}" + (f"; account {account}" if account != "mcn" else ""),
                     extra={"account_id": account})
        if body.role == "owner":
            super_admin.owner_changed(store, actor(request), "user.add", email, f"role {body.role}")
        return auth.public_user(user)

    @router.put("/api/users/{email}")
    def change_user(email: str, body: UserChange, request: Request):
        user = auth.find_user(store, email)
        if not user or not manageable(request, user):
            raise HTTPException(404, "No login with that email.")
        guard_admin_change(request, user)
        guard_owner_change(request, user, body.role)
        changes = {}
        if body.name is not None:
            changes["name"] = body.name.strip()[:80] or user["email"]
        from . import accounts
        if body.role is not None:
            check_role(body.role)
            if body.role == "owner" and accounts.is_client(user):
                raise HTTPException(400, "A client account's login cannot be an owner. Use admin, caller or viewer.")
            changes["role"] = body.role
        role_after = changes.get("role", user.get("role"))
        if body.workspaces is not None or "role" in changes:
            spaces = clean_spaces(request, role_after, body.workspaces if body.workspaces is not None else user.get("workspaces") or [],
                                  accounts.of_user(user))
            if body.workspaces is not None or spaces != (user.get("workspaces") or []):
                changes["workspaces"] = spaces
        if body.can_edit_contacts is not None:
            changes["can_edit_contacts"] = bool(body.can_edit_contacts)
        if body.password is not None:
            check_password(body.password)
            changes.update(password_hash=auth.hash_password(body.password), password_changed_at=auth.now_iso())
        updated = {**user, **changes}
        if "workspaces" in changes or changes.get("role") == "owner":
            updated.pop("no_workspace", None)     # an explicit new choice of workspaces ends "no workspace"
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
        if "owner" in (user.get("role"), updated.get("role")) and (what[0] or what[1]):
            super_admin.owner_changed(store, actor(request), "user.change", user["email"], "; ".join(w for w in what[:2] if w))
        return auth.public_user(updated)

    @router.delete("/api/users/{email}")
    def remove_user(email: str, request: Request):
        user = auth.find_user(store, email)
        if not user or not manageable(request, user):
            raise HTTPException(404, "No login with that email.")
        guard_admin_change(request, user)
        guard_owner_change(request, user, removing=True)
        remaining = [u for u in auth.users(store) if u["email"] != user["email"]]
        if not owner_left(remaining):
            raise HTTPException(400, "Keep at least one owner login (no admin password is set).")
        store.update("settings", lambda s: {**s, "users": [u for u in s.get("users") or []
                                                           if not (isinstance(u, dict) and u.get("email") == user["email"])]})
        from . import audit
        audit.record(store, actor(request), "user.remove", user["email"], f"was {user.get('role')}")
        if user.get("role") == "owner":
            super_admin.owner_changed(store, actor(request), "user.remove", user["email"], "was owner")
        return {"ok": True}

    return router
