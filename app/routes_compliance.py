"""DPDP endpoints (owner only, enforced by the role guard): export everything we hold on a person, erase it,
review cold leads past the retention period, and read the consent ledger."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .privacy import contact_hash, lead_hashes, now_iso, record_consent

LEAD_COLLECTIONS = ("outbox", "whatsapp_outbox", "transactional_outbox", "activities", "tasks", "notifications")
DEFAULT_RETENTION_DAYS = 365
# Leads in these stages are (or were) in a real conversation, so they are not "cold".
WARM_STAGES = ("engaged", "interested", "whatsapp_active", "call_done", "trial", "customer", "advocate")


class EraseBody(BaseModel):
    confirm: bool = False


def _optional(store, key: str) -> list:
    try:
        return store.read(key)
    except KeyError:
        return []


def _last_activity(lead: dict) -> str:
    stamps = [lead.get("created_at") or "", lead.get("last_activity_at") or "", *(e.get("at", "") for e in lead.get("timeline") or [])]
    return max(stamps)


def make_router(store) -> APIRouter:
    router = APIRouter()

    def lead_or_404(lead_id: str) -> dict:
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        return lead

    def contacts(lead: dict) -> set:
        values = {lead.get("email"), lead.get("phone"), *(lead.get("phones") or [])}
        return {v for v in values if v}

    @router.get("/api/leads/{lead_id}/export")
    def export(lead_id: str, request: Request):
        """Everything we hold on this person, as one JSON file (DPDP right of access)."""
        lead = lead_or_404(lead_id)
        from . import audit
        audit.record(store, getattr(request.state, "user", None), "export.download", f"lead-{lead_id}.json",
                     f"everything held on {lead.get('company') or lead.get('domain') or 'a lead'} (DPDP access)")
        mine = contacts(lead)
        data = {"exported_at": now_iso(), "lead": lead,
                **{key: [r for r in store.read(key) if r.get("lead_id") == lead_id] for key in LEAD_COLLECTIONS},
                "inbox": [r for r in store.read("inbox") if r.get("lead_id") == lead_id or r.get("from") in mine],
                "delivery_events": [e for e in store.read("delivery_events") if e.get("email") and e["email"] in mine],
                "consent_ledger": [r for r in store.read("consent_ledger") if r.get("lead_id") == lead_id],
                "suppressed": bool(lead.get("email") and lead["email"] in store.read("suppressed")),
                "data_source_runs": [{"run_id": run.get("id"), "provider": pid, **row}
                                     for run in _optional(store, "provider_runs")
                                     for pid, rows in (run.get("rows") or {}).items() for row in rows if row.get("lead_id") == lead_id]}
        return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="lead-{lead_id}.json"'})

    @router.post("/api/leads/{lead_id}/erase")
    def erase(lead_id: str, body: EraseBody, request: Request):
        """Delete the person and everything linked to them; keep only hashes so they are never imported or contacted
        again. Consent-ledger rows are kept as proof, with the text removed and the lead id replaced by a hash."""
        if not body.confirm:
            raise HTTPException(400, "Send {\"confirm\": true} to erase. This cannot be undone.")
        lead = lead_or_404(lead_id)
        mine = contacts(lead)
        hashes = lead_hashes(lead)
        ref = "erased:" + contact_hash(lead_id)[:12]
        counts = {}

        def drop(key, keep):
            def apply(rows):
                kept = [r for r in rows if keep(r)]
                counts[key] = len(rows) - len(kept)
                return kept
            store.update(key, apply)

        store.update("suppressed_hashes", lambda rows: sorted(set(rows) | hashes))   # first, so nothing re-adds them
        drop("leads", lambda l: l["id"] != lead_id)
        store.update("leads", lambda rows: [{**l, "referred_by": None} if l.get("referred_by") == lead_id else l for l in rows])
        for key in LEAD_COLLECTIONS:
            drop(key, lambda r: r.get("lead_id") != lead_id)
        drop("inbox", lambda r: r.get("lead_id") != lead_id and r.get("from") not in mine)
        drop("delivery_events", lambda e: not (e.get("email") and e["email"] in mine))
        drop("suppressed", lambda email: email not in mine)                    # the hash replaces the plain address
        store.update("consent_ledger", lambda rows: [{**r, "lead_id": ref, "text": "", "redacted_at": now_iso()}
                                                      if r.get("lead_id") == lead_id else r for r in rows])
        if _optional(store, "provider_runs"):
            store.update("provider_runs", lambda runs: [
                {**run, "lead_ids": [i for i in run.get("lead_ids") or [] if i != lead_id],
                 "rows": {pid: [r for r in rows if r.get("lead_id") != lead_id] for pid, rows in (run.get("rows") or {}).items()}}
                for run in runs])
        from . import prospect_db
        from .prospect_ingest import safely
        safely(prospect_db.erase_contacts, store, [lead.get("email")] + [c.get("email") for c in lead.get("other_contacts") or []],
               [lead.get("phone")], [lead.get("linkedin_url")])
        record_consent(store, ref, "withdrawn", "any", "", "data erased on request")
        from . import audit                              # no name, email or company: only the erased reference
        audit.record(store, getattr(request.state, "user", None), "lead.erase", ref,
                     "erased on request; removed " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))
        return {"ok": True, "erased": lead_id, "removed": counts, "suppressed_hashes": len(hashes)}

    @router.get("/api/compliance/retention")
    def retention():
        """Cold leads (never replied, no consent, no real conversation) older than the retention period, for review.
        Nothing is deleted automatically."""
        days = int(store.read("settings").get("retention_days_cold") or DEFAULT_RETENTION_DAYS)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = []
        for lead in store.read("leads"):
            last = _last_activity(lead)
            if ((lead.get("engagement") or {}).get("replies") or lead.get("consent") or lead.get("stage") in WARM_STAGES
                    or not last or last > cutoff):
                continue
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).days
            rows.append({"id": lead["id"], "company": lead.get("company", ""), "stage": lead.get("stage"),
                         "created_at": lead.get("created_at"), "last_activity_at": last, "idle_days": age})
        return {"retention_days_cold": days, "leads": sorted(rows, key=lambda r: r["last_activity_at"])}

    @router.get("/api/compliance/consent-ledger")
    def consent_ledger(lead_id: str = ""):
        rows = store.read("consent_ledger")
        return [r for r in rows if not lead_id or r.get("lead_id") == lead_id]

    return router
