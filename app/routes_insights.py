"""Analysis: how outreach is performing, by source, sector and score band."""
from collections import Counter, defaultdict

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from . import source_stats, workspaces

TOP_LEADS = 10
INTERESTED_STAGES = ("interested", "whatsapp_active", "call_done", "trial", "customer", "advocate")
CUSTOMER_STAGES = ("customer", "advocate")
CLOSED_STAGES = ("unsubscribed", "disqualified")            # never a "top lead" to chase
NOT_SENT = ("failed", "provider_error", "dry_run")          # outbox rows that never went out (dry_run = practice mode)


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def build_insights(store, present, rng: Optional[dict] = None) -> dict:
    """The numbers on the Insights page (also used by its download). Only leads in the caller's workspace scope.
    With a period (analytics_range.parse_range): leads created in it and emails sent in it."""
    from .analytics_range import _in, _t, public_range
    scoped = workspaces.filter_rows(store.read("leads"))
    if rng is not None:
        scoped = [l for l in scoped if _in(_t(l.get("created_at")), rng["start"], rng["end"])]
    leads = [present(lead) for lead in scoped]
    test_ids = {lead["id"] for lead in leads if lead.get("is_test")}
    leads = source_stats.real(leads)                 # labelled test leads are not counted
    in_scope = {l["id"] for l in workspaces.filter_rows(store.read("leads"))}
    outbox = [m for m in store.read("outbox") if m.get("status") not in NOT_SENT and (m.get("lead_id") in in_scope or workspaces._SCOPE.get() == (None, None))
              and (rng is None or _in(_t(m.get("sent_at")), rng["start"], rng["end"]))]
    replied = [lead for lead in leads if lead["engagement"]["replies"] > 0]
    interested = [lead for lead in leads if lead["stage"] in INTERESTED_STAGES]
    emailed_ids = {m["lead_id"] for m in outbox if m.get("lead_id") not in test_ids}
    replied_after_email = [lead for lead in replied if lead["id"] in emailed_ids]

    by_sector = Counter()
    for lead in leads:
        by_sector[lead.get("sector") or "Unknown"] += 1
    spend = store.read("settings").get("source_spend") or {}

    whatsapp = [m for m in store.read("whatsapp_outbox") if rng is None or _in(_t(m.get("sent_at")), rng["start"], rng["end"])]
    return {
        **({"range": public_range(rng)} if rng is not None else {}),
        "totals": {"leads": len(leads), "emails_sent": len(outbox),
                   "whatsapp_sent": len(whatsapp),
                   "customers": sum(1 for lead in leads if lead["stage"] in CUSTOMER_STAGES)},
        "outreach": {
            "emailed_leads": len(emailed_ids),
            "reply_rate": _pct(len(replied_after_email), len(emailed_ids)),
            "interested": len(interested),
            "interested_rate": _pct(sum(1 for lead in interested if lead["id"] in emailed_ids), len(emailed_ids)),
            "unsubscribed": sum(1 for lead in leads if lead["stage"] == "unsubscribed"),
            "unsubscribe_rate": _pct(sum(1 for lead in leads if lead["stage"] == "unsubscribed" and lead["id"] in emailed_ids),
                                     len(emailed_ids)),
        },
        "by_source": source_stats.with_spend(source_stats.by_source(leads), spend),
        "source_spend": spend,
        "by_sector": [{"sector": name, "leads": n} for name, n in by_sector.most_common()],
        "by_band": {band: sum(1 for lead in leads if lead["band"] == band) for band in ("hot", "warm", "cold")},
        "top_leads": [{"id": lead["id"], "company": lead["company"], "score": lead["score"], "stage": lead["stage"]}
                      for lead in sorted((x for x in leads if x["stage"] not in CLOSED_STAGES),
                                         key=lambda x: -x["score"])[:TOP_LEADS]],
    }


def make_router(store, present) -> APIRouter:
    router = APIRouter()

    @router.get("/api/insights")
    def insights(date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"), days: Optional[int] = None):
        if not (date_from or date_to or days is not None):
            return build_insights(store, present)
        from . import analytics_range
        try:
            rng = analytics_range.parse_range(date_from[:10], date_to[:10], days)
        except analytics_range.RangeError as exc:
            raise HTTPException(400, str(exc)) from None
        return build_insights(store, present, rng)

    @router.put("/api/insights/source-spend")
    def save_source_spend(body: dict):
        """Monthly spend per source id (owner only), to show what a lead and a customer cost."""
        spend = {}
        for key, value in ((body or {}).get("spend") or {}).items():
            try:
                amount = float(value or 0)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Spend for {key} must be a number.") from None
            if amount < 0 or amount > 1e9:
                raise HTTPException(400, f"Spend for {key} is out of range.")
            if amount:
                spend[str(key)[:60]] = round(amount, 2)
        store.update("settings", lambda s: {**s, "source_spend": spend})
        return {"source_spend": spend}

    return router
