"""The account guard: one global dependency, run before the role and workspace guards, for three platform rules.

1. A pending account (self sign-up waiting for a super admin, app/signup.py) may only see its own "waiting for
   approval" page, its own profile, password and two-step login, and sign out. Every other request answers
   403 "account pending approval": no import, sending, integrations, tokens, exports or LinkedIn.
2. A super admin's "view as" session (app/view_as.py) is read-only: every write is refused; every request is written
   to the change log with both identities.
3. While the super admins' "Stop all sending" switch is on (app/platform_controls.py), the LinkedIn agent / office
   runner work routes and starting LinkedIn or email sequences answer 423.
Deny by default for (1): a route added later stays closed to pending accounts until it is listed here.
"""
import re

from fastapi import HTTPException, Request

from . import platform_controls, view_as

PENDING_TEXT = "account pending approval"
PENDING_OK = {
    ("GET", "/api/auth/status"), ("GET", "/api/me"), ("POST", "/api/logout"), ("POST", "/api/me/password"),
    ("GET", "/api/me/two-step"), ("POST", "/api/me/two-step/start"), ("POST", "/api/me/two-step/confirm"),
    ("POST", "/api/me/two-step/off"), ("POST", "/api/me/two-step/recovery"),
    ("GET", "/api/platform/status"), ("GET", "/api/signup/pending"), ("GET", "/api/brand/public"), ("GET", "/api/brand/logo"),
}
# Work handed out to LinkedIn agents / office runners, and starting sequences, while all sending is stopped.
STOP_BLOCKED = re.compile(r"^/api/(extension/(queue|agent|agent/event|cloud/.*|leads/[^/]+/claim)|linkedin-runner/slots"
                          r"|leads/[^/]+/linkedin/start|lists/[^/]+/start-(linkedin|email)|linkedin/assign|sequences/run"
                          r"|leads/[^/]+/(enroll|email-now|whatsapp))$")


def guard(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if user is None or request.method == "OPTIONS":
        return
    route = request.scope.get("route")
    path = getattr(route, "path", None) or request.url.path
    if user.get("account_pending") and (request.method, path) not in PENDING_OK:
        raise HTTPException(403, PENDING_TEXT)
    store = getattr(request.app.state, "store", None)
    if user.get("view_as") and store is not None:
        view_as.audit_request(store, user, request.method, path)
        if view_as.write_refused(request.method, path):
            raise HTTPException(403, f"Read-only: you are viewing as {user.get('email')}. Changes are refused. Stop viewing to make changes.")
    if store is not None and request.method != "HEAD" and STOP_BLOCKED.match(request.url.path):
        reason = platform_controls.stop_reason(store)
        if reason:
            raise HTTPException(423, reason)
