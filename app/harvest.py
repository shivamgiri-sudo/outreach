"""Lead harvest jobs: keyword x location searches on Google Places and OpenStreetMap, saved as de-duplicated leads.

A job (collection `harvest_jobs`) holds up to 500 (keyword, location) pairs, the chosen sources, options and a credit cap.
States: draft -> queued -> running -> done | failed, with paused (Pause / Resume) and cancelled (Cancel) on the way.
One background worker runs one job at a time, one small step at a time (one Google page, one tile page, or one
OpenStreetMap query), and saves progress after every step, so Pause, Cancel and a restart pick up exactly where it
stopped: each pair keeps its own cursor (page token, tile number, page count) per source. Between OpenStreetMap calls it
waits as their usage policies ask; between steps it yields, so the 1 GB server stays responsive.

Paid calls (Google Places, 1 credit per request) stop at the job's credit cap, the owner's monthly lookup budget and a client
account's lookup-credit limit (app/prospect_waterfall.py Meter, the same ledger as the other paid lookups).
Which leads were found by which job: `harvest_results` (one row per listing) and `harvest_jobs` on each lead.
"""
import threading
import time
import uuid
import weakref
from datetime import datetime, timezone
from typing import Optional

from . import harvest_leads, harvest_osm, harvest_places
from .campaign_sources import KeyRejected
from .harvest_pairs import DEFAULT_MAX_TILES, MAX_CREDIT_CAP, SOURCES, clamp_tiles, estimate
from .sources.base import RateLimited

JOBS, RESULTS = "harvest_jobs", "harvest_results"
LIVE = ("queued", "running")
OPEN = ("draft", "queued", "running", "paused")
FINAL = ("done", "failed", "cancelled")
MAX_JOBS_KEPT = 200
MAX_RESULTS_PER_JOB, MAX_RESULTS = 5000, 60000
MAX_TRIES = 3
STEP_GAP = 0.3                      # seconds the worker rests between steps
KICK = True                         # tests switch the background worker off and call step() themselves
EST_PER_PAIR = {"google_places": 60, "osm": 30}
_worker = {"running": False}
_worker_lock = threading.Lock()
_last_call = {}                     # "osm" / "nominatim" -> monotonic time of the last call (the politeness gaps)
_ENVS = weakref.WeakKeyDictionary()  # store -> env_for(job), set by the router (tests run steps with it)


def register_env(store, env_for) -> None:
    _ENVS[store] = env_for


def env_of(store):
    return _ENVS.get(store)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def blank_counts() -> dict:
    return {"found": 0, "new": 0, "duplicate": 0, "dnd": 0, "skipped": 0, "closed": 0}


# ---------- jobs ----------
def new_job(pairs: list, sources: list, ws: str, account: str, by: str, name: str = "", deep_crawl: bool = False,
            max_tiles=DEFAULT_MAX_TILES, credit_cap=None) -> dict:
    chosen = [s for s in SOURCES if s in (sources or [])]
    if not chosen:
        raise ValueError("Pick at least one source: Google Places or OpenStreetMap.")
    est = estimate(len(pairs), chosen, max_tiles)
    cap = est["default_cap"] if credit_cap in (None, "") else max(0, min(MAX_CREDIT_CAP, int(credit_cap)))
    return {"id": uuid.uuid4().hex[:10], "name": (name or f"{pairs[0]['keyword']} in {pairs[0]['location']['text']}")[:80],
            "ws": ws or "main", "account": account or "mcn", "by": by[:120], "created_at": now_iso(), "status": "draft",
            "sources": chosen, "options": {"deep_crawl": bool(deep_crawl)}, "max_tiles": clamp_tiles(max_tiles),
            "credit_cap": cap if "google_places" in chosen else 0, "credits_used": 0, "requests": 0, "estimate": est,
            "est_found": len(pairs) * max(EST_PER_PAIR[s] for s in chosen), "cursor": 0, "counts": blank_counts(), "notes": [],
            "pairs": [{"keyword": p["keyword"], "location": p["location"], "status": "pending", "counts": blank_counts(), "cur": {}, "notes": []}
                      for p in pairs]}


def get(store, job_id: str) -> Optional[dict]:
    return next((j for j in store.read(JOBS) if j.get("id") == job_id), None)


def add(store, job: dict) -> dict:
    """Save a new job. Only the newest MAX_JOBS_KEPT finished jobs are kept (open ones always); their results go too."""
    dropped = set()

    def apply(rows: list) -> list:
        finished = [r["id"] for r in rows if r.get("status") not in OPEN]
        extra = len(rows) + 1 - MAX_JOBS_KEPT
        dropped.update(finished[:max(0, extra)])
        return [*(r for r in rows if r["id"] not in dropped), job]
    store.update(JOBS, apply)
    if dropped:
        store.update(RESULTS, lambda rows: [r for r in rows if r.get("job") not in dropped])
    return job


TRANSITIONS = {"start": (("draft",), "queued"), "pause": (("queued", "running"), "paused"),
               "resume": (("paused",), "queued"), "cancel": (OPEN, "cancelled")}


def transition(store, job_id: str, action: str) -> dict:
    """Start / Pause / Resume / Cancel. Raises KeyError (no job) or ValueError (not allowed from its state)."""
    allowed, target = TRANSITIONS[action]
    out = {}

    def apply(rows: list) -> list:
        for r in rows:
            if r.get("id") == job_id:
                if r.get("status") not in allowed:
                    raise ValueError(f"A {r.get('status')} job cannot be {'cancelled' if action == 'cancel' else action + 'ed'}.")
                r["status"], r["updated_at"] = target, now_iso()
                if target == "cancelled":
                    r["finished_at"] = r["updated_at"]
                out["job"] = r
        return rows
    store.update(JOBS, apply)
    if "job" not in out:
        raise KeyError(job_id)
    return out["job"]


def progress(job: dict) -> dict:
    done = sum(1 for p in job["pairs"] if p["status"] in ("done", "failed"))
    return {"pairs_done": done, "pairs_total": len(job["pairs"]), "found": job["counts"]["found"], "est_found": job.get("est_found", 0),
            "pct": round(100 * done / len(job["pairs"])) if job["pairs"] else 100}


def public(job: dict, full: bool = False) -> dict:
    out = {k: v for k, v in job.items() if k != "pairs"}
    out["progress"] = progress(job)
    if full:
        out["pairs"] = [{k: v for k, v in p.items() if k != "cur"} | {"location": p["location"]["text"], "kind": p["location"]["kind"],
                                                                     "tiles": len((p.get("cur", {}).get("google_places") or {}).get("tiles") or [])}
                        for p in job["pairs"]]
    return out


# ---------- one step ----------
def _gate(name: str, gap: float, sleep, clock) -> None:
    wait = _last_call.get(name, -1e9) + gap - clock()
    if wait > 0:
        sleep(wait)
    _last_call[name] = clock()


def _note(target: dict, text: str) -> None:
    if text not in target["notes"]:
        target["notes"] = [*target["notes"], text][-10:]


class Env:
    """What a step needs: HTTP transport, the Google key, OSM contact, the spend meter factory, sleep and clock."""

    def __init__(self, http, key: str = "", contact: str = "", meter=None, sleep=time.sleep, clock=time.monotonic):
        self.http, self.key, self.contact, self.meter, self.sleep, self.clock = http, key, contact, meter, sleep, clock


def _google(job: dict, pair: dict, env: Env) -> list:
    c = pair["cur"].setdefault("google_places", {"phase": "base", "token": "", "page": 0, "tile": 0, "tiles": [], "seen": [], "tries": 0})
    if job.get("google_off"):
        c["phase"] = "done"
        return []
    if job["credits_used"] + 1 > job["credit_cap"]:
        c["phase"] = "done"
        _note(job, f"Credit cap of {job['credit_cap']} reached; Google searches stopped (OpenStreetMap still runs).")
        _note(pair, "Not searched on Google: the job's credit cap was reached.")
        return []
    meter = env.meter() if env.meter else None
    if meter is not None and not meter.can_spend("google_places"):
        c["phase"], job["google_off"] = "done", meter.message()
        _note(job, meter.message())
        return []
    tile = c["tiles"][c["tile"]] if c["phase"] == "tiles" else None
    body = harvest_places.search_body(pair["keyword"], pair["location"], tile, c["token"])
    reply = env.http("POST", harvest_places.URL, harvest_places.headers(env.key), body, None)
    job["credits_used"] += 1
    if meter is not None:
        meter.record("google_places", "search", True, body.get("textQuery", ""))
        meter.flush()
    page = harvest_places.read_page(reply, pair["location"].get("name", ""))
    c["page"], c["tries"] = c["page"] + 1, 0
    if c["phase"] == "base":
        c["seen"] = (c["seen"] + [{"lat": r["lat"], "lng": r["lng"], "pin": r["pin"]} for r in page["rows"]])[:60]
    if page["token"] and c["page"] < harvest_places.MAX_PAGES and page["token"] != c["token"]:
        c["token"] = page["token"]
        return page["rows"]
    c["token"], full = "", page["full"] and c["page"] >= harvest_places.MAX_PAGES
    if c["phase"] == "base":
        tiles = _tiles(job, pair, c, env) if full and job["max_tiles"] > 1 else []
        c.update(phase="tiles" if tiles else "done", tiles=tiles, tile=0, page=0, seen=[] if tiles else c["seen"])
    else:
        c.update(tile=c["tile"] + 1, page=0)
        if c["tile"] >= len(c["tiles"]):
            c["phase"] = "done"
    return page["rows"]


def _tiles(job: dict, pair: dict, c: dict, env: Env) -> list:
    loc, box = pair["location"], None
    if loc["kind"] in ("place", "pin"):
        try:
            _gate("nominatim", harvest_osm.NOMINATIM_GAP, env.sleep, env.clock)
            found = harvest_osm.geocode(env.http, loc, env.contact)
            box = found["box"] if found else None
        except (OSError, ValueError):
            box = None                                   # fall back to the box of the results, then PIN codes
    tiles = harvest_places.plan_tiles(loc, c["seen"], box, job["max_tiles"])
    if tiles:
        _note(pair, f"Google returned a full 60; searched {len(tiles)} smaller areas too.")
    return tiles


def _osm(job: dict, pair: dict, env: Env) -> list:
    c = pair["cur"].setdefault("osm", {"phase": "base", "tries": 0})
    loc, center = pair["location"], None
    if loc["kind"] == "pin":
        _gate("nominatim", harvest_osm.NOMINATIM_GAP, env.sleep, env.clock)
        center = harvest_osm.geocode(env.http, loc, env.contact)
        if not center:
            c["phase"] = "done"
            _note(pair, "OpenStreetMap does not know this PIN code.")
            return []
    try:
        query = harvest_osm.build_query(pair["keyword"], loc, center)
    except ValueError as exc:
        c["phase"] = "done"
        _note(pair, f"OpenStreetMap: {exc}")
        return []
    _gate("osm", harvest_osm.OVERPASS_GAP, env.sleep, env.clock)
    elements = harvest_osm.overpass(env.http, query, env.contact)
    c.update(phase="done", tries=0)
    return [harvest_osm.map_element(e, loc.get("name", "")) for e in elements]


RUNNERS = {"google_places": _google, "osm": _osm}


def _next_source(job: dict, pair: dict) -> Optional[str]:
    return next((s for s in job["sources"] if (pair["cur"].get(s) or {}).get("phase") != "done"), None)


def _save(store, job: dict, results: list) -> dict:
    """Write the job's progress; a Pause or Cancel saved meanwhile keeps its status."""
    out = {}

    def apply(rows: list) -> list:
        for i, r in enumerate(rows):
            if r.get("id") == job["id"]:
                status = r["status"] if r.get("status") in ("paused", "cancelled") and job["status"] in LIVE else job["status"]
                rows[i] = out["job"] = {**job, "status": status, "updated_at": now_iso()}
        return rows
    store.update(JOBS, apply)
    if results:
        def add_results(rows: list) -> list:
            mine = sum(1 for r in rows if r.get("job") == job["id"])
            rows = [*rows, *results[:max(0, MAX_RESULTS_PER_JOB - mine)]]
            return rows[-MAX_RESULTS:]
        store.update(RESULTS, add_results)
    return out.get("job", job)


def runnable(store) -> Optional[dict]:
    live = [j for j in store.read(JOBS) if j.get("status") in LIVE]
    live.sort(key=lambda j: (j.get("status") != "running", j.get("started_at") or j.get("created_at") or ""))
    return live[0] if live else None


def step(store, env_for) -> Optional[dict]:
    """Run one small piece of the next live job and save it. `env_for(job)` gives the job's Env. None when idle."""
    job = runnable(store)
    if job is None:
        return None
    if job["status"] == "queued":
        job.update(status="running", started_at=job.get("started_at") or now_iso())
    if job["cursor"] >= len(job["pairs"]):
        job.update(status="done", finished_at=now_iso())
        return _save(store, job, [])
    i = job["cursor"]
    pair = job["pairs"][i]
    source = _next_source(job, pair)
    if source is None:
        pair["status"] = "done" if pair["status"] != "failed" else "failed"
        job["cursor"] = i + 1
        if job["cursor"] >= len(job["pairs"]):
            job.update(status="done", finished_at=now_iso())
        return _save(store, job, [])
    pair["status"] = "running"
    env = env_for(job)
    results = []
    try:
        rows = RUNNERS[source](job, pair, env)
        job["requests"] = job.get("requests", 0) + 1
        if rows:
            saved = harvest_leads.save_rows(store, job, i, [{**r, "source": source} for r in rows])
            for k, v in saved["counts"].items():
                pair["counts"][k] = pair["counts"].get(k, 0) + v
                job["counts"][k] = job["counts"].get(k, 0) + v
            results = saved["results"]
    except (KeyRejected, PermissionError) as exc:
        job["google_off"] = str(exc) or "Google rejected the key."
        _note(job, f"Google Places stopped: {job['google_off']}")
        pair["cur"].setdefault(source, {})["phase"] = "done"
    except (OSError, RateLimited, ValueError) as exc:
        c = pair["cur"].setdefault(source, {"phase": "base"})
        c["tries"] = c.get("tries", 0) + 1
        if c["tries"] >= MAX_TRIES:
            c["phase"] = "done"
            _note(pair, f"{harvest_leads.SOURCE_LABEL.get(source, source)} failed {MAX_TRIES} times: {str(exc)[:120]}")
            if not pair["counts"].get("found") and _next_source(job, pair) is None:
                pair["status"] = "failed"
    except Exception as exc:                          # noqa: BLE001 - an unexpected bug fails this job, never the server
        job.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:200]}", finished_at=now_iso())
    return _save(store, job, results)


def run_until_idle(store, env_for, max_steps: int = 10000) -> int:
    """Steps until no job is live (tests, and the worker's inner loop)."""
    n = 0
    while n < max_steps and step(store, env_for) is not None:
        n += 1
    return n


# ---------- background worker ----------
def kick(store, env_for, sleep=time.sleep) -> None:
    """Start the worker (one at a time) if a job is live. It stops itself when nothing is left to run."""
    if not KICK:
        return
    with _worker_lock:
        if _worker["running"]:
            return
        _worker["running"] = True

    def loop():
        try:
            while True:
                with _worker_lock:
                    if runnable(store) is None:
                        _worker["running"] = False
                        return
                step(store, env_for)
                sleep(STEP_GAP)
        except Exception as exc:                      # never kill the app; the next kick restarts it
            print(f"harvest worker stopped: {type(exc).__name__}")
            with _worker_lock:
                _worker["running"] = False
    threading.Thread(target=loop, daemon=True, name="lead-harvest").start()
