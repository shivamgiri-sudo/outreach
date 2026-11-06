"""System health for the owner: when the background jobs last ran and how it went, sending pauses and provider errors,
the send queue, backups, the LinkedIn agent per account, which database is in use and the running version.

Background jobs call `mark(store, job, ok, summary)`; the marks live in settings["system_status"] (never shown with
the other settings).
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

STATUS_KEY = "system_status"
JOBS = ("autorun", "sequence_run", "imap_poll", "reminders", "flows", "capture_sync")
STALE_AFTER = {"autorun": 3 * 900, "imap_poll": 3 * 300, "reminders": 600, "flows": 600}
VERSION_ENVS = ("OUTREACH_GIT_COMMIT", "OUTREACH_VERSION", "GIT_COMMIT", "SOURCE_COMMIT", "COMMIT_SHA")


def _now() -> datetime:
    return datetime.now(timezone.utc)


_MEMORY: dict = {}            # (store id, job) -> latest entry; saved to settings at most every PERSIST_EVERY seconds
PERSIST_EVERY = 600
ALWAYS_SAVE = ("autorun", "sequence_run")


def mark(store, job: str, ok: bool = True, summary: str = "", now: Optional[datetime] = None) -> None:
    """Record one run of a background job. Never raises: health bookkeeping must not stop the job itself.

    The latest run is kept in memory; it is written to the data file when it failed, when it changed from failing to
    working (or back), for the 15-minute sending timer, and otherwise at most every 10 minutes (the minute timers would
    rewrite the whole data file every minute)."""
    try:
        now = now or _now()
        entry = {"at": now.isoformat(timespec="seconds"), "ok": bool(ok), "summary": " ".join(str(summary or "").split())[:300]}
        mem_key = (id(store), job)
        previous = _MEMORY.get(mem_key) or ((store.read("settings").get(STATUS_KEY) or {}).get(job) or {})
        entry.update(last_ok_at=entry["at"] if ok else previous.get("last_ok_at"),
                     failures=0 if ok else int(previous.get("failures") or 0) + 1,
                     saved_at=previous.get("saved_at"))
        saved_age = _age_seconds(previous.get("saved_at"), now)
        persist = (job in ALWAYS_SAVE or not ok or previous.get("ok") is not bool(ok) or saved_age is None or saved_age >= PERSIST_EVERY)
        if persist:
            entry["saved_at"] = entry["at"]
            store.update("settings", lambda s: {**s, STATUS_KEY: {**(s.get(STATUS_KEY) or {}), job: entry}})
        _MEMORY[mem_key] = entry
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        print(f"health mark failed: {type(exc).__name__}")


def run_summary(result: dict) -> str:
    """A short line for a sequence run result (run_due / autorun tick)."""
    if not isinstance(result, dict):
        return ""
    if result.get("skipped"):
        return f"skipped: {result['skipped']}"
    parts = [f"{result.get('sent', 0)} {'practised' if result.get('mode') == 'practice' else 'sent'}"]
    for key in ("failed", "blocked", "needs_check"):
        if result.get(key):
            parts.append(f"{result[key]} {key.replace('_', ' ')}")
    if result.get("paused"):
        parts.append(f"paused: {result['paused']}")
    return ", ".join(parts)


def version() -> dict:
    for name in VERSION_ENVS:
        value = os.environ.get(name, "").strip()
        if value:
            return {"commit": value[:40], "from": name}
    head = Path(__file__).resolve().parent.parent / ".git"
    try:
        if head.is_file():                                     # a git worktree: .git points at the real folder
            head = Path(head.read_text().split("gitdir:", 1)[1].strip())
        ref = (head / "HEAD").read_text().strip()
        if ref.startswith("ref:"):
            target = head / ref[4:].strip()
            commit = target.read_text().strip() if target.exists() else ""
            if not commit:
                common = head / "commondir"
                base = (head / common.read_text().strip()).resolve() if common.exists() else head
                packed = base / "packed-refs"
                ref_path = base / ref[4:].strip()
                commit = ref_path.read_text().strip() if ref_path.exists() else next(
                    (line.split()[0] for line in (packed.read_text().splitlines() if packed.exists() else []) if line.endswith(ref[4:].strip())), "")
        else:
            commit = ref
        return {"commit": commit[:12], "from": "git"} if commit else {"commit": "", "from": ""}
    except (OSError, IndexError, ValueError):
        return {"commit": "", "from": ""}


def _age_seconds(iso: Optional[str], now: datetime) -> Optional[int]:
    try:
        at = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return max(0, int((now - at).total_seconds()))


def _backups(store, now: datetime) -> dict:
    path = getattr(store, "path", None)
    if type(store).__name__ == "MySqlStore" or path is None:
        return {"kind": "database", "latest_at": None, "age_seconds": None, "count": 0,
                "note": "MySQL keeps its own backups; download a full backup under Setup to keep a copy."}
    copies = [p for p in Path(path).parent.glob(Path(path).name + ".*") if p.suffix.lstrip(".").isdigit() or ".pre-restore-" in p.name]
    if not copies:
        return {"kind": "file", "latest_at": None, "age_seconds": None, "count": 0, "note": "No backup copies yet."}
    newest = max(copies, key=lambda p: p.stat().st_mtime)
    at = datetime.fromtimestamp(newest.stat().st_mtime, timezone.utc)
    return {"kind": "file", "latest_at": at.isoformat(timespec="seconds"), "age_seconds": int((now - at).total_seconds()), "count": len(copies),
            "note": "Automatic copies of the data file (the newest few are kept)."}


def _jobs(store, settings: dict, now: datetime) -> dict:
    marks = {**(settings.get(STATUS_KEY) or {}), **{job: e for (sid, job), e in _MEMORY.items() if sid == id(store)}}
    out = {}
    for job in JOBS:
        entry = marks.get(job)
        if not entry:
            out[job] = {"at": None, "ok": None, "summary": "Not run since the server started recording.", "age_seconds": None, "stale": None}
            continue
        age = _age_seconds(entry.get("at"), now)
        limit = STALE_AFTER.get(job)
        out[job] = {**entry, "age_seconds": age, "stale": bool(limit and age is not None and age > limit)}
    return out


def _imap(store) -> dict:
    try:
        from .channels.config import entry_of, record
        state = record(entry_of(store.read("integrations"), "inbound_email"), "imap").get("state") or {}
    except Exception:  # noqa: BLE001 - a missing or old channel entry just means "not set up"
        state = {}
    return {"last_poll_at": state.get("last_poll_at"), "last_uid": state.get("last_uid")}


def build(store, now: Optional[datetime] = None) -> dict:
    now = now or _now()
    settings = store.read("settings")
    leads = store.read("leads")
    outbox = store.read("outbox")
    day_ago = (now - timedelta(days=1)).isoformat(timespec="seconds")
    errors = [m for m in outbox if m.get("status") in ("provider_error", "failed") and (m.get("sent_at") or "") >= day_ago]
    active = [l for l in leads if l.get("sequence") and not (l.get("sequence") or {}).get("stopped")]
    from . import linkedin_agent
    from .senders import email_ready
    try:
        agents = linkedin_agent.accounts(store, now)
        agent = linkedin_agent.status(store, now)
    except Exception:  # noqa: BLE001
        agents, agent = [], {}
    live = bool(settings.get("live_sending"))
    jobs = _jobs(store, settings, now)
    problems = []
    if settings.get("sending_paused"):
        problems.append("Sending is paused: " + str((settings["sending_paused"] or {}).get("reason") or ""))
    if errors:
        problems.append(f"{len(errors)} email(s) failed at the provider in the last 24 hours.")
    for job, entry in jobs.items():
        if entry.get("ok") is False:
            problems.append(f"{job.replace('_', ' ').capitalize()} failed at its last run ({entry.get('summary') or 'no detail'}).")
    if os.environ.get("OUTREACH_AUTORUN") == "1" and jobs["autorun"].get("stale"):
        problems.append("The background timer has not ticked for a while.")
    if getattr(store, "secret_errors", 0):
        problems.append("Some saved keys could not be opened (OUTREACH_SECRET_KEY missing or changed).")
    return {
        "checked_at": now.isoformat(timespec="seconds"), "ok": not problems, "problems": problems,
        "autorun_enabled": os.environ.get("OUTREACH_AUTORUN") == "1", "jobs": jobs,
        "imap": {**_imap(store), "last_run": jobs["imap_poll"]},
        "sending": {"live": live, "email_ready": bool(email_ready(store)), "paused": settings.get("sending_paused"),
                    "changed": settings.get("live_sending_changed"),
                    "provider_errors_24h": len(errors), "last_error": (errors[-1].get("error") or errors[-1].get("status")) if errors else None,
                    "last_error_at": errors[-1].get("sent_at") if errors else None},
        "queue": {"active_sequences": len(active), "in_flight": sum(1 for l in leads if (l.get("sequence") or {}).get("in_flight")),
                  "send_failed": sum(1 for l in leads if (l.get("sequence") or {}).get("stopped") == "send_failed"),
                  "outbox_rows": len(outbox)},
        "backups": _backups(store, now),
        "linkedin_agent": {"enabled": bool((agent.get("config") or {}).get("enabled")), "last_seen": agent.get("last_seen"),
                           "online": agent.get("online"), "accounts": [{k: a.get(k) for k in ("account", "name", "last_seen", "online", "state", "paused")} for a in agents]},
        "store": {"kind": "MySQL" if type(store).__name__ == "MySqlStore" else "JSON file",
                  "secrets_encrypted": bool(getattr(store, "secrets_encrypted", False))},
        "version": version(),
    }
