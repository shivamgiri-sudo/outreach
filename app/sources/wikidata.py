"""Wikidata SPARQL endpoint: Indian companies with a website, size and industry. Free, no key, CC0 data.
Docs https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service/queries ; user-agent policy
https://meta.wikimedia.org/wiki/User-Agent_policy (a descriptive agent is required).
GET https://query.wikidata.org/sparql?query=...&format=json -> {results: {bindings: [{cLabel, site, emp, indLabel, hqLabel: {value}}]}}.
Coverage is limited to notable companies, so this is a seed list of larger employers, not a full directory."""
import re

from ..campaign_sources import SIZE_BUCKETS, own_site
from .base import SourceSpec, company_row, field
from .osm_overpass import agent

URL = "https://query.wikidata.org/sparql"
WORD_RE = re.compile(r"[^a-z0-9 ]")


def build_query(words: list, limit: int) -> str:
    cleaned = [WORD_RE.sub("", w.lower()).strip() for w in words]
    cleaned = [w for w in cleaned if w][:5]
    flt = ("FILTER(" + " || ".join(f'CONTAINS(LCASE(STR(?indLabel)), "{w}")' for w in cleaned) + ")") if cleaned else ""
    return ("SELECT ?c ?cLabel ?site ?emp ?indLabel ?hqLabel WHERE { VALUES ?type { wd:Q4830453 wd:Q891723 wd:Q783794 } "
            "?c wdt:P31 ?type; wdt:P17 wd:Q668; wdt:P856 ?site. OPTIONAL { ?c wdt:P1128 ?emp } OPTIONAL { ?c wdt:P452 ?ind } "
            "OPTIONAL { ?c wdt:P159 ?hq } SERVICE wikibase:label { bd:serviceParam wikibase:language \"en\". } "
            f"{flt} }} LIMIT {int(limit) * 3}")


def _bucket_ok(emp, buckets) -> bool:
    if not buckets or emp is None:
        return True
    for b in buckets:
        lo, hi = (int(x) for x in SIZE_BUCKETS[b].split(","))
        if lo <= emp <= hi:
            return True
    return False


def discover(ctx, target, limit):
    words = list(target.get("industries") or []) + list(target.get("keywords") or [])
    query = build_query(words, limit)
    bindings = ctx.cached(f"q:{query}", lambda: ((ctx.get(URL, headers={**agent(ctx), "Accept": "application/sparql-results+json"},
                                                      params={"query": query, "format": "json"}).get("results") or {}).get("bindings") or []))
    rows = []
    for b in bindings:
        val = lambda k: str((b.get(k) or {}).get("value") or "")
        site, domain = own_site(val("site"))
        emp = int(float(val("emp"))) if val("emp").replace(".", "", 1).isdigit() else None
        if not domain or not _bucket_ok(emp, [x for x in target.get("company_size_buckets") or [] if x in SIZE_BUCKETS]):
            continue
        row = company_row("wikidata", val("cLabel"), website=site, city=val("hqLabel"), source_url=val("c") or "https://www.wikidata.org",
                          company_size=emp)
        row["domain"] = domain
        rows.append(row)
    return {"rows": rows[:limit], "requests": ctx.requests}


def test(ctx):
    ctx.get(URL, headers={**agent(ctx), "Accept": "application/sparql-results+json"},
            params={"query": "SELECT ?x WHERE { wd:Q668 wdt:P31 ?x } LIMIT 1", "format": "json"})
    return {"ok": True, "message": "Wikidata's query service answered."}


SPEC = SourceSpec(
    "wikidata", "Wikidata (notable Indian companies)", ("discover_companies",), "Company discovery",
    "Larger, well-known Indian companies with website, employee count and industry. Free seed list, not a full directory.",
    "https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service",
    "CC0 data. The public endpoint has query time limits and a User-Agent policy; the agent names this app.",
    ["Nothing is required. Optionally add your contact email for the User-Agent."],
    fields=[field("contact", "Your contact email (optional, sent in the User-Agent)", False, False, "you@yourcompany.in")],
    keyless=True, rate_per_min=10, free_note="Free, no key.", cost_note="Free; 1 query per run.", discover=discover, test=test)
