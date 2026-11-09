"""Send audit routes: the Outbox CSV with "why it was sent" columns, and the internal emails list (owners and admins)."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from . import audit, exports, outbox_audit


def _owner_or_admin(request: Request) -> None:
    role = (getattr(request.state, "user", None) or {"role": "owner"}).get("role")
    if role not in ("owner", "admin"):
        raise HTTPException(403, "Only owners and workspace admins can see this.")


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/outbox/internal")
    def internal_emails(request: Request, kind: str = Query("", max_length=40), status: str = Query("", max_length=20),
                        limit: int = Query(outbox_audit.MAX_INTERNAL, ge=1, le=outbox_audit.MAX_INTERNAL)):
        """Alerts, reports, password resets, reminders and invites: to whom, why, status and when."""
        _owner_or_admin(request)
        return outbox_audit.internal_rows(store, kind, status, limit)

    @router.get("/api/outbox/export.csv")
    def export_outbox(request: Request, status: str = Query("", max_length=20), step: str = Query("", max_length=120),
                      date_from: str = Query("", alias="from", max_length=10), date_to: str = Query("", alias="to", max_length=10),
                      campaign_id: str = Query("", max_length=40), kind: str = Query("", max_length=20),
                      origin: str = Query("", max_length=20), q: str = Query("", max_length=200)):
        """Every email the Outbox filters keep, with who or which rule sent it and why (one row per email)."""
        _owner_or_admin(request)
        params = {"status": status, "step": step, "from": date_from, "to": date_to, "campaign_id": campaign_id, "kind": kind,
                  "origin": origin, "q": q}
        rows = outbox_audit.export_rows(store, params)
        filters = ", ".join(f"{k}={v[:40]}" for k, v in params.items() if v)
        audit.record(store, getattr(request.state, "user", None), "export.download", "outbox-audit.csv",
                     "; ".join(x for x in (f"{len(rows)} rows", filters) if x))
        name = f"outreach-outbox-audit-{exports.ist_date()}.csv"
        return StreamingResponse(exports.csv_chunks(outbox_audit.EXPORT_COLUMNS, rows), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    return router
