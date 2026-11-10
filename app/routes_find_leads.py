"""Find & enrich leads: start a run, see its progress, and the estimate shown before a real run."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import find_leads as fl, upload_owner


class FindBody(BaseModel):
    mode: str = "both"
    cities: list[str] = []
    titles: list[str] = []
    industries: list[str] = []
    careers_pages: list[str] = []
    max_new: int = fl.DEFAULT_MAX_NEW
    max_enrich: int = fl.DEFAULT_MAX_ENRICH
    max_credits: int = fl.DEFAULT_MAX_CREDITS
    dry_run: bool = False


def make_router(store, transports: dict, notify_sender=None) -> APIRouter:
    router = APIRouter()
    runner = fl.Runner(store, transports, notify_sender)

    def params(body: FindBody) -> dict:
        try:
            return fl.normalise(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/find-leads/estimate")
    def estimate(body: FindBody):
        return fl.estimate(store, params(body))

    @router.get("/api/find-leads")
    def recent():
        """The run that is working now (if any) and the last few runs, newest first."""
        active = runner.active()
        runs = [fl.summary(runner.get(r["id"]) or r) for r in reversed(fl.read_runs(store))][:5]
        return {"active": fl.summary(active) if active else None, "runs": runs}

    @router.post("/api/find-leads")
    def start(body: FindBody, request: Request):
        p = params(body)
        user = getattr(request.state, "user", None)
        if upload_owner.is_member(user) and not p.get("dry_run"):
            upload_owner.check_caps(store, user, body.max_new)
            p["uploader"] = upload_owner.user_name(user)         # a member owns what the run finds and gets a list
        try:
            return runner.start(p)
        except fl.Busy as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get("/api/find-leads/{run_id}")
    def progress(run_id: str):
        run = runner.get(run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return run

    return router
