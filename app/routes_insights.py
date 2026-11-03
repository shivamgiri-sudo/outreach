"""Analysis: how outreach is performing, by source, sector and score band."""
from collections import Counter, defaultdict

from fastapi import APIRouter, HTTPException

from . import source_stats

TOP_LEADS = 10
INTERESTED_STAGES = ("interested", "whatsapp_active", "call_done", "trial", "customer", "advocate")
CUSTOMER_STAGES = ("customer", "advocate")
CLOSED_STAGES = ("unsubscribed", "disqualified")            # never a "top lead" to chase
NOT_SENT = ("failed", "provider_error")                     # outbox rows for attempts that never went out


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def build_insights(store, present) -> dict:
    """The numbers on the Insights page (also used by its download)."""
    leads = [present(lead) for lead in store.read("leads")]
    test_ids = {lead["id"] for lead in leads if lead.get("is_test")}
    leads = source_stats.real(leads)                 # labelled test leads are not counted
    outbox = [m for m in store.read("outbox") if m.get("status") not in NOT_SENT]
    replied = [lead for lead in leads if lead["engagement"]["replies"] > 0]
    interested = [lead for lead in leads if lead["stage"] in INTERESTED_STAGES]
    emailed_ids = {m["lead_id"] for m in outbox if m.get("lead_id") not in test_ids}
    replied_after_email = [lead for lead in replied if lead["id"] in emailed_ids]

    by_sector = Counter()
    for lead in leads:
        by_sector[lead.get("sector") or "Unknown"] += 1
    spend = store.read("settings").get("source_spend") or {}

    return {
        "totals": {"leads": len(leads), "emails_sent": len(outbox),
                   "whatsapp_sent": len(store.read("whatsapp_outbox")),
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
    def insights():
        return build_insights(store, present)

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
