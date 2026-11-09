"""Accounts API (Setup > Accounts). Owners (platform staff, super admins among them) list, create and change accounts
and their sending settings; everyone else only sees its own account's name and status.

  GET  /api/accounts                   owners: every account with counts, limits, grants, masked settings
                                       others: [their own account] (id, name, status)
  POST /api/accounts                   owners: create
  PUT  /api/accounts/{account_id}      owners: rename, status, limits, integrations granted, notes
  PUT  /api/accounts/{account_id}/settings   owners: the account's sender identity, brand basics, caps, own sending keys

Writes are owner-only through the role guard (writes default to owner); the routes check again. Every change goes to
the change log with the account's id (app/audit.py).
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import accounts, audit, auth, integrations, workspaces as ws


class AccountBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    status: str = "active"
    limits: dict = Field(default_factory=dict)
    integrations_granted: List[str] = Field(default_factory=list, max_length=50)
    notes: str = Field(default="", max_length=500)


class AccountChange(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    status: Optional[str] = None
    limits: Optional[dict] = None
    integrations_granted: Optional[List[str]] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None, max_length=500)


class AccountSettingsBody(BaseModel):
    sender_name: Optional[str] = Field(default=None, max_length=120)
    sender_company: Optional[str] = Field(default=None, max_length=120)
    sender_address: Optional[str] = Field(default=None, max_length=300)
    sender_website: Optional[str] = Field(default=None, max_length=200)
    sender_contact_email: Optional[str] = Field(default=None, max_length=200)
    daily_cap: Optional[int] = None
    per_run_cap: Optional[int] = None
    live_sending: Optional[bool] = None
    brand: Optional[dict] = None
    sending: Optional[dict] = None


def make_router(store) -> APIRouter:
    router = APIRouter()

    def user_of(request: Request) -> dict:
        from .routes_auth import LOCAL_OWNER
        return getattr(request.state, "user", None) or LOCAL_OWNER

    def owner_only(request: Request) -> dict:
        user = user_of(request)
        if user.get("role") != "owner" or accounts.is_client(user):
            raise HTTPException(403, "Only the platform owners manage accounts.")
        return user

    def grantable() -> list:
        return [{"id": i["id"], "title": i["title"]} for i in integrations.CATALOGUE + integrations.SOURCE_CATALOGUE]

    def full(row: dict) -> dict:
        users = [u for u in auth.users(store) if accounts.of_user(u) == row["id"]]
        spaces = ws.all_workspaces(store, include_requested=True)
        mine = [w for w in spaces if w["account_id"] == row["id"]]
        own = {w["id"] for w in mine}
        leads = sum(1 for l in store.read("leads") if ws.ws_of(l) in own)
        return {**row, "workspaces": [{"id": w["id"], "name": w["name"], "status": w.get("status") or "active"} for w in mine],
                "logins": len(users), "leads": leads, "settings": accounts.masked_settings(store, row["id"]),
                "sending_ready": bool(accounts.sending_config(store, row["id"])),
                "email_hold": accounts.hold_reason(store, row["id"], "email")}

    @router.get("/api/accounts")
    def list_accounts(request: Request):
        user = user_of(request)
        if user.get("role") == "owner" and not accounts.is_client(user):
            return {"accounts": [full(a) for a in accounts.all_accounts(store)], "grantable": grantable(),
                    "statuses": list(accounts.STATUSES), "limit_keys": list(accounts.LIMIT_KEYS)}
        own = accounts.find(store, accounts.of_user(user)) or {"id": accounts.of_user(user), "name": "", "status": "active"}
        return {"accounts": [accounts.public(own)]}

    @router.post("/api/accounts")
    def create(body: AccountBody, request: Request):
        user = owner_only(request)
        try:
            row = accounts.create(store, body.name, audit.actor_of(user)["name"], body.status, body.limits,
                                  body.integrations_granted, body.notes)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, user, "account.create", row["name"],
                     f"id {row['id']}; status {row['status']}; grants: {', '.join(row['integrations_granted']) or 'none'}",
                     extra={"account_id": row["id"]})
        return full(row)

    @router.put("/api/accounts/{account_id}")
    def change(account_id: str, body: AccountChange, request: Request):
        user = owner_only(request)
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            new, old = accounts.update(store, account_id, changes)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        what = [f"{k}: {old.get(k)} -> {new.get(k)}" for k in ("name", "status", "limits", "integrations_granted", "notes")
                if old.get(k) != new.get(k)]
        if what:
            audit.record(store, user, "account.change", new["name"], f"id {account_id}; " + "; ".join(what),
                         extra={"account_id": account_id})
        return full(new)

    @router.put("/api/accounts/{account_id}/settings")
    def save_settings(account_id: str, body: AccountSettingsBody, request: Request):
        user = owner_only(request)
        if accounts.find(store, account_id) is None:
            raise HTTPException(404, "No such account.")
        raw = {k: v for k, v in body.model_dump().items() if v is not None}
        try:
            accounts.save_settings(store, account_id, raw)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from None
        keys = sorted(raw) + [f"sending.{k}" for k in sorted(raw.get("sending") or {})]
        audit.record(store, user, "account.settings", (accounts.find(store, account_id) or {}).get("name", account_id),
                     f"id {account_id}; saved {', '.join(keys) or 'nothing'}", extra={"account_id": account_id})   # names only
        return full(accounts.find(store, account_id))

    return router
