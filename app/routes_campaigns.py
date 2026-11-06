"""Campaign routes: save targeting and rules, preview a run, run it for real, pause / resume, runs and leads."""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import campaigns
from .campaign_sources import SIZE_BUCKETS
from .hygiene import safe_http_url
from .jobs import JSONLD_RE
from .routes_outreach import resolve_now
from .sources import registry as source_registry

MAX_ITEMS = 20
MAX_ITEM_CHARS = 80
MAX_PAGES = 20
MAX_HTML_CHARS = 2_000_000
MAX_LEAD_IDS = 500


def _clean(values: list) -> list:
    out = list(dict.fromkeys(str(v).strip()[:MAX_ITEM_CHARS] for v in values if str(v).strip()))
    if len(out) > MAX_ITEMS:
        raise ValueError(f"At most {MAX_ITEMS} entries.")
    return out


class Target(BaseModel):
    industries: list[str] = []
    cities: list[str] = []
    roles_hiring: list[str] = []
    company_size_buckets: list[str] = []
    keywords: list[str] = []
    person_titles: list[str] = []

    @field_validator("industries", "cities", "roles_hiring", "keywords", "person_titles")
    @classmethod
    def tidy(cls, values):
        return _clean(values)

    @field_validator("company_size_buckets")
    @classmethod
    def sizes(cls, values):
        unknown = [v for v in values if v not in SIZE_BUCKETS]
        if unknown:
            raise ValueError(f"Unknown company size: {', '.join(unknown)}. Use {', '.join(SIZE_BUCKETS)}.")
        return list(dict.fromkeys(values))


class PastedPage(BaseModel):
    url: str = Field(default="", max_length=2000)
    html: str = Field(default="", max_length=MAX_HTML_CHARS)

    @field_validator("html")
    @classmethod
    def keep_job_data(cls, value):
        """Only the page's JSON-LD blocks are read, so only they are stored (keeps the data file small)."""
        return "\n".join(f'<script type="application/ld+json">{block}</script>' for block in JSONLD_RE.findall(value))


def _known_sources(values, goals) -> list:
    """Ids from the source registry that can do one of `goals`; anything else is refused with a readable message."""
    out = list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))
    for sid in out:
        spec = source_registry.get(sid)
        if spec is None or not (set(goals) & set(spec.caps)):
            raise ValueError(f"Unknown source: {sid[:40]}")
    return out


def _params(values) -> dict:
    """Per-source settings typed in the wizard: only company identifiers ('slugs'), as text, kept short."""
    out = {}
    for sid, item in (values or {}).items():
        if source_registry.get(str(sid)) is None or not isinstance(item, dict):
            raise ValueError(f"Unknown source: {str(sid)[:40]}")
        slugs = item.get("slugs", "")
        slugs = "\n".join(str(x) for x in slugs) if isinstance(slugs, list) else str(slugs)
        out[str(sid)] = {"slugs": slugs[:2000]}
    return out


class Sources(BaseModel):
    extra: list[str] = Field(default=[], max_length=40)
    extra_params: dict = {}
    careers_pages: list[str] = []
    careers_html: list[PastedPage] = Field(default=[], max_length=10)
    apollo_search: bool = False
    apollo_hiring: bool = False
    google_places: bool = False
    csv_import_ids: list[str] = Field(default=[], max_length=MAX_LEAD_IDS)
    max_results: int = Field(default=25, ge=1, le=100)

    @field_validator("extra")
    @classmethod
    def extra_sources(cls, values):
        return _known_sources(values, ("discover_companies", "discover_people", "hiring_signal"))

    @field_validator("extra_params")
    @classmethod
    def extra_settings(cls, values):
        return _params(values)

    @field_validator("careers_pages")
    @classmethod
    def pages(cls, values):
        cleaned = [str(v).strip() for v in values if str(v).strip()]
        bad = [v for v in cleaned if not safe_http_url(v)]
        if bad:
            raise ValueError(f"Careers pages must be http(s) links: {bad[0][:80]}")
        if len(cleaned) > MAX_PAGES:
            raise ValueError(f"At most {MAX_PAGES} careers pages per campaign.")
        return list(dict.fromkeys(cleaned))

    @field_validator("csv_import_ids")
    @classmethod
    def ids(cls, values):
        return list(dict.fromkeys(str(v).strip()[:40] for v in values if str(v).strip()))


class Enrich(BaseModel):
    crawler: bool = True
    hunter: bool = False
    firecrawl: bool = False
    apollo_match: bool = False
    extra: list[str] = Field(default=[], max_length=40)
    extra_params: dict = {}

    @field_validator("extra")
    @classmethod
    def extra_sources(cls, values):
        return _known_sources(values, ("find_email", "discover_people", "verify_email"))

    @field_validator("extra_params")
    @classmethod
    def extra_settings(cls, values):
        return _params(values)


class Rules(BaseModel):
    min_score: int = Field(default=40, ge=0, le=100)
    require_business_email: bool = True
    skip_role_addresses: bool = False
    max_new_leads_per_run: int = Field(default=50, ge=1, le=200)
    auto_enrol: bool = False
    credit_cap: int = Field(default=100, ge=0, le=5000)
    caller: str = Field(default="", max_length=60)
    only_companies: List[str] = Field(default_factory=list, max_length=500)    # empty = every qualifying lead
    sequence_id: str = Field(default="default", max_length=40)                 # the email sequence auto-enrol starts


class CampaignBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    target: Target = Target()
    sources: Sources = Sources()
    enrich: Enrich = Enrich()
    rules: Rules = Rules()
    status: Optional[str] = None

    @field_validator("name")
    @classmethod
    def named(cls, value):
        if value is None:
            return None
        if not value.strip():
            raise ValueError("Give the campaign a name.")
        return value.strip()


class CampaignChanges(CampaignBody):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)


class RunBody(BaseModel):
    dry_run: bool = False
    now: Optional[str] = None


def _spec(body: CampaignBody) -> dict:
    return body.model_dump(exclude={"status"})


def make_router(store, transports: dict, present, notify_sender=None) -> APIRouter:
    router = APIRouter()

    def check_sequence(body) -> None:
        """Auto-enrol may only point at a sequence that exists and is not archived."""
        rules = body.model_dump(exclude_unset=True).get("rules") or {}
        if "sequence_id" not in rules:
            return
        from .sequence_library import get_sequence
        chosen = get_sequence(store, rules["sequence_id"] or "default")
        if chosen is None or chosen.get("archived"):
            raise HTTPException(400, "That email sequence does not exist or is archived. Pick another one.")

    def found(campaign_id: str) -> dict:
        campaign = campaigns.get_campaign(store, campaign_id)
        if campaign is None:
            raise HTTPException(404, "Campaign not found")
        return campaign

    @router.get("/api/campaign-sources")
    def source_status():
        return campaigns.sources_status(store)

    @router.get("/api/campaigns")
    def list_all():
        from .campaign_metrics import outcomes
        rows = campaigns.list_campaigns(store)
        results = outcomes(store, [c["id"] for c in rows])
        return [{**c, "outcomes": results.get(c["id"])} for c in rows]

    @router.post("/api/campaigns")
    def create(body: CampaignBody):
        check_sequence(body)
        return campaigns.create_campaign(store, _spec(body))

    @router.get("/api/campaigns/{campaign_id}")
    def get_one(campaign_id: str):
        from .campaign_metrics import outcomes
        return {**found(campaign_id), "outcomes": outcomes(store, [campaign_id])[campaign_id]}

    @router.put("/api/campaigns/{campaign_id}")
    def save(campaign_id: str, body: CampaignChanges):
        """Change the name, status or any part; parts left out (and keys left out of a part) keep their saved values."""
        found(campaign_id)
        check_sequence(body)
        if body.status is not None and body.status not in campaigns.STATUSES:
            raise HTTPException(400, f"Status must be one of {', '.join(campaigns.STATUSES)}")
        return campaigns.update_campaign(store, campaign_id, body.model_dump(exclude_unset=True))

    @router.delete("/api/campaigns/{campaign_id}")
    def delete(campaign_id: str, request: Request):
        from . import audit
        campaign = found(campaign_id)
        campaigns.delete_campaign(store, campaign_id)
        audit.record(store, getattr(request.state, "user", None), "campaign.delete", campaign.get("name") or campaign_id,
                     f"id {campaign_id} (its leads are kept)")
        return {"deleted": True, "note": "The campaign's leads are kept."}

    @router.post("/api/campaigns/{campaign_id}/run")
    def run(campaign_id: str, body: RunBody):
        found(campaign_id)
        try:
            return campaigns.run_campaign(store, campaign_id, transports, dry_run=body.dry_run, now=resolve_now(body.now),
                                          notify_sender=notify_sender)
        except campaigns.CampaignBusy as exc:
            raise HTTPException(409, str(exc))

    @router.post("/api/campaigns/{campaign_id}/pause")
    def pause(campaign_id: str):
        found(campaign_id)
        return campaigns.set_status(store, campaign_id, "paused")

    @router.post("/api/campaigns/{campaign_id}/resume")
    def resume(campaign_id: str):
        found(campaign_id)
        return campaigns.set_status(store, campaign_id, "running")

    @router.get("/api/campaigns/{campaign_id}/runs")
    def runs(campaign_id: str):
        return list(reversed(found(campaign_id).get("runs") or []))

    @router.get("/api/campaigns/{campaign_id}/leads")
    def leads(campaign_id: str, page: Optional[int] = None, size: int = 25, stage: str = "", q: str = "", state: str = "", sort: str = "score"):
        """Without `page`: every lead (older callers). With `page`: one page, filtered by stage, text and outreach state."""
        found(campaign_id)
        if page is None:
            return sorted((present(l) for l in campaigns.campaign_leads(store, campaign_id)), key=lambda l: -l["score"])
        from .campaign_metrics import leads_page
        return leads_page(store, campaign_id, present, page, size, stage[:40], q, state[:20], sort[:20])

    return router
