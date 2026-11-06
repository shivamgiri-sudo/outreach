"""Find a company's open jobs from the address a person gives (homepage, careers page or one job page).

Order, stopping at the first that yields jobs; every page goes through the robots-respecting, SSRF-guarded fetcher:
  1. schema.org JobPosting on the page itself (JSON-LD, or microdata);
  2. the site's own Careers / Jobs link from that page, read the same way;
  3. the careers site's public sitemap (robots.txt "Sitemap:" lines or /sitemap.xml): career platforms such as SAP
     SuccessFactors publish every job page there; the job title (and city/country when the address carries them)
     comes from the job page address and the date from <lastmod>.
Returns (jobs, note): `note` says in plain words how the jobs were found, or why none were.
"""
import json
import re
from html import unescape
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from .hygiene import business_domain
from .jobs import parse_job_postings

MAX_JOBS = 3000
MAX_CHILD_SITEMAPS = 4
_CAREERS_HREF = re.compile(r'href=["\']([^"\'#]+)["\'][^>]*>(?:\s*<[^>]+>)*\s*([^<]{0,60})', re.I)
_CAREERS_WORDS = re.compile(r"\b(careers?|jobs?|join us|work with us|openings|vacancies|hiring)\b", re.I)
_JOB_PATH = re.compile(r"/(job|jobs|job-detail|jobdetail|careers/job|position|positions|opening|openings|vacancy|vacancies)/", re.I)
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>(?:\s*<lastmod>\s*([^<\s]+)\s*</lastmod>)?", re.I)
_COUNTRY = {"IND": "India", "USA": "United States", "GBR": "United Kingdom", "SGP": "Singapore", "DEU": "Germany", "CAN": "Canada",
            "AUS": "Australia", "ARE": "United Arab Emirates", "FRA": "France", "NLD": "Netherlands", "JPN": "Japan", "PHL": "Philippines",
            "POL": "Poland", "ROU": "Romania", "BRA": "Brazil", "MEX": "Mexico", "ZAF": "South Africa", "SAU": "Saudi Arabia"}


def _microdata(html: str, url: str) -> list:
    """schema.org JobPosting as microdata (itemtype=".../JobPosting" with itemprop fields)."""
    out = []
    for block in re.split(r'(?=<[^>]+itemtype=["\']https?://schema\.org/JobPosting["\'])', html)[1:]:
        block = block[:20000]

        def prop(name):
            m = re.search(rf'itemprop=["\']{name}["\'][^>]*content=["\']([^"\']+)', block) or \
                re.search(rf'itemprop=["\']{name}["\'][^>]*>([^<]+)<', block)
            return unescape(m.group(1)).strip() if m else ""
        title = prop("title")
        if title:
            out.append({"title": title, "company": prop("name") or prop("hiringOrganization"), "company_site": "",
                        "location": prop("addressLocality") or prop("jobLocation"), "date_posted": prop("datePosted"), "url": url})
    return out


def _site_name(html: str, url: str) -> str:
    m = re.search(r'property=["\']og:site_name["\'][^>]*content=["\']([^"\']+)', html, re.I) or \
        re.search(r"<title[^>]*>([^<|\-–]+)", html, re.I)
    name = unescape(m.group(1)).strip() if m else ""
    if not name or len(name) > 60:
        label = (business_domain(url) or urlparse(url).hostname or "").split(".")[0]
        name = label.capitalize()
    return re.sub(r"\s*(careers?|jobs?)\s*$", "", name, flags=re.I).strip() or name


def _from_page(html: str, url: str, company: str, site: str) -> list:
    jobs = parse_job_postings(html, url) or _microdata(html, url)
    for j in jobs:
        j["company"] = j.get("company") or company
        j["company_site"] = j.get("company_site") or site
    return [j for j in jobs if j["title"] and j["company"]]


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _same_company(a: str, b: str) -> bool:
    """careers.wipro.com belongs to wipro.com (a subdomain of the same site)."""
    ha, hb = _host(a), _host(b)
    return bool(ha and hb) and (ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha))


def _careers_link(html: str, url: str) -> Optional[str]:
    """The site's own Careers / Jobs link (same company domain), first one whose address or text says so."""
    for href, text in _CAREERS_HREF.findall(html):
        link = urljoin(url, unescape(href.strip()))
        if not link.startswith(("http://", "https://")) or not _same_company(link, url):
            continue
        if _CAREERS_WORDS.search(urlparse(link).netloc + urlparse(link).path) or _CAREERS_WORDS.search(text):
            if link.rstrip("/") != url.rstrip("/"):
                return link
    return None


_REQ_ID = re.compile(r"[_-]+(?:[A-Z]{1,5}-?)?\d{4,}(?:-\d{1,3})?$")


def _workday_address(parts: list) -> tuple:
    """Workday: /<site>/job/<Country-State-City>/<Title-Words>_<ReqID>; "--" stands for ", " and "---" for " - "."""
    i = max(i for i, p in enumerate(parts) if p.lower() == "job")
    slug = _REQ_ID.sub("", parts[-1]) if len(parts) > i + 1 else ""
    title = re.sub(r"\s+", " ", slug.replace("---", " \0 ").replace("--", ", ").replace("-", " ").replace("\0", "-")).strip(" ,-")
    place = parts[i + 1].split("-") if len(parts) > i + 2 else []
    head = []
    while place and len(head) < 2 and len(place[0]) <= 3 and place[0].isupper() and len(place) > 1:
        head.append(place.pop(0))                       # "US-CA-Santa-Clara" -> "Santa Clara, CA, US"
    location = ", ".join(x for x in [" ".join(place), *reversed(head)] if x)
    return title[:120], location


def _title_from_address(address: str) -> tuple:
    """(title, location) from a job page address, e.g. /job/Pune-SOLUTION-ARCHITECT-L1-IND-411005/1363531455/."""
    path = unquote(urlparse(address).path)
    parts = [p for p in path.split("/") if p]
    if (urlparse(address).hostname or "").endswith("myworkdayjobs.com") and any(p.lower() == "job" for p in parts):
        return _workday_address(parts)
    slug = next((p for p in reversed(parts) if not p.isdigit() and not re.fullmatch(r"[0-9a-f-]{8,}", p, re.I)
                 and p.lower() not in ("job", "jobs", "en", "en_us", "details")), "")
    words = re.split(r"[-_]+", slug)
    city, country = "", ""
    if len(words) >= 3 and words[-1].isdigit():
        words = words[:-1]                                  # trailing post code
    if words and words[-1].upper() in _COUNTRY:
        country = _COUNTRY[words.pop().upper()]
    rest_upper = all(w.isupper() or not w.isalpha() for w in words[1:])
    if len(words) >= 2 and words[0][:1].isupper() and words[0][1:].islower() and rest_upper:
        city = words.pop(0)                                 # SuccessFactors puts the city first ("Pune-SOLUTION-ARCHITECT")
    title = " ".join(w for w in words if w).strip()
    title = title.title() if title.isupper() else title
    location = ", ".join(x for x in (city, country) if x)
    return title[:120], location


def _sitemap_jobs(get: Callable, start: str, company: str, site: str) -> list:
    root = f"{urlparse(start).scheme}://{urlparse(start).netloc}"
    candidates = []
    try:
        robots = get(root + "/robots.txt")
        candidates += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)
    except Exception:
        pass
    candidates.append(root + "/sitemap.xml")
    seen, jobs = set(), []
    queue = list(dict.fromkeys(candidates))
    children = 0
    while queue and len(jobs) < MAX_JOBS:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            xml = get(sm)
        except Exception:
            continue
        if "<sitemapindex" in xml[:2000].lower():
            for loc, _ in _LOC.findall(xml):
                if children < MAX_CHILD_SITEMAPS and re.search(r"job|career|position|vacanc", loc, re.I):
                    queue.append(loc)
                    children += 1
            continue
        for loc, lastmod in _LOC.findall(xml):
            if not _JOB_PATH.search(urlparse(loc).path):
                continue
            title, location = _title_from_address(loc)
            if title:
                jobs.append({"title": title, "company": company, "company_site": site, "location": location,
                             "date_posted": lastmod or "", "url": loc})
            if len(jobs) >= MAX_JOBS:
                break
    return jobs


BOARD_API_BYTES = 6_000_000


def _get_big(get: Callable, url: str) -> str:
    try:
        return get(url, max_bytes=BOARD_API_BYTES)
    except TypeError:                                   # a fetcher without the size option
        return get(url)


def _iso_date(value) -> str:
    return str(value or "")[:10]


def _board_token(url: str, hosts: tuple) -> Optional[tuple]:
    """(api host prefix, board name) for a hosted job board address such as job-boards.greenhouse.io/acme."""
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    for h, region in hosts:
        if host == h:
            token = (parse_qs(parts.query).get("for") or [""])[0] or next((p for p in parts.path.split("/") if p), "")
            if token and token.lower() not in ("embed", "jobs"):
                return region, token
    return None


GREENHOUSE_HOSTS = (("boards.greenhouse.io", ""), ("job-boards.greenhouse.io", ""), ("job-boards.eu.greenhouse.io", "eu."),
                    ("boards.eu.greenhouse.io", "eu."))
LEVER_HOSTS = (("jobs.lever.co", ""), ("jobs.eu.lever.co", "eu."))
_LEVER_POSTING = re.compile(r'<a class="posting-title" href="([^"]+)"[^>]*>\s*<h5[^>]*>([^<]+)</h5>(.*?)</a>', re.S)


def _board_jobs(url: str, get: Callable) -> Optional[tuple]:
    """Greenhouse and Lever boards are drawn by JavaScript; their public job board APIs list the same openings."""
    gh = _board_token(url, GREENHOUSE_HOSTS)
    if gh:
        region, token = gh
        data = json.loads(_get_big(get, f"https://boards-api.{region}greenhouse.io/v1/boards/{token}/jobs"))
        jobs = [{"title": (j.get("title") or "").strip(), "company": j.get("company_name") or token.capitalize(), "company_site": "",
                 "location": ((j.get("location") or {}).get("name") or "").strip(),
                 "date_posted": _iso_date(j.get("first_published") or j.get("updated_at")), "url": j.get("absolute_url") or url}
                for j in data.get("jobs", [])]
        return [j for j in jobs if j["title"]][:MAX_JOBS], f"Read the open jobs from the public Greenhouse job board of {token}."
    lv = _board_token(url, LEVER_HOSTS)
    if lv:                                              # the board page lists every opening (the JSON API adds full texts)
        region, token = lv
        board = f"https://jobs.{region}lever.co/{token}"
        html = _get_big(get, board)
        title = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        company = unescape(title.group(1)).strip() if title and len(title.group(1).strip()) <= 60 else token.capitalize()
        jobs = []
        for href, name, cats in _LEVER_POSTING.findall(html):
            loc = re.search(r'class="[^"]*\blocation\b[^"]*">([^<]+)<', cats)
            jobs.append({"title": unescape(name).strip(), "company": company, "company_site": "",
                         "location": unescape(loc.group(1)).strip() if loc else "", "date_posted": "", "url": href})
        return [j for j in jobs if j["title"]][:MAX_JOBS], f"Read the open jobs from the public Lever job board of {company}."
    return None


def discover_jobs(url: str, get: Callable) -> tuple:
    try:
        board = _board_jobs(url, get)
    except ValueError:                                  # not the JSON we expected: read the page itself below
        board = None
    if board and board[0]:
        return board
    html = get(url)
    company, site = _site_name(html, url), f"https://{business_domain(url) or urlparse(url).hostname}"
    jobs = _from_page(html, url, company, site)
    if jobs:
        return jobs, f"Read the job details published on {urlparse(url).netloc}."
    careers = _careers_link(html, url)
    if careers:
        try:
            chtml = get(careers)
            jobs = _from_page(chtml, careers, company, site)
            if jobs:
                return jobs, f"Followed the Careers link to {urlparse(careers).netloc} and read its job details."
        except Exception:
            pass                                    # the Careers page could not be read: try its job list below
    for start in [u for u in (careers, url) if u]:
        jobs = _sitemap_jobs(get, start, company, site)
        if jobs:
            capped = " (first 3,000)" if len(jobs) >= MAX_JOBS else ""
            return jobs, (f"Read {len(jobs):,} job page addresses{capped} from the public job list (sitemap) of "
                          f"{urlparse(start).netloc}. Titles and places come from the job page addresses.")
    where = f" or {urlparse(careers).netloc}" if careers and urlparse(careers).netloc != urlparse(url).netloc else ""
    return [], (f"No job openings found on {urlparse(url).netloc}{where}: the page publishes no job details and its careers "
                "site has no public job list. Paste the address of one job page, or of a jobs board page (Naukri, "
                "Greenhouse, Lever, Workday ...), or upload a CSV of jobs.")
