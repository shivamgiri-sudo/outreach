"""LinkedIn sequence API: today's queue, start / sent / accepted / replied / stop, reply branches, settings."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, lead_access, linkedin_bridge, linkedin_config, linkedin_seq as li, opener_lab, provenance, ws_templates as wst


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


class FindBody(BaseModel):
    action: str = Field(pattern="^(pick|link|skip)$")
    url: str = Field(default="", max_length=500)


class NoteBody(BaseModel):
    text: str = Field(default="", max_length=600)


class VariantsBody(BaseModel):
    A: Optional[str] = Field(default=None, max_length=600)
    B: Optional[str] = Field(default=None, max_length=600)
    workspace: Optional[str] = Field(default=None, max_length=60)


class GenerateBody(BaseModel):
    context: str = Field(default="", max_length=500)
    n: int = Field(default=4, ge=2, le=6)


class SequenceBody(BaseModel):
    steps: Optional[list] = None
    personas: Optional[list] = None
    default_hook: Optional[str] = Field(default=None, max_length=600)
    variant_b: Optional[str] = Field(default=None, max_length=600)
    workspace: Optional[str] = Field(default=None, max_length=60)


class WorkspaceBody(BaseModel):
    workspace: Optional[str] = Field(default=None, max_length=60)


class SequencePreviewBody(BaseModel):
    text: str = Field(max_length=4000)
    kind: str = "message"
    lead_id: Optional[str] = None
    title: str = Field(default="", max_length=200)
    workspace: Optional[str] = Field(default=None, max_length=60)


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
    def queue(request: Request):
        return lead_access.filter_queue(store, getattr(request.state, "user", None), li.queue(store))

    def ws_view(request: Request, wanted: Optional[str] = None) -> tuple:
        """(workspace, settings as that workspace sees them) for the LinkedIn templates shown / saved."""
        ws = wst.target_ws(store, wst.user_of(request), wanted)
        return ws, wst.settings_for(store.read("settings"), ws)

    def ws_fields(request: Request, ws: str) -> dict:
        settings = store.read("settings")
        return {"workspace": ws, "workspace_name": wst.name_of(store, ws), "can_edit": wst.can_edit(wst.user_of(request), ws),
                "own": ws == wst.MAIN or bool(wst.own(settings, ws).get(linkedin_config.SETTINGS_KEY)),
                "own_notes": ws == wst.MAIN or bool(wst.own(settings, ws).get("linkedin_connect_variants"))}

    def own_snapshot(ws: str) -> dict:
        settings = store.read("settings")
        if ws == wst.MAIN:
            return {linkedin_config.SETTINGS_KEY: settings.get(linkedin_config.SETTINGS_KEY), "notes": settings.get("linkedin_connect_variants")}
        mine = wst.own(settings, ws)
        return {linkedin_config.SETTINGS_KEY: mine.get(linkedin_config.SETTINGS_KEY), "notes": mine.get("linkedin_connect_variants")}

    @router.get("/api/linkedin/steps")
    def steps(request: Request, workspace: str = ""):
        _ws, settings = ws_view(request, workspace)
        return [{**s, "skipped": bool(s.get("needs") and not settings.get(s["needs"]))} for s in li.current_steps(settings)]

    @router.get("/api/linkedin/sequence")
    def get_sequence(request: Request, workspace: str = ""):
        ws, settings = ws_view(request, workspace)
        cfg = linkedin_config.current(settings)
        notes = linkedin_config.variants(settings)
        steps = [{**s, "text": notes["A"]} if s["n"] == 1 else s for s in cfg["steps"]]
        return {**{k: v for k, v in cfg.items() if k != "history"}, "steps": steps, "variant_b": notes["B"],
                "history_versions": [h["version"] for h in cfg.get("history") or []], "note_limit": li.NOTE_LIMIT,
                "placeholders": {"single": list(linkedin_config.SINGLE)},
                "in_flight": len(linkedin_config.leads_in_flight(store, ws)), **ws_fields(request, ws)}

    @router.put("/api/linkedin/sequence")
    def put_sequence(body: SequenceBody, request: Request):
        from . import audit_settings
        ws = wst.target_ws(store, wst.user_of(request), body.workspace)
        wst.require_edit(wst.user_of(request), ws)
        who = audit.actor_of(getattr(request.state, "user", None))["name"]
        try:
            if ws == wst.MAIN:
                with audit_settings.watch(store, request, "LinkedIn sequence", action="settings.save", key=linkedin_config.SETTINGS_KEY):
                    saved = linkedin_config.save(store, body.model_dump(exclude={"workspace"}), who)
            else:
                before = own_snapshot(ws)
                saved = linkedin_config.save(store, body.model_dump(exclude={"workspace"}), who, ws)
                wst.record_diff(store, request, "template.linkedin_sequence", ws, "LinkedIn sequence", before, own_snapshot(ws))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {**{k: v for k, v in saved.items() if k != "history"}, **ws_fields(request, ws)}

    @router.post("/api/linkedin/sequence/reset")
    def reset_sequence(request: Request, body: Optional[WorkspaceBody] = None):
        ws = wst.target_ws(store, wst.user_of(request), body.workspace if body else None)
        wst.require_edit(wst.user_of(request), ws)
        cfg = linkedin_config.reset(store, ws)
        if ws == wst.MAIN:
            audit.record(store, getattr(request.state, "user", None), "settings.save", "LinkedIn sequence", "reset to built-in")
        else:
            wst.record(store, request, "template.linkedin_sequence", ws, "LinkedIn sequence", "back to Main's sequence")
        return {**{k: v for k, v in cfg.items() if k != "history"}, **ws_fields(request, ws)}

    @router.post("/api/linkedin/sequence/copy")
    def copy_sequence(request: Request, body: Optional[WorkspaceBody] = None):
        """Copy into my workspace: the workspace gets its own, editable copy of main's steps, personas and notes."""
        ws = wst.target_ws(store, wst.user_of(request), body.workspace if body else None)
        if ws == wst.MAIN:
            raise HTTPException(400, "Pick the workspace to copy Main's LinkedIn sequence into.")
        wst.require_edit(wst.user_of(request), ws)
        cfg = linkedin_config.copy_from_main(store, ws, audit.actor_of(getattr(request.state, "user", None))["name"])
        wst.record(store, request, "template.linkedin_sequence", ws, "LinkedIn sequence", "copied from Main")
        return {**{k: v for k, v in cfg.items() if k != "history"}, **ws_fields(request, ws)}

    @router.post("/api/linkedin/sequence/preview")
    def preview_sequence_step(body: SequencePreviewBody, request: Request):
        """A step's text filled in for a sample lead (or a real one), with its length against LinkedIn's 300 limit."""
        from . import workspaces
        ws, settings = ws_view(request, body.workspace)
        lead = next((l for l in store.read("leads") if l["id"] == body.lead_id), None) if body.lead_id else None
        if lead is not None and not workspaces.visible(lead):
            lead = None
        lead = lead or {**SAMPLE_LEAD, "title": body.title or SAMPLE_LEAD["title"], **({"workspace": ws} if ws != wst.MAIN else {})}
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
    def start(lead_id: str, request: Request):
        lead_access.check_http(store, request, lead_or_404(lead_id))
        out = call(lambda st, lid: li.start(st, lid, find=True), lead_id)
        who = provenance.person_of(getattr(request.state, "user", None))
        out["lead"] = provenance.stamp_linkedin_start(store, lead_id, who["name"], who["email"], "by hand") or out["lead"]
        return out

    @router.get("/api/leads/{lead_id}/linkedin/contacts")
    def contacts(lead_id: str):
        """Who in this lead's workspace is connected to / has contacted this LinkedIn person (never another workspace)."""
        from . import linkedin_dupes, workspaces
        lead = lead_or_404(lead_id)
        if not workspaces.visible(lead):
            raise HTTPException(404, "Lead not found")
        return {"contacts": linkedin_dupes.contacts(store, lead)}

    @router.post("/api/leads/{lead_id}/linkedin/find")
    def find(lead_id: str, body: FindBody, request: Request):
        """A person settles a profile lookup: pick one of the people found, paste the link, or skip."""
        from . import linkedin_finder
        lead_access.check_http(store, request, lead_or_404(lead_id))
        who = provenance.person_of(getattr(request.state, "user", None))
        out = call(lambda st, lid: linkedin_finder.person_choice(st, lid, body.action, body.url, who), lead_id)
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if body.action != "skip" and lead:
            lead = provenance.stamp_linkedin_start(store, lead_id, who["name"], who["email"], "by hand") or lead
        return {**out, "lead": lead}

    @router.post("/api/leads/{lead_id}/linkedin/sent")
    def sent(lead_id: str, body: SentBody, request: Request):
        lead = lead_or_404(lead_id)
        lead_access.check_http(store, request, lead)
        origin = provenance.build(store, "linkedin_by_hand", True, lead, provenance.person_of(getattr(request.state, "user", None)),
                                  {"step": f"LinkedIn step {body.step}"}, provenance.linkedin_enrolment(lead), "linkedin",
                                  hours=provenance.HOURS_PERSON)
        try:
            from .routes_extension import _with_ai_note
            return li.mark_sent(store, lead_id, body.step, origin=_with_ai_note(store, lead, body.step, origin))
        except LookupError as exc:
            raise HTTPException(404, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

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
    def get_variants(request: Request, workspace: str = ""):
        ws = wst.target_ws(store, wst.user_of(request), workspace)
        return {**opener_lab.current(store, ws), **ws_fields(request, ws)}

    @router.put("/api/linkedin/variants")
    def put_variants(body: VariantsBody, request: Request):
        from . import audit_settings
        from .workspace_guard import limited
        user = wst.user_of(request)
        ws = wst.target_ws(store, user, body.workspace)
        if not (wst.can_edit(user, ws) or (ws == wst.MAIN and user.get("role") == "caller" and not limited(user))):
            raise HTTPException(403, wst.refusal(user, ws))      # main's notes: owners, and unlimited callers as before
        who, stamp = audit.actor_of(getattr(request.state, "user", None))["name"], li.iso(li.now_utc())
        try:
            if ws == wst.MAIN:
                with audit_settings.watch(store, request, "LinkedIn connection notes", action="settings.change", key="linkedin_connect_variants"):
                    return {**opener_lab.save(store, body.A, body.B, who, stamp), **ws_fields(request, ws)}
            before = own_snapshot(ws)
            out = opener_lab.save(store, body.A, body.B, who, stamp, ws)
            wst.record_diff(store, request, "template.linkedin_notes", ws, "LinkedIn connection notes", before, own_snapshot(ws))
            return {**out, **ws_fields(request, ws)}
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
