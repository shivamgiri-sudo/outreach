"""Reverse lookup on the Email checker page: who is behind each address (app/reverse_lookup.py).

POST /api/email-checker/reverse                      start a run (paste or CSV text, at most 2,000 addresses)
GET  /api/email-checker/reverse/{run_id}             progress, then the first 200 rows
GET  /api/email-checker/reverse/{run_id}/export.csv  every row (cells neutralised for spreadsheets)
GET  /api/email-checker/reverse/{run_id}/import      {csv}: the rows worth adding, for the smart import
                                                     (/api/import/commit creates new leads and fills only EMPTY fields)
Owners and workspace admins (routes_auth ADMIN_WRITES), like the bulk checker. A run belongs to the login that started it;
our own data (leads, other contacts, prospects) is cut to what that request may see before the run starts; a login limited
to some workspaces never gets prospect database matches."""
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import audit, bulk_email_check as bec, email_domain, reverse_lookup as rl, workspace_guard, workspaces
from .routes_email_checker import login_key

BACKGROUND = True                  # tests set False: the run then finishes inside the request
DNS: Optional[Callable] = None     # tests put a fake DNS here; None = app/email_domain (cached)
GRAVATAR: Optional[Callable] = None   # tests put a fake Gravatar fetch here; None = app/gravatar.http_fetch


class ReverseBody(BaseModel):
    text: str = Field(default="", max_length=bec.MAX_TEXT_CHARS)
    filename: str = Field(default="", max_length=200)


def make_router(store, fetch: Callable) -> APIRouter:
    router = APIRouter()
    runner = rl.Runner()

    def find(run_id: str, request: Request) -> dict:
        run = runner.get(run_id, login_key(request))
        if run is None:
            raise HTTPException(404, "That lookup was not found (finished lookups are kept for 6 hours).")
        return run

    def done(run_id: str, request: Request) -> dict:
        run = find(run_id, request)
        if run["status"] != "done":
            raise HTTPException(409, "The lookup is still working.")
        return run

    @router.post("/api/email-checker/reverse")
    def start(body: ReverseBody, request: Request):
        try:
            addresses = rl.read_addresses(body.text)
        except rl.TooMany as exc:
            raise HTTPException(413, str(exc)) from None
        if not addresses:
            raise HTTPException(400, "Paste some email addresses, or choose a CSV file with an email column.")
        user = getattr(request.state, "user", None)
        visible = workspaces.filter_rows(store.read("leads"))              # what this login may see (ownership too)
        known = {"check": bec.known_data(store, visible), "ours": rl.our_index(visible),
                 # SECURITY: a login limited to some workspaces may not read the shared prospect database (workspace_guard)
                 "prospects": {} if workspace_guard.limited(user) else rl.prospect_matches(store, addresses, user)}
        tools = {"store": store, "fetch": fetch, "dns": DNS or email_domain.status_of_domain, "gravatar": GRAVATAR}
        go = runner.start if BACKGROUND else runner.run_now
        try:
            run = go(login_key(request), addresses, known, tools)
        except bec.Busy as exc:
            raise HTTPException(409, "Other lookups are working right now. Try again in a minute.") from None
        audit.record(store, user, "email_checker.reverse", "reverse email lookup",
                     f"{len(addresses)} address(es){' from ' + body.filename if body.filename else ''}")
        return run

    @router.get("/api/email-checker/reverse/{run_id}")
    def progress(run_id: str, request: Request):
        return rl.Runner.view(find(run_id, request))

    @router.get("/api/email-checker/reverse/{run_id}/export.csv")
    def export(run_id: str, request: Request):
        return Response(rl.to_csv(done(run_id, request)["rows"]), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="reverse-lookup-{run_id}.csv"'})

    @router.get("/api/email-checker/reverse/{run_id}/import")
    def for_import(run_id: str, request: Request):
        rows = done(run_id, request)["rows"]
        return {"csv": rl.import_csv(rows), "rows": len(rl.importable(rows))}

    return router
