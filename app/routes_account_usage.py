"""An account's own page for usage, WhatsApp provider and alert settings, and the console's Alerts strip.

  GET    /api/account/usage                 usage against every limit (admins of the account; owners: ?account_id=)
  GET    /api/account/whatsapp              the account's own WhatsApp provider (secrets never returned) + the catalogue
  PUT    /api/account/whatsapp              {provider, values, live?}: save it (a blank or masked secret keeps the saved one)
  DELETE /api/account/whatsapp              forget it
  PUT    /api/account/alerts                {alert_managers, alerts_email, public_base_url}: who hears about leads, and links
  GET    /api/console/alerts                super admins: the abuse and limit alerts feed (the Alerts strip)
  POST   /api/console/accounts/{id}/release-hold   super admins: lift an automatic sending hold

Admins of a client account manage only their own account; owners may name another with ?account_id=. Everything changed
is written to the change log with the account's id.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import abuse_alerts, account_limits, account_whatsapp, accounts, audit, auth


class WhatsAppBody(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    values: dict = Field(default_factory=dict)
    live: Optional[bool] = None


class AlertsBody(BaseModel):
    alert_managers: Optional[list] = None
    alerts_email: Optional[bool] = None
    public_base_url: Optional[str] = Field(default=None, max_length=300)


def make_router(store) -> APIRouter:
    router = APIRouter()

    def target(request: Request, account_id: str = "") -> tuple:
        """(user, account id) for a page of the account: its own admins, or an owner naming the account."""
        user = getattr(request.state, "user", None) or {"role": "owner"}
        if user.get("role") == "owner" and not accounts.is_client(user):
            aid = account_id.strip()[:40] or accounts.current() or accounts.MCN
            if accounts.find(store, aid) is None:
                raise HTTPException(404, "No such account.")
            return user, aid
        if user.get("role") != "admin" or not accounts.is_client(user):
            raise HTTPException(403, "Only the account's admins see this page.")
        return user, accounts.of_user(user)

    @router.get("/api/account/usage")
    def usage(request: Request, account_id: str = ""):
        user, aid = target(request, account_id)
        row = accounts.find(store, aid) or {}
        return {"account": {"id": aid, "name": row.get("name", aid)}, "limits": account_limits.summary(store, aid), "hold": accounts.hold_reason(store, aid, "email")}

    @router.get("/api/account/whatsapp")
    def get_whatsapp(request: Request, account_id: str = ""):
        user, aid = target(request, account_id)
        if aid == accounts.MCN:
            raise HTTPException(400, "Mas Callnet uses the platform's WhatsApp connection in Integrations.")
        return {**account_whatsapp.shown(store, aid), "providers": account_whatsapp.catalogue()}

    @router.put("/api/account/whatsapp")
    def put_whatsapp(body: WhatsAppBody, request: Request, account_id: str = ""):
        user, aid = target(request, account_id)
        try:
            out = account_whatsapp.save(store, aid, body.provider, body.values, body.live)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, user, "account.whatsapp", (accounts.find(store, aid) or {}).get("name", aid),
                     f"id {aid}; own WhatsApp provider {body.provider} saved (live {'on' if out['live'] else 'off'}); keys not shown",
                     extra={"account_id": aid})
        return {**out, "providers": account_whatsapp.catalogue()}

    @router.delete("/api/account/whatsapp")
    def delete_whatsapp(request: Request, account_id: str = ""):
        user, aid = target(request, account_id)
        account_whatsapp.remove(store, aid)
        audit.record(store, user, "account.whatsapp", (accounts.find(store, aid) or {}).get("name", aid), f"id {aid}; own WhatsApp provider removed",
                     extra={"account_id": aid})
        return {**account_whatsapp.shown(store, aid), "providers": account_whatsapp.catalogue()}

    @router.put("/api/account/alerts")
    def put_alerts(body: AlertsBody, request: Request, account_id: str = ""):
        user, aid = target(request, account_id)
        if aid == accounts.MCN:
            raise HTTPException(400, "Mas Callnet sets these in Setup > Outbox.")
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            accounts.save_settings(store, aid, changes)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, user, "account.alerts", (accounts.find(store, aid) or {}).get("name", aid), f"id {aid}; alert settings: {', '.join(sorted(changes)) or 'none'}",
                     extra={"account_id": aid})
        mine = accounts.own_settings(store, aid)
        return {k: mine.get(k) for k in ("alert_managers", "alerts_email", "public_base_url")}

    def super_only(request: Request) -> dict:
        user = getattr(request.state, "user", None) or {}
        if not auth.is_super_admin(user) or user.get("view_as") or accounts.is_client(user):
            raise HTTPException(403, "Only a super admin can do this.")
        return user

    @router.get("/api/console/alerts")
    def alerts_feed(request: Request, limit: int = 30):
        super_only(request)
        return {"alerts": abuse_alerts.feed(store, max(1, min(limit, 100)))}

    @router.post("/api/console/accounts/{account_id}/release-hold")
    def release_hold(account_id: str, request: Request):
        user = super_only(request)
        if accounts.find(store, account_id) is None or account_id == accounts.MCN:
            raise HTTPException(404, "No such account.")
        if not abuse_alerts.release_hold(store, account_id, user):
            raise HTTPException(400, "This account has no automatic sending hold.")
        return {"ok": True}

    return router
