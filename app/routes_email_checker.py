"""Bulk email checker page: start a check of a pasted list / CSV, follow its progress, download every row as CSV, and
list the valid new addresses (the page adds them through the normal quick-paste import, /api/leads/import).

Owners and workspace admins (routes_auth ADMIN_WRITES), like the email checks before the first send. A run belongs to the
login that started it; "already a lead" and "bounced before" only look at the leads of that request's workspaces."""
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import audit, bulk_email_check as bec, email_domain, workspaces


BACKGROUND = True                  # tests set False: the run then finishes inside the request
DNS: Optional[Callable] = None     # tests put a fake DNS here; None = app/email_domain (cached)


class CheckBody(BaseModel):
    text: str = Field(default="", max_length=bec.MAX_TEXT_CHARS)
    filename: str = Field(default="", max_length=200)


def login_key(request: Request) -> str:
    user = getattr(request.state, "user", None) or {}
    return str(user.get("sub") or user.get("email") or "local")


def make_router(store) -> APIRouter:
    router = APIRouter()
    runner = bec.Runner()

    def find(run_id: str, request: Request) -> dict:
        run = runner.get(run_id, login_key(request))
        if run is None:
            raise HTTPException(404, "That check was not found (finished checks are kept for 6 hours).")
        return run

    @router.post("/api/email-checker/runs")
    def start(body: CheckBody, request: Request):
        try:
            addresses = bec.read_addresses(body.text)
        except bec.TooMany as exc:
            raise HTTPException(413, str(exc)) from None
        if not addresses:
            raise HTTPException(400, "Paste some email addresses, or choose a CSV file with an email column.")
        scoped = workspaces.filter_rows(store.read("leads"), owned=False)
        known = bec.known_data(store, scoped)
        go = runner.start if BACKGROUND else runner.run_now
        try:
            run = go(login_key(request), addresses, known, DNS or email_domain.status_of_domain)
        except bec.Busy as exc:
            raise HTTPException(409, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "email_checker.run", "bulk email checker",
                     f"{len(addresses)} address(es){' from ' + body.filename if body.filename else ''}")
        return run

    @router.get("/api/email-checker/runs/{run_id}")
    def progress(run_id: str, request: Request):
        return bec.Runner.view(find(run_id, request))

    @router.get("/api/email-checker/runs/{run_id}/export.csv")
    def export(run_id: str, request: Request):
        run = find(run_id, request)
        if run["status"] != "done":
            raise HTTPException(409, "The check is still working.")
        return Response(bec.to_csv(run["rows"]), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="email-check-{run_id}.csv"'})

    @router.get("/api/email-checker/runs/{run_id}/valid")
    def valid(run_id: str, request: Request):
        run = find(run_id, request)
        if run["status"] != "done":
            raise HTTPException(409, "The check is still working.")
        return {"emails": bec.valid_addresses(run["rows"])}

    return router
