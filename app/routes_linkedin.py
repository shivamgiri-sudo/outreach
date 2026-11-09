"""LinkedIn sequence API: today's queue, start / sent / accepted / replied / stop, reply branches, settings."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, linkedin_bridge, linkedin_config, linkedin_seq as li, opener_lab


class SentBody(BaseModel):
    step: int = Field(ge=1, le=10)


class ReplyBody(BaseModel):
    text: str = ""


class BranchBody(BaseModel):
    key: str
    option_1: str = ""
    option_2: str = ""
    date: str = ""
    time: str = ""


class ResultsBody(BaseModel):
    csv: str = Field(max_length=linkedin_bridge.MAX_RESULT_CHARS)


class NoteBody(BaseModel):
    text: str = Field(default="", max_length=600)


class VariantsBody(BaseModel):
    A: Optional[str] = Field(default=None, max_length=600)
    B: Optional[str] = Field(default=None, max_length=600)


class GenerateBody(BaseModel):
    context: str = Field(default="", max_length=500)
    n: int = Field(default=4, ge=2, le=6)


class SequenceBody(BaseModel):
    steps: Optional[list] = None
    personas: Optional[list] = None
    default_hook: Optional[str] = Field(default=None, max_length=600)
    variant_b: Optional[str] = Field(default=None, max_length=600)


class SequencePreviewBody(BaseModel):
    text: str = Field(max_length=4000)
    kind: str = "message"
    lead_id: Optional[str] = None
    title: str = Field(default="", max_length=200)


SAMPLE_LEAD = {"id": "sample", "contact_name": "Venkataraghavan Iyer", "company": "Example Realty Pvt Ltd", "title": "Founder"}


class SettingsBody(BaseModel):
    case_study_url: Optional[str] = None
    linkedin_daily_connections: Optional[int] = Field(default=None, ge=1, le=40)
    linkedin_owner: Optional[str] = None
    linkedin_auto_start: Optional[bool] = None


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
        return [{**s, "skipped": bool(s.get("needs") and not settings.get(s["needs"]))} for s in li.current_steps(settings)]

    @router.get("/api/linkedin/sequence")
    def get_sequence():
        settings = store.read("settings")
        cfg = linkedin_config.current(settings)
        notes = linkedin_config.variants(settings)
        steps = [{**s, "text": notes["A"]} if s["n"] == 1 else s for s in cfg["steps"]]
        return {**{k: v for k, v in cfg.items() if k != "history"}, "steps": steps, "variant_b": notes["B"],
                "history_versions": [h["version"] for h in cfg.get("history") or []], "note_limit": li.NOTE_LIMIT,
                "placeholders": {"single": list(linkedin_config.SINGLE)},
                "in_flight": len(linkedin_config.leads_in_flight(store))}

    @router.put("/api/linkedin/sequence")
    def put_sequence(body: SequenceBody, request: Request):
        from . import audit_settings
        try:
            with audit_settings.watch(store, request, "LinkedIn sequence", action="settings.save", key=linkedin_config.SETTINGS_KEY):
                saved = linkedin_config.save(store, body.model_dump(), audit.actor_of(getattr(request.state, "user", None))["name"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {k: v for k, v in saved.items() if k != "history"}

    @router.post("/api/linkedin/sequence/reset")
    def reset_sequence(request: Request):
        cfg = linkedin_config.reset(store)
        audit.record(store, getattr(request.state, "user", None), "settings.save", "LinkedIn sequence", "reset to built-in")
        return {k: v for k, v in cfg.items() if k != "history"}

    @router.post("/api/linkedin/sequence/preview")
    def preview_sequence_step(body: SequencePreviewBody):
        """A step's text filled in for a sample lead (or a real one), with its length against LinkedIn's 300 limit."""
        settings = store.read("settings")
        lead = next((l for l in store.read("leads") if l["id"] == body.lead_id), None) if body.lead_id else None
        lead = lead or {**SAMPLE_LEAD, "title": body.title or SAMPLE_LEAD["title"]}
        from .lead_fields import sample_custom
        if not body.lead_id:
            lead = {**lead, "custom": sample_custom(store)}
        problem = ""
        try:
            linkedin_config.check_placeholders(body.text, "This text")
        except ValueError as exc:
            problem = str(exc)
        kind = "connect" if body.kind == "connect" else "message"
        step = {"n": 1 if kind == "connect" else 2, "kind": kind, "text": body.text}
        text = li.render(step, {**lead, "linkedin_seq": {}}, settings)
        worst = linkedin_config.stand_in_length(body.text, max([p[2] for p in linkedin_config.personas(settings)[0]]
                                                              + [linkedin_config.personas(settings)[1]], key=len))
        return {"text": text, "length": len(text), "longest_length": worst, "limit": li.NOTE_LIMIT if kind == "connect" else None,
                "persona": li.persona(lead.get("title", ""), settings)[0], "problem": problem}

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

    @router.post("/api/linkedin/results")
    def import_results(body: ResultsBody):
        """The results file of a third-party LinkedIn tool: mark accepted / replied leads, matched by profile link."""
        try:
            return linkedin_bridge.import_results(store, body.csv)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/api/linkedin/variants")
    def get_variants():
        return opener_lab.current(store)

    @router.put("/api/linkedin/variants")
    def put_variants(body: VariantsBody, request: Request):
        from . import audit_settings
        try:
            with audit_settings.watch(store, request, "LinkedIn connection notes", action="settings.change", key="linkedin_connect_variants"):
                return opener_lab.save(store, body.A, body.B, audit.actor_of(getattr(request.state, "user", None))["name"], li.iso(li.now_utc()))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/linkedin/variants/score")
    def score_variant(body: NoteBody):
        return opener_lab.score_note(body.text)

    @router.post("/api/linkedin/variants/generate")
    def generate_variants(body: GenerateBody):
        return opener_lab.candidates(store, body.context, body.n)

    @router.get("/api/linkedin/settings")
    def get_settings():
        s = store.read("settings")
        return {"case_study_url": s.get("case_study_url", ""), "linkedin_daily_connections": s.get("linkedin_daily_connections") or li.DEFAULT_DAILY_CONNECTIONS,
                "linkedin_owner": s.get("linkedin_owner") or "", "linkedin_auto_start": bool(s.get("linkedin_auto_start"))}

    @router.put("/api/linkedin/settings")
    def put_settings(body: SettingsBody, request: Request):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        url = changes.get("case_study_url")
        if url and not url.startswith("https://"):
            raise HTTPException(400, "The case-study link must start with https://")
        from . import audit_settings
        with audit_settings.watch(store, request, "LinkedIn settings", action="linkedin.settings"):
            store.update("settings", lambda s: {**s, **changes})
        return get_settings()

    return router
