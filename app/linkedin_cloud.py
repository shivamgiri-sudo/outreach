"""Cloud LinkedIn agent: LinkedIn outreach that runs on THIS server, so nobody has to install anything.

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

from . import extension_api as ext, linkedin_agent as agent
from .activities import iso, now_utc

SETTINGS_KEY, STATE_KEY = "linkedin_cloud", "linkedin_cloud_state"
TOKEN_LABEL = "cloud:"
TOKEN_DAYS = 3650                         # the slot's own token; rotated when the slot is removed
JOB_TTL = 600                             # seconds a login or code job waits for the agent
LOGIN_ATTEMPTS_PER_HOUR = 5
MAX_PASSWORD = 200
ONLINE_SECONDS = 180
STATES = ("starting", "needs_login", "logging_in", "needs_code", "needs_app_approval", "needs_manual", "connected", "paused",
          "stopped", "logging_out", "error")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,63}$")
CODE = re.compile(r"^\d{4,8}$")


def max_slots() -> int:
    try:
        return max(0, min(10, int(os.environ.get("OUTREACH_CLOUD_LI_MAX", "1"))))
    except ValueError:
        return 1


def cloud_dir(data_file) -> Path:
    raw = os.environ.get("OUTREACH_CLOUD_LI_DIR", "").strip()
    return Path(raw) if raw else Path(data_file).resolve().parent / "linkedin-cloud"


class CloudJobs:
    """Login and code jobs, in memory only. One read each; gone after JOB_TTL seconds. Rate limit per slot."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
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
    return any(t.get("id") == token_id for t in ext.tokens(store))


def profile_dir(base: Path, slot_id: str) -> Path:
    return base / "profiles" / slot_id


def write_slots_file(store, base: Path, raw_tokens: dict) -> Path:
    """slots.json for the supervisor: [{slot_id, label, token, enabled, profile_dir, run_seq, wipe_seq, restart_seq}], mode 0600, atomic.
    `raw_tokens` maps slot id -> raw token for tokens made just now; others keep the token already in the file."""
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    path = base / "slots.json"
    try:
        old = {r["slot_id"]: r for r in json.loads(path.read_text(encoding="utf-8")).get("slots", []) if isinstance(r, dict)}
    except (OSError, ValueError, KeyError, TypeError):
        old = {}
    rows = []
    for s in slots(store):
        token = raw_tokens.get(s["id"]) or (old.get(s["id"]) or {}).get("token") or ""
        if not token:
            continue
        rows.append({"slot_id": s["id"], "label": s.get("label", ""), "token": token, "enabled": bool(s.get("enabled")),
                     "profile_dir": str(profile_dir(base, s["id"])), "run_seq": int(s.get("run_seq") or 0),
                     "wipe_seq": int(s.get("wipe_seq") or 0), "restart_seq": int(s.get("restart_seq") or 0)})
    temp = base / f".slots.{secrets.token_hex(4)}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"written_at": iso(now_utc()), "max": max_slots(), "slots": rows}, fh, indent=1)
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


def add_slot(store, base: Path, user: dict, label: str) -> dict:
    label = " ".join(str(label or "").split())[:60] or "LinkedIn account"
    if len(slots(store)) >= max_slots():
        raise ValueError(f"This server runs at most {max_slots()} cloud LinkedIn account(s). Remove one first "
                         "(the limit is OUTREACH_CLOUD_LI_MAX; each account needs its own browser, about 400 MB of memory).")
    sid = secrets.token_hex(5)
    raw, row = ext.create_token(store, user, TOKEN_LABEL + sid, days=TOKEN_DAYS)
    new = {"id": sid, "label": label, "enabled": True, "created_by": user.get("name") or user.get("email") or "owner",
           "created_sub": user.get("sub", ""), "created_at": iso(now_utc()), "token_id": row["id"], "run_seq": 0, "wipe_seq": 0}
    _save_slots(store, lambda rows: [*rows, new])
    set_state(store, sid, "starting", "waiting for the cloud runner to start the browser")
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


def describe(store, base: Path, jobs: CloudJobs) -> dict:
    now = now_utc()
    states, held = _states(store), agent.paused(store)
    sup = supervisor_status(base)
    out = []
    for s in slots(store):
        st = states.get(s["id"]) or {}
        seen = st.get("seen_at")
        online = bool(seen) and (now - datetime.fromisoformat(seen)).total_seconds() < ONLINE_SECONDS
        state = st.get("state") or "starting"
        account = st.get("account", "")
        if not s.get("enabled"):
            state = "stopped"
        elif account and held.get(account) and state == "connected":
            state = "paused"
        elif state not in ("needs_manual", "logging_out") and not online and seen:
            state = "stopped"
        out.append({"id": s["id"], "label": s.get("label", ""), "enabled": bool(s.get("enabled")), "created_by": s.get("created_by", ""),
                    "created_at": s.get("created_at"), "state": state, "note": st.get("note", ""), "account": account,
                    "name": st.get("name", ""), "last_seen": seen, "online": online, "paused": held.get(account) if account else None,
                    "pending": jobs.pending(s["id"]), "runner": (sup["agents"] or {}).get(s["id"])})
    return {"max": max_slots(), "slots": out, "supervisor": {"running": sup["running"], "at": sup["at"]}}
