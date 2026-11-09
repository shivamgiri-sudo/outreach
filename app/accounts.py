"""Accounts: one Account = one client company, above its workspaces.

Data (all in the settings document, so nothing heavy is ever altered or locked):
  settings["accounts"]          [{id, name, status, created_at, created_by, limits, integrations_granted, notes}]
  settings["workspaces"][i]     gains account_id (missing = "mcn"); status "requested" until an owner approves it
  settings["users"][i]          gains account_id (missing = "mcn")
  settings["account_settings"]  {account_id: {sender_name, sender_company, ..., brand, sending: {...}}}

Account "mcn" (Mas Callnet) is the platform's own account: it holds "main", every workspace made before accounts
existed, and every owner. Its settings are the old global settings, unchanged. Owners are platform staff and see
every account; a client login (account_id other than "mcn") is admin / caller / viewer only and never sees another
account's workspaces, leads, templates, logins, audit rows, reports or settings (enforced centrally through
workspaces.allowed_of and the workspace guard).

Later phases reuse: status (active / pending / suspended; Phase 3 sign-up makes "pending" accounts), limits (None =
unlimited), integrations_granted (platform integrations a client may use), settings_for() and hold_reason().
"""
import contextvars
import re
import uuid
from typing import Optional

from .lead_model import now_iso

MCN = "mcn"
MCN_NAME = "Mas Callnet"
STATUSES = ("active", "pending", "suspended")
LIMIT_KEYS = ("seats", "workspaces", "daily_sends", "imports_per_day", "enrich_credits", "ai_credits")
MAX_ACCOUNTS = 500
# Per-account values kept in settings["account_settings"][id] (the "mcn" account keeps the old global keys).
IDENTITY_KEYS = ("sender_name", "sender_company", "sender_address", "sender_website", "sender_contact_email")
CAP_KEYS = ("daily_cap", "per_run_cap")
SENDING_KEYS = ("provider", "api_key", "from_email", "from_name", "reply_to", "domain")
BRAND_KEYS = ("company_name", "product_name", "website")
HOLD_EMAIL = ("Sending is held for this account: connect your sending domain or ask the platform admin to grant one.")
HOLD_WHATSAPP = "WhatsApp is held for this account: ask the platform admin to grant WhatsApp."
EMAIL_GRANTS = ("sending_domain", "email_send")
# The request's account: (account id or None = every account, client login?) set by workspaces.scope_dependency.
_CURRENT = contextvars.ContextVar("account_scope", default=(None, False))


# ---------------------------------------------------------------- reading
def _settings(source) -> dict:
    return (source.read("settings") if hasattr(source, "read") else source) or {}


def mcn_name(settings: dict) -> str:
    brand = settings.get("brand") or {}
    return str(brand.get("company_name") or settings.get("sender_company") or MCN_NAME)[:80]


def _mcn_row(settings: dict) -> dict:
    return {"id": MCN, "name": mcn_name(settings), "status": "active", "created_at": "", "created_by": "",
            "limits": {}, "integrations_granted": [], "notes": "Platform account (Mas Callnet staff, main workspace)"}


def all_accounts(source) -> list:
    settings = _settings(source)
    rows = [a for a in settings.get("accounts") or [] if isinstance(a, dict) and a.get("id")]
    if not any(a["id"] == MCN for a in rows):
        rows = [_mcn_row(settings), *rows]
    return [{"limits": {}, "integrations_granted": [], "notes": "", "status": "active", **a} for a in rows]


def find(source, account_id: str) -> Optional[dict]:
    return next((a for a in all_accounts(source) if a["id"] == account_id), None)


def of_user(user: Optional[dict]) -> str:
    return (user or {}).get("account_id") or MCN


def is_client(user: Optional[dict]) -> bool:
    return of_user(user) != MCN


def ws_status(row: dict) -> str:
    return (row or {}).get("status") or "active"


def ws_account_map(source, active_only: bool = False) -> dict:
    """{workspace id: account id}, "main" included."""
    out = {"main": MCN}
    for w in _settings(source).get("workspaces") or []:
        if isinstance(w, dict) and w.get("id") and w["id"] != "main" and (not active_only or ws_status(w) == "active"):
            out[w["id"]] = w.get("account_id") or MCN
    return out


def account_of_ws(source, ws_id: str) -> str:
    return ws_account_map(source).get(ws_id or "main", MCN)


def account_of_lead(amap: dict, lead: dict) -> str:
    return amap.get((lead or {}).get("workspace") or "main", MCN)


def workspaces_of(source, account_id: str, active_only: bool = True) -> set:
    return {w for w, a in ws_account_map(source, active_only).items() if a == account_id}


def login_account_ws(source, user: dict) -> Optional[list]:
    """The workspaces a login's account holds (the boundary its scope is cut to), or None for no boundary: owners,
    and Mas Callnet logins while no other account has a workspace (so nothing changes until a client exists)."""
    aid = of_user(user)
    if aid == MCN and user.get("role") == "owner":
        return None
    if aid != MCN:
        return sorted(workspaces_of(source, aid))
    amap = ws_account_map(source)
    if all(a == MCN for a in amap.values()):
        return None
    return sorted(w for w, a in amap.items() if a == MCN and w in ws_account_map(source, True))


def account_active(source, account_id: str) -> bool:
    row = find(source, account_id)
    return account_id == MCN or (row is not None and row.get("status") == "active")


def limit_of(source, account_id: str, key: str) -> Optional[int]:
    value = ((find(source, account_id) or {}).get("limits") or {}).get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def granted(source, account_id: str, key: str) -> bool:
    return account_id == MCN or key in ((find(source, account_id) or {}).get("integrations_granted") or [])


def public(row: dict) -> dict:
    """What a client login sees of its own account."""
    return {k: row.get(k) for k in ("id", "name", "status")}


# ---------------------------------------------------------------- the request's account
def set_current(account_id: Optional[str], client: bool) -> None:
    _CURRENT.set((account_id, client))


def current() -> Optional[str]:
    return _CURRENT.get()[0]


def current_is_client() -> bool:
    return _CURRENT.get()[1]


def visible_row(row: dict) -> bool:
    """A row stamped with account_id (audit rows, notifications) is shown to a client login of that account only."""
    aid, client = _CURRENT.get()
    return not client or (row or {}).get("account_id") == aid


# ---------------------------------------------------------------- migration (startup; idempotent; settings only)
def migrate(store) -> bool:
    """Existing data becomes account "mcn": an accounts list with the mcn row, account_id on every workspace and login.
    Writes only when something is missing, so a second run changes nothing. Returns True when it wrote."""
    settings = store.read("settings") or {}
    need = (not any(isinstance(a, dict) and a.get("id") == MCN for a in settings.get("accounts") or [])
            or any(isinstance(w, dict) and not w.get("account_id") for w in settings.get("workspaces") or [])
            or any(isinstance(u, dict) and u.get("email") and not u.get("account_id") for u in settings.get("users") or []))
    if not need:
        return False

    def apply(s: dict) -> dict:
        rows = [a for a in s.get("accounts") or [] if isinstance(a, dict)]
        if not any(a.get("id") == MCN for a in rows):
            rows = [{**_mcn_row(s), "created_at": now_iso(), "created_by": "migration"}, *rows]
        return {**s, "accounts": rows,
                "workspaces": [{**w, "account_id": w.get("account_id") or MCN} if isinstance(w, dict) else w for w in s.get("workspaces") or []],
                "users": [{**u, "account_id": u.get("account_id") or MCN} if isinstance(u, dict) and u.get("email") else u
                          for u in s.get("users") or []]}
    store.update("settings", apply)
    return True


# ---------------------------------------------------------------- changes (owners only; the routes check)
def _clean_name(name) -> str:
    return " ".join(str(name or "").split())[:80]


def clean_limits(raw) -> dict:
    out = {}
    for key in LIMIT_KEYS:
        value = (raw or {}).get(key)
        if value in (None, ""):
            continue
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"Limit {key.replace('_', ' ')} must be a whole number (or blank for unlimited).") from None
        if value < 0 or value > 10_000_000:
            raise ValueError(f"Limit {key.replace('_', ' ')} is out of range.")
        out[key] = value
    return out


def clean_grants(raw) -> list:
    from .integrations import known_ids
    keys = list(dict.fromkeys(str(k)[:40] for k in raw or []))
    unknown = [k for k in keys if k not in known_ids()]
    if unknown:
        raise ValueError(f"Unknown integration: {', '.join(unknown)}")
    return keys


def create(store, name: str, by: str, status: str = "active", limits=None, grants=None, notes: str = "") -> dict:
    name = _clean_name(name)
    if not name:
        raise ValueError("Give the account a name.")
    if status not in STATUSES:
        raise ValueError("Status must be active, pending or suspended.")
    existing = all_accounts(store)
    if any(a["name"].lower() == name.lower() for a in existing):
        raise ValueError("An account with that name already exists.")
    if len(existing) >= MAX_ACCOUNTS:
        raise ValueError(f"At most {MAX_ACCOUNTS} accounts.")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "acct"
    row = {"id": f"{slug}-{uuid.uuid4().hex[:4]}", "name": name, "status": status, "created_at": now_iso(), "created_by": by,
           "limits": clean_limits(limits), "integrations_granted": clean_grants(grants), "notes": str(notes or "")[:500]}

    def apply(s: dict) -> dict:
        rows = [a for a in s.get("accounts") or [] if isinstance(a, dict)]
        if not any(a.get("id") == MCN for a in rows):
            rows = [_mcn_row(s), *rows]
        return {**s, "accounts": [*rows, row]}
    store.update("settings", apply)
    return row


def update(store, account_id: str, changes: dict) -> tuple:
    """(new row, old row). `changes` may hold name, status, limits, integrations_granted, notes."""
    old = find(store, account_id)
    if old is None:
        raise LookupError("No such account.")
    new = dict(old)
    if "name" in changes:
        name = _clean_name(changes["name"])
        if not name:
            raise ValueError("Give the account a name.")
        if any(a["name"].lower() == name.lower() and a["id"] != account_id for a in all_accounts(store)):
            raise ValueError("An account with that name already exists.")
        new["name"] = name
    if "status" in changes:
        if changes["status"] not in STATUSES:
            raise ValueError("Status must be active, pending or suspended.")
        if account_id == MCN and changes["status"] != "active":
            raise ValueError("The platform account always stays active.")
        new["status"] = changes["status"]
    if "limits" in changes:
        new["limits"] = clean_limits(changes["limits"])
    if "integrations_granted" in changes:
        new["integrations_granted"] = [] if account_id == MCN else clean_grants(changes["integrations_granted"])
    if "notes" in changes:
        new["notes"] = str(changes["notes"] or "")[:500]

    def apply(s: dict) -> dict:
        rows = [a for a in s.get("accounts") or [] if isinstance(a, dict)]
        if not any(a.get("id") == account_id for a in rows):
            rows.append(new)
        return {**s, "accounts": [new if a.get("id") == account_id else a for a in rows]}
    store.update("settings", apply)
    return new, old


# ---------------------------------------------------------------- per-account settings and sending
def own_settings(source, account_id: str) -> dict:
    found = (_settings(source).get("account_settings") or {}).get(account_id)
    return found if isinstance(found, dict) else {}


def clean_account_settings(raw: dict) -> dict:
    out = {}
    for key in IDENTITY_KEYS:
        if key in raw and raw[key] is not None:
            out[key] = " ".join(str(raw[key]).split())[:300]
    for key in CAP_KEYS:
        if raw.get(key) not in (None, ""):
            value = int(raw[key])
            if not 1 <= value <= (500 if key == "daily_cap" else 50):
                raise ValueError(f"{key.replace('_', ' ').capitalize()} is out of range.")
            out[key] = value
    if "live_sending" in raw and raw["live_sending"] is not None:
        out["live_sending"] = bool(raw["live_sending"])
    if isinstance(raw.get("brand"), dict):
        out["brand"] = {k: " ".join(str(raw["brand"].get(k) or "").split())[:120] for k in BRAND_KEYS if raw["brand"].get(k) is not None}
    if isinstance(raw.get("sending"), dict):
        out["sending"] = {k: str(raw["sending"].get(k) or "").strip()[:300] for k in SENDING_KEYS if raw["sending"].get(k) is not None}
    return out


def save_settings(store, account_id: str, raw: dict) -> dict:
    if account_id == MCN:
        raise ValueError("The platform account uses the main Setup pages.")
    changes = clean_account_settings(raw)
    if "sending" in changes:
        old = own_settings(store, account_id).get("sending") or {}
        if str(changes["sending"].get("api_key", "")).startswith("••••"):
            changes["sending"]["api_key"] = old.get("api_key", "")
        changes["sending"] = {**old, **changes["sending"]}

    def apply(s: dict) -> dict:
        all_acc = dict(s.get("account_settings") or {})
        all_acc[account_id] = {**(all_acc.get(account_id) or {}), **changes}
        return {**s, "account_settings": all_acc}
    store.update("settings", apply)
    return own_settings(store, account_id)


def masked_settings(source, account_id: str) -> dict:
    out = dict(own_settings(source, account_id))
    if out.get("sending"):
        key = out["sending"].get("api_key") or ""
        out["sending"] = {**out["sending"], "api_key": ("••••" + key[-4:]) if key else ""}
    return out


def sending_config(source, account_id: str) -> dict:
    """The account's own email sending (Resend) when complete, else {}."""
    sending = own_settings(source, account_id).get("sending") or {}
    return sending if all(sending.get(k) for k in ("api_key", "from_email", "from_name")) else {}


def settings_for(source, account_id: Optional[str]) -> dict:
    """Settings as one account's sending code reads them. "mcn" = the global settings unchanged. Another account gets
    only its own identity, brand, caps and live switch (nothing of Mas Callnet's sender, brand or offer), plus the
    shared machinery (server secret, workspaces, templates)."""
    settings = _settings(source)
    if not account_id or account_id == MCN:
        return settings
    mine = own_settings(settings, account_id)
    row = find(settings, account_id) or {"name": account_id}
    brand = {"company_name": row["name"], **{k: v for k, v in (mine.get("brand") or {}).items() if v}}
    if mine.get("sender_name"):
        brand["sender_name"] = mine["sender_name"]
    shared = {k: settings[k] for k in ("server_secret", "workspaces", "workspace_templates", "public_base_url", "lead_fields")
              if k in settings}
    out = {**shared, "live_sending": False, **{k: v for k, v in mine.items() if k not in ("sending", "brand")},
           "brand": brand, "account_id": account_id}
    out.setdefault("sender_name", brand.get("sender_name", ""))
    out.setdefault("sender_company", brand["company_name"])
    return out


def hold_reason(source, account_id: Optional[str], channel: str = "email") -> Optional[str]:
    """Why nothing may be sent for this account on `channel`, or None. Mas Callnet is never held here."""
    if not account_id or account_id == MCN:
        return None
    row = find(source, account_id)
    if row is None or row.get("status") != "active":
        return f"Sending is held: this account is {(row or {}).get('status', 'unknown')}."
    if channel == "email":
        if sending_config(source, account_id) or any(granted(source, account_id, k) for k in EMAIL_GRANTS):
            return None
        return HOLD_EMAIL
    if channel == "whatsapp":
        return None if granted(source, account_id, "whatsapp") else HOLD_WHATSAPP
    return None if granted(source, account_id, channel) else f"{channel} is not granted to this account."


def account_of_lead_in(source, lead: Optional[dict]) -> str:
    return account_of_lead(ws_account_map(source), lead or {})


def same_account_as_new(store):
    """A test "is this lead in the account new leads of this request go into?" (dedupe within one account)."""
    from . import workspaces
    amap = ws_account_map(store)
    target = amap.get(workspaces.for_new_lead(), MCN)
    return lambda lead: account_of_lead(amap, lead) == target


def same_account_as(store, lead: dict):
    """A test "is this lead in the same account as `lead`?"."""
    amap = ws_account_map(store)
    target = account_of_lead(amap, lead)
    return lambda other: account_of_lead(amap, other) == target


class AccountView:
    """A read-only store-like view for one client account's request: its own settings (settings_for), no platform
    integrations, and only the leads in the request's scope. Lets brand / offer / script / ICP helpers that take a
    store answer for the client without Mas Callnet's values."""

    def __init__(self, store, account_id: str):
        self._store, self._account = store, account_id

    def read(self, key: str):
        if key == "settings":
            return settings_for(self._store, self._account)
        if key == "integrations":
            return {}
        if key == "leads":
            from .workspaces import filter_rows
            return filter_rows(self._store.read("leads"))
        return self._store.read(key)


def view_for(store, user):
    """The store itself for Mas Callnet logins, an AccountView for a client login."""
    return AccountView(store, of_user(user)) if is_client(user) else store
