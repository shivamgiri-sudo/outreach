"""Office runner: the cloud LinkedIn agents run on ONE always-on office computer instead of this server.

Why: LinkedIn (Cloudflare) blocks our cloud server's address before any login, so the browser has to run on an ordinary
office internet connection. Everything is still done from the portal (add an account, log in, codes, switch on/off,
status); the office computer only hosts the browser. Nothing here hides the automation: the office computer's normal
connection is the whole point.

Settings:
  settings["linkedin_cloud"]["where"]  "office" (default) or "server". In office mode slots.json marks every slot
                                       disabled, so the server supervisor idles, and the runner gets the enabled slots.
  settings["linkedin_runners"]         runner records {id, label, created_by, created_at, last_seen, hostname, os, version,
                                       status: active|revoked, hash (SHA-256 of the runner token; never the token)}.
  settings["linkedin_runner_pairing"]  the one pending pairing code {hash, expires_at, created_by, replace, failures,
                                       failed_by_ip}. `hash` is HMAC-SHA256 keyed with the server secret ("v2:" prefix), so a
                                       leaked settings row cannot be brute-forced offline.

Pairing: the owner makes a one-time 12-character code shown as XXXX-XXXX-XXXX (60 bits; 15 minutes, single use). Wrong
guesses are counted per address: an address with MAX_FAILED wrong guesses is refused until the code expires, but the code
itself stays valid, so a stranger cannot cancel the owner's code (and the pairing route is rate limited per address). The office computer swaps it for a long-lived runner token
("mcnr_..."), which may call only RUNNER_PATHS. Only one runner is active at a time: pairing a new one revokes the old
and rotates the slot tokens, so the old computer's agents stop at once (two machines never drive one LinkedIn account).

Per client account (app/linkedin_scope.py): each account pairs its own office computer. A runner row carries account_id
(missing = "mcn"), the "only one active" rule and the token rotation hold per account, and a runner gets only its own
account's slots. Mas Callnet's pending code stays in settings["linkedin_runner_pairing"]; another account's is in
settings["linkedin_runner_pairing_by_account"][account_id]. A code is matched against every pending code (HMAC), so the
office computer never says which account it is for.
"""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional

from .activities import iso, now_utc

TOKEN_PREFIX = "mcnr_"
SETTINGS_KEY, PAIR_KEY, PAIR_BY = "linkedin_runners", "linkedin_runner_pairing", "linkedin_runner_pairing_by_account"
MCN = "mcn"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0/O, 1/I: read aloud or typed from a screen
CODE_LEN = 12
CODE_TTL = 15 * 60
MAX_FAILED = 10                                         # wrong codes from one address before that address is refused
MAX_TRACKED_IPS = 500
ONLINE_SECONDS = 90
KEEP_REVOKED = 5
WHERE = ("office", "server")
RUNNER_PATHS = (
    ("GET", re.compile(r"^/api/linkedin-runner/slots$")),
    ("POST", re.compile(r"^/api/linkedin-runner/heartbeat$")),
    ("GET", re.compile(r"^/api/linkedin-runner/bundle\.(tar\.gz|zip)$")),
)
STATES = ("starting", "running", "held", "restarting", "waiting", "off", "stopped")


class Conflict(Exception):
    """An office computer is already connected and the owner has not confirmed replacing it."""


def path_allowed(method: str, path: str) -> bool:
    return any(method == m and rx.match(path) for m, rx in RUNNER_PATHS)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _code_hash(store, code: str) -> str:
    """Keyed hash of a pairing code: without the server secret a stored hash says nothing about the code."""
    from .channels.security import server_secret
    key = hashlib.sha256(("linkedin-runner-pairing:" + server_secret(store)).encode()).digest()
    return "v2:" + hmac.new(key, code.encode("utf-8", "replace"), hashlib.sha256).hexdigest()


def show_code(code: str) -> str:
    """XXXX-XXXX-XXXX: easier to read aloud and type; norm_code takes it with or without the dashes."""
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def norm_code(code) -> str:
    return re.sub(r"[\s-]", "", str(code or "")).upper()[:32]


# ---------- where LinkedIn runs ----------
def where(store) -> str:
    value = (store.read("settings").get("linkedin_cloud") or {}).get("where")
    return value if value in WHERE else "office"


def set_where(store, value: str) -> str:
    if value not in WHERE:
        raise ValueError("Choose office or server.")
    store.update("settings", lambda s: {**s, "linkedin_cloud": {**(s.get("linkedin_cloud") or {}), "where": value}})
    return value


# ---------- runner records ----------
def runners(store) -> list:
    return [r for r in store.read("settings").get(SETTINGS_KEY) or [] if isinstance(r, dict) and r.get("id")]


def account_of(row: Optional[dict]) -> str:
    return (row or {}).get("account_id") or MCN


def active(store, account: str = MCN) -> Optional[dict]:
    return next((r for r in runners(store) if r.get("status") == "active" and account_of(r) == (account or MCN)), None)


def online(row: Optional[dict], now: Optional[datetime] = None) -> bool:
    seen = (row or {}).get("last_seen")
    if not seen:
        return False
    try:
        return ((now or now_utc()) - datetime.fromisoformat(seen)).total_seconds() < ONLINE_SECONDS
    except (TypeError, ValueError):
        return False


def public(row: dict) -> dict:
    out = {k: row.get(k) for k in ("id", "label", "created_by", "created_at", "last_seen", "hostname", "os", "version", "status",
                                   "revoked_at")}
    out["online"] = row.get("status") == "active" and online(row)
    out["account_id"] = account_of(row)
    out["agents"] = row.get("agents") or []
    return out


def _fresh(p) -> bool:
    if not isinstance(p, dict) or not p.get("hash"):
        return False
    try:
        return datetime.fromisoformat(p["expires_at"]) > now_utc()
    except (KeyError, TypeError, ValueError):
        return False


def _rows(s: dict) -> dict:
    """{account id: pending row} of every unexpired pairing code."""
    out = {MCN: s.get(PAIR_KEY)} if _fresh(s.get(PAIR_KEY)) else {}
    for acct, p in (s.get(PAIR_BY) or {}).items():
        if acct != MCN and _fresh(p):
            out[acct] = p
    return out


def _put(s: dict, account: str, row: Optional[dict]) -> dict:
    """The settings with `account`'s pending code replaced (None = removed)."""
    if account == MCN:
        rest = {k: v for k, v in s.items() if k != PAIR_KEY}
        return {**rest, PAIR_KEY: row} if row else rest
    by = {k: v for k, v in (s.get(PAIR_BY) or {}).items() if k != account and _fresh(v)}
    if row:
        by[account] = row
    return {**s, PAIR_BY: by}


def pending(store, account: str = MCN) -> Optional[dict]:
    return _rows(store.read("settings")).get(account or MCN)


def new_code(store, user: dict, replace: bool = False, account: str = MCN) -> tuple:
    """(code, expires_at). The code exists only in this return value; the store keeps its hash. A new code replaces any
    earlier unused one of the same client account. Raises Conflict when that account's office computer is connected and
    `replace` was not confirmed."""
    account = account or MCN
    current = active(store, account)
    if current and not replace:
        raise Conflict(f"An office computer ({current.get('label') or current.get('hostname') or 'connected'}) is already connected. "
                       "Connecting another one disconnects it.")
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    expires = iso(now_utc() + timedelta(seconds=CODE_TTL))
    row = {"hash": _code_hash(store, code), "expires_at": expires, "created_by": user.get("name") or user.get("email") or "owner",
           "created_sub": user.get("sub", ""), "replace": bool(current), "failures": 0, "account_id": account}
    store.update("settings", lambda s: _put(s, account, row))
    return code, expires


def cancel_code(store, account: str = MCN) -> None:
    store.update("settings", lambda s: _put(s, account or MCN, None))


def _ip_key(ip: str) -> str:
    return str(ip or "unknown")[:64]


def ip_blocked(pending_row: Optional[dict], ip: str) -> bool:
    return int(((pending_row or {}).get("failed_by_ip") or {}).get(_ip_key(ip), 0)) >= MAX_FAILED


def _matches(store, p: dict, code: str) -> bool:
    return len(code) == CODE_LEN and hmac.compare_digest(str(p.get("hash") or ""), _code_hash(store, code))


def code_valid(store, code, ip: str = "") -> bool:
    """True when `code` is a pending, unexpired code (not used up) and this address has not used up its guesses. A
    wrong guess counts against the address (never cancels a code)."""
    code = norm_code(code)
    rows = _rows(store.read("settings"))
    if not rows or all(ip_blocked(p, ip) for p in rows.values()):
        return False
    if any(_matches(store, p, code) and not ip_blocked(p, ip) for p in rows.values()):
        return True
    if code:
        _count_failure(store, ip)
    return False


def _count_failure(store, ip: str = "") -> None:
    key = _ip_key(ip)

    def bump(p: dict) -> dict:
        by_ip = dict(p.get("failed_by_ip") or {})
        by_ip[key] = int(by_ip.get(key, 0)) + 1
        if len(by_ip) > MAX_TRACKED_IPS:                               # bounded: the oldest addresses are forgotten first
            by_ip = dict(list(by_ip.items())[-MAX_TRACKED_IPS:])
        return {**p, "failures": int(p.get("failures") or 0) + 1, "failed_by_ip": by_ip}

    def apply(s):
        for acct, p in _rows(s).items():
            s = _put(s, acct, bump(p))
        return s
    store.update("settings", apply)


def pair(store, code, info: dict, ip: str = "") -> tuple:
    """Swap the one-time code for a runner token: (raw token, new runner row, [revoked rows]). ValueError on a bad code.
    Single use: the code is consumed in the same atomic update that adds the runner and revokes the other one of the
    same client account."""
    code = norm_code(code)
    digest = _code_hash(store, code)
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = iso(now_utc())
    hostname = _clean(info.get("hostname"), 80)
    row = {"id": secrets.token_hex(5), "label": hostname or "Office computer", "hostname": hostname, "os": _clean(info.get("os"), 80),
           "version": _clean(info.get("version"), 40), "created_at": now, "last_seen": now, "status": "active", "hash": _hash(raw)}
    outcome = {}

    def apply(s):
        hit = next(((acct, p) for acct, p in _rows(s).items() if len(code) == CODE_LEN and not ip_blocked(p, ip)
                    and hmac.compare_digest(str(p["hash"]), digest)), None)
        if hit is None:
            return s
        account, p = hit
        outcome["ok"] = True
        row["created_by"] = p.get("created_by") or "owner"
        row["account_id"] = account
        kept, revoked = [], []
        for r in s.get(SETTINGS_KEY) or []:
            if isinstance(r, dict) and r.get("status") == "active" and account_of(r) == account:
                r = {k: v for k, v in r.items() if k != "hash"}
                r.update(status="revoked", revoked_at=now, revoked_why="replaced by a new office computer")
                revoked.append(r)
            kept.append(r)
        outcome["revoked"] = revoked
        others = [r for r in kept if isinstance(r, dict) and r.get("status") == "active"]
        old = [r for r in kept if isinstance(r, dict) and r.get("status") != "active"][-KEEP_REVOKED:]
        return {**_put(s, account, None), SETTINGS_KEY: [*old, *others, row]}
    store.update("settings", apply)
    if not outcome.get("ok"):
        if code:
            _count_failure(store, ip)
        raise ValueError("That code is wrong, already used or expired. Make a new one in the portal (LinkedIn outreach, Office computer).")
    return raw, public(row), [public(r) for r in outcome["revoked"]]


def authenticate(store, raw: str) -> Optional[dict]:
    if not raw.startswith(TOKEN_PREFIX) or len(raw) > 200:
        return None
    digest = _hash(raw)
    row = next((r for r in runners(store) if r.get("status") == "active" and r.get("hash") and hmac.compare_digest(r["hash"], digest)), None)
    if row is None:
        return None
    return {"sub": f"runner:{row['id']}", "email": "", "name": row.get("label") or "Office computer", "kind": "runner",
            "runner_id": row["id"], "role": "caller", "claims": None,
            "runner_account": account_of(row)}    # not account_id: the runner never reads leads, only its account's slots


def heartbeat(store, runner_id: str, info: dict) -> dict:
    agents = []
    for a in (info.get("agents") or [])[:20]:
        if not isinstance(a, dict):
            continue
        state = str(a.get("state") or "")
        agents.append({"slot_id": _clean(a.get("slot_id"), 20), "state": state if state in STATES else "unknown",
                       "pid": int(a["pid"]) if isinstance(a.get("pid"), int) else None})
    change = {"last_seen": iso(now_utc()), "agents": agents}
    for key, limit in (("hostname", 80), ("os", 80), ("version", 40)):
        if info.get(key):
            change[key] = _clean(info[key], limit)
    out = {}

    def apply(s):
        rows = []
        for r in s.get(SETTINGS_KEY) or []:
            if isinstance(r, dict) and r.get("id") == runner_id and r.get("status") == "active":
                r = {**r, **change}
                out["row"] = r
            rows.append(r)
        return {**s, SETTINGS_KEY: rows}
    store.update("settings", apply)
    return public(out["row"]) if out else {}


def rename(store, runner_id: str, label: str) -> dict:
    label = _clean(label, 60)
    if not label:
        raise ValueError("Give the office computer a name.")
    out = {}

    def apply(s):
        rows = []
        for r in s.get(SETTINGS_KEY) or []:
            if isinstance(r, dict) and r.get("id") == runner_id:
                r = {**r, "label": label}
                out["row"] = r
            rows.append(r)
        return {**s, SETTINGS_KEY: rows}
    store.update("settings", apply)
    if not out:
        raise LookupError("No such office computer.")
    return public(out["row"])


def revoke(store, runner_id: str) -> dict:
    out = {}

    def apply(s):
        rows = []
        for r in s.get(SETTINGS_KEY) or []:
            if isinstance(r, dict) and r.get("id") == runner_id and r.get("status") == "active":
                r = {**{k: v for k, v in r.items() if k != "hash"}, "status": "revoked", "revoked_at": iso(now_utc()),
                     "revoked_why": "disconnected in the portal"}
                out["row"] = r
            rows.append(r)
        return {**s, SETTINGS_KEY: rows}
    store.update("settings", apply)
    if not out:
        raise LookupError("No such connected office computer.")
    return public(out["row"])


def describe(store, account: str = MCN) -> dict:
    act = active(store, account)
    p = pending(store, account)
    return {"where": where(store), "runner": public(act) if act else None,
            "pairing": {"expires_at": p["expires_at"], "replace": bool(p.get("replace"))} if p else None,
            "online_seconds": ONLINE_SECONDS}
