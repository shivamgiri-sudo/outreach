"""View as: a super admin looks at the app exactly as one client login sees it, read-only, for at most 30 minutes.

The super admin asks for it in Setup > Accounts (POST /api/console/view-as {email}); the answer is a separate token
for the client login carrying a signed claim "va" (the super admin's subject) and "van" (the super admin's name/email).
  * It lasts MINUTES and is never renewed; signing out (the banner's Stop) ends it at once.
  * Every request made with it is written to the change log with both identities, stamped with the client's account,
    so the client's own admins see it in their change log (app/account_guard.py).
  * Writes are refused (only GET / HEAD and signing out pass), whatever the client login's role.
  * It stops working the moment the super admin is no longer a super admin, or the client login / account is gone or
    suspended (auth.authenticate checks the client login as for any token).
Only client logins (accounts other than Mas Callnet) can be viewed, and never an owner.
"""
from typing import Optional

from . import accounts, auth
from .hygiene import normalise_email

MINUTES = 30
READ_METHODS = ("GET", "HEAD", "OPTIONS")
STOP_PATHS = {("POST", "/api/logout")}


def start(store, admin_password: str, super_user: dict, email: str) -> dict:
    """{token, until, target} for a read-only session as `email`. Raises ValueError / LookupError."""
    if not auth.is_super_admin(super_user) or super_user.get("view_as"):
        raise PermissionError("Only a super admin can view as another login.")
    target = auth.find_user(store, normalise_email(email))
    if not target:
        raise LookupError("No login with that email.")
    if not accounts.is_client(target) or target.get("role") == "owner":
        raise ValueError("View as works for a client account's logins only.")
    principal = auth.subject_principal(store, admin_password, "user:" + target["email"])
    if principal is None:
        raise ValueError("That login's account is suspended, so there is nothing to view.")
    by = super_user.get("email") or super_user.get("name") or "Admin"
    token = auth.issue_token(store, principal, seconds=MINUTES * 60, extra={"va": super_user.get("sub") or "admin", "van": by})
    claims = auth._claims(store, token, auth._now())
    return {"token": token, "until": claims["exp"], "target": {"email": target["email"], "name": target.get("name") or target["email"],
                                                               "role": target.get("role"), "account_id": accounts.of_user(target)}}


def _super_still(store, admin_password: str, sub: str) -> Optional[dict]:
    if sub == auth.ADMIN_SUBJECT:
        return {"sub": sub, "name": "Admin", "email": ""} if admin_password else None
    principal = auth.subject_principal(store, admin_password, sub) if str(sub).startswith("user:") else None
    if not principal or principal.get("role") != "owner" or not auth.is_super_admin(principal):
        return None
    return principal


def principal_for(store, admin_password: str, principal: dict, claims: dict) -> Optional[dict]:
    """The client login's principal marked as a view-as session, or None when the super admin no longer may."""
    who = _super_still(store, admin_password, str(claims.get("va") or ""))
    if who is None or not accounts.is_client(principal) or principal.get("role") == "owner":
        return None
    return {**principal, "view_as": {"by": who.get("email") or who.get("name") or "Admin", "by_sub": claims["va"],
                                     "until": int(claims.get("exp", 0)), "as": principal.get("email", "")}}


def write_refused(method: str, path: str) -> bool:
    return method not in READ_METHODS and (method, path) not in STOP_PATHS


def audit_request(store, user: dict, method: str, path: str) -> None:
    """One change-log row per request, with both identities and the client's account."""
    from . import audit
    va = user.get("view_as") or {}
    actor = {"name": f"{va.get('by')} (super admin, viewing as {user.get('email')})", "email": va.get("by", ""), "role": "owner"}
    action = "view_as.stop" if (method, path) in STOP_PATHS else "view_as.request"
    audit.record(store, actor, action, user.get("email", ""), f"{method} {path} as {user.get('email')} (read-only)",
                 extra={"account_id": accounts.of_user(user), "view_as": user.get("email", ""), "super_admin": va.get("by", "")})


def public(user: Optional[dict]) -> Optional[dict]:
    va = (user or {}).get("view_as")
    if not va:
        return None
    return {"by": va.get("by"), "as": (user or {}).get("email"), "name": (user or {}).get("name"), "until": va.get("until")}
