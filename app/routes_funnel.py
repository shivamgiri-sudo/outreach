"""Sales funnel API (Lead Funnel dashboard + a lead's sales stage).

  GET  /api/funnel                  all 18 components for a date range and filters (cohort basis); ?since=<stamp> answers
                                    {"unchanged": true} when nothing changed (the page polls every 60 s)
  GET  /api/funnel/drill            the leads behind one number: ?key=<drill key from /api/funnel>, same range and filters
  GET  /api/funnel/settings         SLA hours, stalled days, monthly targets (+ the lists the forms use)
  PUT  /api/funnel/settings         the same, owners and workspace admins only
  POST /api/funnel/recalculate      back-fill blank stage dates from each lead's current sales status (migration)
  GET  /api/leads/{id}/sales        a lead's sales stage as the drawer shows it
  PUT  /api/leads/{id}/sales        change status and values; the rules are enforced here and the reason is the message

Only leads this request may see are counted (workspace scope, account, test leads left out). Results are cached for a few
seconds and rebuilt only when leads, activities or settings were written (Store.stamp).
"""
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from . import accounts, sales_calc, sales_model as M, sales_range, sales_service, sales_view, workspaces
from .source_stats import real

CACHE_SECONDS = 20
CACHE_ROWS = 24
DRILL_CAP = 10000


class SalesBody(BaseModel):
    status: Optional[str] = None
    est_value: Optional[float] = None
    closed_value: Optional[float] = None
    tenure: Optional[float] = None
    lost_reason: Optional[str] = None
    fit: Optional[str] = None
    engagement: Optional[str] = None
    industry: Optional[str] = None
    service: Optional[str] = None
    source: Optional[str] = None


class SettingsBody(BaseModel):
    sla_hours: Optional[int] = None
    stalled_days: Optional[int] = None
    targets: Optional[list] = None


def split_ids(node, path: str, registry: dict):
    """Replace every `ids` list with a drill key (its path in the result); the lists stay on the server in `registry`."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "ids" and isinstance(v, list):
                registry[path] = v
                out["drill"] = path
            else:
                out[k] = split_ids(v, f"{path}.{k}" if path else str(k), registry)
        return out
    if isinstance(node, list):
        return [split_ids(v, f"{path}.{i}" if path else str(i), registry) for i, v in enumerate(node)]
    return node


def make_router(store) -> APIRouter:
    router = APIRouter()
    lock = threading.Lock()
    leads_cache = {}                       # scope -> (stamp, [FLead], {id: FLead})
    result_cache = {}                      # (scope, params) -> (stamp, time, public result, registry)

    def scope_key() -> tuple:
        selected, allowed = workspaces._SCOPE.get()
        return (selected, tuple(sorted(allowed)) if allowed is not None else None, accounts.current(), sales_service.account_key())

    def current_stamp() -> tuple:
        return (*store.stamp("leads", "activities", "settings"), M.ist_day(datetime.now(timezone.utc)))

    def funnel_leads(scope: tuple, stamp: tuple) -> tuple:
        with lock:
            hit = leads_cache.get(scope)
            if hit and hit[0] == stamp:
                return hit[1], hit[2]
        rows = real(workspaces.filter_rows(store.peek("leads")))          # read-only view, no copy of 20,000 leads
        auto = sales_view.auto_index(store.peek("activities"))
        built = [sales_view.funnel_lead(r, auto) for r in rows]
        by_id = {f.id: f for f in built}
        with lock:
            leads_cache[scope] = (stamp, built, by_id)
            while len(leads_cache) > CACHE_ROWS:
                leads_cache.pop(next(iter(leads_cache)))
        return built, by_id

    def compute(date_from: str, date_to: str, filters: dict) -> tuple:
        scope, stamp = scope_key(), current_stamp()
        params = (date_from, date_to, tuple((k, tuple(sorted(v))) for k, v in sorted(filters.items())))
        with lock:
            hit = result_cache.get((scope, params))
            if hit and hit[0] == stamp and time.time() - hit[1] < CACHE_SECONDS:
                return hit[2], hit[3], hit[0], scope
        built, _by_id = funnel_leads(scope, stamp)
        settings = sales_service.read_settings(store)
        now = datetime.now(timezone.utc)
        raw = sales_calc.compute(built, now, date_from, date_to, filters, settings["sla_hours"], settings["stalled_days"], settings["targets"])
        if raw.get("error"):
            return raw, {}, stamp, scope
        registry = {}
        public = split_ids(raw, "", registry)
        public["presets"] = {name: sales_range.preset(name, now) for name in sales_range.PRESETS}
        public["today"] = M.ist_day(now)
        public["refreshedAt"] = M.iso(now)
        public["lists"] = lists()
        with lock:
            result_cache[(scope, params)] = (stamp, time.time(), public, registry)
            while len(result_cache) > CACHE_ROWS:
                result_cache.pop(next(iter(result_cache)))
        return public, registry, stamp, scope

    def lists() -> dict:
        return {"statuses": list(M.STATUSES), "lostReasons": list(M.LOST_REASONS), "fits": list(M.FITS), "engagements": list(M.ENGAGEMENTS)}

    def parse(date_from: str, date_to: str, source: List[str], industry: List[str], service: List[str], owner: List[str]) -> tuple:
        clip = lambda xs: [x[:120] for x in xs[:50]]
        filters = {k: clip(v) for k, v in (("sources", source), ("industries", industry), ("services", service), ("owners", owner)) if v}
        return date_from[:10], date_to[:10], filters

    def token(stamp: tuple) -> str:
        return ".".join(str(x) for x in stamp)

    @router.get("/api/funnel")
    def funnel(from_: str = Query("", alias="from"), to: str = Query(""), source: List[str] = Query([]), industry: List[str] = Query([]),
               service: List[str] = Query([]), owner: List[str] = Query([]), since: str = ""):
        f, t, filters = parse(from_, to, source, industry, service, owner)
        if since and since == token(current_stamp()):
            with lock:
                cached = result_cache.get((scope_key(), (f, t, tuple((k, tuple(sorted(v))) for k, v in sorted(filters.items())))))
            if cached and cached[0] == current_stamp():
                return {"unchanged": True, "stamp": since}
        result, _registry, stamp, _scope = compute(f, t, filters)
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return {**result, "stamp": token(stamp)}

    @router.get("/api/funnel/drill")
    def drill(key: str = Query(..., max_length=200), from_: str = Query("", alias="from"), to: str = Query(""), source: List[str] = Query([]),
              industry: List[str] = Query([]), service: List[str] = Query([]), owner: List[str] = Query([])):
        f, t, filters = parse(from_, to, source, industry, service, owner)
        result, registry, stamp, scope = compute(f, t, filters)
        if result.get("error"):
            raise HTTPException(400, result["error"])
        if key not in registry:
            raise HTTPException(404, "That number is not on the page any more. Refresh and try again.")
        _built, by_id = funnel_leads(scope, stamp)
        ids = registry[key]
        rows = sales_calc.drill_by_id(by_id, ids[:DRILL_CAP])
        return {"key": key, "count": len(ids), "rows": rows, "truncated": len(ids) > DRILL_CAP}

    @router.get("/api/funnel/settings")
    def get_settings():
        return {**sales_service.read_settings(store), "lists": lists(), "defaults": M.DEFAULTS}

    @router.put("/api/funnel/settings")
    def put_settings(body: SettingsBody, request: Request):
        user = getattr(request.state, "user", None) or {"role": "owner"}
        if user.get("role") not in ("owner", "admin"):
            raise HTTPException(403, "Only the owner or a workspace admin changes the sales funnel settings.")
        try:
            return {**sales_service.save_settings(store, body.model_dump(exclude_none=True) if hasattr(body, "model_dump") else body.dict(exclude_none=True), user),
                    "lists": lists(), "defaults": M.DEFAULTS}
        except sales_service.SalesRefused as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/funnel/recalculate")
    def recalc(request: Request):
        user = getattr(request.state, "user", None) or {"role": "owner"}
        if user.get("role") not in ("owner", "admin"):
            raise HTTPException(403, "Only the owner or a workspace admin can back-fill stage dates.")
        return sales_service.recalculate(store, user)

    def visible_lead(lead_id: str) -> dict:
        lead = next((l for l in store.peek("leads") if l.get("id") == lead_id), None)
        if lead is None or not workspaces.visible(lead):
            raise HTTPException(404, "Lead not found")
        return lead

    @router.get("/api/leads/{lead_id}/sales")
    def get_sales(lead_id: str):
        lead = visible_lead(lead_id)
        return sales_service.detail(lead, sales_view.auto_index([a for a in store.peek("activities") if a.get("lead_id") == lead_id]))

    @router.put("/api/leads/{lead_id}/sales")
    def put_sales(lead_id: str, body: SalesBody, request: Request):
        visible_lead(lead_id)
        data = body.model_dump(exclude_unset=True) if hasattr(body, "model_dump") else body.dict(exclude_unset=True)
        try:
            out = sales_service.set_sales(store, lead_id, data, getattr(request.state, "user", None))
        except sales_service.SalesRefused as exc:
            raise HTTPException(400, str(exc)) from None
        except LookupError:
            raise HTTPException(404, "Lead not found") from None
        return {**sales_service.detail(out["lead"]), "changed": out["changed"], "from": out["from"], "to": out["to"]}

    return router
