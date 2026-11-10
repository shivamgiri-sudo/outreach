"""Chrome assistant support: pairing tokens for the extension and the lead-matching / what-may-be-sent rules.

The extension is an ASSISTANT, not automation: a person opens a LinkedIn profile, the side panel shows the ready-made
message, and the person sends it themselves. Nothing here sends anything to LinkedIn; the sent / accepted / replied
calls only record what the person confirmed in the panel.

Pairing tokens: a long random string shown once, stored only as a SHA-256 hash in settings["extension_tokens"].
An extension token authenticates as the person who created it (role caller, or viewer if that person is now a viewer)
and may call ONLY the /api/extension/ routes in ALLOWED (never /api/leads, settings, users, token management, ...).
"""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import unquote, urlsplit

from . import auth, linkedin_seq as li
from .activities import iso, now_utc
from .hygiene import safe_http_url

TOKEN_PREFIX = "mcnx_"
TOKEN_DAYS = 90
MAX_TOKENS_PER_USER = 10
TOUCH_SECONDS = 300                     # last_used_at is refreshed at most this often (avoids a store write per request)
SETTINGS_KEY = "extension_tokens"
ALLOWED = (                             # (method, path regex) an extension token may call; nothing else
    ("GET", re.compile(r"^/api/extension/match$")),
    ("GET", re.compile(r"^/api/extension/queue$")),
    ("POST", re.compile(r"^/api/extension/inbox$")),                 # the agent read the LinkedIn messaging list
    ("POST", re.compile(r"^/api/extension/leads/[^/]+/(sent|accepted|replied|profile|claim)$")),
    ("POST", re.compile(r"^/api/extension/save-profile$")),           # the rep pressed "Save to portal" on one profile
    ("GET", re.compile(r"^/api/extension/agent$")),                  # the LinkedIn agent program on your computer
    ("POST", re.compile(r"^/api/extension/agent/event$")),
    ("GET", re.compile(r"^/api/extension/cloud/(login-job|code)$")),  # the cloud agent on this server (linkedin_cloud);
    ("POST", re.compile(r"^/api/extension/cloud/state$")),            # the routes refuse any token that is not a cloud slot's
    ("POST", re.compile(r"^/api/mcp$")),                              # Claude / MCP clients: read-only tools (see mcp_server)
)
GRACE = timedelta(hours=12)             # same "due" window as the web queue
COMPANY_NOISE = re.compile(r"\b(pvt|private|ltd|limited|llp|inc|incorporated|co|corp|corporation|the)\b")


def _now() -> datetime:
    return now_utc()


def path_allowed(method: str, path: str) -> bool:
    return any(method == m and rx.match(path) for m, rx in ALLOWED)


# ---------- pairing tokens ----------
def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def public_token(row: dict) -> dict:
    return {k: row.get(k) for k in ("id", "label", "owner", "created_at", "expires_at", "last_used_at", "hint")}


def tokens(store) -> list:
    return [t for t in store.read("settings").get(SETTINGS_KEY) or [] if isinstance(t, dict) and t.get("hash")]


def create_token(store, user: dict, label: str = "", days: int = TOKEN_DAYS, account_id: str = "") -> tuple:
    """(raw token, public row). The raw value exists only in this return value. The token works for `account_id` (a
    cloud slot's client account; default the person's own): its agent only sees and claims that account's leads."""
    sub = user.get("sub", "")
    mine = [t for t in tokens(store) if t.get("sub") == sub]
    if len(mine) >= MAX_TOKENS_PER_USER:
        raise ValueError(f"You already have {MAX_TOKENS_PER_USER} pairing tokens. Revoke one you no longer use.")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = _now()
    row = {"id": secrets.token_hex(6), "hash": _hash(raw), "label": (label or "").strip()[:60] or "Chrome", "sub": sub,
           "owner": user.get("name") or user.get("email") or sub, "created_at": iso(now),
           "expires_at": iso(now + timedelta(days=days)), "last_used_at": None, "hint": raw[-4:],
           "account_id": account_id or user.get("account_id") or "mcn"}
    store.update("settings", lambda s: {**s, SETTINGS_KEY: [*(s.get(SETTINGS_KEY) or []), row]})
    return raw, public_token(row)


def revoke_token(store, token_id: str, user: dict) -> bool:
    """Owners revoke any token; everyone else only their own. False when there is no such token for them."""
    found = []

    def drop(settings: dict) -> dict:
        keep = []
        for t in settings.get(SETTINGS_KEY) or []:
            if isinstance(t, dict) and t.get("id") == token_id and (user.get("role") == "owner" or t.get("sub") == user.get("sub")):
                found.append(t)
            else:
                keep.append(t)
        return {**settings, SETTINGS_KEY: keep}
    store.update("settings", drop)
    return bool(found)


def authenticate(store, admin_password: str, raw: str) -> Optional[dict]:
    """The principal for a pairing token, or None (unknown, expired, revoked, or its person no longer exists)."""
    if not raw.startswith(TOKEN_PREFIX) or len(raw) > 200:
        return None
    digest = _hash(raw)
    row = next((t for t in tokens(store) if hmac.compare_digest(t["hash"], digest)), None)
    if row is None:
        return None
    now = _now()
    try:
        if datetime.fromisoformat(row["expires_at"]) <= now:
            return None
    except (KeyError, ValueError, TypeError):
        return None
    sub = row.get("sub", "")
    if sub == "local":
        if auth.auth_on(store, admin_password):
            return None                       # a token made before logins were switched on does not carry over
        principal = {"sub": sub, "email": "", "name": "This computer", "role": "owner"}
    else:
        principal = auth.subject_principal(store, admin_password, sub)
        if principal is None or principal.get("account_pending"):
            return None                       # a pending (not yet approved) account has no paired tools
    last = row.get("last_used_at")
    if not last or (now - datetime.fromisoformat(last)).total_seconds() > TOUCH_SECONDS:
        store.update("settings", lambda s: {**s, SETTINGS_KEY: [{**t, "last_used_at": iso(now)} if t.get("id") == row["id"] else t
                                                                 for t in s.get(SETTINGS_KEY) or []]})
    out = {"sub": sub, "email": principal.get("email", ""), "name": principal.get("name", ""), "kind": "extension",
           "token_id": row["id"], "token_label": row.get("label", ""), "role": "viewer" if principal["role"] == "viewer" else "caller", "claims": None,
           # the person's workspace limits travel with the token (owners: none), so a saved lead lands where they work
           "workspaces": [] if principal["role"] == "owner" else list(principal.get("workspaces") or []),
           # and so does the account boundary (app/accounts.py): a client's token never reaches another account
           "account_id": principal.get("account_id") or "mcn",
           "account_ws": None if principal["role"] == "owner" else principal.get("account_ws")}
    made_for = row.get("account_id") or out["account_id"]
    if made_for != out["account_id"]:            # an owner's cloud slot for a client account: that account's workspaces only
        if principal["role"] != "owner":
            return None
        from .accounts import workspaces_of
        mine = sorted(workspaces_of(store, made_for))
        out.update(account_id=made_for, workspaces=mine, account_ws=mine)
    return out


# ---------- matching ----------
def profile_slug(url: str) -> str:
    """'in.linkedin.com/in/Ravi-Kumar/?utm=1' -> 'ravi-kumar'. '' when it is not a LinkedIn /in/<slug> link."""
    text = (url or "").strip()
    if not text or len(text) > 2000:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return ""
    segs = [unquote(s) for s in parts.path.split("/") if s]
    for i, seg in enumerate(segs[:2]):
        if seg.lower() == "in" and i + 1 < len(segs):
            return segs[i + 1].strip().lower()
    return ""


def _words(text: str, company: bool = False) -> str:
    t = re.sub(r"[^a-z0-9]+", " ", (text or "").casefold())
    if company:
        t = COMPANY_NOISE.sub(" ", t)
    return " ".join(t.split())


def find_lead(store, profile_url: str = "", name: str = "", company: str = "") -> Optional[dict]:
    from .accounts import same_account_as_new
    ours = same_account_as_new(store)                 # never another client company's lead
    leads = [l for l in store.read("leads") if ours(l)]
    slug = profile_slug(profile_url)
    if slug:
        hits = [l for l in leads if profile_slug(l.get("linkedin_url", "")) == slug]
        if hits:
            return hits[0]
    n, c = _words(name), _words(company, True)
    if n and c:
        hits = [l for l in leads if _words(l.get("contact_name")) == n and _words(l.get("company"), True) == c]
        if len(hits) == 1:
            return hits[0]                    # an ambiguous name + company never guesses
    return None


# ---------- what may be sent ----------
def _open_task(store, lead_id: str) -> Optional[dict]:
    rows = [t for t in store.read("tasks") if t.get("lead_id") == lead_id and t.get("kind") == "linkedin" and t.get("status", "open") == "open"]
    return min(rows, key=lambda t: t.get("due_at") or "") if rows else None


def owner_of(lead: dict, settings: Optional[dict] = None) -> str:
    """The LinkedIn account this lead belongs to: the one that took it, else the one picked for it (per lead or the
    owner's default; see app/linkedin_accounts.py). '' = any account may take it."""
    from .linkedin_accounts import assigned
    return assigned(lead, settings)


def describe(store, lead: dict, account: str = "") -> dict:
    """Everything the side panel needs for one lead: status, the message (only when sending is allowed) and why not."""
    seq = lead.get("linkedin_seq") or None
    settings = store.read("settings")
    now = _now()
    out = {"matched": True, "lead_id": lead["id"], "name": lead.get("contact_name"), "company": lead.get("company"),
           "title": lead.get("title"), "stage": lead.get("stage"),
           "linkedin_seq": {"status": (seq or {}).get("status"), "next_step": None, "due": None},
           "message": None, "can_send": False, "reason": "", "actions": [],
           "open_profile": bool((lead.get("linkedin_profile") or {}).get("open_profile")),
           "has_profile_link": bool(lead.get("linkedin_url"))}
    if lead.get("stage") in ("unsubscribed", "disqualified"):
        out["reason"] = f"This person is {lead['stage']}; no LinkedIn outreach."
        out["actions"] = ["replied"] if seq else []
        return out
    if not seq:
        out["reason"] = "Not on the LinkedIn sequence yet. Start it from the LinkedIn page in the app."
        return out
    status = seq.get("status")
    holder = owner_of(lead, settings)
    if account and holder and holder != account:
        out["reason"] = f"Another LinkedIn account ({holder}) is handling this person; only that account messages them."
        return out
    if status == "stopped":
        out["reason"] = "The sequence is stopped" + (" because they replied." if seq.get("stopped") == "replied" else ".")
        return out
    nxt = li._next_step(seq, li.lead_settings(settings, lead))
    if status == "finished" or nxt is None:
        out["reason"] = "All steps are done for this person."
        out["actions"] = ["replied"]
        return out
    out["linkedin_seq"]["next_step"] = nxt["n"]
    out["actions"] = ["replied"]
    if status == "waiting_for_acceptance":
        out["actions"] = ["accepted", "replied"]
        out["reason"] = "Waiting for them to accept the connection request. No message until they do."
        return out
    task = _open_task(store, lead["id"])
    due = (task or {}).get("due_at")
    out["linkedin_seq"]["due"] = due
    last = seq.get("last_sent_at") or ""
    if last[:10] == iso(now)[:10]:
        out["reason"] = "Already sent today. The next step is not due yet."
        return out
    if due and datetime.fromisoformat(due) > now + GRACE:
        from .sequences import IST
        when = datetime.fromisoformat(due).astimezone(IST).strftime("%-d %b, %-I:%M %p IST")
        out["reason"] = f"Step {nxt['n']} is not due yet (due {when})."
        return out
    out["can_send"] = True
    out["actions"] = ["sent", "replied"]
    out["message"] = {"step": nxt["n"], "kind": nxt["kind"], "purpose": nxt["purpose"], "text": li.render(nxt, lead, settings)}
    if nxt["kind"] == "connect":
        out["message"]["short_text"] = li.render_short(lead, settings)
    return out


def no_match() -> dict:
    return {"matched": False, "lead_id": None, "name": None, "company": None, "title": None,
            "linkedin_seq": {"status": None, "next_step": None, "due": None}, "message": None, "can_send": False,
            "reason": "This profile is not one of your leads.", "actions": []}


def queue(store, account: str = "") -> dict:
    """Today's due steps with a verified http(s) profile link, for the popup list. With an account: only its own leads
    and leads no account has contacted yet."""
    q = li.queue(store)
    settings = store.read("settings")
    leads = {l["id"]: l for l in store.read("leads")}
    from . import workspaces
    mine = lambda lead: (not account or owner_of(lead, settings) in ("", account)) and workspaces.visible(lead)
    due = []
    for d in q["due"]:
        lead = leads.get(d["lead_id"]) or {}
        if lead.get("stage") in ("unsubscribed", "disqualified") or not mine(lead):
            continue
        kind = next((s["kind"] for s in li.lead_steps(lead, settings) if s["n"] == d["step"]), None)
        due.append({"lead_id": d["lead_id"], "name": d["name"], "company": d["company"], "title": d["title"], "step": d["step"],
                    "kind": kind, "due_at": d["due_at"], "profile_url": safe_http_url(d.get("linkedin_url"))})
    from . import linkedin_guard
    waiting = [{"lead_id": w["lead_id"], "name": w["name"], "company": w["company"], "since": w["since"],
                "profile_url": safe_http_url(w.get("linkedin_url"))} for w in q["waiting_for_acceptance"]
               if (leads.get(w["lead_id"]) or {}).get("stage") not in ("unsubscribed", "disqualified") and mine(leads.get(w["lead_id"]) or {})
               and (not account or linkedin_guard.check_due(leads.get(w["lead_id"]) or {}))]
    if account:                                  # an agent: each profile looked at once per 6 hours, whatever restarts it
        waiting = waiting[:3]
    if account and waiting:
        linkedin_guard.stamp_checked(store, [w["lead_id"] for w in waiting])
    due = [d for d in due if not linkedin_guard.held_until(leads.get(d["lead_id"]) or {})]   # held after failed tries
    return {"due": due, "waiting_for_acceptance": waiting, "owner": q["owner"]}
