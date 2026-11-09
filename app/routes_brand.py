"""Brand & offer, the logo, stage / lead-term labels and the first-run onboarding state (owner writes; anyone signed
in reads). The sign-in page reads /api/brand/public and recipients' mail apps load /api/brand/logo, so those two
answer without a login."""
import base64
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import audit, audit_settings
from . import brand as b

ONBOARDING_STEPS = ("business", "offer", "industry", "email", "leads", "team", "done")


class BrandBody(BaseModel):
    app_name: Optional[str] = Field(default=None, max_length=200)
    company_name: Optional[str] = Field(default=None, max_length=200)
    product_name: Optional[str] = Field(default=None, max_length=200)
    tagline: Optional[str] = Field(default=None, max_length=400)
    primary_color: Optional[str] = Field(default=None, max_length=20)
    accent_color: Optional[str] = Field(default=None, max_length=20)
    website: Optional[str] = Field(default=None, max_length=300)
    support_email: Optional[str] = Field(default=None, max_length=200)
    sender_name: Optional[str] = Field(default=None, max_length=200)
    sign_off: Optional[str] = Field(default=None, max_length=400)
    postal_address: Optional[str] = Field(default=None, max_length=600)
    industry: Optional[str] = Field(default=None, max_length=200)


class OfferProfileBody(BaseModel):
    what: Optional[str] = Field(default=None, max_length=800)
    who: Optional[str] = Field(default=None, max_length=200)
    seller: Optional[str] = Field(default=None, max_length=200)
    status_note: Optional[str] = Field(default=None, max_length=200)
    value_1: Optional[str] = Field(default=None, max_length=300)
    value_2: Optional[str] = Field(default=None, max_length=300)
    value_3: Optional[str] = Field(default=None, max_length=300)
    cta: Optional[str] = Field(default=None, max_length=300)
    booking_link: Optional[str] = Field(default=None, max_length=400)
    goal: Optional[str] = Field(default=None, max_length=400)
    need_label: Optional[str] = Field(default=None, max_length=200)
    pain: Optional[str] = Field(default=None, max_length=200)
    topic: Optional[str] = Field(default=None, max_length=200)
    next_step: Optional[str] = Field(default=None, max_length=300)
    offer_line: Optional[str] = Field(default=None, max_length=600)
    demo_kind: Optional[str] = Field(default=None, max_length=20)


class PreviewBody(BaseModel):
    brand: BrandBody = BrandBody()
    offer: OfferProfileBody = OfferProfileBody()


SAMPLE_BODY = ("{{greeting}}\n\n{{my_product}} helps teams like {{company}}:\n"
               "- {{my_value_1}}\n- {{my_value_2}}\n- {{my_value_3}}\n\n{{my_cta}}\n\n{{sign_off}}\n\n"
               "Not relevant? Unsubscribe: {{unsub_link}}")


class LogoBody(BaseModel):
    content_type: str = Field(max_length=60)
    data: str = Field(max_length=1_500_000)          # base64 of at most 1 MB (checked again after decoding)
    filename: str = Field(default="", max_length=200)


class LabelsBody(BaseModel):
    stage_labels: Optional[Dict[str, str]] = None
    lead_terms: Optional[Dict[str, str]] = None


class OnboardingBody(BaseModel):
    step: Optional[str] = Field(default=None, max_length=20)
    done_steps: Optional[List[str]] = None
    finished: Optional[bool] = None
    skipped: Optional[bool] = None
    reopen: Optional[bool] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def onboarding_state(store) -> dict:
    """Whether to show the first-run wizard, and where the owner left it.

    An install that already holds leads (one set up before the wizard existed) counts as onboarded, so the live
    deployment does not suddenly greet its owner with a wizard; it can still be opened from Setup."""
    settings = store.read("settings")
    saved = settings.get("onboarding") or {}
    onboarded = settings.get("onboarded")
    established = onboarded is None and bool(store.read("leads"))
    return {"onboarded": bool(onboarded) or established, "show": not (bool(onboarded) or established),
            "step": saved.get("step") if saved.get("step") in ONBOARDING_STEPS else ONBOARDING_STEPS[0],
            "done_steps": [s for s in saved.get("done_steps", []) if s in ONBOARDING_STEPS],
            "skipped": bool(saved.get("skipped")), "steps": list(ONBOARDING_STEPS), "updated_at": saved.get("updated_at")}


def make_router(store) -> APIRouter:
    router = APIRouter()

    def logo() -> dict:
        return (store.read("brand_assets") or {}).get("logo") or {}

    def full() -> dict:
        settings = store.read("settings")
        b.remember(store)
        return {"brand": b.get_brand(settings), "offer": b.get_offer_profile(settings), "public": b.public_brand(settings, logo()),
                "stage_labels": b.stage_labels(settings), "default_stage_labels": b.DEFAULT_STAGE_LABELS,
                "lead_terms": b.lead_terms(settings), "default_lead_terms": b.DEFAULT_LEAD_TERMS,
                "placeholders": list(b.BRAND_PLACEHOLDERS), "variables": b.brand_variables(settings),
                "is_default": b.brand_is_default(settings), "saved_brand": settings.get("brand") or {},
                "saved_offer": settings.get("offer") or {}, "logo": {k: v for k, v in logo().items() if k != "data"},
                "contrast": b.contrast_ratio(b.get_brand(settings)["primary_color"], "#ffffff"),
                "min_contrast": b.MIN_CONTRAST, "onboarding": onboarding_state(store)}

    def who(request: Request):
        return getattr(request.state, "user", None)

    @router.get("/api/brand/public")
    def public():
        return b.public_brand(store.read("settings"), logo())

    @router.get("/api/brand")
    def read_brand():
        return full()

    @router.put("/api/brand")
    def write_brand(body: BrandBody, request: Request):
        try:
            changes = b.validate_brand(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        def apply(settings: dict) -> dict:
            out = {**settings, "brand": {**(settings.get("brand") or {}), **changes}}
            # One truth for who signs and what the email footer says: the older Outbox fields follow the brand.
            synced = {"sender_name": "sender_name", "company_name": "sender_company", "postal_address": "sender_address",
                      "website": "sender_website", "support_email": "sender_contact_email"}
            for field, key in synced.items():
                if field in changes:
                    out[key] = changes[field]
            return out
        with audit_settings.watch(store, request, "Brand", action="brand.save"):
            store.update("settings", apply)
        return full()

    @router.put("/api/brand/offer")
    def write_offer_profile(body: OfferProfileBody, request: Request):
        try:
            changes = b.validate_offer(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        with audit_settings.watch(store, request, "What you sell", action="offer.save"):
            store.update("settings", lambda s: {**s, "offer": {**(s.get("offer") or {}), **changes}})
        return full()

    @router.post("/api/brand/reset")
    def reset_brand(request: Request):
        """Back to the original brand and offer (the uploaded logo is removed too)."""
        with audit_settings.watch(store, request, "Brand (back to the default brand)", action="brand.reset"):
            store.update("settings", lambda s: {k: v for k, v in s.items() if k not in ("brand", "offer")})
        store.update("brand_assets", lambda a: {k: v for k, v in (a or {}).items() if k != "logo"})
        return full()

    @router.post("/api/brand/logo")
    def upload_logo(body: LogoBody, request: Request):
        try:
            content_type, raw = b.check_logo(body.content_type, body.data)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        record = b.logo_record(content_type, raw, _now())
        store.update("brand_assets", lambda a: {**(a or {}), "logo": record})
        store.update("settings", lambda s: {**s, "brand": {**(s.get("brand") or {}), "logo_url": f"/api/brand/logo?v={record['sha']}"}})
        audit.record(store, who(request), "brand.logo", "Brand", f"Logo uploaded ({content_type}, {len(raw) // 1024} KB)")
        return full()

    @router.delete("/api/brand/logo")
    def delete_logo(request: Request):
        store.update("brand_assets", lambda a: {k: v for k, v in (a or {}).items() if k != "logo"})
        store.update("settings", lambda s: {**s, "brand": {k: v for k, v in (s.get("brand") or {}).items() if k != "logo_url"}})
        audit.record(store, who(request), "brand.logo", "Brand", "Logo removed")
        return full()

    @router.get("/api/brand/logo")
    def serve_logo():
        record = logo()
        if not record.get("data"):
            raise HTTPException(404, "No logo uploaded")
        headers = {"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff",
                   # An SVG opened on its own can never run anything, even if a check were ever missed.
                   "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox"}
        return Response(base64.b64decode(record["data"]), media_type=record["content_type"], headers=headers)

    @router.post("/api/brand/preview")
    def preview(body: PreviewBody):
        """The designed email as it would look with these (unsaved) brand and offer values."""
        from .email_html import render as render_html
        from .growth_core import public_base_url
        from .sequences import lead_variables, render as fill
        try:
            brand_changes = b.validate_brand(body.brand.model_dump())
            offer_changes = b.validate_offer(body.offer.model_dump())
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        settings = store.read("settings")
        draft = {**settings, "brand": {**(settings.get("brand") or {}), **brand_changes},
                 "offer": {**(settings.get("offer") or {}), **offer_changes}}
        if "sender_name" in brand_changes:
            draft["sender_name"] = brand_changes["sender_name"]
        base = public_base_url(store, "")
        sample = {"id": "sample", "contact_name": "Alex Example", "company": "Example Co", "role": ""}
        text, missing = fill(SAMPLE_BODY, lead_variables(sample, b.sender_name(draft), base or "https://example.invalid", brand=draft))
        logo_rec = logo()
        # the preview is shown inside the app, so the logo comes from this server whatever the public address is
        svg = bool(b.get_brand(draft)["logo_url"]) and logo_rec.get("content_type") == "image/svg+xml"
        html = render_html(text, "" if svg else "/", "", brand=draft)     # SVG logos show as the name (mail apps block SVG)
        return {"ok": True, "html": html, "text": text, "missing": missing, "public": b.public_brand(draft, logo_rec),
                "contrast": b.contrast_ratio(b.get_brand(draft)["primary_color"], "#ffffff")}

    @router.get("/api/labels")
    def read_labels():
        settings = store.read("settings")
        return {"stage_labels": b.stage_labels(settings), "default_stage_labels": b.DEFAULT_STAGE_LABELS,
                "lead_terms": b.lead_terms(settings), "default_lead_terms": b.DEFAULT_LEAD_TERMS}

    @router.put("/api/labels")
    def write_labels(body: LabelsBody, request: Request):
        try:
            stages, terms = b.validate_labels(body.stage_labels, body.lead_terms)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        def apply(settings: dict) -> dict:
            out = dict(settings)
            if body.stage_labels is not None:
                out["stage_labels"] = stages
            if body.lead_terms is not None:
                out["lead_terms"] = terms
            return out
        with audit_settings.watch(store, request, "Labels", action="labels.save"):
            store.update("settings", apply)
        return read_labels()

    @router.get("/api/onboarding")
    def read_onboarding():
        return onboarding_state(store)

    @router.put("/api/onboarding")
    def write_onboarding(body: OnboardingBody):
        if body.step is not None and body.step not in ONBOARDING_STEPS:
            raise HTTPException(400, "Unknown onboarding step.")
        if body.done_steps is not None and any(s not in ONBOARDING_STEPS for s in body.done_steps):
            raise HTTPException(400, "Unknown onboarding step.")

        def apply(settings: dict) -> dict:
            saved = dict(settings.get("onboarding") or {})
            if body.step is not None:
                saved["step"] = body.step
            if body.done_steps is not None:
                saved["done_steps"] = list(dict.fromkeys(body.done_steps))
            if body.skipped is not None:
                saved["skipped"] = body.skipped
            saved["updated_at"] = _now()
            out = {**settings, "onboarding": saved}
            if body.finished or body.skipped:
                out["onboarded"] = True
            if body.reopen:
                out["onboarded"] = False
                saved["skipped"] = False
            return out
        store.update("settings", apply)
        return onboarding_state(store)

    return router
