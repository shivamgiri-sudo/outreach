"""Lead harvest API: plan, create and run keyword x location jobs, read their results, export them, upload DND numbers.

Roles (app/routes_auth.py): reading is open to every login; creating and running jobs follows lead import (callers too),
but only an owner may run paid Google Places searches (the platform's own key and credits); the CSV export and the DND
upload are for owners and workspace admins. Every job belongs to one workspace and is only seen inside it.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import accounts, audit, exports, exports_xlsx, harvest, harvest_dnd, workspaces
from .harvest_pairs import (DEFAULT_MAX_TILES, MAX_CREDIT_CAP, MAX_CSV_CHARS, MAX_PAIRS, MAX_TILES_CAP, TEMPLATE_CSV, PairError,
                            estimate, pairs_from_csv, pairs_from_form)
from .sources.base import default_http

PLACES_HINT = "Connect Google Places under Integrations > Lead data > Google Places (your own Google key) to search Google."
EXPORT_COLUMNS = ["company", "category", "phone", "phone_line_type", "website", "rating", "reviews_count", "city", "pin", "dnd",
                  "outcome", "matched_by", "source", "source_url", "maps_url", "lead_id", "keyword", "location"]
RESULTS_PAGE = 500


class PlanBody(BaseModel):
    keywords: str = Field(default="", max_length=20_000)
    locations: str = Field(default="", max_length=40_000)
    csv: str = Field(default="", max_length=MAX_CSV_CHARS)
    sources: list = Field(default=["osm"], max_length=4)
    max_tiles: int = Field(default=DEFAULT_MAX_TILES, ge=1, le=MAX_TILES_CAP)


class JobBody(PlanBody):
    name: str = Field(default="", max_length=80)
    deep_crawl: bool = False
    credit_cap: Optional[int] = Field(default=None, ge=0, le=MAX_CREDIT_CAP)
    start: bool = False


class DndBody(BaseModel):
    text: str = Field(min_length=1, max_length=harvest_dnd.MAX_CHARS)


def _user(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _is_owner(request: Request) -> bool:
    return _user(request).get("role") == "owner"


def _manager(request: Request) -> None:
    if _user(request).get("role") not in ("owner", "admin"):
        raise HTTPException(403, "Only an owner or a workspace admin can do this.")


def places_status(store) -> dict:
    entry = store.read("integrations").get("google_places") or {}
    ok = entry.get("mode") == "api" and bool((entry.get("values") or {}).get("api_key"))
    return {"connected": ok, "reason": "" if ok else PLACES_HINT}


def _pairs(body: PlanBody) -> dict:
    try:
        if body.csv.strip():
            return pairs_from_csv(body.csv)
        return {"pairs": pairs_from_form(body.keywords, body.locations), "errors": []}
    except PairError as exc:
        raise HTTPException(400, str(exc)) from None


def make_router(store, transports: Optional[dict] = None) -> APIRouter:
    router = APIRouter()
    http = (transports or {}).get("source_http") or default_http

    def env_for(job: dict) -> harvest.Env:
        saved = store.read("integrations")
        key = ((saved.get("google_places") or {}).get("values") or {}).get("api_key", "")
        contact = ((saved.get("osm_overpass") or {}).get("values") or {}).get("contact", "")

        def meter():
            from .prospect_waterfall import Meter
            m = Meter(store, defaults={"google_places": 1.0})
            m.account = job["account"] if job.get("account") not in (None, "", accounts.MCN) else None
            return m
        return harvest.Env(http, key, contact, meter)

    harvest.register_env(store, env_for)

    def kick() -> None:
        harvest.kick(store, env_for)

    def visible_job(job_id: str) -> dict:
        job = harvest.get(store, job_id)
        if job is None or not workspaces.visible_ws({"workspace": job["ws"]}):
            raise HTTPException(404, "No such harvest job.")
        return job

    def check_sources(request: Request, sources) -> None:
        if "google_places" in (sources or []):
            if not _is_owner(request):
                raise HTTPException(403, "Only an owner can run Google Places searches (they use the paid Google key). Pick OpenStreetMap, or ask an owner.")
            if not places_status(store)["connected"]:
                raise HTTPException(400, PLACES_HINT)

    @router.get("/api/harvest")
    def overview(request: Request):
        return {"sources": {"google_places": {**places_status(store), "paid": True, "allowed": _is_owner(request),
                                              "cost_note": "1 credit per Google request (up to 20 results); billed by Google to your key."},
                            "osm": {"connected": True, "reason": "", "paid": False, "allowed": True,
                                    "cost_note": "Free. Coverage in Indian cities is patchy."}},
                "limits": {"max_pairs": MAX_PAIRS, "default_max_tiles": DEFAULT_MAX_TILES, "max_tiles": MAX_TILES_CAP,
                           "max_credit_cap": MAX_CREDIT_CAP},
                "dnd_numbers": harvest_dnd.count(store), "template_csv": TEMPLATE_CSV}

    @router.get("/api/harvest/template.csv")
    def template():
        return Response(exports.BOM + TEMPLATE_CSV, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="harvest-template.csv"'})

    @router.post("/api/harvest/estimate")
    def plan(body: PlanBody):
        found = _pairs(body)
        return {"pairs": len(found["pairs"]), "errors": found["errors"][:50], "estimate": estimate(len(found["pairs"]), body.sources, body.max_tiles),
                "sample": [{"keyword": p["keyword"], "location": p["location"]["text"], "kind": p["location"]["kind"]} for p in found["pairs"][:10]]}

    @router.post("/api/harvest/jobs")
    def create(body: JobBody, request: Request):
        check_sources(request, body.sources)
        found = _pairs(body)
        if not found["pairs"]:
            raise HTTPException(400, "No usable rows: " + "; ".join(f"row {e['row']}: {e['error']}" for e in found["errors"][:5]))
        ws = workspaces.for_new_lead()
        try:
            job = harvest.new_job(found["pairs"], body.sources, ws, accounts.account_of_ws(store, ws), audit.actor_of(_user(request))["name"],
                                  body.name, body.deep_crawl, body.max_tiles, body.credit_cap)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if body.start:
            job["status"] = "queued"
        harvest.add(store, job)
        audit.record(store, _user(request), "harvest.create", job["name"], f"{len(job['pairs'])} pairs; sources {', '.join(job['sources'])}; cap {job['credit_cap']}")
        if body.start:
            kick()
        return {**harvest.public(job), "row_errors": found["errors"][:50]}

    @router.get("/api/harvest/jobs")
    def jobs():
        rows = [j for j in store.read(harvest.JOBS) if workspaces.visible_ws({"workspace": j.get("ws")})]
        return [harvest.public(j) for j in reversed(rows)]

    @router.get("/api/harvest/jobs/{job_id}")
    def job_detail(job_id: str):
        return harvest.public(visible_job(job_id), full=True)

    def act(job_id: str, action: str, request: Request) -> dict:
        job = visible_job(job_id)
        if action in ("start", "resume"):
            check_sources(request, job["sources"])
        try:
            job = harvest.transition(store, job_id, action)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        audit.record(store, _user(request), f"harvest.{action}", job["name"], f"job {job_id}")
        if action in ("start", "resume"):
            kick()
        return harvest.public(job)

    @router.post("/api/harvest/jobs/{job_id}/start")
    def start(job_id: str, request: Request):
        return act(job_id, "start", request)

    @router.post("/api/harvest/jobs/{job_id}/pause")
    def pause(job_id: str, request: Request):
        return act(job_id, "pause", request)

    @router.post("/api/harvest/jobs/{job_id}/resume")
    def resume(job_id: str, request: Request):
        return act(job_id, "resume", request)

    @router.post("/api/harvest/jobs/{job_id}/cancel")
    def cancel(job_id: str, request: Request):
        return act(job_id, "cancel", request)

    def result_rows(job: dict, outcome: str = "") -> list:
        leads = {l["id"]: l for l in store.read("leads") if l.get("id")}
        out = []
        for r in store.read(harvest.RESULTS):
            if r.get("job") != job["id"] or (outcome and r.get("outcome") != outcome):
                continue
            pair = job["pairs"][r["pair"]] if 0 <= r.get("pair", -1) < len(job["pairs"]) else {}
            lead = leads.get(r.get("lead_id")) if r.get("lead_id") else None
            shown = lead is not None and workspaces.visible(lead)
            out.append({**r, "lead_id": r.get("lead_id") if shown else None, "dnd": bool(r.get("dnd") or (lead or {}).get("dnc")),
                        "keyword": pair.get("keyword", ""), "location": (pair.get("location") or {}).get("text", "")})
        return out

    @router.get("/api/harvest/jobs/{job_id}/results")
    def results(job_id: str, outcome: str = "", offset: int = 0):
        rows = result_rows(visible_job(job_id), outcome if outcome in ("new", "duplicate", "skipped", "closed") else "")
        offset = max(0, offset)
        return {"total": len(rows), "offset": offset, "rows": rows[offset:offset + RESULTS_PAGE],
                "attribution": "Google Places results: Google Maps. OpenStreetMap results: (c) OpenStreetMap contributors (ODbL)."}

    def export_rows(job_id: str, request: Request, kind: str) -> tuple:
        _manager(request)
        job = visible_job(job_id)
        rows = [{c: r.get(c) for c in EXPORT_COLUMNS} for r in result_rows(job)]
        audit.record(store, _user(request), "export.download", f"harvest.{kind}", f"{len(rows)} rows; job {job_id}")
        headers = {"Content-Disposition": f'attachment; filename="outreach-harvest-{job_id}-{exports.ist_date()}.{kind}"', "Cache-Control": "no-store"}
        return rows, headers

    @router.get("/api/harvest/jobs/{job_id}/export.csv")
    def export(job_id: str, request: Request):
        rows, headers = export_rows(job_id, request, "csv")
        return StreamingResponse(exports.csv_chunks(EXPORT_COLUMNS, rows), media_type="text/csv; charset=utf-8", headers=headers)

    @router.get("/api/harvest/jobs/{job_id}/export.xlsx")
    def export_xlsx(job_id: str, request: Request):
        rows, headers = export_rows(job_id, request, "xlsx")
        return StreamingResponse(exports_xlsx.chunks(EXPORT_COLUMNS, rows, "Harvest"), media_type=exports_xlsx.MEDIA_TYPE, headers=headers)

    @router.get("/api/harvest/dnd")
    def dnd_count():
        return {"numbers": harvest_dnd.count(store)}

    @router.post("/api/harvest/dnd")
    def dnd_upload(body: DndBody, request: Request):
        _manager(request)
        try:
            found = harvest_dnd.numbers_in(body.text)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if not found["numbers"]:
            raise HTTPException(400, f"No valid phone numbers found ({found['invalid']} could not be read).")
        saved = harvest_dnd.add(store, found["numbers"])
        audit.record(store, _user(request), "dnd.upload", "do-not-contact list", f"{saved['added']} numbers added, {saved['already']} already listed")
        return {**saved, "invalid": found["invalid"], "total": harvest_dnd.count(store)}

    if any(j.get("status") in harvest.LIVE for j in store.read(harvest.JOBS)):
        kick()                                        # jobs that were running before a restart carry on from their cursor
    return router
