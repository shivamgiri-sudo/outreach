"""LinkedIn agent: the portal switch and score, and the calls the agent program makes with its pairing token.

Per client account (app/linkedin_scope.py): an owner sees and changes any account's agent (?account=, default the account
picked in the header; ?account=all is the overview), an admin only its own account's, a caller or viewer only reads its own
account's status (no log, no settings). Who may call is decided in routes_auth (LINKEDIN_ACCOUNT_ROUTES)."""
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import linkedin_agent as agent, linkedin_scope as scope


class ConfigBody(BaseModel):
    enabled: Optional[bool] = None
    weekly_connections: Optional[int] = None
    daily_messages: Optional[int] = None
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    weekdays_only: Optional[bool] = None
    connect_without_note: Optional[bool] = None
    gap_minutes: Optional[int] = None
    check_replies: Optional[bool] = None
    daily_connections: Optional[int] = None
    auto_start: Optional[bool] = None
    find_profiles: Optional[bool] = None
    lookups_per_day: Optional[int] = None
    days: Optional[list[int]] = None
    resume_account: Optional[str] = None
    pause_account: Optional[str] = None
    merge_from: Optional[str] = None
    merge_into: Optional[str] = None


class EventBody(BaseModel):
    kind: str
    lead_id: str = ""
    note: str = ""
    state: str = ""
    fatal: bool = False
    verified: Optional[bool] = None
    step: Optional[int] = None
    dry: bool = False
    # what the agent saw at the moment of the action (optional; see app/linkedin_send.py)
    observed_name: str = Field(default="", max_length=200)
    observed_url: str = Field(default="", max_length=500)
    chat_title: str = Field(default="", max_length=200)
    page_title: str = Field(default="", max_length=300)
    text_sent: str = Field(default="", max_length=2000)


class KillBody(BaseModel):
    on: bool


def make_router(store, notify_sender=None) -> APIRouter:
    router = APIRouter()

    # ----- the portal (owners: any account; admins: their own; callers / viewers: read their own status) -----
    def user_of(request: Request) -> dict:
        from .routes_auth import LOCAL_OWNER
        return getattr(request.state, "user", None) or LOCAL_OWNER

    def view(user: dict, owner) -> dict:
        out = agent.status(store, owner=owner)
        if owner is None:
            names = {}
            for row in out["accounts"]:
                names.setdefault(row["account_id"], scope.name_of(store, row["account_id"]))
                row["account_name"] = names[row["account_id"]]
        out["account_name"] = scope.name_of(store, owner) if owner else "All accounts"
        from . import workspaces
        seen = workspaces.visible_lead_ids(store)          # None = every workspace; else a log line about another workspace's lead is hidden
        if seen is not None:
            out["log"] = [r for r in out["log"] if not r.get("lead_id") or r["lead_id"] in seen]
        mine = scope.my_slugs(store, user)
        if mine is not None:                         # not an owner: only the LinkedIn profiles that are theirs
            out["accounts"] = [a for a in out["accounts"] if a["account"] in mine]
            out["log"] = [r for r in out["log"] if not r.get("account") or r["account"] in mine]
        out["can_manage"] = scope.may_manage(user)
        out["is_owner"] = user.get("role") == "owner"
        if not out["can_manage"]:                    # callers and viewers: the status only
            out = {k: out[k] for k in ("control", "online", "last_seen", "state", "account_id", "account_name", "can_manage", "is_owner", "kill")} | {
                "config": {"enabled": out["config"]["enabled"]}, "accounts": [], "log": []}
        return out

    @router.get("/api/linkedin-agent")
    def get_status(request: Request, account: str = Query("", max_length=40)):
        user = user_of(request)
        return view(user, scope.target(store, user, account, allow_all=user.get("role") == "owner"))

    @router.put("/api/linkedin-agent")
    def put_config(body: ConfigBody, request: Request, account: str = Query("", max_length=40)):
        from . import audit, audit_settings
        user = user_of(request)
        if not scope.may_manage(user):
            raise HTTPException(403, "Only an owner or an admin of this account can change the LinkedIn agent.")
        owner = scope.target(store, user, account)
        stamp = {"account_id": owner}
        before = agent.config(store, owner)
        was = bool(before.get("enabled"))
        try:
            if body.resume_account:
                if not agent.resume_allowed(store, body.resume_account, owner):
                    raise ValueError(f"No LinkedIn account {agent.norm_account(body.resume_account)} in this account.")
                agent.resume(store, body.resume_account)
                audit.record(store, user, "linkedin_agent.resume", agent.norm_account(body.resume_account),
                             f"LinkedIn account resumed ({scope.name_of(store, owner)})", stamp)
            if body.pause_account:
                if not agent.resume_allowed(store, body.pause_account, owner):
                    raise ValueError(f"No LinkedIn account {agent.norm_account(body.pause_account)} in this account.")
                agent.pause(store, body.pause_account, f"paused by {user.get('email') or user.get('name') or 'a person'}")
                audit.record(store, user, "linkedin_agent.pause", agent.norm_account(body.pause_account),
                             f"LinkedIn account paused by hand ({scope.name_of(store, owner)})", stamp)
            if body.merge_from or body.merge_into:
                agent.merge(store, body.merge_from or "", body.merge_into or "", owner)
                audit.record(store, user, "linkedin_agent.merge", agent.norm_account(body.merge_into or ""),
                             f"{agent.norm_account(body.merge_from or '')} merged into it", stamp)
            asked = body.model_dump(exclude={"resume_account", "pause_account", "merge_from", "merge_into"})
            after = agent.save_config(store, asked, owner)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if body.enabled is not None and body.enabled != was:
            audit.record(store, user, "linkedin_agent.on" if body.enabled else "linkedin_agent.off",
                         f"LinkedIn agent ({scope.name_of(store, owner)})", "", stamp)
        lowered = [k for k in scope.CEILINGS if isinstance(asked.get(k), int) and after.get(k) is not None and asked[k] > after[k]]
        changes = [c for c in audit_settings.diff(before, after) if c["key"].split(".")[0] != "enabled"]
        if changes:
            audit.record(store, user, "linkedin_agent.settings", f"LinkedIn agent ({scope.name_of(store, owner)})",
                         audit_settings.describe(changes) + (f"; lowered to the platform ceiling: {', '.join(lowered)}" if lowered else ""),
                         {"changes": changes, **stamp})
        out = view(user, owner)
        if lowered:
            out["clamped"] = {k: after[k] for k in lowered}
        return out

    @router.put("/api/linkedin-agent/kill")
    def kill(body: KillBody, request: Request):
        """The owner's platform-wide stop (routes_auth keeps this route owner-only)."""
        from . import audit
        user = user_of(request)
        if user.get("role") != "owner":
            raise HTTPException(403, "Only an owner can stop every LinkedIn agent.")
        scope.set_kill(store, body.on, user)
        audit.record(store, user, "linkedin_agent.kill_on" if body.on else "linkedin_agent.kill_off", "every LinkedIn agent",
                     "platform-wide stop switched " + ("on" if body.on else "off"), {"account_id": "mcn"})
        return view(user, scope.target(store, user, request.query_params.get("account", ""), allow_all=True))

    # ----- why was nothing sent to this person? (owners and admins) -----
    @router.get("/api/linkedin-agent/lead/{lead_id}/why")
    def why(lead_id: str, request: Request):
        from . import lead_access, linkedin_why, workspaces
        user = user_of(request)
        if not scope.may_manage(user):
            raise HTTPException(403, "Only an owner or an admin can see this.")
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None or not workspaces.visible(lead) or not lead_access.can_see(store, user, lead):
            raise HTTPException(404, "Lead not found")
        return {"lead_id": lead_id, "reasons": linkedin_why.reasons(store, lead), "sends": (lead.get("linkedin_sends") or [])[-20:]}

    @router.get("/api/linkedin-agent/stuck")
    def stuck(request: Request):
        from . import lead_access, linkedin_why, workspaces
        user = user_of(request)
        if not scope.may_manage(user):
            raise HTTPException(403, "Only an owner or an admin can see this.")
        leads = [l for l in store.read("leads") if workspaces.visible(l) and lead_access.can_see(store, user, l)]
        return {"stuck": linkedin_why.stuck(store, leads)}

    # ----- the agent program (pairing token; see extension_api.ALLOWED) -----
    def agent_slug(request: Request) -> tuple:
        """(LinkedIn account slug, client account of the token). Another client account's LinkedIn account: refused."""
        user = getattr(request.state, "user", None)
        owner = scope.token_account(user)
        slug = agent.norm_account(request.headers.get("x-linkedin-account", ""))
        try:
            scope.check_slug(store, slug, owner)
        except scope.NotYours as exc:
            raise HTTPException(403, str(exc)) from None
        return slug, owner

    @router.get("/api/extension/agent")
    def control(request: Request):
        account, owner = agent_slug(request)
        from . import linkedin_cloud
        linkedin_cloud.touch(store, getattr(request.state, "user", None))
        agent.heartbeat(store, "asking", account=account, name=unquote(request.headers.get("x-linkedin-name", ""))[:80], owner=owner)
        return agent.control(store, account=account, owner=owner)

    @router.post("/api/extension/agent/event")
    def event(body: EventBody, request: Request):
        account, owner = agent_slug(request)
        try:
            return agent.record(store, {**body.model_dump(), "account": account}, notify_sender=notify_sender, owner=owner)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    return router
