"""Cloud LinkedIn agent: the portal routes (/api/linkedin-cloud...: owners for any client account, an account's admins for
their own; routes_auth LINKEDIN_ACCOUNT_ROUTES and the checks here) and the three calls the cloud agent makes with its slot
token (/api/extension/cloud/..., see extension_api.ALLOWED).

The password and the code are taken from the request body, kept in memory only (linkedin_cloud.CloudJobs) and never
echoed back: the body is validated by hand so a validation error cannot return the value it refused."""
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import linkedin_cloud as cloud, linkedin_scope as scope


class SlotBody(BaseModel):
    label: str = Field(default="", max_length=60)


class SlotChange(BaseModel):
    enabled: bool | None = None
    label: str | None = Field(default=None, max_length=60)


class StateBody(BaseModel):
    state: str = Field(max_length=40)
    note: str = Field(default="", max_length=300)
    account: str = Field(default="", max_length=120)
    name: str = Field(default="", max_length=200)


def make_router(store, data_file, jobs: cloud.CloudJobs = None) -> APIRouter:
    router = APIRouter()
    base: Path = cloud.cloud_dir(data_file)
    jobs = jobs or cloud.CloudJobs()
    router.jobs = jobs
    router.base = base
    try:
        if cloud.slots(store):
            cloud.sync(store, base)                      # a lost slots.json or a token revoked elsewhere is repaired at start
    except (OSError, ValueError):
        pass

    def actor(request: Request) -> dict:
        from .routes_auth import LOCAL_OWNER
        return getattr(request.state, "user", None) or LOCAL_OWNER

    def audit(request: Request, action: str, target: str, detail: str = "", account: str = "mcn") -> None:
        from . import audit as audit_mod
        audit_mod.record(store, actor(request), action, target, detail, {"account_id": account})

    def manager(request: Request) -> dict:
        user = actor(request)
        if not scope.may_manage(user):
            raise HTTPException(403, "Only an owner or an admin of this account can do this.")
        return user

    def account_for(request: Request, allow_all: bool = False):
        return scope.target(store, actor(request), request.query_params.get("account", ""), allow_all)

    def found(slot_id: str, request: Request = None) -> dict:
        s = cloud.slot(store, slot_id)
        try:
            if not s:
                raise LookupError
            if request is not None:
                scope.ensure_mine(manager(request), cloud.account_of(s))
        except LookupError:
            raise HTTPException(404, "No such cloud account.") from None
        return s

    def view(request: Request, account=None) -> dict:
        """The box for the request's account (an owner's overview: every account, each slot with its account name)."""
        acct = account_for(request, allow_all=True) if account is None else account
        out = cloud.describe(store, base, jobs, acct)
        for row in out["slots"]:
            row["account_name"] = scope.name_of(store, row["account_id"])
        out["account_id"] = acct
        out["is_owner"] = actor(request).get("role") == "owner"
        return out

    async def raw_body(request: Request, limit: int = 2000) -> dict:
        body = await request.body()
        if len(body) > limit:
            raise HTTPException(400, "That is too long.")
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            raise HTTPException(400, "Send JSON.") from None
        if not isinstance(data, dict):
            raise HTTPException(400, "Send JSON.")
        return data

    # ----- portal (owners: any account; admins: their own) -----
    @router.get("/api/linkedin-cloud")
    def list_slots(request: Request):
        manager(request)
        return view(request)

    @router.post("/api/linkedin-cloud/slots")
    def add(body: SlotBody, request: Request):
        manager(request)
        acct = account_for(request)
        try:
            new = cloud.add_slot(store, base, actor(request), body.label, acct)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit(request, "linkedin_cloud.add", new["label"], f"cloud account {new['id']} ({scope.name_of(store, acct)})", acct)
        return view(request)

    @router.put("/api/linkedin-cloud/slots/{slot_id}")
    def change(slot_id: str, body: SlotChange, request: Request):
        s = found(slot_id, request)
        cloud.change_slot(store, base, slot_id, enabled=body.enabled, label=body.label, bump_run=bool(body.enabled))
        if body.enabled is not None and body.enabled != bool(s.get("enabled")):
            audit(request, "linkedin_cloud.on" if body.enabled else "linkedin_cloud.off", s["label"], f"cloud account {slot_id}", cloud.account_of(s))
        return view(request)

    @router.delete("/api/linkedin-cloud/slots/{slot_id}")
    def remove(slot_id: str, request: Request):
        found(slot_id, request)
        try:
            s = cloud.remove_slot(store, base, slot_id, jobs)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        audit(request, "linkedin_cloud.remove", s.get("label", ""), f"cloud account {slot_id}; its token was revoked and its browser profile deleted", cloud.account_of(s))
        return view(request)

    @router.post("/api/linkedin-cloud/slots/{slot_id}/login")
    async def login(slot_id: str, request: Request):
        s = found(slot_id, request)
        data = await raw_body(request)
        try:
            email, password = cloud.check_login(data.get("email"), data.get("password"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if not jobs.allow_attempt(slot_id):
            raise HTTPException(429, f"Too many login attempts for this account ({cloud.LOGIN_ATTEMPTS_PER_HOUR} an hour). Wait and try again; "
                                "repeated attempts are exactly what makes LinkedIn lock an account.")
        jobs.put_login(slot_id, email, password)
        del password, data
        if not s.get("enabled"):
            cloud.change_slot(store, base, slot_id, enabled=True)
        cloud.change_slot(store, base, slot_id, bump_run=True)          # a stopped agent is started again for this attempt
        cloud.set_state(store, slot_id, "logging_in", "login details handed to the cloud agent (kept in memory for 10 minutes)")
        domain = email.rsplit("@", 1)[-1]
        audit(request, "linkedin_cloud.login", s["label"], f"login attempt for an @{domain} address (cloud account {slot_id})", cloud.account_of(s))
        return view(request)

    @router.post("/api/linkedin-cloud/slots/{slot_id}/code")
    async def code(slot_id: str, request: Request):
        s = found(slot_id, request)
        data = await raw_body(request, 500)
        try:
            value = cloud.check_code(data.get("code"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        jobs.put_code(slot_id, value)
        cloud.set_state(store, slot_id, "logging_in", "code handed to the cloud agent")
        audit(request, "linkedin_cloud.code", s["label"], f"verification code sent to cloud account {slot_id}", cloud.account_of(s))
        return view(request)

    @router.post("/api/linkedin-cloud/slots/{slot_id}/logout")
    def logout(slot_id: str, request: Request):
        s = found(slot_id, request)
        jobs.drop(slot_id)
        cloud.change_slot(store, base, slot_id, bump_wipe=True, bump_run=True)
        cloud.set_state(store, slot_id, "logging_out", "the cloud runner deletes this account's browser session on its next pass")
        audit(request, "linkedin_cloud.logout", s["label"], f"cloud account {slot_id} logged out (browser session deleted)", cloud.account_of(s))
        return view(request)

    @router.post("/api/linkedin-cloud/slots/{slot_id}/restart")
    def restart(slot_id: str, request: Request):
        s = found(slot_id, request)
        cloud.change_slot(store, base, slot_id, enabled=True, bump_run=True, bump_restart=True)
        cloud.set_state(store, slot_id, "starting", "restarting the cloud browser")
        audit(request, "linkedin_cloud.restart", s["label"], f"cloud account {slot_id} restarted", cloud.account_of(s))
        return view(request)

    # ----- the cloud agent (its slot's pairing token only) -----
    def my_slot(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        s = cloud.slot_for_token(store, user)
        if not s:
            raise HTTPException(403, "Only a cloud LinkedIn agent can use this.")
        cloud.touch(store, user)
        return s

    @router.get("/api/extension/cloud/login-job")
    def take_login(request: Request):
        job = jobs.take_login(my_slot(request)["id"])
        return {"job": job}

    @router.get("/api/extension/cloud/code")
    def take_code(request: Request):
        return {"code": jobs.take_code(my_slot(request)["id"])}

    @router.post("/api/extension/cloud/state")
    def report(body: StateBody, request: Request):
        s = my_slot(request)
        if body.state not in cloud.STATES:
            raise HTTPException(400, "Unknown state.")
        try:
            cloud.renew_due(store, base)                 # server mode: the supervisor restarts the agent with its new token
        except (OSError, ValueError):
            pass
        return cloud.set_state(store, s["id"], body.state, body.note, body.account, body.name)

    return router
