"""GET/PUT /api/call-retry (owner changes, anyone on the call desk reads) and GET /api/best-time (read-only, scoped like the call log)."""
from fastapi import APIRouter, HTTPException, Request

from . import best_time, call_retry, lead_access, rep_workflow, workspaces


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/call-retry")
    def get_retry():
        return {**call_retry.get_settings(store), "limits": {"min_delay": call_retry.MIN_DELAY, "max_delay": call_retry.MAX_DELAY,
                                                              "max_steps": call_retry.MAX_STEPS, "max_attempts": call_retry.MAX_ATTEMPTS}}

    @router.put("/api/call-retry")
    def put_retry(body: dict, request: Request):
        user = getattr(request.state, "user", None)
        if user and user.get("role") != "owner":
            raise HTTPException(403, "Only an owner can change the call retry rules.")
        try:
            cfg = call_retry.clean(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        from . import audit_settings
        with audit_settings.watch(store, request, "Call retry rules"):
            store.update("settings", lambda s: {**s, "call_retry": cfg})
        return get_retry()

    @router.get("/api/best-time")
    def best(request: Request):
        user = getattr(request.state, "user", None)
        hider = lead_access.hidden_test(store, user)
        leads = [l for l in workspaces.filter_rows(store.read("leads")) if not (hider and hider(l))]
        return best_time.compute(leads, store.read("activities"))

    return router
