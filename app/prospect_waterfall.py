"""Cost-aware provider waterfall on top of the connected providers (official APIs only, via app/sources).

1. Cache first: before any provider call, the prospect DB is asked for a fresh value (default 90 days, owner setting
   `freshness_days`): a person at that company with a verified/valid email seen or verified within the window. A hit
   costs nothing and is recorded as a cached lookup.
2. Order: providers run by the owner's priority, then cost per lookup (defaults: free first, then the registry's credit
   cost), and the chain stops at the first verified email (app/sources/waterfall.py). Spend is counted only when a
   provider returned a usable value.
3. Ledger: every lookup is a row in pdb_lookups (provider, field, hit/miss, cached, cost, month). `spend_report` gives monthly
   spend and hit rate per provider; `estimate` gives the expected and worst-case cost of a run before it starts.
4. Budget: the owner's monthly cap (`monthly_budget`); a paid lookup that would pass it is not made, with a clear message.
Results are written to the prospect DB with the provider as provenance.
"""
from datetime import datetime, timedelta, timezone

from . import prospect_db as P
from . import prospect_fields as F
from .prospect_repo import repo_for

DEFAULT_HIT_RATE = 0.4
MAX_ENRICH = 50
STATUS_FROM_VERDICT = {"valid": "verified", "risky": "risky", "catch_all": "catch_all", "unknown": "unknown", "plausible": "unknown"}


def _raw(store) -> dict:
    return (store.read("settings") or {}).get("prospect_db") or {}


def budget(store):
    value = _raw(store).get("monthly_budget")
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def provider_config(store) -> list:
    """Every provider that can find or verify emails, with the owner's cost per lookup, priority and on/off switch."""
    from .sources import registry
    saved, over = store.read("integrations"), _raw(store).get("providers") or {}
    out = []
    for spec in registry.SOURCES:
        if not (spec.find_email or spec.find_people or spec.verify):
            continue
        o = over.get(spec.id) or {}
        cost = float(o["cost"]) if o.get("cost") not in (None, "") else float(spec.cost)
        out.append({"id": spec.id, "title": spec.title, "cost": cost, "priority": int(o.get("priority", 0 if cost == 0 else 10)),
                    "enabled": o.get("enabled", True) is not False, "connected": registry.connection(spec, saved)["connected"],
                    "finds": bool(spec.find_email or spec.find_people), "verifies": spec.verify is not None, "free": cost == 0})
    return sorted(out, key=lambda p: (p["priority"], p["cost"], p["id"]))


def order_specs(store, specs: list) -> list:
    """The specs the owner left switched on, by priority, then cost per lookup, then id."""
    cfg = {p["id"]: p for p in provider_config(store)}
    keep = [s for s in specs if cfg.get(s.id, {}).get("enabled", True)]
    return sorted(keep, key=lambda s: (cfg.get(s.id, {}).get("priority", 10), cfg.get(s.id, {}).get("cost", s.cost), s.id))


def month_of(now=None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m")


def month_spend(store, month: str) -> float:
    return float(repo_for(store).scalar("SELECT SUM(cost) FROM pdb_lookups WHERE month = ?", (month,)) or 0)


def budget_message(cap: float) -> str:
    return (f"Monthly lookup budget of {cap:g} reached; paid lookups stopped. Free sources and the prospect database still work. "
            "Raise the budget in Prospect search > Spend, or wait for next month.")


class Meter:
    """Spend guard + ledger for one run. Rows are written once at the end (flush)."""

    def __init__(self, store, now=None):
        self.store, self.now = store, now or datetime.now(timezone.utc)
        self.month = month_of(self.now)
        self.cfg = {p["id"]: p for p in provider_config(store)}
        self.cap = budget(store)
        self.spent = month_spend(store, self.month)
        self.freshness = P.settings(store)["freshness_days"]
        self.rows, self.blocked = [], False

    def cost(self, spec_id: str) -> float:
        return self.cfg.get(spec_id, {}).get("cost", 0.0)

    def can_spend(self, spec_id: str) -> bool:
        c = self.cost(spec_id)
        ok = c == 0 or self.cap is None or self.spent + c <= self.cap + 1e-9
        self.blocked = self.blocked or not ok
        return ok

    def message(self) -> str:
        return budget_message(self.cap or 0)

    def record(self, provider: str, field: str, hit: bool, target: str = "", cached: bool = False) -> None:
        cost = self.cost(provider) if hit and not cached else 0.0
        self.spent += cost
        self.rows.append({"at": self.now.isoformat(timespec="seconds"), "month": self.month, "provider": provider, "field": field,
                          "target": str(target or "")[:200], "hit": 1 if hit else 0, "cached": 1 if cached else 0, "cost": cost})

    def flush(self) -> None:
        if self.rows:
            repo_for(self.store).bulk_insert("pdb_lookups", self.rows)
            self.rows = []

    def cached_email(self, domain: str, full_name: str = "", titles=()):
        return fresh_email(self.store, domain, full_name, self.freshness, titles, self.now)


def fresh_email(store, domain: str, full_name: str = "", days: int = 90, titles=(), now=None):
    """A prospect-DB person at `domain` with a verified/valid email seen within `days`, or None. Named lookups match
    the name; unnamed ones take the best-ranked person (matching the titles when given)."""
    domain = F.clean_domain(domain)
    if not domain:
        return None
    repo = repo_for(store)
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat(timespec="seconds")
    base = ("SELECT id, full_name, title, email, email_status, linkedin_url FROM pdb_people p WHERE p.suppressed = 0 AND p.has_email = 1 "
            "AND p.email_status IN ('verified', 'valid') AND (p.last_verified >= ? OR p.last_seen >= ?)")
    params = [cutoff, cutoff]
    nk = F.name_key(full_name)
    if nk:
        row = repo.q1(base + " AND p.id IN (SELECT ref FROM pdb_keys WHERE k = ?)", [*params, f"n:{nk}|{domain}"])
    else:
        sql, vals = repo.text_match("people", "p", "title", list(titles or []))
        row = repo.q1(base + " AND p.company_domain = ?" + (f" AND {sql}" if sql else "") + " ORDER BY p.rank_score DESC LIMIT 1",
                      [*params, domain, *vals])
    if not row:
        return None
    return {"email": row["email"], "name": row["full_name"], "title": row["title"], "linkedin_url": row["linkedin_url"] or "",
            "confidence": None, "verified": row["email_status"] == "verified", "source": "prospect_db", "source_url": f"prospect_db:{row['id']}",
            "status": row["email_status"]}


def save_hits(store, found: list) -> int:
    """[(hit person, verdict status, domain, company)] -> prospect DB, credited to the provider that found each one."""
    n = 0
    for person, status, domain, company in found:
        if person.get("source") == "prospect_db":
            continue
        res = P.upsert_person(store, {"company_domain": domain, "company_name": company, "full_name": person.get("name"), "title": person.get("title"),
                                      "email": person.get("email"), "phones": [person["phone"]] if person.get("phone") else [],
                                      "linkedin_url": person.get("linkedin_url"), "email_confidence": person.get("confidence"),
                                      "email_status": STATUS_FROM_VERDICT.get(status, "unknown"), "source_url": person.get("source_url")},
                              person.get("source") or "unknown")
        n += 1 if res.get("id") else 0
    return n


# ---------------------------------------------------------------- reporting and estimates
def hit_rates(store, days: int = 90) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = repo_for(store).q("SELECT provider, COUNT(*) AS n, SUM(hit) AS hits FROM pdb_lookups WHERE cached = 0 AND at >= ? GROUP BY provider", (since,))
    return {r["provider"]: (r["hits"] or 0) / r["n"] for r in rows if r["n"] >= 5}


def spend_report(store, months: int = 6) -> dict:
    repo = repo_for(store)
    rows = repo.q("SELECT month, provider, COUNT(*) AS lookups, SUM(hit) AS hits, SUM(cached) AS cached, SUM(cost) AS spend "
                  "FROM pdb_lookups GROUP BY month, provider ORDER BY month DESC, provider")
    keep = sorted({r["month"] for r in rows}, reverse=True)[:months]
    table = []
    for r in rows:
        if r["month"] in keep:
            paid = r["lookups"] - (r["cached"] or 0)
            table.append({**r, "hits": r["hits"] or 0, "cached": r["cached"] or 0, "spend": round(r["spend"] or 0, 4),
                          "hit_rate": round((r["hits"] or 0) / r["lookups"], 3) if r["lookups"] else 0, "paid_lookups": paid})
    this = month_of()
    cap = budget(store)
    spent = month_spend(store, this)
    return {"month": this, "spent": round(spent, 4), "budget": cap, "left": None if cap is None else round(max(0.0, cap - spent), 4),
            "rows": table, "providers": provider_config(store), "freshness_days": P.settings(store)["freshness_days"]}


def estimate(store, targets: list) -> dict:
    """targets = [{domain, full_name}] -> what a run would cost: cache hits are free, the rest walk the providers in order."""
    cfg = [p for p in provider_config(store) if p["enabled"] and p["connected"] and p["finds"]]
    rates = hit_rates(store)
    days = P.settings(store)["freshness_days"]
    cached = sum(1 for t in targets if fresh_email(store, t.get("domain"), t.get("full_name"), days))
    remaining = len(targets) - cached
    expected = worst = 0.0
    reach = 1.0
    for p in cfg:
        worst += p["cost"]
        rate = rates.get(p["id"], DEFAULT_HIT_RATE)
        expected += reach * rate * p["cost"]          # spend is only counted on a hit
        reach *= 1 - rate
    cap = budget(store)
    spent = month_spend(store, month_of())
    left = None if cap is None else max(0.0, cap - spent)
    return {"targets": len(targets), "cached": cached, "to_look_up": remaining, "providers": [p["id"] for p in cfg],
            "expected_cost": round(remaining * expected, 2), "max_cost": round(remaining * max((p["cost"] for p in cfg), default=0), 2),
            "worst_case_calls_cost": round(remaining * worst, 2), "budget": cap, "budget_left": None if left is None else round(left, 2),
            "over_budget": left is not None and remaining * expected > left,
            "note": "" if cfg else "No email finder is connected and switched on. Connect one under Integrations > Lead data."}


def _targets(store, person_ids: list) -> list:
    out = []
    for pid in list(dict.fromkeys(person_ids))[:MAX_ENRICH]:
        p = P.get_person(store, pid)
        if not p or p.get("suppressed") or not p.get("company_domain") or p.get("email_status") in ("verified", "valid"):
            continue
        out.append({"id": pid, "domain": p["company_domain"], "full_name": p.get("full_name") or "", "company": (p.get("company") or {}).get("name") or ""})
    return out


def enrich_prospects(store, person_ids: list, transports: dict, dry_run: bool = True, now=None) -> dict:
    """Find emails for picked prospects without a verified one. dry_run returns the estimate only (spends nothing)."""
    targets = _targets(store, person_ids)
    est = estimate(store, targets)
    if dry_run or not targets:
        return {"dry_run": True, **est} if dry_run else {"dry_run": False, "found": 0, **est}
    from .sources import registry
    from .sources import waterfall as wf
    chosen = [p["id"] for p in provider_config(store) if p["enabled"] and p["connected"]]
    finders = [s for s in registry.plan("find_email", chosen)]
    finders += [s for s in registry.plan("discover_people", chosen) if s not in finders and s.find_people]
    finders = order_specs(store, finders)
    verifiers = order_specs(store, registry.plan("verify_email", chosen))
    report = {"source_credits": 0.0, "errors": []}
    campaign = {"id": "prospect-search", "name": "Prospect search", "enrich": {"extra": chosen, "extra_params": {}}, "rules": {"credit_cap": 100000}}
    run = wf.Run(store, campaign, transports, report, finders, verifiers, now or datetime.now(timezone.utc))
    taken, suppressed = set(), set(store.read("suppressed"))
    found = 0
    for t in targets:
        lead = {"domain": t["domain"], "company": t["company"], "contact_name": t["full_name"]}
        candidate, _extras = wf.find_one(run, lead, [], taken, suppressed)
        if candidate:
            found += 1
            p = candidate["person"]
            if p.get("source") == "prospect_db":
                continue
            P.upsert_person(store, {"company_domain": t["domain"], "full_name": t["full_name"] or p.get("name"), "email": p["email"],
                                    "title": p.get("title"), "linkedin_url": p.get("linkedin_url"),
                                    "email_status": STATUS_FROM_VERDICT.get(candidate["status"], "unknown")}, p["source"])
    spent = sum(r["cost"] for r in run.meter.rows)
    run.meter.flush()
    run.ctx.cache.flush()
    return {"dry_run": False, "found": found, "errors": [{"source": k, "message": v} for k, v in run.errors.items()],
            "spent": round(spent, 4), **est}
