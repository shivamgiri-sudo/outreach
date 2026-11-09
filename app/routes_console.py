"""Super-admin console API (Setup > Accounts for super admins). Every route checks the caller is a super admin; every
action goes to the hash-chained change log (stamped with the account, so its own admins see it) and alerts the super
admins (bell + email), as the Phase 1 security alerts do.

  GET    /api/console/accounts?status=          accounts with figures, pending first; stop switch; grantable list
  POST   /api/console/accounts/{id}/approve      pending -> active (+ its requested workspaces); applicant emailed
  POST   /api/console/accounts/{id}/reject       {reason}: applicant emailed the reason; the account is erased
  POST   /api/console/accounts/{id}/ask          {message}: emailed; shown on their waiting page
  POST   /api/console/accounts/{id}/suspend      {reason}: logins and sessions out at once, tokens deleted, sending held
  POST   /api/console/accounts/{id}/reactivate
  GET    /api/console/accounts/{id}/export       zip of the account's data
  POST   /api/console/accounts/{id}/delete       {confirm: account name}: erases the account (owner super admins only)
  POST   /api/console/view-as                    {email}: a 30-minute read-only token for a client login
  PUT    /api/console/stop-sending               {on, reason}: stop / resume all sending for every account
  GET    /api/account/audit                     a client account's admins: their own account's change log
Limits, grants and workspace requests use the existing owner routes (PUT /api/accounts/{id}, /api/workspaces/{id}/approve).
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import accounts, audit, auth, platform_controls, super_admin, super_console, view_as


class ReasonBody(BaseModel):
    reason: str = Field(default="", max_length=1000)


class AskBody(BaseModel):
    message: str = Field(default="", max_length=1000)


class DeleteBody(BaseModel):
    confirm: str = Field(default="", max_length=200)


class ViewAsBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class StopBody(BaseModel):
    on: bool
    reason: str = Field(default="", max_length=300)


def make_router(store, admin_password: str, base_url_of, sender) -> APIRouter:
    router = APIRouter()

    def super_only(request: Request) -> dict:
        user = getattr(request.state, "user", None) or {}
        if not auth.is_super_admin(user) or user.get("view_as") or accounts.is_client(user):
            raise HTTPException(403, "Only a super admin can do this.")
        return user

    def who(user: dict) -> str:
        return user.get("email") or user.get("name") or "Admin"

    def log(user: dict, action: str, row: dict, detail: str, title: str = "", text: str = "") -> None:
        audit.record(store, user, action, row.get("name", ""), f"id {row.get('id')}; {detail}", extra={"account_id": row.get("id")})
        if title:
            super_admin.alert(store, title, text or f"{who(user)}: {title}. {detail}")

    def mail_admins(account_id: str, subject: str, body: str) -> int:
        sent = 0
        for u in super_console.admins_of(store, account_id):
            platform_controls.send_mail(store, sender, u["email"], subject, f"Hello {u.get('name') or u['email']},\n\n{body}", "account_notice")
            sent += 1
        return sent

    def row_of(account_id: str) -> dict:
        row = accounts.find(store, account_id)
        if row is None or account_id == accounts.MCN:
            raise HTTPException(404, "No such account.")
        return row

    @router.get("/api/console/accounts")
    def list_accounts(request: Request, status: str = ""):
        super_only(request)
        from . import integrations          # the same grantable list as Setup > Accounts
        from . import abuse_alerts
        return {"accounts": super_console.overview(store, status[:20]), "stop_all_sending": platform_controls.public_stop(store), "signup_open": platform_controls.signup_open(store),
                "alerts": abuse_alerts.feed(store),
                "grantable": [{"id": i["id"], "title": i["title"]} for i in integrations.CATALOGUE + integrations.SOURCE_CATALOGUE],
                "limit_keys": list(accounts.LIMIT_KEYS), "statuses": list(accounts.STATUSES)}

    @router.post("/api/console/accounts/{account_id}/approve")
    def approve(account_id: str, request: Request):
        user = super_only(request)
        row_of(account_id)
        try:
            row = super_console.approve(store, account_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        mail_admins(account_id, "Your account is approved",
                    f"Your account for {row['name']} is approved. Sign in to start: {base_url_of()}/\n")
        log(user, "account.approve", row, "approved", f"Account approved: {row['name']}")
        return {"ok": True, "account": accounts.public(row)}

    @router.post("/api/console/accounts/{account_id}/reject")
    def reject(account_id: str, body: ReasonBody, request: Request):
        user = super_only(request)
        row = row_of(account_id)
        reason = " ".join(body.reason.split())
        if not reason:
            raise HTTPException(400, "Give the reason (it is emailed to the applicant).")
        mail_admins(account_id, "About your account request",
                    f"We could not approve the account for {row['name']}.\n\nReason: {reason}\n\nYou may reply to this email with questions.")
        counts = super_console.delete(store, account_id)
        log(user, "account.reject", row, f"rejected: {reason[:200]}; erased {counts['logins']} login(s)", f"Account request rejected: {row['name']}")
        return {"ok": True}

    @router.post("/api/console/accounts/{account_id}/ask")
    def ask(account_id: str, body: AskBody, request: Request):
        user = super_only(request)
        row_of(account_id)
        try:
            row = super_console.ask(store, account_id, body.message, who(user))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        sent = mail_admins(account_id, "We need a few details for your account",
                           f"Before we approve the account for {row['name']}, we need:\n\n{body.message.strip()}\n\n"
                           "Reply to this email with the details. You can also see this on your sign-in page.")
        log(user, "account.ask", row, f"asked for details (emailed {sent})")
        return {"ok": True}

    @router.post("/api/console/accounts/{account_id}/suspend")
    def suspend(account_id: str, body: ReasonBody, request: Request):
        user = super_only(request)
        row_of(account_id)
        row = super_console.suspend(store, account_id, body.reason)
        log(user, "account.suspend", row, f"suspended{': ' + body.reason.strip()[:200] if body.reason.strip() else ''}; sessions signed out; "
                                         f"{row['tokens_removed']} token(s) deleted; sending held", f"Account suspended: {row['name']}")
        return {"ok": True, "account": accounts.public(row), "tokens_removed": row["tokens_removed"]}

    @router.post("/api/console/accounts/{account_id}/reactivate")
    def reactivate(account_id: str, request: Request):
        user = super_only(request)
        row_of(account_id)
        row = super_console.reactivate(store, account_id)
        log(user, "account.reactivate", row, "reactivated", f"Account reactivated: {row['name']}")
        return {"ok": True, "account": accounts.public(row)}

    @router.get("/api/console/accounts/{account_id}/export")
    def export(account_id: str, request: Request):
        user = super_only(request)
        row = accounts.find(store, account_id)
        if row is None:
            raise HTTPException(404, "No such account.")
        data = super_console.export_zip(store, account_id)
        log(user, "account.export", row, f"data exported ({len(data)} bytes)", f"Account data exported: {row['name']}")
        name = "".join(ch if ch.isalnum() else "-" for ch in row["name"].lower()).strip("-") or account_id
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="account-{name}.zip"'})

    @router.post("/api/console/accounts/{account_id}/delete")
    def delete(account_id: str, body: DeleteBody, request: Request):
        user = super_only(request)
        if user.get("role") != "owner":
            raise HTTPException(403, "Only an owner super admin can delete an account.")
        row = row_of(account_id)
        if body.confirm.strip() != row["name"]:
            raise HTTPException(400, f"Type the account name exactly ({row['name']}) to delete it.")
        counts = super_console.delete(store, account_id)
        log(user, "account.delete", row, f"deleted: {counts['leads']} leads, {counts['logins']} logins, {counts['workspaces']} workspaces erased",
            f"Account deleted: {row['name']}")
        return {"ok": True, **counts}

    @router.post("/api/console/view-as")
    def start_view_as(body: ViewAsBody, request: Request):
        user = super_only(request)
        try:
            out = view_as.start(store, admin_password, user, body.email)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except (ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc)) from None
        target = out["target"]
        row = accounts.find(store, target["account_id"]) or {"id": target["account_id"], "name": target["account_id"]}
        log(user, "view_as.start", row, f"viewing as {target['email']} (read-only, {view_as.MINUTES} minutes)",
            f"View as started: {target['email']}", f"{who(user)} started a read-only view as {target['email']} ({row['name']}) for {view_as.MINUTES} minutes.")
        return out

    @router.get("/api/account/audit")
    def own_account_audit(request: Request, limit: int = 200, offset: int = 0):
        """A client account's admins: their own account's change log (console actions and super admins' view-as included)."""
        user = getattr(request.state, "user", None) or {}
        if user.get("role") != "admin" or not accounts.is_client(user):
            raise HTTPException(403, "Only a client account's admins see this page (owners use Setup > Audit).")
        return audit.trail(store, limit=max(1, min(limit, 500)), offset=max(0, offset), account=accounts.of_user(user))

    @router.put("/api/console/signup-open")
    def signup_switch(body: StopBody, request: Request):
        user = super_only(request)
        before = platform_controls.signup_open(store)
        platform_controls.set_signup(store, body.on)
        if before != body.on:
            word = "OPEN: anyone can ask for an account" if body.on else "CLOSED: no new sign-ups"
            audit.record(store, user, "platform.signup", "public sign-up", word)
            super_admin.alert(store, f"Public sign-up {'opened' if body.on else 'closed'}", f"{who(user)} set public sign-up {word}.")
        return {"signup_open": platform_controls.signup_open(store)}

    @router.put("/api/console/stop-sending")
    def stop_sending(body: StopBody, request: Request):
        user = super_only(request)
        before = platform_controls.stop_state(store)
        row = platform_controls.set_stop(store, body.on, who(user), body.reason)
        if bool(before) != body.on:
            word = "ON: all sending stopped" if body.on else "OFF: sending may resume"
            audit.record(store, user, "platform.stop_sending", "every account", f"{word}{'; ' + row['reason'] if row['reason'] else ''}")
            super_admin.alert(store, f"Stop all sending {'switched on' if body.on else 'switched off'}",
                              f"{who(user)} switched Stop all sending {word}.{(' Reason: ' + row['reason']) if row['reason'] else ''}")
        return {"stop_all_sending": platform_controls.public_stop(store)}

    return router
