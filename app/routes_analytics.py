"""GET /api/intelligence, the change-log CSV export, and one-click reminders for leads that are going cold."""
import csv
import io

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import activities, analytics, audit


def _cell(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text      # spreadsheet formula guard


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/intelligence")
    def intelligence():
        return analytics.build(store)

    @router.get("/api/change-log.csv")
    def change_log_csv():
        rows = audit.log_for(store, "", 20000)
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

    return router
