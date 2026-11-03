"""Per-source numbers for the Dashboard ("where leads come from") and Insights. Labelled test leads are never counted."""
from datetime import datetime, timedelta, timezone

from .sequences import IST
from .source_names import canonical, is_inbound_id, kind_of, label_of, touches

INTERESTED = ("interested", "whatsapp_active", "call_done", "trial", "customer", "advocate")
CUSTOMERS = ("customer", "advocate")


def real(leads: list) -> list:
    return [lead for lead in leads if not lead.get("is_test")]


def source_key(lead: dict) -> str:
    """The canonical id; for a source the registry does not know, the old name before any ':detail' so nothing is lumped together."""
    sid = lead.get("source_id") or canonical(lead.get("source"))[0]
    if sid == "unknown":
        head = str(lead.get("source") or "").split(":")[0].strip()
        return head or "unknown"
    return sid


def _age_ok(stamp: str, since: datetime) -> bool:
    try:
        return datetime.fromisoformat(stamp) >= since
    except (TypeError, ValueError):
        return False


def by_source(leads: list, now: datetime = None, band_of=None) -> list:
    """Rows sorted by size: leads, new_7d, hot, interested, customers, inbound, plus a stage count and 30-day figures."""
    now = now or datetime.now(timezone.utc)
    week, month = now - timedelta(days=7), now - timedelta(days=30)
    rows = {}
    for lead in real(leads):
        key = source_key(lead)
        row = rows.setdefault(key, {"source": key, "label": label_of(key), "kind": kind_of(key), "leads": 0, "new_7d": 0, "hot": 0,
                                    "replied": 0, "interested": 0, "customers": 0, "inbound": 0, "stages": {}, "leads_30d": 0,
                                    "customers_30d": 0})
        row["leads"] += 1
        row["new_7d"] += _age_ok(lead.get("created_at"), week)
        row["hot"] += (lead.get("band") == "hot") if band_of is None else (band_of(lead) == "hot")
        row["replied"] += (lead.get("engagement") or {}).get("replies", 0) > 0
        row["interested"] += lead.get("stage") in INTERESTED
        row["customers"] += lead.get("stage") in CUSTOMERS
        row["inbound"] += bool(lead.get("inbound"))
        row["stages"][lead.get("stage")] = row["stages"].get(lead.get("stage"), 0) + 1
        if _age_ok(lead.get("created_at"), month):
            row["leads_30d"] += 1
            row["customers_30d"] += lead.get("stage") in CUSTOMERS
    return sorted(rows.values(), key=lambda r: (-r["leads"], r["label"]))


def inbound_counts(leads: list, now: datetime = None) -> dict:
    """Captures (a lead first seen or seen again through an inbound source) today in IST and in the last 7 days."""
    now = now or datetime.now(timezone.utc)
    week, today = now - timedelta(days=7), now.astimezone(IST).date()
    seven = day = 0
    for lead in real(leads):
        if not lead.get("inbound"):
            continue
        for touch in touches(lead):
            if not is_inbound_id(touch.get("id", "")):
                continue
            if _age_ok(touch.get("at"), week):
                seven += 1
            try:
                day += datetime.fromisoformat(touch["at"]).astimezone(IST).date() == today
            except (KeyError, TypeError, ValueError):
                pass
    return {"inbound_7d": seven, "inbound_today": day}


def with_spend(rows: list, spend: dict) -> list:
    """Adds the monthly spend the owner typed and what a lead and a customer cost (last 30 days of leads)."""
    out = []
    for row in rows:
        amount = float((spend or {}).get(row["source"]) or 0)
        out.append({**row, "spend": amount or None,
                    "cost_per_lead": round(amount / row["leads_30d"], 2) if amount and row["leads_30d"] else None,
                    "cost_per_customer": round(amount / row["customers_30d"], 2) if amount and row["customers_30d"] else None})
    return out
