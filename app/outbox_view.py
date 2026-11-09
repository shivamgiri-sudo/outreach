"""The Outbox page's table: emails newest first, filtered and paged on the server, and one email's full text with its
designed (HTML) version. Rows are matched to their lead for the company, the campaign and the workspace scope."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import provenance, workspaces

IST = timezone(timedelta(hours=5, minutes=30))
MAX_PAGE_SIZE = 200
STATUSES = {"sent": ("sent",), "practice": ("dry_run",), "failed": ("failed", "provider_error")}


def row_key(row: dict, index: int) -> str:
    return str(row.get("idempotency_key") or row.get("message_id") or f"row-{index}")


def _ist_day(value) -> str:
    try:
        at = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return ""
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(IST).date().isoformat()


def _rows(store) -> list:
    """(key, row, lead) newest first, only rows whose lead is in scope (rows of erased leads only without a scope)."""
    leads = {l["id"]: l for l in store.read("leads")}
    unscoped = workspaces._SCOPE.get() == (None, None)
    out = []
    for i, m in enumerate(store.read("outbox")):
        lead = leads.get(m.get("lead_id"))
        if (lead is not None and workspaces.visible(lead)) or (lead is None and unscoped):
            out.append((row_key(m, i), m, lead))
    out.reverse()                                   # newest saved first, then by send time (stable for equal times)
    out.sort(key=lambda x: str(x[1].get("sent_at") or ""), reverse=True)
    return out


def _is_manual(m: dict) -> bool:
    """A one-off email a person wrote. "Send email now" also marks its row manual (a person chose it), but that row is a
    sequence step with its own step name, so it stays under "Sequence only"."""
    return m.get("variant") == "manual" or (bool(m.get("manual")) and m.get("step") in (None, "", "One-off email"))


ORIGINS = ("automatic", "manual", "unknown")


def _summary(key: str, m: dict, lead: Optional[dict], campaigns: dict, flows: Optional[dict] = None) -> dict:
    cid = (lead or {}).get("campaign_id")
    why = provenance.describe(m, lead, flows)
    return {"key": key, "origin_type": why["type"], "origin_label": why["label"], "origin_legacy": why["legacy"],
            "origin_actor": why["actor"], "origin_trigger": why["trigger"], "why": why["why"], "lead_id": m.get("lead_id"), "company": (lead or {}).get("company") or (lead or {}).get("domain") or "",
            "to": m.get("to"), "subject": m.get("subject"), "step": m.get("step"), "variant": m.get("variant"), "status": m.get("status"),
            "sent_at": m.get("sent_at"), "delivered_at": m.get("delivered_at"), "error": m.get("error"), "by": m.get("by"),
            "manual": _is_manual(m), "campaign_id": cid, "campaign": campaigns.get(cid, "") if cid else "",
            "preview": " ".join(str(m.get("body") or "").split())[:140],
            "opens": int(m.get("opens") or 0), "clicks": int(m.get("clicks") or 0), "opened_at": m.get("opened_at"), "clicked_at": m.get("clicked_at")}


def search(store, status: str = "", step: str = "", date_from: str = "", date_to: str = "", campaign_id: str = "", kind: str = "",
           q: str = "", page: int = 1, size: int = 50, origin: str = "") -> dict:
    """`kind`: "sequence" / "manual" (one-off) as before, or "automatic" / "by_person" (who sent it, app/provenance.py).
    `origin`: "automatic", "manual" or "unknown" (sent before audit tracking and the row does not say)."""
    size = max(1, min(int(size or 50), MAX_PAGE_SIZE))
    page = max(1, int(page or 1))
    campaigns = {c["id"]: c.get("name") or c["id"] for c in store.read("campaigns")}
    flows = _flow_names(store)
    rows, shown = filtered(store, status, step, date_from, date_to, campaign_id, kind, q, origin)
    steps = sorted({m.get("step") for _, m, _ in rows if m.get("step")})
    start = (page - 1) * size
    return {"items": [_summary(k, m, lead, campaigns, flows) for k, m, lead in shown[start:start + size]], "total": len(shown), "all": len(rows),
            "page": page, "size": size, "pages": max(1, -(-len(shown) // size)), "steps": steps,
            "campaigns": [{"id": cid, "name": name} for cid, name in campaigns.items()]}


def _flow_names(store) -> dict:
    try:
        flows = store.read("flows")
    except Exception:                                   # noqa: BLE001 - a store that never had a flow has no collection
        flows = []
    return {f.get("id"): f.get("name") or f.get("id") for f in flows or [] if isinstance(f, dict)}


def filtered(store, status: str = "", step: str = "", date_from: str = "", date_to: str = "", campaign_id: str = "", kind: str = "",
             q: str = "", origin: str = "") -> tuple:
    """(every row in scope, the rows these filters keep), both as (key, row, lead) newest first."""
    q = " ".join(str(q or "").lower().split())[:80]
    origin = {"by_person": "manual"}.get(kind, kind) if kind in ("automatic", "by_person") and not origin else origin
    rows = _rows(store)

    def keep(m: dict, lead: Optional[dict]) -> bool:
        if status == "delivered" and not m.get("delivered_at"):
            return False
        if status in STATUSES and m.get("status") not in STATUSES[status]:
            return False
        if step and m.get("step") != step:
            return False
        day = _ist_day(m.get("sent_at"))
        if date_from and (not day or day < date_from):
            return False
        if date_to and (not day or day > date_to):
            return False
        if campaign_id and (lead or {}).get("campaign_id") != campaign_id:
            return False
        if kind == "manual" and not _is_manual(m):
            return False
        if kind == "sequence" and _is_manual(m):
            return False
        if origin in ORIGINS and provenance.origin_type(m) != origin:
            return False
        if q and q not in " ".join(str(x or "") for x in (m.get("to"), m.get("subject"), (lead or {}).get("company"))).lower():
            return False
        return True
    return rows, [(k, m, lead) for k, m, lead in rows if keep(m, lead)]


def message(store, key: str, base_url: str) -> Optional[dict]:
    """One email in full: the plain text as sent and the designed HTML version (as email_send would build it)."""
    from .email_html import render
    campaigns = {c["id"]: c.get("name") or c["id"] for c in store.read("campaigns")}
    for k, m, lead in _rows(store):
        if k == key:
            body = str(m.get("body") or "")
            html = render(body, base_url, brand=store) if body and store.read("settings").get("html_email") is not False else ""
            return {**_summary(k, m, lead, campaigns, _flow_names(store)), "body": body, "html": html}
    return None
