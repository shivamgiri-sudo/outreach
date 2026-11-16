"""Reverse lookup: "who is behind this email address?" for a pasted list or a CSV of up to MAX_ADDRESSES addresses.

Free steps only (never a paid provider), in this order for each address:
  1. our own data, cut to what the caller may see: a lead's email, a lead's other contact, then a prospect database
     person with that exact email (suppressed people and people whose contacts are hidden from the caller are skipped);
  2. the company's own website (company domains only, never Gmail & co.): the team / contact pages read by
     app/enrich.find_public_contacts (app/site_people.py pairs a name with an email on one card) and the home page's
     markup (app/site_markup.py) for the company name and logo. A domain is read once per run, at most MAX_SITE_DOMAINS
     domains per run; a recent hiring-signal read (settings.hiring_signal_cache) is reused for the facts;
  3. a public Gravatar profile (app/gravatar.py; only the hash leaves the app), also for free webmail addresses.
Steps 2-3 are skipped when our own data already names the person.

Confidence: high   = an exact match in our data, or name + email on the same card of the company's website
            medium = a name from a Gravatar profile
            low    = only the company is known
Every row also carries the bulk email checker's status (app/bulk_email_check.check_list: DNS + our history, no SMTP).
Flags: free (webmail), free_mail_company (webmail used by someone our data ties to a company), already_lead.
"""
import csv
import io
from typing import Callable, Optional

from . import bulk_email_check as bec, enrich, gravatar, hiring_signal, site_markup
from .email_checker_data import PERSONAL
from .hygiene import FREE_MAIL_DOMAINS, csv_safe, normalise_email

MAX_ADDRESSES = 2_000
MAX_SITE_DOMAINS = 100
CSV_COLUMNS = ("email", "status", "confidence", "name", "title", "company", "domain", "linkedin", "photo", "flags", "sources")
IMPORT_COLUMNS = ("email", "contact_name", "title", "company", "website", "linkedin_url")
NOT_IMPORTED = ("invalid_syntax", "duplicate", "suppressed", "disposable", "no_mail_server", "bounced", "typo")
FIELDS = ("name", "title", "company", "domain", "linkedin", "photo", "logo")


class TooMany(ValueError):
    """More than MAX_ADDRESSES addresses."""


def read_addresses(text: str) -> list:
    """The addresses of a paste or CSV (as the bulk checker reads them), at most MAX_ADDRESSES."""
    try:
        found = bec.read_addresses(text)
    except bec.TooMany:
        found = None
    if found is None or len(found) > MAX_ADDRESSES:
        raise TooMany(f"Look up at most {MAX_ADDRESSES:,} addresses at a time: split the list.")
    return found


def is_free(domain: str) -> bool:
    return domain in PERSONAL or domain in FREE_MAIL_DOMAINS


# ---------- our own data (built on the request, inside the caller's scope) ----------
def _lead_facts(lead: dict) -> dict:
    return {"company": lead.get("company") or "", "domain": lead.get("domain") or "", "lead_id": lead.get("id")}


def our_index(leads: list) -> dict:
    """{"emails": {email: facts}, "domains": {domain: company}} from the leads the caller may see."""
    emails, domains = {}, {}
    for lead in leads:
        base = _lead_facts(lead)
        if base["domain"] and base["company"]:
            domains.setdefault(base["domain"], base["company"])
        main = normalise_email(lead.get("email"))
        if main:
            emails.setdefault(main, {**base, "name": lead.get("contact_name") or "", "title": lead.get("title") or "",
                                     "linkedin": lead.get("linkedin_url") or "", "photo": lead.get("photo_url") or "",
                                     "step": "lead"})
        for c in lead.get("other_contacts") or []:
            addr = normalise_email(c.get("email")) if isinstance(c, dict) else None
            if addr:
                emails.setdefault(addr, {**base, "company": c.get("company") or base["company"], "name": c.get("contact_name") or "",
                                         "title": c.get("title") or "", "linkedin": c.get("linkedin_url") or "", "photo": "",
                                         "step": "lead_contact"})
    return {"emails": emails, "domains": domains}


def prospect_matches(store, addresses: list, user: Optional[dict]) -> dict:
    """{email: facts} for prospect database people with exactly that email; suppressed / hidden people left out.
    Never raises: a missing or broken prospect database just gives nothing."""
    wanted = sorted({a for a in (normalise_email(x) for x in addresses) if a})
    if not wanted:
        return {}
    try:
        from . import prospect_access, prospect_db
        from .prospect_repo import repo_for
        repo, refs = repo_for(store), {}
        for i in range(0, len(wanted), 500):
            part = ["e:" + a for a in wanted[i:i + 500]]
            for r in repo.q(f"SELECT k, ref FROM pdb_keys WHERE k IN ({', '.join('?' for _ in part)})", part):
                refs.setdefault(r["ref"], r["k"][2:])
        hidden = prospect_access.hidden_people(store, user, list(refs))
        out = {}
        for pid, addr in refs.items():
            person = prospect_db.get_person(store, pid) if pid not in hidden else None
            if not person or person.get("suppressed"):
                continue
            company = person.get("company") or {}
            out[addr] = {"name": person.get("full_name") or "", "title": person.get("title") or "", "company": company.get("name") or "",
                         "domain": company.get("domain") or "", "linkedin": person.get("linkedin_url") or "", "photo": "",
                         "lead_id": None, "step": "prospect_db"}
        return out
    except Exception:                                    # noqa: BLE001 - the prospect DB is optional here
        return {}


# ---------- one company website, read once per run ----------
class Sites:
    """Team / contact pages and home-page markup of each company domain, read at most once per run."""

    def __init__(self, store, fetch: Callable, limit: Optional[int] = None):
        self.store, self.fetch, self.limit, self.done = store, fetch, MAX_SITE_DOMAINS if limit is None else limit, {}

    def read(self, domain: str) -> Optional[dict]:
        if domain in self.done:
            return self.done[domain]
        if len(self.done) >= self.limit:
            return None
        pages = {}

        def caching(url):
            if url not in pages:
                pages[url] = self.fetch(url)
            return pages[url]
        try:
            found = enrich.find_public_contacts(domain, fetch=caching, wide=True)
        except Exception:                                # noqa: BLE001 - a broken site never stops the run
            found = {"people": []}
        cached = hiring_signal.cached(self.store, domain)
        home = f"https://{domain}/"
        facts = (cached or {}).get("facts") or (site_markup.facts(pages[home], home) if pages.get(home) else {})
        self.done[domain] = {"people": {p["email"]: p for p in found.get("people") or []}, "facts": facts or {}}
        return self.done[domain]


# ---------- one address ----------
def _take(row: dict, facts: dict, step: str) -> None:
    gave = []
    for f in FIELDS:
        value = str(facts.get(f) or "").strip()
        if value and not row[f]:
            row[f] = value[:200]
            gave.append(f)
    if gave:
        row["sources"].append({"step": step, "gave": gave, **({"url": facts["url"]} if facts.get("url") else {})})


def lookup_one(store, check_row: dict, known: dict, sites: Sites, gravatar_fetch: Optional[Callable] = None) -> dict:
    raw = check_row["email"]
    addr = normalise_email(raw)
    row = {"email": raw, "status": check_row["status"], "flags": list(check_row.get("flags") or []), "confidence": "",
           "sources": [], "lead_id": None, **{f: "" for f in FIELDS}}
    if not addr or check_row["status"] == "duplicate":
        return row
    domain = addr.rsplit("@", 1)[1]
    free = is_free(domain)
    if free and "free" not in row["flags"]:
        row["flags"].append("free")
    ours = known["ours"]["emails"].get(addr) or known["prospects"].get(addr)
    if ours:
        _take(row, ours, ours["step"])
        row["lead_id"] = ours.get("lead_id")
        if row["name"]:
            row["confidence"] = "high"
    if not row["confidence"]:
        site = None if free else sites.read(domain)
        if site is not None:
            person = site["people"].get(addr)
            if person:                                   # a name on the company's own card: the strongest public signal
                _take(row, {"name": person.get("name"), "title": person.get("title"), "url": person.get("source"),
                            "domain": domain}, "website")
                row["confidence"] = "high"
            facts = site["facts"]
            _take(row, {"company": facts.get("name"), "logo": facts.get("logo_url"),
                        "domain": domain if facts.get("name") else ""}, "site_markup")
        found = gravatar.lookup(store, addr, gravatar_fetch)
        profile = found.get("profile") or {}
        if found.get("status") == "found":
            _take(row, {"name": profile.get("name"), "linkedin": profile.get("linkedin_url"), "photo": profile.get("photo_url"),
                        "url": profile.get("profile_url")}, "gravatar")
            if row["name"] and not row["confidence"]:
                row["confidence"] = "medium"
    if not free and not row["company"] and known["ours"]["domains"].get(domain):
        _take(row, {"company": known["ours"]["domains"][domain], "domain": domain}, "lead_domain")
    if not free and not row["domain"] and row["company"]:
        row["domain"] = domain
    if not row["confidence"] and row["company"]:
        row["confidence"] = "low"
    if free and row["company"]:
        row["flags"].append("free_mail_company")
    return row


def lookup_list(store, addresses: list, known: dict, fetch: Callable, dns: Optional[Callable] = None,
                gravatar_fetch: Optional[Callable] = None, progress: Optional[Callable] = None) -> list:
    """Rows for every address, in the order given. `known` = {"check": bec.known_data(...), "ours": our_index(...),
    "prospects": prospect_matches(...)}."""
    checked = bec.check_list(addresses, known.get("check"), dns)
    sites = Sites(store, fetch)
    rows = []
    for n, check_row in enumerate(checked, 1):
        try:
            rows.append(lookup_one(store, check_row, known, sites, gravatar_fetch))
        except Exception:                                # noqa: BLE001 - one odd address never stops the list
            rows.append({"email": check_row["email"], "status": check_row["status"], "flags": check_row.get("flags") or [],
                         "confidence": "", "sources": [], "lead_id": None, **{f: "" for f in FIELDS}})
        if progress:
            progress(n, len(checked))
    return rows


# ---------- results ----------
def summary(rows: list) -> dict:
    out = {"total": len(rows), "high": 0, "medium": 0, "low": 0, "none": 0, "named": 0, "with_company": 0}
    for r in rows:
        out[r["confidence"] or "none"] += 1
        out["named"] += bool(r["name"])
        out["with_company"] += bool(r["company"])
    return out


def _sources_text(row: dict) -> str:
    return "; ".join(f"{s['step']}: {', '.join(s['gave'])}" for s in row["sources"])


def to_csv(rows: list) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    for r in rows:
        w.writerow([csv_safe(v) for v in (r["email"], r["status"], r["confidence"], r["name"], r["title"], r["company"],
                                          r["domain"], r["linkedin"], r["photo"], " ".join(r["flags"]), _sources_text(r))])
    return out.getvalue()


def importable(rows: list) -> list:
    """Rows worth turning into (or filling) leads: a usable address that names a person or a company."""
    return [r for r in rows if r["status"] not in NOT_IMPORTED and "suppressed" not in r["flags"] and (r["name"] or r["company"])]


def import_csv(rows: list) -> str:
    """A CSV for the smart import (/api/import/commit): it creates new leads and only fills EMPTY fields of existing ones."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(IMPORT_COLUMNS)
    for r in importable(rows):
        website = "" if is_free(r["domain"]) or not r["domain"] else r["domain"]
        w.writerow([csv_safe(v) for v in (normalise_email(r["email"]), r["name"], r["title"], r["company"], website, r["linkedin"])])
    return out.getvalue()


# ---------- background runs (the bulk checker's runner, other work) ----------
class Runner(bec.Runner):
    def _work(self, run: dict, addresses: list, known: dict, tools: dict) -> None:
        def progress(done, total):
            run.update(done=done, total=total)
        try:
            rows = lookup_list(tools["store"], addresses, known, tools["fetch"], tools.get("dns"), tools.get("gravatar"), progress)
            run.update(rows=rows, status="done", done=run["total"], finished_at=bec.now_text(), _at=self.clock())
        except Exception as exc:                         # noqa: BLE001 - the page must always get an answer
            run.update(status="failed", error=f"The lookup stopped because of an unexpected problem: {str(exc)[:200]}",
                       finished_at=bec.now_text(), _at=self.clock())

    @staticmethod
    def view(run: dict) -> dict:
        out = {k: v for k, v in run.items() if k not in ("rows", "owner", "_at")}
        if run["status"] == "done":
            out.update(summary=summary(run["rows"]), rows=run["rows"][:bec.SCREEN_ROWS], shown=min(bec.SCREEN_ROWS, len(run["rows"])),
                       importable=len(importable(run["rows"])))
        return out
