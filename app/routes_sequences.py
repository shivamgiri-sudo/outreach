"""Sequence library routes: list / create / edit / duplicate / archive sequences, step previews, A/B stats, snippets.

Everyone signed in can read the templates of their workspaces and main's. Sequences and snippets belong to a workspace
(app/ws_templates.py): owners change any; a workspace admin changes only its own workspace's (main's are read-only for
it: "Copy into my workspace"). In main, an unlimited caller still makes its own snippets and changes or deletes them.
"""
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import sequence_library as lib, ws_templates as wst
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
    workspace: Optional[str] = Field(default=None, max_length=60)     # "Copy into my workspace": where the copy goes


class DefaultBody(BaseModel):
    workspace: Optional[str] = Field(default=None, max_length=60)


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
    workspace: Optional[str] = Field(default=None, max_length=60)


def _user(request: Request) -> dict:
    from .routes_auth import LOCAL_OWNER
    user = getattr(request.state, "user", None) or LOCAL_OWNER
    return {"name": user.get("name") or user.get("email") or "Unknown", "email": user.get("email") or "", "role": user.get("role") or ""}


def render_preview(store, step: dict) -> dict:
    """The designed (HTML) email for one step, filled for a sample person, as the engine would send it."""
    from .email_html import render as render_html
    from .sequences import lead_variables, pick_variant, render as fill
    base = public_base_url(store, BASE_URL)
    from .brand import sender_name
    from .lead_fields import sample_custom
    variables = lead_variables({**SAMPLE_LEAD, "custom": sample_custom(store)}, sender_name(store), base,
                               {"offer_line": offer_line(get_offer(store)), "demo_link": f"{base}/demo/sample"}, brand=store)
    chosen = pick_variant(step, variables)
    subject, missing_s = fill(chosen["subject"], variables)
    body, missing_b = fill(chosen["body"], variables)
    return {"subject": subject, "text": body, "html": render_html(body, base, brand=store), "missing": sorted(set(missing_s + missing_b))}


def _seq_ws(seq: dict) -> str:
    return wst.MAIN if seq.get("builtin") else wst.tpl_ws(seq)


def _stored_ws(ws: str) -> str:
    return "" if ws == wst.MAIN else ws


def make_router(store) -> APIRouter:
    router = APIRouter()

    def found(seq_id: str) -> dict:
        seq = lib.get_sequence(store, seq_id)
        if seq is None or not (seq.get("builtin") or wst.readable(seq)):
            raise HTTPException(404, "Sequence not found")
        return seq

    def shown(seq: dict, leads=None, request: Optional[Request] = None, default_id: Optional[str] = None) -> dict:
        from . import workspaces
        leads = workspaces.filter_rows(store.read("leads") if leads is None else leads)
        mine = [l for l in leads if ((l.get("sequence") or {}).get("sequence_id") or lib.DEFAULT_ID) == seq["id"] and l.get("sequence")]
        ws = _seq_ws(seq)
        user = wst.user_of(request)
        return {**seq, "in_use": sum(1 for l in mine if not l["sequence"].get("stopped")), "enrolled": len(mine),
                "workspace_id": ws, "workspace_name": wst.name_of(store, ws),
                "can_edit": not seq.get("builtin") and wst.can_edit(user, ws),
                **({"is_default": seq["id"] == default_id} if default_id is not None else {})}

    def step_of(seq: dict, index: int) -> dict:
        if not 0 <= index < len(seq["steps"]):
            raise HTTPException(404, "No such step")
        return seq["steps"][index]

    @router.get("/api/sequences")
    def list_sequences(request: Request, archived: bool = False):
        leads = store.read("leads")
        user = wst.user_of(request)
        here = wst.target_ws(store, user)
        default_id = wst.default_sequence(store.read("settings"), here)
        return [shown(s, leads, request, default_id) for s in lib.all_sequences(store, include_archived=archived)
                if s.get("builtin") or wst.readable(s)]

    @router.post("/api/sequences")
    def create(body: SequenceBody, request: Request):
        user = wst.user_of(request)
        ws = wst.target_ws(store, user, body.workspace)
        wst.require_edit(user, ws)
        try:
            row = lib.create_sequence(store, {**body.model_dump(), "workspace": _stored_ws(ws)}, _user(request)["name"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        wst.record(store, request, "sequence.create", ws, row["name"], f"id {row['id']}; {len(row['steps'])} step(s)")
        return shown(row, request=request)

    @router.post("/api/sequences/preview-draft")
    def preview_draft(body: DraftPreviewBody):
        """Live preview of a step that is being edited (not saved yet)."""
        step = body.step or {}
        if not str(step.get("subject") or "").strip() and not str(step.get("body") or "").strip():
            raise HTTPException(400, "Write a subject or a body first.")
        clean = {"subject": str(step.get("subject") or "")[:lib.MAX_SUBJECT], "body": str(step.get("body") or "")[:lib.MAX_BODY]}
        return render_preview(store, clean)

    @router.get("/api/sequences/{seq_id}")
    def read_one(seq_id: str, request: Request):
        return shown(found(seq_id), request=request)

    @router.put("/api/sequences/{seq_id}")
    def edit(seq_id: str, body: SequenceBody, request: Request):
        user = wst.user_of(request)
        seq = found(seq_id)
        wst.require_edit(user, _seq_ws(seq))
        changes = body.model_dump()
        if body.workspace is not None:
            moved_to = wst.target_ws(store, user, body.workspace or wst.MAIN)
            wst.require_edit(user, moved_to)
            changes["workspace"] = _stored_ws(moved_to)
        try:
            saved = lib.update_sequence(store, seq_id, changes, _user(request)["name"])
        except LookupError:
            raise HTTPException(404, "Sequence not found") from None
        except (PermissionError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None
        pick = lambda r: {"name": r.get("name"), "notes": r.get("notes"), "workspace": r.get("workspace") or wst.MAIN,
                          "steps": [{k: st.get(k) for k in ("day", "subject")} for st in r.get("steps") or []],
                          "wording": [len(st.get("body") or "") for st in r.get("steps") or []]}
        wst.record_diff(store, request, "sequence.change", _seq_ws(saved), saved.get("name") or seq_id, pick(seq), pick(saved))
        return shown(saved, request=request)

    @router.post("/api/sequences/{seq_id}/duplicate")
    def duplicate(seq_id: str, request: Request, body: Optional[DuplicateBody] = None):
        user = wst.user_of(request)
        source = found(seq_id)
        wanted = body.workspace if body else None
        if wanted is None and wst.is_owner(user):
            into = None                                         # an owner's plain duplicate stays in the source's workspace
            target = _seq_ws(source)
        else:
            target = wst.target_ws(store, user, wanted)
            wst.require_edit(user, target)
            into = _stored_ws(target)
        try:
            row = lib.duplicate_sequence(store, seq_id, _user(request)["name"], (body.name if body else "").strip(), workspace=into)
        except LookupError:
            raise HTTPException(404, "Sequence not found") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        wst.record(store, request, "sequence.copy", target, row["name"], f"copy of {source['name']} ({wst.name_of(store, _seq_ws(source))})")
        return shown(row, request=request)

    @router.post("/api/sequences/{seq_id}/archive")
    def archive(seq_id: str, request: Request, body: Optional[ArchiveBody] = None):
        seq = found(seq_id)
        wst.require_edit(wst.user_of(request), _seq_ws(seq))
        try:
            saved = lib.set_archived(store, seq_id, body.archived if body else True, _user(request)["name"])
            wst.record(store, request, "sequence.archive", _seq_ws(saved), saved.get("name") or seq_id,
                       f"id {seq_id}; " + ("archived" if (body.archived if body else True) else "brought back"))
            return shown(saved, request=request)
        except LookupError:
            raise HTTPException(404, "Sequence not found") from None
        except PermissionError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/sequences/{seq_id}/make-default")
    def make_default(seq_id: str, request: Request, body: Optional[DefaultBody] = None):
        """The sequence a workspace's leads start when nobody picks one (lists, campaigns, bulk, the Enrol button)."""
        user = wst.user_of(request)
        seq = found(seq_id)
        ws = wst.target_ws(store, user, body.workspace if body else None)
        wst.require_edit(user, ws)
        if seq.get("archived") or _seq_ws(seq) not in (ws, wst.MAIN):
            raise HTTPException(400, "Pick an active sequence of this workspace or of Main.")
        before = wst.own(store.read("settings"), ws).get("default_sequence") or ""
        value = None if seq.get("builtin") else seq_id
        store.update("settings", lambda s: wst.put_own(s, ws, {"default_sequence": value}))
        wst.record(store, request, "sequence.default", ws, seq["name"], f"default sequence: {before or 'built-in'} -> {value or 'built-in'}")
        return {"workspace": ws, "default_sequence": value or lib.DEFAULT_ID}

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
    def snippet_rights(request: Request, ws: str) -> bool:
        """True: may change any snippet of `ws`; False: only its own (an unlimited caller in main); else 403."""
        from .workspace_guard import limited
        user = wst.user_of(request)
        if wst.can_edit(user, ws):
            return True
        if user.get("role") == "caller" and not limited(user) and ws == wst.MAIN:
            return False
        raise HTTPException(403, wst.refusal(user, ws))

    def snippet_shown(request: Request, row: dict) -> dict:
        from .workspace_guard import limited
        user, ws = wst.user_of(request), wst.tpl_ws(row)
        mine = bool(user.get("email")) and row.get("created_by_email") == user.get("email")
        legacy = user.get("role") == "caller" and not limited(user) and ws == wst.MAIN and mine
        return {**row, "workspace_id": ws, "workspace_name": wst.name_of(store, ws), "can_edit": wst.can_edit(user, ws) or legacy}

    def snippet_or_404(snippet_id: str) -> dict:
        row = lib.get_snippet(store, snippet_id)
        if row is None or not wst.readable(row):
            raise HTTPException(404, "Snippet not found")
        return row

    @router.get("/api/snippets")
    def snippets(request: Request, channel: str = ""):
        return [snippet_shown(request, r) for r in wst.filter_readable(lib.list_snippets(store, channel))]

    @router.post("/api/snippets")
    def add_snippet(body: SnippetBody, request: Request):
        ws = wst.target_ws(store, wst.user_of(request), body.workspace)
        snippet_rights(request, ws)
        try:
            row = lib.create_snippet(store, body.model_dump(exclude={"workspace"}), _user(request), workspace=ws)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        wst.record(store, request, "snippet.create", ws, row["name"], f"id {row['id']}; {row['channel']}")
        return snippet_shown(request, row)

    @router.put("/api/snippets/{snippet_id}")
    def change_snippet(snippet_id: str, body: SnippetBody, request: Request):
        before = snippet_or_404(snippet_id)
        ws = wst.tpl_ws(before)
        any_row = snippet_rights(request, ws)
        try:
            row = lib.update_snippet(store, snippet_id, body.model_dump(exclude={"workspace"}), _user(request), allowed=any_row)
        except LookupError:
            raise HTTPException(404, "Snippet not found") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        pick = lambda r: {k: r.get(k) for k in ("name", "channel", "subject", "body")}
        wst.record_diff(store, request, "snippet.change", ws, row["name"], pick(before), pick(row))
        return snippet_shown(request, row)

    @router.delete("/api/snippets/{snippet_id}")
    def remove_snippet(snippet_id: str, request: Request):
        row = snippet_or_404(snippet_id)
        ws = wst.tpl_ws(row)
        any_row = snippet_rights(request, ws)
        try:
            lib.delete_snippet(store, snippet_id, _user(request), allowed=any_row)
        except LookupError:
            raise HTTPException(404, "Snippet not found") from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        wst.record(store, request, "snippet.delete", ws, row.get("name") or snippet_id, f"id {snippet_id}")
        return {"ok": True}

    return router
