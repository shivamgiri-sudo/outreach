"""Analysis: how outreach is performing, by source, sector and score band."""
from collections import Counter, defaultdict

from fastapi import APIRouter

TOP_LEADS = 10
INTERESTED_STAGES = ("interested", "whatsapp_active", "call_done", "trial", "customer", "advocate")
CUSTOMER_STAGES = ("customer", "advocate")


def _pct(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def make_router(store, present) -> APIRouter:
    router = APIRouter()

    @router.get("/api/insights")
    def insights():
        leads = [present(lead) for lead in store.read("leads")]
        outbox = store.read("outbox")
        replied = [lead for lead in leads if lead["engagement"]["replies"] > 0]
        interested = [lead for lead in leads if lead["stage"] in INTERESTED_STAGES]
        emailed_ids = {m["lead_id"] for m in outbox}
        replied_after_email = [lead for lead in replied if lead["id"] in emailed_ids]

        by_source = defaultdict(lambda: {"leads": 0, "replied": 0, "interested": 0, "customers": 0})
        by_sector = Counter()
        for lead in leads:
            row = by_source[(lead["source"] or "unknown").split(":")[0]]
            row["leads"] += 1
            row["replied"] += lead["engagement"]["replies"] > 0
            row["interested"] += lead["stage"] in INTERESTED_STAGES
            row["customers"] += lead["stage"] in CUSTOMER_STAGES
            by_sector[lead.get("sector") or "Unknown"] += 1

        return {
            "totals": {"leads": len(leads), "emails_sent": len(outbox),
                       "whatsapp_sent": len(store.read("whatsapp_outbox")),
                       "customers": sum(1 for lead in leads if lead["stage"] in CUSTOMER_STAGES)},
            "outreach": {
                "emailed_leads": len(emailed_ids),
                "reply_rate": _pct(len(replied_after_email), len(emailed_ids)),
                "interested": len(interested),
                "interested_rate": _pct(len(interested), len(emailed_ids)) if emailed_ids else 0.0,
                "unsubscribed": sum(1 for lead in leads if lead["stage"] == "unsubscribed"),
                "unsubscribe_rate": _pct(sum(1 for lead in leads if lead["stage"] == "unsubscribed"), len(emailed_ids)),
            },
            "by_source": [{"source": name, **row} for name, row in sorted(by_source.items(), key=lambda kv: -kv[1]["leads"])],
            "by_sector": [{"sector": name, "leads": n} for name, n in by_sector.most_common()],
            "by_band": {band: sum(1 for lead in leads if lead["band"] == band) for band in ("hot", "warm", "cold")},
            "top_leads": [{"id": lead["id"], "company": lead["company"], "score": lead["score"], "stage": lead["stage"]}
                          for lead in sorted(leads, key=lambda x: -x["score"])[:TOP_LEADS]],
        }

    return router
