"""Cloud LinkedIn agent: LinkedIn outreach run from the portal, on THIS server or on one office computer.

Where it runs (settings linkedin_cloud.where, see linkedin_runner): "office" (default; LinkedIn blocks our cloud server's
address) runs the agents on the paired office computer (tools/linkedin_runner.mjs), and slots.json then marks every slot
switched off so the server supervisor idles; "server" runs them here through the supervisor.

The owner adds "cloud accounts" (slots) in the portal. For each slot the portal makes an internal pairing token
(extension_api, label "cloud:<slot id>") and writes slots.json for the supervisor (tools/linkedin_cloud_supervisor.mjs),
which runs one headless agent per enabled slot. The agent is an ordinary pairing-token agent with --cloud: when its
browser profile is not logged in, it reports "needs_login" and waits for a login job.

Secrets:
  - the LinkedIn password and the verification code live ONLY in this process's memory (CloudJobs), for at most
    10 minutes, and are deleted the first time the agent reads them. They are never written to the store, slots.json,
    the audit log or any log line, and never sent back to a browser.
  - slots.json holds the raw pairing tokens (the agent's credential for the few /api/extension/ routes). It is written
    atomically with mode 0600 inside a 0700 folder owned by the service user.
"""
import json
import os
import re
import secrets
import shutil
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import extension_api as ext, linkedin_agent as agent, linkedin_runner as runner_mod
from .activities import iso, now_utc

SETTINGS_KEY, STATE_KEY = "linkedin_cloud", "linkedin_cloud_state"
TOKEN_LABEL = "cloud:"
TOKEN_DAYS = 90                           # the slot's own token; rotated when the slot is removed or the office computer changes
RENEW_DAYS = 30                           # renewed (new token, agent restarted with it) once fewer days than this are left
JOB_TTL = 600                             # seconds a login or code job waits for the agent
LOGIN_ATTEMPTS_PER_HOUR = 5
MAX_PASSWORD = 200
ONLINE_SECONDS = 180
STATES = ("starting", "needs_login", "logging_in", "needs_code", "needs_app_approval", "needs_manual", "connected", "paused",
          "stopped", "logging_out", "error")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,63}$")
CODE = re.compile(r"^\d{4,8}$")


def max_slots(store=None) -> int:
    """How many accounts may run: on the server OUTREACH_CLOUD_LI_MAX (default 1, memory), on the office computer
    OUTREACH_OFFICE_LI_MAX (default 3)."""
    office = store is not None and runner_mod.where(store) == "office"
    name, default = ("OUTREACH_OFFICE_LI_MAX", "3") if office else ("OUTREACH_CLOUD_LI_MAX", "1")
    try:
        return max(0, min(10, int(os.environ.get(name, default))))
    except ValueError:
        return int(default)


def cloud_dir(data_file) -> Path:
    raw = os.environ.get("OUTREACH_CLOUD_LI_DIR", "").strip()
    return Path(raw) if raw else Path(data_file).resolve().parent / "linkedin-cloud"


class CloudJobs:
    """Login and code jobs, in memory only. One read each; gone after JOB_TTL seconds. Rate limit per slot."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.started_at = now_utc().replace(microsecond=0)    # memory-only jobs: a "logging_in" set before this lost its job
        self._lock = threading.Lock()
        self._login, self._code = {}, {}
        self._attempts = {}

    def _fresh(self, table: dict, slot_id: str):
        item = table.get(slot_id)
        if item and self._clock() - item[0] > JOB_TTL:
            table.pop(slot_id, None)
            return None
        return item

    def allow_attempt(self, slot_id: str) -> bool:
        with self._lock:
            now = self._clock()
            seen = self._attempts.setdefault(slot_id, deque())
            while seen and now - seen[0] > 3600:
                seen.popleft()
            if len(seen) >= LOGIN_ATTEMPTS_PER_HOUR:
                return False
            seen.append(now)
            return True

    def put_login(self, slot_id: str, email: str, password: str) -> None:
        with self._lock:
            self._login[slot_id] = (self._clock(), {"email": email, "password": password})
            self._code.pop(slot_id, None)

    def take_login(self, slot_id: str) -> Optional[dict]:
        with self._lock:
            item = self._fresh(self._login, slot_id)
            self._login.pop(slot_id, None)
            return item[1] if item else None

    def put_code(self, slot_id: str, code: str) -> None:
        with self._lock:
            self._code[slot_id] = (self._clock(), code)

    def take_code(self, slot_id: str) -> Optional[str]:
        with self._lock:
            item = self._fresh(self._code, slot_id)
            self._code.pop(slot_id, None)
            return item[1] if item else None

    def pending(self, slot_id: str) -> dict:
        with self._lock:
            return {"login": bool(self._fresh(self._login, slot_id)), "code": bool(self._fresh(self._code, slot_id))}

    def drop(self, slot_id: str) -> None:
        with self._lock:
            self._login.pop(slot_id, None)
            self._code.pop(slot_id, None)


# ---------- slots ----------
def slots(store) -> list:
    return [s for s in (store.read("settings").get(SETTINGS_KEY) or {}).get("slots") or [] if isinstance(s, dict) and s.get("id")]


def slot(store, slot_id: str) -> Optional[dict]:
    return next((s for s in slots(store) if s["id"] == slot_id), None)


def _save_slots(store, change) -> None:
    store.update("settings", lambda s: {**s, SETTINGS_KEY: {**(s.get(SETTINGS_KEY) or {}), "slots": change(
        [x for x in (s.get(SETTINGS_KEY) or {}).get("slots") or [] if isinstance(x, dict)])}})


def _states(store) -> dict:
    return store.read("settings").get(STATE_KEY) or {}


def set_state(store, slot_id: str, state: str, note: str = "", account: str = "", name: str = "") -> dict:
    state = state if state in STATES else "error"
    row = {"state": state, "note": " ".join(str(note or "").split())[:300], "at": iso(now_utc())}
    if account:
        row["account"] = agent.norm_account(account)
    if name:
        row["name"] = str(name)[:80]

    def apply(s):
        all_states = dict(s.get(STATE_KEY) or {})
        old = all_states.get(slot_id) or {}
        keep = {k: old[k] for k in ("account", "name") if k in old and state not in ("needs_login", "logging_out")}
        all_states[slot_id] = {**keep, **row, "seen_at": row["at"]}
        return {**s, STATE_KEY: all_states}
    store.update("settings", apply)
    return row


def touch(store, user: Optional[dict]) -> None:
    """The cloud agent asked the portal something: it is alive (seen_at), at most once a minute."""
    found = slot_for_token(store, user)
    if not found:
        return
    seen = (_states(store).get(found["id"]) or {}).get("seen_at")
    if seen and (now_utc() - datetime.fromisoformat(seen)).total_seconds() < 60:
        return
    store.update("settings", lambda s: {**s, STATE_KEY: {**(s.get(STATE_KEY) or {}), found["id"]: {
        **((s.get(STATE_KEY) or {}).get(found["id"]) or {"state": "starting"}), "seen_at": iso(now_utc())}}})


def slot_for_token(store, user: Optional[dict]) -> Optional[dict]:
    if not user or user.get("kind") != "extension" or not user.get("token_id"):
        return None
    return next((s for s in slots(store) if s.get("token_id") == user["token_id"]), None)


def _token_ok(store, token_id: str) -> bool:
    """The slot's token exists, has more than RENEW_DAYS left and no more than TOKEN_DAYS (an old 10-year token is
    replaced too); otherwise sync() replaces it."""
    row = next((t for t in ext.tokens(store) if t.get("id") == token_id), None)
    if row is None:
        return False
    try:
        left = (datetime.fromisoformat(row["expires_at"]) - now_utc()).total_seconds()
        return RENEW_DAYS * 86400 < left <= (TOKEN_DAYS + 1) * 86400
    except (KeyError, TypeError, ValueError):
        return False


def renew_due(store, base: Path) -> bool:
    """Renew slot tokens that are close to expiry (called from the runner's and the cloud agent's regular calls).
    True when something was renewed."""
    if not any(s.get("token_id") and not _token_ok(store, s["token_id"]) for s in slots(store)):
        return False
    sync(store, base)
    return True


def profile_dir(base: Path, slot_id: str) -> Path:
    return base / "profiles" / slot_id


def write_slots_file(store, base: Path, raw_tokens: dict) -> Path:
    """slots.json for the supervisor: [{slot_id, label, token, enabled, profile_dir, run_seq, wipe_seq, restart_seq}], mode 0600, atomic.
    `raw_tokens` maps slot id -> raw token for tokens made just now; others keep the token already in the file.
    In office mode every slot is written switched off (the server supervisor idles); the office runner reads the tokens
    through runner_slots()."""
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    path = base / "slots.json"
    try:
        old = {r["slot_id"]: r for r in json.loads(path.read_text(encoding="utf-8")).get("slots", []) if isinstance(r, dict)}
    except (OSError, ValueError, KeyError, TypeError):
        old = {}
    rows = []
    on_server = runner_mod.where(store) == "server"
    for s in slots(store):
        token = raw_tokens.get(s["id"]) or (old.get(s["id"]) or {}).get("token") or ""
        if not token:
            continue
        rows.append({"slot_id": s["id"], "label": s.get("label", ""), "token": token, "enabled": bool(s.get("enabled")) and on_server,
                     "profile_dir": str(profile_dir(base, s["id"])), "run_seq": int(s.get("run_seq") or 0),
                     "wipe_seq": int(s.get("wipe_seq") or 0), "restart_seq": int(s.get("restart_seq") or 0)})
    temp = base / f".slots.{secrets.token_hex(4)}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"written_at": iso(now_utc()), "where": runner_mod.where(store), "max": max_slots(), "slots": rows}, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temp, path)
    os.chmod(path, 0o600)
    return path


def sync(store, base: Path, owner: Optional[dict] = None) -> None:
    """Make sure every slot has a working token in slots.json (a token revoked elsewhere, or a lost file, gets a new one)."""
    try:
        on_file = {r["slot_id"]: r.get("token") for r in json.loads((base / "slots.json").read_text(encoding="utf-8")).get("slots", [])}
    except (OSError, ValueError, KeyError, TypeError):
        on_file = {}
    fresh = {}
    for s in slots(store):
        if on_file.get(s["id"]) and _token_ok(store, s.get("token_id", "")):
            continue
        who = owner or {"sub": s.get("created_sub") or "local", "name": s.get("created_by") or "owner"}
        if s.get("token_id"):
            ext.revoke_token(store, s["token_id"], {"role": "owner"})
        raw, row = ext.create_token(store, who, TOKEN_LABEL + s["id"], days=TOKEN_DAYS)
        fresh[s["id"]] = raw
        _save_slots(store, lambda rows, sid=s["id"], tid=row["id"]: [{**r, "token_id": tid} if r["id"] == sid else r for r in rows])
    if fresh or slots(store) or (base / "slots.json").exists():
        write_slots_file(store, base, fresh)


def rotate_tokens(store, base: Path) -> None:
    """New tokens for every slot (the old ones stop working at once): used when an office computer is replaced or
    disconnected, so agents still running there cannot keep using LinkedIn accounts."""
    for s in slots(store):
        if s.get("token_id"):
            ext.revoke_token(store, s["token_id"], {"role": "owner"})
    sync(store, base)


def runner_slots(store, base: Path) -> list:
    """What the office runner runs: every slot with its run/wipe/restart counters; the slot token only for slots that are
    switched on in office mode (in server mode none, so the office computer stops its agents without deleting sessions)."""
    def tokens_on_file():
        try:
            return {r["slot_id"]: r.get("token") for r in json.loads((base / "slots.json").read_text(encoding="utf-8")).get("slots", [])}
        except (OSError, ValueError, KeyError, TypeError):
            return {}
    office = runner_mod.where(store) == "office"
    renew_due(store, base)                                  # a token near its expiry: a new one, the runner restarts that agent
    on_file = tokens_on_file()
    if office and any(s.get("enabled") and not on_file.get(s["id"]) for s in slots(store)):
        sync(store, base)                                   # a lost slots.json: new tokens, written again
        on_file = tokens_on_file()
    out = []
    for s in slots(store):
        enabled = office and bool(s.get("enabled")) and bool(on_file.get(s["id"]))
        out.append({"slot_id": s["id"], "label": s.get("label", ""), "enabled": enabled, "token": on_file.get(s["id"]) if enabled else "",
                    "run_seq": int(s.get("run_seq") or 0), "wipe_seq": int(s.get("wipe_seq") or 0), "restart_seq": int(s.get("restart_seq") or 0)})
    return out


def add_slot(store, base: Path, user: dict, label: str) -> dict:
    label = " ".join(str(label or "").split())[:60] or "LinkedIn account"
    limit = max_slots(store)
    if len(slots(store)) >= limit:
        if runner_mod.where(store) == "office":
            raise ValueError(f"The office computer runs at most {limit} LinkedIn account(s). Remove one first (the limit is OUTREACH_OFFICE_LI_MAX).")
        raise ValueError(f"This server runs at most {limit} cloud LinkedIn account(s). Remove one first "
                         "(the limit is OUTREACH_CLOUD_LI_MAX; each account needs its own browser, about 400 MB of memory).")
    sid = secrets.token_hex(5)
    raw, row = ext.create_token(store, user, TOKEN_LABEL + sid, days=TOKEN_DAYS)
    new = {"id": sid, "label": label, "enabled": True, "created_by": user.get("name") or user.get("email") or "owner",
           "created_sub": user.get("sub", ""), "created_at": iso(now_utc()), "token_id": row["id"], "run_seq": 0, "wipe_seq": 0}
    _save_slots(store, lambda rows: [*rows, new])
    set_state(store, sid, "starting", "waiting for the office computer to start the browser" if runner_mod.where(store) == "office"
              else "waiting for the cloud runner to start the browser")
    write_slots_file(store, base, {sid: raw})
    return new


def remove_slot(store, base: Path, slot_id: str, jobs: CloudJobs) -> dict:
    found = slot(store, slot_id)
    if not found:
        raise LookupError("No such cloud account.")
    if found.get("token_id"):
        ext.revoke_token(store, found["token_id"], {"role": "owner"})
    _save_slots(store, lambda rows: [r for r in rows if r["id"] != slot_id])
    store.update("settings", lambda s: {**s, STATE_KEY: {k: v for k, v in (s.get(STATE_KEY) or {}).items() if k != slot_id}})
    jobs.drop(slot_id)
    write_slots_file(store, base, {})
    shutil.rmtree(profile_dir(base, slot_id), ignore_errors=True)       # the supervisor also cleans it once the browser stopped
    return found


def change_slot(store, base: Path, slot_id: str, enabled: Optional[bool] = None, label: Optional[str] = None,
                bump_run: bool = False, bump_wipe: bool = False, bump_restart: bool = False) -> dict:
    if not slot(store, slot_id):
        raise LookupError("No such cloud account.")

    def apply(rows):
        out = []
        for r in rows:
            if r["id"] == slot_id:
                r = {**r}
                if enabled is not None:
                    r["enabled"] = bool(enabled)
                if label is not None:
                    r["label"] = " ".join(str(label).split())[:60] or r["label"]
                if bump_run:
                    r["run_seq"] = int(r.get("run_seq") or 0) + 1
                if bump_wipe:
                    r["wipe_seq"] = int(r.get("wipe_seq") or 0) + 1
                if bump_restart:
                    r["restart_seq"] = int(r.get("restart_seq") or 0) + 1
            out.append(r)
        return out
    _save_slots(store, apply)
    write_slots_file(store, base, {})
    return slot(store, slot_id)


def check_login(email: str, password: str) -> tuple:
    email = str(email or "").strip()
    if len(email) > 254 or not EMAIL.match(email):
        raise ValueError("Enter the email address of the LinkedIn account.")
    if not isinstance(password, str) or not password or len(password) > MAX_PASSWORD:
        raise ValueError(f"Enter the LinkedIn password (at most {MAX_PASSWORD} characters).")
    return email, password


def check_code(code: str) -> str:
    code = re.sub(r"\s", "", str(code or ""))
    if not CODE.match(code):
        raise ValueError("The code is 4 to 8 digits.")
    return code


def supervisor_status(base: Path) -> dict:
    try:
        info = json.loads((base / "supervisor.json").read_text(encoding="utf-8"))
        age = (now_utc() - datetime.fromisoformat(str(info["at"]).replace("Z", "+00:00"))).total_seconds()   # written by Node
        return {"running": age < 90, "at": info["at"], "agents": info.get("agents") or {}}
    except (OSError, ValueError, KeyError, TypeError):
        return {"running": False, "at": None, "agents": {}}


LOGIN_LOST = "The login did not reach the LinkedIn agent (the portal restarted, or the agent never picked it up). Log in again."
LOGIN_STUCK_SECONDS = JOB_TTL + 120          # the agent types a login and settles within minutes; a job lives JOB_TTL


def login_lost(st: dict, jobs: CloudJobs, pending: dict, now: datetime) -> bool:
    """A "logging_in" state whose login / code job is gone: set before this portal process started (jobs are memory-only,
    so a restart drops them), or older than any job can live. Shown as "needs login" so the owner can log in again
    instead of watching "Logging in..." forever."""
    if pending.get("login") or pending.get("code"):
        return False
    try:
        at = datetime.fromisoformat(st.get("at") or "")
    except (TypeError, ValueError):
        return False
    return at < jobs.started_at or (now - at).total_seconds() > LOGIN_STUCK_SECONDS


def describe(store, base: Path, jobs: CloudJobs) -> dict:
    now = now_utc()
    states, held = _states(store), agent.paused(store)
    sup = supervisor_status(base)
    office = runner_mod.where(store) == "office"
    run = runner_mod.describe(store)
    office_agents = {a.get("slot_id"): a.get("state") for a in ((run["runner"] or {}).get("agents") or [])} if run["runner"] and run["runner"]["online"] else {}
    out = []
    for s in slots(store):
        st = states.get(s["id"]) or {}
        seen = st.get("seen_at")
        online = bool(seen) and (now - datetime.fromisoformat(seen)).total_seconds() < ONLINE_SECONDS
        state = st.get("state") or "starting"
        note = st.get("note", "")
        account = st.get("account", "")
        if state == "logging_in" and login_lost(st, jobs, jobs.pending(s["id"]), now):
            state, note = "needs_login", LOGIN_LOST
        if not s.get("enabled"):
            state = "stopped"
        elif account and held.get(account) and state == "connected":
            state = "paused"
        elif state not in ("needs_manual", "logging_out") and not online and seen:
            state = "stopped"
        out.append({"id": s["id"], "label": s.get("label", ""), "enabled": bool(s.get("enabled")), "created_by": s.get("created_by", ""),
                    "created_at": s.get("created_at"), "state": state, "note": note, "account": account,
                    "name": st.get("name", ""), "last_seen": seen, "online": online, "paused": held.get(account) if account else None,
                    "pending": jobs.pending(s["id"]), "runner": office_agents.get(s["id"]) if office else (sup["agents"] or {}).get(s["id"])})
    return {"max": max_slots(store), "slots": out, "supervisor": {"running": sup["running"], "at": sup["at"]}, **run}
