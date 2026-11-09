"""Workspaces API: list (with lead counts), overview, create, rename, delete (moving or deleting the leads), members,
move leads.

Who may do what (the role guard in routes_auth.py makes create and delete owner-only; the rest is checked here):
  owners          everything, every workspace
  workspace admin rename its own workspaces, see and change their members (its own logins only)
  others          list the workspaces they may see, move leads between them
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import accounts, audit, auth, workspace_admin, workspaces as ws


class WsBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class NewWsBody(WsBody):
    account_id: str = Field(default="", max_length=40)      # owners: which account it is for (default: the picked one)


class MoveBody(BaseModel):
    lead_ids: List[str] = Field(default_factory=list, max_length=5000)


class DeleteBody(BaseModel):
    confirm_name: str = Field(default="", max_length=80)
    move_to: str = Field(default="", max_length=40)
    delete_leads: bool = False


class MemberBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)


def make_router(store) -> APIRouter:
    router = APIRouter()

    def user_of(request: Request) -> Optional[dict]:
        return getattr(request.state, "user", None)

    def who(request: Request) -> str:
        return audit.actor_of(user_of(request))["name"]

    def managed(request: Request, ws_id: str) -> None:
        """Owners manage every workspace; a workspace admin only its own; nobody else manages any."""
        user = user_of(request) or {"role": "owner"}
        if workspace_admin.find(store, ws_id) is None:
            raise HTTPException(404, "No such workspace.")
        if user.get("role") == "owner":
            return
        allowed = ws.allowed_of(user)
        if (user.get("role") == "admin" and ws_id in (user.get("workspaces") or []) and not user.get("no_workspace")
                and (allowed is None or ws_id in allowed)):
            return
        raise HTTPException(403, "Only an owner or this workspace's admin can manage it.")

    def is_owner(request: Request) -> bool:
        return (user_of(request) or {"role": "owner"}).get("role") == "owner"

    def check_target(request: Request, email: str) -> dict:
        """A workspace admin changes only logins it manages (admins, callers, viewers of its own workspaces)."""
        target = auth.find_user(store, email)
        if not target:
            raise HTTPException(404, "No login with that email.")
        if is_owner(request):
            return target
        mine = set((user_of(request) or {}).get("workspaces") or [])
        spaces = set(target.get("workspaces") or [])
        if target.get("role") not in workspace_admin.ADMIN_GIVES or not spaces or not spaces <= mine:
            raise HTTPException(404, "No login with that email.")
        return target

    @router.get("/api/workspaces")
    def list_all(request: Request):
        allowed = ws.allowed_of(user_of(request))
        if allowed is None:
            allowed = getattr(request.state, "workspace_scope", (None, None))[1]     # an owner's account picker
        n = ws.counts(store)
        return [{**w, "leads": n.get(w["id"], 0)} for w in ws.all_workspaces(store) if allowed is None or w["id"] in allowed]

    @router.get("/api/workspaces/overview")
    def overview(request: Request):
        """Lead and member counts for the Setup > Workspaces page (owners: all; admins: their own; others: refused)."""
        user = user_of(request) or {"role": "owner"}
        if user.get("role") not in ("owner", "admin"):
            raise HTTPException(403, "Only owners and workspace admins can manage workspaces.")
        return workspace_admin.overview(store, ws.allowed_of(user), include_requested=user.get("role") == "owner")

    @router.post("/api/workspaces")
    def create(body: NewWsBody, request: Request):
        """Owners create a workspace for any account; a workspace admin only asks for one in its own account (status
        "requested" until an owner approves it in Setup > Workspaces)."""
        user = user_of(request) or {"role": "owner"}
        owner = user.get("role") == "owner"
        if owner:
            account = body.account_id.strip() or accounts.current() or accounts.MCN
            if accounts.find(store, account) is None:
                raise HTTPException(400, "Pick an account from the list.")
        else:
            if not accounts.is_client(user):
                raise HTTPException(403, "Only an owner can create workspaces.")       # Mas Callnet's admins: as before
            account = accounts.of_user(user)
            if body.account_id.strip() and body.account_id.strip() != account:
                raise HTTPException(403, "You can ask for workspaces of your own company only.")
        cap = accounts.limit_of(store, account, "workspaces")
        if cap is not None and len(accounts.workspaces_of(store, account, active_only=False)) >= cap:
            raise HTTPException(400, f"This account may have at most {cap} workspaces. Ask the platform admin.")
        try:
            row = ws.create(store, body.name, who(request), account, "active" if owner else "requested", user.get("email") or "")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, user, "workspace.create" if owner else "workspace.request", row["name"],
                     f"id {row['id']}; account {account}" + ("" if owner else "; waiting for an owner's approval"),
                     extra={"account_id": account})
        if not owner:                          # super admins: bell + email for every workspace request
            from . import super_admin
            from .growth_core import public_base_url
            acct = accounts.find(store, account) or {"name": account}
            super_admin.alert(store, f"Workspace request: {row['name']} ({acct['name']})",
                              f"{user.get('email') or 'A client admin'} asked for the workspace {row['name']} in {acct['name']}. "
                              f"Approve or reject it: {public_base_url(store, '')}/#setup/accounts (sign in as a super admin).")
        return row

    @router.post("/api/workspaces/{ws_id}/approve")
    def approve(ws_id: str, request: Request):
        """Owners only (role guard: writes default to owner). The asker gets the new workspace."""
        row = next((w for w in ws.all_workspaces(store, include_requested=True) if w["id"] == ws_id), None)
        if row is None or (row.get("status") or "active") != "requested":
            raise HTTPException(404, "No workspace request with that id.")
        workspace_admin.approve(store, row)
        audit.record(store, user_of(request), "workspace.approve", row["name"], f"id {ws_id}; account {row['account_id']}",
                     extra={"account_id": row["account_id"]})
        return {**row, "status": "active"}

    @router.post("/api/workspaces/{ws_id}/reject")
    def reject(ws_id: str, request: Request):
        row = next((w for w in ws.all_workspaces(store, include_requested=True) if w["id"] == ws_id), None)
        if row is None or (row.get("status") or "active") != "requested":
            raise HTTPException(404, "No workspace request with that id.")
        store.update("settings", lambda s: {**s, "workspaces": [w for w in s.get("workspaces") or []
                                                                if not (isinstance(w, dict) and w.get("id") == ws_id)]})
        audit.record(store, user_of(request), "workspace.reject", row["name"], f"id {ws_id}; account {row['account_id']}",
                     extra={"account_id": row["account_id"]})
        return {"ok": True}

    @router.put("/api/workspaces/{ws_id}")
    def rename(ws_id: str, body: WsBody, request: Request):
        managed(request, ws_id)
        old = workspace_admin.find(store, ws_id)["name"]
        try:
            row = workspace_admin.rename(store, ws_id, body.name)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if row["name"] != old:
            audit.record(store, user_of(request), "workspace.rename", row["name"], f"id {ws_id}; was {old}")
        return row

    @router.delete("/api/workspaces/{ws_id}")
    def remove(ws_id: str, request: Request, body: Optional[DeleteBody] = None):
        body = body or DeleteBody()
        if body.delete_leads and not is_owner(request):
            raise HTTPException(403, "Only an owner can delete leads permanently.")
        try:
            done = workspace_admin.delete(store, ws_id, body.confirm_name, body.move_to, body.delete_leads, who(request))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        what = [f"{done['moved']} leads moved to {done['moved_to']}" if done["moved_to"] else "",
                f"{done['deleted']} leads deleted permanently ({done['tasks_removed']} tasks)" if done["deleted"] else "",
                f"{len(done['members_removed'])} logins lost access" if done["members_removed"] else "",
                f"left with no workspace: {', '.join(done['left_without_workspace'])}" if done["left_without_workspace"] else ""]
        audit.record(store, user_of(request), "workspace.delete", done["name"], f"id {ws_id}; " + ("; ".join(w for w in what if w) or "empty"),
                     extra={"moved": done["moved"], "deleted": done["deleted"]})
        return {"ok": True, **done}

    @router.get("/api/workspaces/{ws_id}/members")
    def members(ws_id: str, request: Request):
        managed(request, ws_id)
        rows = workspace_admin.members(store, ws_id)
        if not is_owner(request):
            mine = set((user_of(request) or {}).get("workspaces") or [])
            rows = [r for r in rows if r["access"] == "member" and r["role"] in workspace_admin.ADMIN_GIVES and set(r["workspaces"]) <= mine]
        return rows

    @router.post("/api/workspaces/{ws_id}/members")
    def add_member(ws_id: str, body: MemberBody, request: Request):
        managed(request, ws_id)
        target = check_target(request, body.email)
        try:
            updated = workspace_admin.add_member(store, ws_id, target["email"])
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, user_of(request), "workspace.member_add", target["email"], f"added to workspace {ws_id}")
        return auth.public_user(updated)

    @router.delete("/api/workspaces/{ws_id}/members/{email}")
    def remove_member(ws_id: str, email: str, request: Request):
        managed(request, ws_id)
        target = check_target(request, email)
        if not is_owner(request) and target["email"] == (user_of(request) or {}).get("email"):
            raise HTTPException(400, "You cannot take yourself out of a workspace. Ask the owner.")
        try:
            updated = workspace_admin.remove_member(store, ws_id, target["email"])
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        left = " (now has no workspace: sees nothing)" if updated.get("no_workspace") else ""
        audit.record(store, user_of(request), "workspace.member_remove", target["email"], f"removed from workspace {ws_id}{left}")
        return {**auth.public_user(updated), "no_workspace": bool(updated.get("no_workspace"))}

    @router.post("/api/workspaces/{ws_id}/move")
    def move(ws_id: str, body: MoveBody, request: Request):
        allowed = ws.allowed_of(user_of(request))
        if allowed is not None and ws_id not in allowed:
            raise HTTPException(403, "You do not have access to that workspace.")
        try:
            moved = ws.move(store, body.lead_ids, ws_id, who(request))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        if moved:
            audit.record(store, user_of(request), "bulk.workspace_move", ws_id, f"{moved} of {len(body.lead_ids)} leads moved")
        return {"moved": moved}

    return router
