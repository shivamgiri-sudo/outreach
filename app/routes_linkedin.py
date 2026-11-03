"""LinkedIn sequence API: today's queue, start / sent / accepted / replied / stop, reply branches, settings."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import linkedin_seq as li


class SentBody(BaseModel):
    step: int = Field(ge=1, le=7)


class ReplyBody(BaseModel):
    text: str = ""


class BranchBody(BaseModel):
    key: str
    option_1: str = ""
    option_2: str = ""
    date: str = ""
    time: str = ""


class SettingsBody(BaseModel):
    case_study_url: Optional[str] = None
    linkedin_daily_connections: Optional[int] = Field(default=None, ge=1, le=40)
    linkedin_owner: Optional[str] = None


def make_router(store) -> APIRouter:
    router = APIRouter()

    def lead_or_404(lead_id: str) -> dict:
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        return lead

    def call(fn, *args):
        try:
            return fn(store, *args)
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.get("/api/linkedin/queue")
    def queue():
        return li.queue(store)

    @router.get("/api/linkedin/steps")
    def steps():
        settings = store.read("settings")
        return [{**s, "skipped": bool(s.get("needs") and not settings.get(s["needs"]))} for s in li.STEPS]

    @router.post("/api/leads/{lead_id}/linkedin/start")
    def start(lead_id: str):
        return call(li.start, lead_id)

    @router.post("/api/leads/{lead_id}/linkedin/sent")
    def sent(lead_id: str, body: SentBody):
        return call(li.mark_sent, lead_id, body.step)

    @router.post("/api/leads/{lead_id}/linkedin/accepted")
    def accepted(lead_id: str):
        return call(li.mark_accepted, lead_id)

    @router.post("/api/leads/{lead_id}/linkedin/replied")
    def replied(lead_id: str, body: ReplyBody):
        return call(li.mark_replied, lead_id, body.text)

    @router.post("/api/leads/{lead_id}/linkedin/stop")
    def stop(lead_id: str):
        lead_or_404(lead_id)
        return li.stop(store, lead_id)

    @router.post("/api/leads/{lead_id}/linkedin/branch")
    def branch(lead_id: str, body: BranchBody):
        lead = lead_or_404(lead_id)
        if body.key not in li.BRANCHES:
            raise HTTPException(400, f"key must be one of: {', '.join(li.BRANCHES)}")
        settings = store.read("settings")
        if body.key == "case_study_yes" and not settings.get("case_study_url"):
            raise HTTPException(400, "Add the real case-study link under LinkedIn settings first.")
        gaps = [k for k in li.BRANCH_NEEDS.get(body.key, ()) if not getattr(body, k).strip()]
        if gaps:
            raise HTTPException(400, f"Fill in {' and '.join(g.replace('_', ' ') for g in gaps)} first.")
        values = body.model_dump(exclude={"key"})
        return {"text": li.render_branch(body.key, lead, settings, **values)}

    @router.get("/api/linkedin/settings")
    def get_settings():
        s = store.read("settings")
        return {"case_study_url": s.get("case_study_url", ""), "linkedin_daily_connections": s.get("linkedin_daily_connections") or li.DEFAULT_DAILY_CONNECTIONS,
                "linkedin_owner": s.get("linkedin_owner") or ""}

    @router.put("/api/linkedin/settings")
    def put_settings(body: SettingsBody):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        url = changes.get("case_study_url")
        if url and not url.startswith("https://"):
            raise HTTPException(400, "The case-study link must start with https://")
        store.update("settings", lambda s: {**s, **changes})
        return get_settings()

    return router
