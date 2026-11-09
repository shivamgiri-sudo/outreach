"""Reports page: GET /api/reports/daily (daily report, per person, activity log), the CSV download, "Email me this
report" (HTML email + CSV attachment + signed download link) and the public signed-link download itself."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import analytics_range, audit, daily_report, report_mail, workspaces
from .hygiene import normalise_email

PUBLIC_PREFIX = "/api/reports/file/"


class SlaBody(BaseModel):
    first_touch_hours: float = Field(gt=0, le=720)
    reply_hours: float = Field(gt=0, le=720)


class EmailBody(BaseModel):
    date_from: str = Field(default="", alias="from", max_length=10)
    to_date: str = Field(default="", alias="to", max_length=10)
    days: Optional[int] = None
    who: str = Field(default="", max_length=120)
    kind: str = Field(default="", max_length=20)
    send_to: list = []                 # extra addresses (owners / workspace admins only); the asker always gets it


def make_router(store, base_url_of, sender) -> APIRouter:
    router = APIRouter()

    def period(date_from: str, date_to: str, days: Optional[int]) -> dict:
        try:
            return analytics_range.parse_range(date_from[:10], date_to[:10], days)
        except analytics_range.RangeError as exc:
            raise HTTPException(400, str(exc)) from None

    def may_export(request: Request) -> None:
        role = (getattr(request.state, "user", None) or {"role": "owner"}).get("role")
        if role not in ("owner", "admin"):
            raise HTTPException(403, "Only owners and workspace admins can download or send reports to others.")

    @router.get("/api/reports/daily")
    def daily(date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"), days: Optional[int] = None,
              who: str = Query("", max_length=120), kind: str = Query("", max_length=20)):
        rng = period(date_from, date_to, days)
        return {**daily_report.build(store, rng, who, kind), "range": analytics_range.public_range(rng)}

    @router.get("/api/reports/sla")
    def response_times(date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"), days: Optional[int] = None):
        from . import sla
        rng = period(date_from, date_to, days)
        return {**sla.measure(store, rng), "range": analytics_range.public_range(rng)}

    @router.put("/api/reports/sla-targets")
    def sla_targets(body: SlaBody, request: Request):
        role = (getattr(request.state, "user", None) or {"role": "owner"}).get("role")
        if role != "owner":
            raise HTTPException(403, "Only an owner can change the response-time targets.")
        from . import audit_settings
        with audit_settings.watch(store, request, "response-time targets"):
            store.update("settings", lambda s: {**s, "sla_first_touch_hours": body.first_touch_hours, "sla_reply_hours": body.reply_hours})
        from . import sla
        return sla.targets(store)

    @router.get("/api/reports/daily.csv")
    def daily_csv(request: Request, date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"),
                  days: Optional[int] = None, who: str = Query("", max_length=120), kind: str = Query("", max_length=20)):
        may_export(request)
        rng = period(date_from, date_to, days)
        rows = [r for r in daily_report.activity_log(store, rng) if (not who or r["who"] == who) and (not kind or r["kind"] == kind)]
        audit.record(store, getattr(request.state, "user", None), "export.download", "activity-log.csv", f"{len(rows)} rows")
        return Response(daily_report.csv_text(rows), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="activity-log-{rng["from"]}-to-{rng["to"]}.csv"'})

    @router.post("/api/reports/email")
    def email(body: EmailBody, request: Request):
        """Email this report: to the person asking, plus (owners / workspace admins) other addresses."""
        user = getattr(request.state, "user", None) or {}
        me = normalise_email(user.get("email") or "")
        extra = [normalise_email(a) for a in body.send_to or []][:20]
        if any(not a for a in extra):
            raise HTTPException(400, "One of the addresses is not a valid email.")
        if extra:
            may_export(request)
        to = list(dict.fromkeys([a for a in [me, *extra] if a]))
        if not to:
            raise HTTPException(400, "Your login has no email address: add one to send yourself reports.")
        rng = period(body.date_from, body.to_date, body.days)
        scope = getattr(request.state, "workspace_scope", (None, None))
        who = audit.actor_of(user)["name"]
        mail = report_mail.compose(store, rng, body.who, body.kind, scope, who, base_url_of())
        results = {a: sender({"to": a, "subject": mail["subject"], "body": mail["body"], "html": mail["html"],
                              "attachments": mail["attachments"], "kind": "report"}).get("status") for a in to}
        from .provenance import internal, person_of
        from .reminders import _log_transactional
        for a, status in results.items():               # listed on the Outbox "Internal emails" tab, with who asked for it
            _log_transactional(store, a, mail["subject"], f"Activity report {rng['from']} to {rng['to']} (download link not kept)",
                               "report", None, {"status": status or "dry_run"},
                               extra={"origin": internal("report", person_of(user), live=status != "dry_run",
                                                         rule={"period": f"{rng['from']} to {rng['to']}"})})
        audit.record(store, user, "report.email", ", ".join(to), f"{rng['from']} to {rng['to']}: " + ", ".join(f"{a} {s}" for a, s in results.items()))
        return {"sent_to": to, "results": results, "subject": mail["subject"], "link": mail["link"], "html": mail["html"]}

    @router.get(PUBLIC_PREFIX + "{token}")
    def file(token: str):
        """The download link in a report email (no login: the token is the key, valid for a week)."""
        row = report_mail.find_link(store, token[:100])
        if row is None:
            raise HTTPException(404, "This download link has expired. Ask for the report again.")
        allowed = set(row["allowed"]) if row.get("allowed") is not None else None
        workspaces._SCOPE.set((row.get("selected"), allowed))          # exactly what the asker could see
        rng = analytics_range.parse_range(row["from"], row["to"])
        rows = [r for r in daily_report.activity_log(store, rng)
                if (not row.get("who") or r["who"] == row["who"]) and (not row.get("kind") or r["kind"] == row["kind"])]
        return Response(daily_report.csv_text(rows), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="activity-log-{row["from"]}-to-{row["to"]}.csv"'})

    return router
