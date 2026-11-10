"""Lead ownership and list assignment API (admins of the workspace only): assign / split / share a list, the
per-workspace ownership switch, the migration report and "fix owners". Teams and rules come in a later phase."""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, lead_access, list_assign, lists, ownership_migration as mig, workspaces as ws


class AssignBody(BaseModel):
    to: str = Field(default="", max_length=200)           # '' = back to the admin pool
    take_over: bool = False


class SplitBody(BaseModel):
    members: List[str] = Field(min_length=1, max_length=50)
    mode: str = "even"
    counts: Optional[List[int]] = None
    take_over: bool = False


class ShareBody(BaseModel):
    users: List[str] = Field(default_factory=list, max_length=100)


class ModeBody(BaseModel):
    on: bool
    acknowledge: bool = False
    workspace: str = Field(default="", max_length=40)


class FixBody(BaseModel):
    confirm: bool = False
    lead_ids: Optional[List[str]] = None
    workspace: str = Field(default="", max_length=40)


def make_router(store) -> APIRouter:
    router = APIRouter()

    def user_of(request: Request):
        return getattr(request.state, "user", None)

    def who(request: Request) -> str:
        return audit.actor_of(user_of(request))["name"]

    def admin_ws(request: Request, workspace: str = "") -> str:
        """The workspace this call is about; the caller must be an owner / admin of it, else 403 (404 if not theirs)."""
        target = (workspace or "").strip() or ws.for_new_lead()
        user = user_of(request)
        allowed = ws.allowed_of(user)
        known = {w["id"] for w in ws.all_workspaces(store)}
        if target not in known or (allowed is not None and target not in allowed):
            raise HTTPException(404, "Workspace not found")
        if user is not None and not lead_access.is_admin(user, target):
            raise HTTPException(403, "Only an admin of this workspace can do this.")
        return target

    def admin_list(list_id: str, request: Request) -> dict:
        row = lists.get(store, list_id)
        if row is None or not ws.visible(row):
            raise HTTPException(404, "List not found")
        admin_ws(request, ws.ws_of(row))
        return row

    def guarded(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except (list_assign.AssignRefused, mig.SwitchRefused) as exc:
            raise HTTPException(400, str(exc))

    @router.put("/api/lists/{list_id}/assign")
    def assign(list_id: str, body: AssignBody, request: Request):
        row = admin_list(list_id, request)
        out = guarded(list_assign.assign, store, list_id, body.to.strip(), who(request), body.take_over)
        audit.record(store, user_of(request), "list.assign", row["name"], f"to {body.to or 'admin pool'}: {out['moved']} leads moved, {len(out['skipped'])} left with their owner")
        return out

    @router.post("/api/lists/{list_id}/split")
    def split(list_id: str, body: SplitBody, request: Request):
        row = admin_list(list_id, request)
        out = guarded(list_assign.split, store, list_id, body.members, who(request), body.mode, body.counts, body.take_over)
        audit.record(store, user_of(request), "list.split", row["name"], f"into {len(out['lists'])} lists, {len(out['skipped'])} left with their owner")
        return out

    @router.put("/api/lists/{list_id}/share")
    def share(list_id: str, body: ShareBody, request: Request):
        row = admin_list(list_id, request)
        out = guarded(list_assign.share, store, list_id, body.users, who(request))
        audit.record(store, user_of(request), "list.share", row["name"], f"view-only for {', '.join(out.get('shared_with') or []) or 'nobody'}")
        return {k: v for k, v in out.items() if k != "lead_ids"}

    @router.get("/api/ownership-mode")
    def get_mode(request: Request, workspace: str = ""):
        target = admin_ws(request, workspace)
        info = mig.report(store, target)
        return {"workspace": target, "on": info["ownership_on"], "unowned": info["unowned"], "legacy_lists": info["legacy_lists"]}

    @router.put("/api/ownership-mode")
    def put_mode(body: ModeBody, request: Request):
        target = admin_ws(request, body.workspace)
        if body.on:
            out = guarded(mig.turn_on, store, target, body.acknowledge)
        else:
            mig.set_mode(store, target, False)
            out = {"on": False}
        audit.record(store, user_of(request), "ownership.mode", target, "on" if body.on else "off")
        return {"workspace": target, **out}

    @router.get("/api/ownership/report")
    def get_report(request: Request, workspace: str = ""):
        return mig.report(store, admin_ws(request, workspace))

    @router.post("/api/ownership/migrate")
    def migrate(request: Request, workspace: str = ""):
        target = admin_ws(request, workspace)
        out = mig.migrate_lists(store, target)
        audit.record(store, user_of(request), "ownership.migrate", target, f"{out['migrated']} lists")
        return out

    @router.post("/api/ownership/fix-owners")
    def fix_owners(body: FixBody, request: Request):
        target = admin_ws(request, body.workspace)
        if not body.confirm:
            raise HTTPException(400, "Confirm to make the list assignees the owners of these leads.")
        out = mig.fix_owners(store, target, who(request), body.lead_ids)
        audit.record(store, user_of(request), "ownership.fix_owners", target, f"{out['fixed']} leads, {len(out['ambiguous'])} ambiguous")
        return out

    return router
