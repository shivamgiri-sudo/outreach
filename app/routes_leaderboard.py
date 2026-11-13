"""GET /api/leaderboard?period=today|yesterday|week|month|custom&from=&to=&rank_by=calls|connected|interested|meetings.
Scoped like the call log: workspace + client account + lead ownership, then the lead_access Hider. Output is caller names and
numbers only (see app/leaderboard.py for the visibility rule)."""
from fastapi import APIRouter, HTTPException, Query, Request

from . import lead_access, leaderboard as lb, rep_workflow, workspaces


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/leaderboard")
    def board(request: Request, period: str = "today", day_from: str = Query("", alias="from"), day_to: str = Query("", alias="to"),
              rank_by: str = "calls"):
        user = getattr(request.state, "user", None)
        try:
            window = lb.resolve(period, day_from, day_to)
            hider = lead_access.hidden_test(store, user)
            leads = [l for l in workspaces.filter_rows(store.read("leads")) if not (hider and hider(l))]
            ids = {l["id"] for l in leads}
            acts = [a for a in store.read("activities") if a.get("lead_id") in ids]
            out = lb.build(store, leads, acts, window, rank_by, rep_workflow.actor_name(store, user) if user else "")
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        out["targets_note"] = "Targets are set on My day."
        return out

    return router
