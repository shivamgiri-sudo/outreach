"""Hiring now on a lead, from the jobs its company publishes on its own website, plus company facts from the same pages.

Built on the existing job readers: app/job_discover.discover_jobs (schema.org JobPosting JSON-LD / microdata on the page,
the site's own Careers link, then its public job sitemap; Greenhouse / Lever boards through their public APIs) and
app/jobs.py. Every page goes through the app's fetcher (app/enrich.polite_fetch -> app/scraper.fetch_page: robots.txt
respected, SSRF guard, one-second polite delay). The home page is fetched once and read for both jobs and company facts
(app/site_markup.py). Job-board and login sites (Indeed, Glassdoor, LinkedIn, Naukri ...) are never followed; hosted
career pages (Greenhouse, Lever, Workday, SmartRecruiters ...) are followed only when the company's own page links to them.

Per domain at most MAX_PAGES pages; a domain is not read again within CACHE_DAYS (settings.hiring_signal_cache).
Lead fields: hiring_signal = {open_roles, titles (top 10), locations (top 10), latest_posted, sources, checked_at, note};
open_roles / max_job_age_days / role are also set when roles are found (the same fields CSV / page job ingest sets, so
scoring sees them); company facts fill ONLY empty fields (site_markup.changes_for) with provenance "site-markup".
"""
import re
import threading
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

from . import site_markup
from .job_discover import discover_jobs
from .jobs import SHARED_HOSTS, is_job_board, job_age_days
from .lead_model import event, now_iso

CACHE_DAYS = 7
CACHE_KEY = "hiring_signal_cache"
MAX_CACHED = 1000
MAX_PAGES = 8                    # pages read per domain (home, careers, sitemap pieces, a hosted job board)
MAX_ATS_LINKS = 2
MAX_TITLES = 10
MAX_SOURCES = 5
BATCH_MAX = 50                   # domains per batch run
SINGLE_MIN_SECONDS = 3600        # "refresh" of one lead re-reads the site at most hourly
SOCIAL_HOSTS = ("facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com")
ATS_HOSTS = tuple(h for h in SHARED_HOSTS if h not in SOCIAL_HOSTS)
_HREF = re.compile(r'href=["\']([^"\'#\s]+)["\']', re.I)
LOGIN_WORDS = re.compile(r"/(login|signin|sign-in|account|auth)\b", re.I)


class PageBudget(OSError):
    """MAX_PAGES pages were read for this domain."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


class Pages:
    """The fetcher for one domain: each address read once (the home page serves jobs and facts), MAX_PAGES at most."""

    def __init__(self, fetch: Callable, limit: int = MAX_PAGES):
        self.fetch, self.limit, self.cache, self.read = fetch, limit, {}, []

    def __call__(self, url: str, **kw) -> str:
        if url in self.cache:
            return self.cache[url]
        if is_job_board(_host(url)):
            raise PermissionError("job boards are not read")
        if len(self.read) >= self.limit:
            raise PageBudget("page limit for this company reached")
        self.read.append(url)
        try:
            html = self.fetch(url, **kw) if kw else self.fetch(url)
        except TypeError:
            html = self.fetch(url)
        self.cache[url] = html
        return html


def ats_links(html: str, base: str) -> list:
    """Hosted career-site links (Greenhouse, Lever, Workday ...) found on the company's own page; never job boards,
    social sites or login pages."""
    out = []
    for href in _HREF.findall(html or ""):
        link = urljoin(base, unescape(href))
        host = _host(link)
        if not link.startswith(("http://", "https://")) or is_job_board(host) or LOGIN_WORDS.search(urlparse(link).path):
            continue
        if any(host == h or host.endswith("." + h) for h in ATS_HOSTS) and link not in out:
            out.append(link)
    return out[:MAX_ATS_LINKS]


def summarise(jobs: list, now: datetime, note: str = "") -> dict:
    seen, titles, places, dates, sources = set(), [], [], [], []
    for j in jobs:
        title, place = str(j.get("title") or "").strip(), str(j.get("location") or "").strip()
        key = (title.lower(), place.lower())
        if not title or key in seen:
            continue
        seen.add(key)
        if title not in titles:
            titles.append(title)
        if place and place not in places:
            places.append(place)
        posted = str(j.get("date_posted") or "")[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", posted):
            dates.append(posted)
        if j.get("url") and j["url"] not in sources:
            sources.append(j["url"])
    return {"open_roles": len(seen), "titles": titles[:MAX_TITLES], "locations": places[:MAX_TITLES],
            "latest_posted": max(dates, default=""), "sources": sources[:MAX_SOURCES],
            "max_job_age_days": max((job_age_days(d, now) for d in dates), default=0),
            "checked_at": now.isoformat(timespec="seconds"), "note": note[:300]}


def check_domain(domain: str, fetch: Callable, now: Optional[datetime] = None) -> dict:
    """{hiring, facts, pages, error} for one company domain. Network problems and robots.txt refusals become `error`
    (and an empty result), never an exception."""
    now = now or now_utc()
    pages = Pages(fetch)
    home = f"https://{domain}/"
    try:
        html = pages(home)
    except PermissionError as exc:
        return {"hiring": summarise([], now, str(exc)), "facts": {}, "pages": pages.read, "error": str(exc) or "not allowed"}
    except (OSError, ValueError) as exc:
        return {"hiring": summarise([], now, "the website could not be read"), "facts": {}, "pages": pages.read,
                "error": str(exc)[:200] or "the website could not be read"}
    facts = site_markup.facts(html, home)
    jobs, note = [], ""
    try:
        jobs, note = discover_jobs(home, pages)
    except (OSError, ValueError, PermissionError) as exc:
        note = f"stopped early: {str(exc)[:120]}"
    if not jobs:
        for link in ats_links(" ".join(pages.cache.values()), home):
            try:
                jobs, note = discover_jobs(link, pages)
            except (OSError, ValueError, PermissionError):
                continue
            if jobs:
                break
    return {"hiring": summarise(jobs, now, note), "facts": facts, "pages": pages.read, "error": ""}


# ---------- cache ----------
def cached(store, domain: str, now: Optional[datetime] = None, max_age: timedelta = timedelta(days=CACHE_DAYS)) -> Optional[dict]:
    row = (store.read("settings").get(CACHE_KEY) or {}).get(domain)
    if not row:
        return None
    try:
        age = (now or now_utc()) - datetime.fromisoformat(row["checked_at"])
    except (KeyError, ValueError):
        return None
    return row if age < max_age else None


def _remember(store, domain: str, result: dict) -> None:
    row = {"checked_at": result["hiring"]["checked_at"], "hiring": result["hiring"], "facts": result["facts"], "error": result["error"]}

    def keep(s):
        rows = {k: v for k, v in (s.get(CACHE_KEY) or {}).items() if k != domain}
        newest = dict(sorted(rows.items(), key=lambda kv: kv[1].get("checked_at", ""))[-(MAX_CACHED - 1):])
        return {**s, CACHE_KEY: {**newest, domain: row}}
    store.update("settings", keep)


def lookup(store, domain: str, fetch: Callable, now: Optional[datetime] = None, max_age: timedelta = timedelta(days=CACHE_DAYS)) -> dict:
    """The cached result when younger than max_age, else a fresh read (then cached)."""
    hit = cached(store, domain, now, max_age)
    if hit:
        return {**hit, "from_cache": True}
    result = check_domain(domain, fetch, now)
    _remember(store, domain, result)
    return {"checked_at": result["hiring"]["checked_at"], **result, "from_cache": False}


# ---------- writing onto leads ----------
def apply(store, lead_id: str, result: dict, job_event: bool = True) -> dict:
    """Save hiring_signal (and the job fields) and fill empty company fields on the latest copy of the lead.
    job_event=False leaves out the "Hiring now" timeline line (app/recrawl.py writes its own change lines)."""
    out = {"filled": []}
    erased = set(store.read("suppressed_hashes"))

    def change(lead: dict):
        hiring = {k: v for k, v in result["hiring"].items() if k != "max_job_age_days"}
        before = lead.get("hiring_signal") or {}
        lead["hiring_signal"] = hiring
        n = hiring["open_roles"]
        events = []
        if n:
            lead.update(open_roles=n, max_job_age_days=result["hiring"].get("max_job_age_days", 0),
                        role=lead.get("role") or " and ".join(hiring["titles"][:2]))
            if job_event and n != before.get("open_roles"):
                events.append(event("jobs", f"Hiring now: {n} open role(s) on the company's website"))
        elif before.get("open_roles") and lead.get("open_roles") == before.get("open_roles"):
            lead["open_roles"] = 0                      # the roles we counted earlier are gone from the site
        changes, filled = site_markup.changes_for(lead, result.get("facts") or {}, erased)
        lead.update(changes)
        lead["company_facts"] = {"found": result.get("facts") or {}, "checked_at": result.get("checked_at") or hiring["checked_at"],
                                 "source": site_markup.SOURCE, "filled": filled}
        if filled:
            events.append(event("enrichment", "Filled from the company's website markup: " + ", ".join(f.replace("_", " ") for f in filled)))
        out["filled"] = filled
        lead["timeline"] = [*lead.get("timeline", []), *events]
        return lead
    saved = store.update_lead(lead_id, change)
    return {"lead": saved, **out}


def refresh_lead(store, lead: dict, fetch: Callable, now: Optional[datetime] = None) -> dict:
    domain = lead.get("domain")
    if not domain:
        raise ValueError("This lead has no company website to read.")
    result = lookup(store, domain, fetch, now, max_age=timedelta(seconds=SINGLE_MIN_SECONDS))
    saved = apply(store, lead["id"], result)
    return {"hiring_signal": (saved["lead"] or {}).get("hiring_signal"), "company_facts": (saved["lead"] or {}).get("company_facts"),
            "filled": saved["filled"], "from_cache": result["from_cache"], "error": result.get("error") or ""}


# ---------- bounded batch ----------
def pick(leads: list, store, limit: int, now: Optional[datetime] = None) -> list:
    """[(domain, [lead ids])] for up to `limit` domains whose leads were not checked within CACHE_DAYS (closed leads skipped)."""
    now = now or now_utc()
    since = (now - timedelta(days=CACHE_DAYS)).isoformat(timespec="seconds")
    by_domain: dict = {}
    for lead in leads:
        domain = lead.get("domain")
        if not domain or lead.get("stage") in ("unsubscribed", "disqualified"):
            continue
        if (lead.get("hiring_signal") or {}).get("checked_at", "") >= since:
            continue
        by_domain.setdefault(domain, []).append(lead["id"])
    return list(by_domain.items())[:max(1, min(int(limit), BATCH_MAX))]


class Batch:
    """One batch at a time, in a background thread, with progress."""

    def __init__(self, store, fetch: Callable):
        self.store, self.fetch, self._lock = store, fetch, threading.Lock()
        self.state = {"status": "idle"}

    def start(self, work: list, background: bool = True) -> dict:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A hiring check is already running. Wait for it to finish.")
        self.state = {"status": "running", "done": 0, "total": len(work), "hiring": 0, "filled": 0, "errors": 0,
                      "started_at": now_iso(), "finished_at": ""}
        if background:
            threading.Thread(target=self._run, args=(work,), daemon=True).start()
        else:
            self._run(work)
        return dict(self.state)

    def _run(self, work: list) -> None:
        try:
            for domain, ids in work:
                try:
                    result = lookup(self.store, domain, self.fetch)
                    for lead_id in ids:
                        saved = apply(self.store, lead_id, result)
                        self.state["filled"] += len(saved["filled"])
                    self.state["hiring"] += bool(result["hiring"]["open_roles"])
                    self.state["errors"] += bool(result.get("error"))
                except Exception:
                    self.state["errors"] += 1
                self.state["done"] += 1
            self.state["status"] = "done"
        finally:
            self.state["finished_at"] = now_iso()
            if self.state["status"] == "running":
                self.state["status"] = "failed"
            self._lock.release()
