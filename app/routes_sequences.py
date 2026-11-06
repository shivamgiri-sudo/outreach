"""Sequence library routes: list / create / edit / duplicate / archive sequences, step previews, A/B stats, snippets.

Everyone signed in can read. Creating and changing sequences is owner-only (routes_auth.OWNER_ROUTES); snippets can be
made by callers, changed or deleted by their maker, and by the owner (any snippet).
"""
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import sequence_library as lib
from .growth_core import get_offer, offer_line, public_base_url

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")
SAMPLE_LEAD = {"id": "sample", "contact_name": "Alex Example", "company": "Example Retail", "role": "Store Manager"}


class SequenceBody(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=1000)
    workspace: Optional[str] = Field(default=None, max_length=60)
    steps: Optional[list] = None


class DuplicateBody(BaseModel):
    name: str = Field(default="", max_length=200)


class ArchiveBody(BaseModel):
    archived: bool = True


class DraftPreviewBody(BaseModel):
    step: dict


class PreviewTo(BaseModel):
    to: str = Field(min_length=3, max_length=320)


class SnippetBody(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    channel: Optional[str] = Field(default=None, max_length=20)
    subject: Optional[str] = Field(default=None, max_length=300)
    body: Optional[str] = Field(default=None, max_length=8000)


def _user(request: Request) -> dict:
    from .routes_auth import LOCAL_OWNER
    user = getattr(request.state, "user", None) or LOCAL_OWNER
    return {"name": user.get("name") or user.get("email") or "Unknown", "email": user.get("email") or "", "role": user.get("role") or ""}


def render_preview(store, step: dict) -> dict:
    """The designed (HTML) email for one step, filled for a sample person, as the engine would send it."""
    from .email_html import render as render_html
    from .sequences import lead_variables, pick_variant, render as fill
    base = public_base_url(store, BASE_URL)
    variables = lead_variables(SAMPLE_LEAD, "BookMyInterview team", base,
                               {"offer_line": offer_line(get_offer(store)), "demo_link": f"{base}/demo/sample"})
    chosen = pick_variant(step, variables)
    subject, missing_s = fill(chosen["subject"], variables)
    body, missing_b = fill(chosen["body"], variables)
    return {"subject": subject, "text": body, "html": render_html(body, base), "missing": sorted(set(missing_s + missing_b))}


def make_router(store) -> APIRouter:
    router = APIRouter()

    def found(seq_id: str) -> dict:
        seq = lib.get_sequence(store, seq_id)
        if seq is None:
            raise HTTPException(404, "Sequence not found")
        return seq

    def shown(seq: dict, leads=None) -> dict:
        leads = store.read("leads") if leads is None else leads
        mine = [l for l in leads if ((l.get("sequence") or {}).get("sequence_id") or lib.DEFAULT_ID) == seq["id"] and l.get("sequence")]
        return {**seq, "in_use": sum(1 for l in mine if not l["sequence"].get("stopped")), "enrolled": len(mine)}

    def step_of(seq: dict, index: int) -> dict:
        if not 0 <= index < len(seq["steps"]):
            raise HTTPException(404, "No such step")
        return seq["steps"][index]

    @router.get("/api/sequences")
    def list_sequences(archived: bool = False):
        leads = store.read("leads")
        return [shown(s, leads) for s in lib.all_sequences(store, include_archived=archived)]

    @router.post("/api/sequences")
    def create(body: SequenceBody, request: Request):
        try:
            return shown(lib.create_sequence(store, body.model_dump(), _user(request)["name"]))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/sequences/preview-draft")
    def preview_draft(body: DraftPreviewBody):
        """Live preview of a step that is being edited (not saved yet)."""
        step = body.step or {}
        if not str(step.get("subject") or "").strip() and not str(step.get("body") or "").strip():
            raise HTTPException(400, "Write a subject or a body first.")
        clean = {"subject": str(step.get("subject") or "")[:lib.MAX_SUBJECT], "body": str(step.get("body") or "")[:lib.MAX_BODY]}
        return render_preview(store, clean)

    @router.get("/api/sequences/{seq_id}")
    def read_one(seq_id: str):
        return shown(found(seq_id))

    @router.put("/api/sequences/{seq_id}")
    def edit(seq_id: str, body: SequenceBody, request: Request):
        try:
            return shown(lib.update_sequence(store, seq_id, body.model_dump(), _user(request)["name"]))
        except LookupError:
            raise HTTPException(404, "Sequence not found") from None
        except PermissionError as exc:
            raise HTTPException(400, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/sequences/{seq_id}/duplicate")
    def duplicate(seq_id: str, request: Request, body: Optional[DuplicateBody] = None):
        try:
            return shown(lib.duplicate_sequence(store, seq_id, _user(request)["name"], (body.name if body else "").strip()))
        except LookupError:
            raise HTTPException(404, "Sequence not found") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/sequences/{seq_id}/archive")
    def archive(seq_id: str, request: Request, body: Optional[ArchiveBody] = None):
        try:
            return shown(lib.set_archived(store, seq_id, body.archived if body else True, _user(request)["name"]))
        except LookupError:
            raise HTTPException(404, "Sequence not found") from None
        except PermissionError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/api/sequences/{seq_id}/stats")
    def stats(seq_id: str):
        seq = found(seq_id)
        cells = lib.step_stats(store, seq_id) if seq_id != lib.DEFAULT_ID else {}
        steps = []
        for step in seq["steps"]:
            names = lib.variant_names(step)
            per = cells.get(step["day"], {})
            steps.append({"day": step["day"], "name": step["name"], "winner": step.get("winner"), "winner_at": step.get("winner_at"),
                          "variants": [{"name": n, **per.get(n, {"sent": 0, "practice": 0, "opens": 0, "replies": 0, "reply_rate": 0.0})}
                                       for n in names]})
        return {"steps": steps, "rule": {"min_sends_per_variant": lib.MIN_SENDS_PER_VARIANT, "min_replies": lib.MIN_WINNER_REPLIES,
                                         "min_lead_points": lib.MIN_LEAD_POINTS, "win_ratio": lib.WIN_RATIO}}

    @router.get("/api/sequences/{seq_id}/steps/{index}/preview")
    def preview(seq_id: str, index: int, variant: str = "A"):
        step = step_of(found(seq_id), index)
        if variant != "A" and variant not in lib.variant_names(step):
            raise HTTPException(404, "No such variant")
        return {**render_preview(store, lib.apply_variant(step, variant) if variant != "A" else step), "variant": variant}

    @router.post("/api/sequences/{seq_id}/steps/{index}/send-preview")
    def send_preview(seq_id: str, index: int, body: PreviewTo, variant: str = "A"):
        """Owner only: send the designed preview of one step to an address (marked [Preview])."""
        from .channels.config import config_for
        from .channels.email_send import deliver
        from .hygiene import normalise_email
        to = normalise_email(body.to)
        if not to:
            raise HTTPException(400, "Give a valid email address.")
        cfg = config_for(store, "email_send")
        if not cfg:
            raise HTTPException(400, "Connect the sending domain first (Integrations > Email sending).")
        shown_mail = preview(seq_id, index, variant)
        result = deliver(cfg, to, f"[Preview] {shown_mail['subject']}", shown_mail["text"], {}, html=shown_mail["html"])
        if result.get("status") != "sent":
            raise HTTPException(400, f"Not sent: {result.get('error') or result.get('status')}")
        return {"ok": True, "to": to}

    # ---- snippets ----
    @router.get("/api/snippets")
    def snippets(channel: str = ""):
        return lib.list_snippets(store, channel)

    @router.post("/api/snippets")
    def add_snippet(body: SnippetBody, request: Request):
        try:
            return lib.create_snippet(store, body.model_dump(), _user(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.put("/api/snippets/{snippet_id}")
    def change_snippet(snippet_id: str, body: SnippetBody, request: Request):
        try:
            return lib.update_snippet(store, snippet_id, body.model_dump(), _user(request))
        except LookupError:
            raise HTTPException(404, "Snippet not found") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.delete("/api/snippets/{snippet_id}")
    def remove_snippet(snippet_id: str, request: Request):
        try:
            lib.delete_snippet(store, snippet_id, _user(request))
        except LookupError:
            raise HTTPException(404, "Snippet not found") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        return {"ok": True}

    return router
