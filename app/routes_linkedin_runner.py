"""Office runner routes.

Owner (portal, /api/linkedin-cloud/... so owner-only via routes_auth OWNER_PREFIXES): where LinkedIn runs, make a pairing
code (with the one-line install commands), cancel it, rename or disconnect the office computer.

Office computer (/api/linkedin-runner/...):
  POST pair               public, the one-time code -> runner token (rate limited; HTTPS unless local)
  GET  slots              runner token: the accounts to run, with their slot tokens (HTTPS unless local)
  POST heartbeat          runner token: hostname / OS / version / agents
  GET  install.ps1|sh     public, ?code= the pending code: the installer with the portal address and code filled in
  GET  bundle.tar.gz|zip  ?code= the pending code, or the runner token: the runner files
"""
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from . import linkedin_cloud as cloud, linkedin_runner as runner, linkedin_runner_install as installer
from .web_security import client_ip, is_https

PUBLIC_PATHS = ("/api/linkedin-runner/pair", "/api/linkedin-runner/install.ps1", "/api/linkedin-runner/install.sh",
                "/api/linkedin-runner/bundle.tar.gz", "/api/linkedin-runner/bundle.zip")


class WhereBody(BaseModel):
    where: str = Field(max_length=10)


class PairCodeBody(BaseModel):
    replace: bool = False


class RenameBody(BaseModel):
    label: str = Field(max_length=60)


class PairBody(BaseModel):
    code: str = Field(max_length=32)
    hostname: str = Field(default="", max_length=200)
    os: str = Field(default="", max_length=200)
    version: str = Field(default="", max_length=100)


class Agent(BaseModel):
    slot_id: str = Field(default="", max_length=40)
    pid: int | None = None
    state: str = Field(default="", max_length=20)


class HeartbeatBody(BaseModel):
    hostname: str = Field(default="", max_length=200)
    os: str = Field(default="", max_length=200)
    version: str = Field(default="", max_length=100)
    agents: list[Agent] = Field(default_factory=list, max_length=20)


def secure_enough(request: Request) -> bool:
    """Secrets (the runner token, slot tokens) only travel over HTTPS, or on this computer (local tests and setups)."""
    if is_https(request) or os.environ.get("OUTREACH_RUNNER_ALLOW_HTTP") == "1":
        return True
    from .main import _is_local
    return _is_local(request)


def make_router(store, cloud_router) -> APIRouter:
    router = APIRouter()
    base, jobs = cloud_router.base, cloud_router.jobs

    def actor(request: Request) -> dict:
        from .routes_auth import LOCAL_OWNER
        return getattr(request.state, "user", None) or LOCAL_OWNER

    def audit(request: Request, action: str, target: str, detail: str = "", who=None) -> None:
        from . import audit as audit_mod
        audit_mod.record(store, who or actor(request), action, target, detail)

    def view() -> dict:
        return cloud.describe(store, base, jobs)

    # ----- owner -----
    @router.put("/api/linkedin-cloud/where")
    def set_where(body: WhereBody, request: Request):
        before = runner.where(store)
        try:
            runner.set_where(store, body.where)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        cloud.write_slots_file(store, base, {})
        if before != body.where:
            audit(request, "linkedin_cloud.where", body.where, f"LinkedIn agents now run on the {'office computer' if body.where == 'office' else 'server'}")
        return view()

    @router.post("/api/linkedin-cloud/runner/pair")
    def make_code(body: PairCodeBody, request: Request):
        portal = installer.portal_base(store, request)
        try:
            installer.commands(portal, "A" * runner.CODE_LEN)               # refuse before making a code when the address is unusable
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        try:
            code, expires = runner.new_code(store, actor(request), body.replace)
        except runner.Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        current = runner.active(store)
        audit(request, "linkedin_runner.code", "office computer",
              "pairing code made (15 minutes, single use)" + ("; pairing replaces the connected office computer" if current else ""))
        return {**view(), "code": code, "code_display": runner.show_code(code), "expires_at": expires, "portal": portal, "commands": installer.commands(portal, code),
                "uninstall": installer.UNINSTALL, "insecure": not portal.startswith("https://")}

    @router.delete("/api/linkedin-cloud/runner/pair")
    def cancel_code(request: Request):
        runner.cancel_code(store)
        return view()

    @router.put("/api/linkedin-cloud/runner/{runner_id}")
    def rename(runner_id: str, body: RenameBody, request: Request):
        try:
            row = runner.rename(store, runner_id, body.label)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        audit(request, "linkedin_runner.rename", row["label"], f"office computer {runner_id}")
        return view()

    @router.delete("/api/linkedin-cloud/runner/{runner_id}")
    def revoke(runner_id: str, request: Request):
        try:
            row = runner.revoke(store, runner_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        cloud.rotate_tokens(store, base)
        audit(request, "linkedin_runner.revoke", row.get("label") or "office computer",
              f"office computer {runner_id} ({row.get('hostname') or 'unknown host'}) disconnected; its token and the account tokens were revoked")
        return view()

    # ----- the office computer -----
    @router.post("/api/linkedin-runner/pair")
    def pair(body: PairBody, request: Request):
        if not secure_enough(request):
            raise HTTPException(403, "Connect over HTTPS (use the https:// address of the portal).")
        try:
            token, row, replaced = runner.pair(store, body.code, body.model_dump(), client_ip(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if replaced:
            cloud.rotate_tokens(store, base)                 # agents still running on the old computer stop at once
        who = {"name": f"office computer {row['hostname'] or row['id']}", "email": "", "role": "runner"}
        audit(request, "linkedin_runner.pair", row["label"], f"office computer {row['id']} connected ({row['os'] or 'unknown OS'})", who)
        for old in replaced:
            audit(request, "linkedin_runner.replace", old.get("label") or "office computer",
                  f"office computer {old['id']} replaced by {row['id']}; its token was revoked", who)
        return {"token": token, "runner_id": row["id"]}

    def me(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if not user or user.get("kind") != "runner":
            raise HTTPException(403, "Only the connected office computer can use this.")
        return user

    @router.get("/api/linkedin-runner/slots")
    def runner_slots(request: Request):
        me(request)
        if not secure_enough(request):
            raise HTTPException(403, "The account tokens are only sent over HTTPS (use the https:// address of the portal).")
        return {"where": runner.where(store), "max": cloud.max_slots(store), "slots": cloud.runner_slots(store, base),
                "poll_seconds": 15, "heartbeat_seconds": 30}

    @router.post("/api/linkedin-runner/heartbeat")
    def heartbeat(body: HeartbeatBody, request: Request):
        user = me(request)
        row = runner.heartbeat(store, user["runner_id"], body.model_dump())
        return {"ok": bool(row), "where": runner.where(store)}

    def install(kind: str, request: Request):
        code = runner.norm_code(request.query_params.get("code"))
        if not runner.code_valid(store, code, client_ip(request)):
            text = installer.refusal(kind, "This install command has expired or was already used. In the portal press Connect an office computer again and paste the new line.")
        else:
            try:
                text = installer.script(kind, installer.portal_base(store, request), code)
            except ValueError as exc:
                text = installer.refusal(kind, str(exc))
        return PlainTextResponse(text, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})

    @router.get("/api/linkedin-runner/install.ps1")
    def install_ps1(request: Request):
        return install("ps1", request)

    @router.get("/api/linkedin-runner/install.sh")
    def install_sh(request: Request):
        return install("sh", request)

    def bundle(kind: str, request: Request):
        raw = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not (runner.authenticate(store, raw) if raw else None) and not runner.code_valid(store, request.query_params.get("code"), client_ip(request)):
            raise HTTPException(403, "This download link has expired. Make a new code in the portal.")
        media = "application/zip" if kind == "zip" else "application/gzip"
        return Response(installer.bundle(kind), media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="mcn-linkedin-runner.{"zip" if kind == "zip" else "tar.gz"}"'})

    @router.get("/api/linkedin-runner/bundle.tar.gz")
    def bundle_tar(request: Request):
        return bundle("tar", request)

    @router.get("/api/linkedin-runner/bundle.zip")
    def bundle_zip(request: Request):
        return bundle("zip", request)

    return router
