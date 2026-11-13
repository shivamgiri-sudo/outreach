"""GET /api/call-log (rows + per-caller summary) and GET /api/call-log/export (CSV, owner only, audited).
Scoping: workspace + client account + lead ownership via workspaces.filter_rows, then the lead_access Hider again."""
import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from . import audit, call_log as cl, lead_access, lead_quality, rep_workflow, workspaces
from .hygiene import csv_safe

EXPORT_MAX = 20000
COLUMNS = ("at", "caller", "channel", "outcome", "company", "contact_name", "phone", "notes", "lead_id")


def make_router(store) -> APIRouter:
    router = APIRouter()

    def select(request, day_from, day_to, by, outcome, q, channel):
        if channel not in cl.CHANNELS:
            raise HTTPException(400, f"channel must be one of {', '.join(cl.CHANNELS)}")
        user = getattr(request.state, "user", None)
        hider = lead_access.hidden_test(store, user)
        leads = [l for l in workspaces.filter_rows(store.read("leads")) if not (hider and hider(l))]
        ids = {l["id"] for l in leads}
        acts = [a for a in store.read("activities") if a.get("lead_id") in ids]
        try:
            rows = cl.in_range(cl.collect(leads, acts, channel), day_from, day_to)
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        rows = [r for r in rows if cl.matches(r, q)]
        summary = cl.summarise(rows)                       # before the caller / outcome filters: it compares callers
        if by:
            rows = [r for r in rows if (r["by"] or "Unknown").lower() == by.strip().lower()]
        if outcome:
            rows = [r for r in rows if r["outcome"] == outcome]
        return rows, summary, user

    @router.get("/api/call-log")
    def call_log(request: Request, day_from: str = Query("", alias="from"), day_to: str = Query("", alias="to"), by: str = "",
                 outcome: str = "", q: str = "", channel: str = "call", page: int = Query(1, ge=1), size: int = Query(50, ge=1)):
        rows, summary, _ = select(request, day_from, day_to, by, outcome, q, channel)
        size = min(size, cl.MAX_SIZE)
        start = (page - 1) * size
        return {"rows": [cl.public(r) for r in rows[start:start + size]], "total": len(rows), "page": page, "size": size,
                "summary": summary, "callers": [s["caller"] for s in summary]}

    @router.get("/api/lead-quality")
    def quality(request: Request):
        """Invalid / consultant rates per source and caller, over the leads this person may see."""
        user = getattr(request.state, "user", None)
        hider = lead_access.hidden_test(store, user)
        leads = [l for l in workspaces.filter_rows(store.read("leads")) if not (hider and hider(l))]
        out = lead_quality.report(leads)
        out["reason_names"] = rep_workflow.INVALID_REASONS
        return out

    @router.get("/api/call-log/export")
    def export(request: Request, day_from: str = Query("", alias="from"), day_to: str = Query("", alias="to"), by: str = "",
               outcome: str = "", q: str = "", channel: str = "call"):
        rows, _, user = select(request, day_from, day_to, by, outcome, q, channel)
        rows = rows[:EXPORT_MAX]
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(COLUMNS)
        for r in rows:
            values = {**cl.public(r), "caller": r["by"]}
            writer.writerow([csv_safe(values.get(k)) for k in COLUMNS])
        audit.record(store, user, "call_log.export", f"{len(rows)} rows", f"{day_from or 'start'}..{day_to or 'now'} channel {channel}")
        return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="call-log.csv"', "Cache-Control": "no-store", "X-Exported": str(len(rows))})

    return router
