"""GET /api/intelligence and /api/analytics (with a period: from / to as IST days, or days), the change-log CSV export,
one-click reminders for leads that are going cold, the owner's audit trail and the system health check."""
import csv
import io

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from . import activities, analytics, analytics_range, audit


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text      # spreadsheet formula guard


def make_router(store) -> APIRouter:
    router = APIRouter()

    def period(date_from: str, date_to: str, days: Optional[int]) -> dict:
        try:
            return analytics_range.parse_range(date_from[:10], date_to[:10], days)
        except analytics_range.RangeError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/api/intelligence")
    def intelligence(date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"), days: Optional[int] = None):
        if not (date_from or date_to or days is not None):
            return analytics.build(store)
        return analytics.build(store, rng=period(date_from, date_to, days))

    @router.get("/api/analytics")
    def analytics_all(date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"), days: Optional[int] = None):
        """KPI tiles with the change against the previous period, the funnel by campaign / step / variant / source,
        stage velocity and team activity."""
        return analytics_range.build(store, period(date_from, date_to, days))

    @router.get("/api/analytics/overview")
    def analytics_overview(date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"), days: Optional[int] = None):
        return analytics_range.overview(store, period(date_from, date_to, days))

    @router.get("/api/audit")
    def audit_trail(action: str = "", by: str = "", q: str = "", date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"),
                    limit: int = 200, offset: int = 0):
        """Owner only (routes_auth): who did what and when, newest first."""
        return audit.trail(store, action[:60], by[:80], q[:80], date_from[:10], date_to[:10], max(1, min(limit, 1000)), max(0, offset))

    @router.get("/api/audit.csv")
    def audit_csv(action: str = "", by: str = "", q: str = "", date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to")):
        from .backup import ist_label
        from datetime import datetime
        rows = audit.trail(store, action[:60], by[:80], q[:80], date_from[:10], date_to[:10], audit.MAX_AUDIT_ROWS)["items"]
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["at_ist", "at", "by", "by_email", "role", "action", "what", "target", "detail"])
        for r in rows:
            try:
                when = ist_label(datetime.fromisoformat(r["at"]))
            except (TypeError, ValueError):
                when = ""
            writer.writerow([_cell(v) for v in (when, r["at"], r["by"], r["by_email"], r["by_role"], r["action"],
                                                audit.ACTIONS.get(r["action"], r["action"]), r["target"], r["detail"])])
        return Response("\ufeff" + out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'})

    @router.get("/api/system/health")
    def system_health():
        """Owner only (routes_auth): background jobs, sending, queue, backups, LinkedIn agent, database, version."""
        from . import system_health as health
        return health.build(store)

    @router.get("/api/change-log.csv")
    def change_log_csv(request: Request):
        rows = audit.log_for(store, "", 20000)
        audit.record(store, getattr(request.state, "user", None), "export.download", "change-log.csv", f"{len(rows)} rows")
        fields = list(dict.fromkeys(k for r in rows for k in r)) or ["at", "by", "field", "old", "new"]
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(fields)
        for r in rows:
            writer.writerow([_cell(r.get(f)) for f in fields])
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="change-log.csv"'})

    @router.post("/api/intelligence/remind-stale")
    def remind_stale(request: Request):
        """A follow-up task for every lead that has gone quiet; one open task per lead, so pressing twice adds nothing."""
        user = getattr(request.state, "user", None) or {}
        owner = audit.actor_of(user)["name"] if user.get("email") else None
        info = analytics.stale(store.read("leads"), limit=None)
        made = 0
        for item in info["leads"]:
            before = {t["id"] for t in store.read("tasks")}
            task = activities.add_task(store, item["id"], "follow_up", f"No activity for {info['days']}+ days. Pick this lead up before it goes cold.",
                                       due_at=activities.now_utc(), owner=owner, priority="high", source="going_cold", dedupe_open=True)
            made += task["id"] not in before
        return {"stale": info["count"], "created": made}

    @router.get("/api/leads/{lead_id}/company-health")
    def company_health_get(lead_id: str):
        from . import company_health
        from .hygiene import business_domain
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        domain = lead.get("domain") or business_domain(lead.get("website")) or business_domain(lead.get("email"))
        return {"report": company_health.cached(store, domain) if domain else None, "domain": domain}

    @router.post("/api/leads/{lead_id}/company-health")
    def company_health_refresh(lead_id: str):
        """Build (or rebuild, at most hourly per company) the health report from free public data."""
        from . import company_health
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        try:
            return {"report": company_health.refresh(store, lead)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    return router
