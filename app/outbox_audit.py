"""Audit views of what went out: the Outbox CSV with every "why it was sent" column, and the internal emails list
(alerts, reports, password resets, reminders, invites) from `transactional_outbox`.

Both follow the request's workspace scope the same way the Outbox table does (app/outbox_view.py, app/workspaces.py):
an email row is kept only when its lead is in scope; an internal email with no lead is kept only in an unscoped request.
"""
from . import outbox_view, provenance, workspaces
from .exports import STATUS_LABEL, cols, ist_text

EXPORT_COLUMNS = cols("@sent_at", "to", "company", "lead_id", "campaign", "step", "subject", "status", "delivered",
                      "origin_type", "trigger", "actor_name", "actor_email", "rule_sequence", "rule_version", "rule_step", "rule_day",
                      "rule_flow", "rule_campaign", "enrolled_by", "enrolled_by_email", "enrolled_via", "enrolled_detail",
                      "@enrolled_at", "mode", "live_sending", "daily_cap", "business_hours", "track_email", "consent_basis",
                      "legacy", "why")
INTERNAL_KINDS = {"internal": "Alert", "meeting_reminder": "Meeting reminder", "reminder": "Reminder", "report": "Report",
                  "password_reset": "Password reset", "meeting_invite": "Meeting invite", "meeting_cancel": "Meeting cancelled"}
MAX_INTERNAL = 500


def export_rows(store, params: dict) -> list:
    """Every outbox row the Outbox filters keep (status, step, from, to, campaign_id, kind, origin, q), in scope."""
    p = {k: str(params.get(k) or "") for k in ("status", "step", "from", "to", "campaign_id", "kind", "origin", "q")}
    campaigns = {c["id"]: c.get("name") or c["id"] for c in store.read("campaigns")}
    flows = outbox_view._flow_names(store)
    _all, shown = outbox_view.filtered(store, p["status"][:20], p["step"][:120], p["from"][:10], p["to"][:10], p["campaign_id"][:40],
                                       p["kind"][:20], p["q"], p["origin"][:20])
    out = []
    for _key, m, lead in shown:
        cid = (lead or {}).get("campaign_id")
        row = {"sent_at": m.get("sent_at"), "to": m.get("to"), "company": (lead or {}).get("company") or (lead or {}).get("domain") or "",
               "lead_id": m.get("lead_id"), "campaign": campaigns.get(cid, "") if cid else "", "step": m.get("step"),
               "subject": m.get("subject"), "status": STATUS_LABEL.get(m.get("status"), m.get("status")),
               "delivered": bool(m.get("delivered_at")), **provenance.columns(m),
               "why": " ".join(provenance.describe(m, lead, flows)["why"])}
        out.append({c: (ist_text(row.get(c[:-4])) if c.endswith("_ist") else row.get(c)) for c in EXPORT_COLUMNS})
    return out


def internal_rows(store, kind: str = "", status: str = "", limit: int = MAX_INTERNAL) -> dict:
    """Internal / transactional emails newest first: to, subject, kind, why, status, time."""
    rows = workspaces.filter_by_lead(store, store.read("transactional_outbox"))
    kinds = sorted({r.get("kind") for r in rows if r.get("kind")})
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if status in outbox_view.STATUSES:
        rows = [r for r in rows if r.get("status") in outbox_view.STATUSES[status]]
    rows = list(reversed(rows))
    items = []
    for r in rows[:max(1, min(int(limit or MAX_INTERNAL), MAX_INTERNAL))]:
        why = provenance.describe(r)
        items.append({"id": r.get("id"), "at": r.get("at"), "to": r.get("to"), "subject": r.get("subject"), "kind": r.get("kind"),
                      "kind_label": INTERNAL_KINDS.get(r.get("kind"), str(r.get("kind") or "").replace("_", " ").capitalize()),
                      "status": r.get("status"), "error": r.get("error"), "lead_id": r.get("lead_id"),
                      "origin_type": why["type"], "origin_label": why["label"], "origin_actor": why["actor"], "why": why["why"]})
    return {"items": items, "total": len(rows), "kinds": [{"id": k, "name": INTERNAL_KINDS.get(k, k)} for k in kinds]}
