"""Saved provider settings for hub channels, kept inside the `integrations` collection.

Exclusive channel (one provider sends), e.g. integrations["whatsapp"]:
  {"mode": "api", "values": {"provider": "<active id>", ...that provider's fields},
   "providers": {"<id>": {"values": {...}, "last_test": {ok, message, at}, "tokens": {...}, "state": {...}}},
   "webhook_tokens": {"<id>": "<secret>"}}
`values` always mirrors the active provider, so older code (senders.py, Setup → Connections) keeps working.
Multi channel (inbound_email, email_events): only "providers"; every saved provider is listened to.
Tokens, webhook secrets and state are never returned by describe().
"""
import re
from datetime import datetime, timezone

from ..hygiene import normalise_email
from .netguard import check_https, check_public_url
from .registry import (BY_KEY, CHANNELS, channel, defaults, get_provider, hub_provider_for_legacy, providers_for)
from .security import is_masked, mask, new_token

IMPLIED_ACTIVE = {"email_send": ("resend", ("api_key", "from_email", "domain", "from_name")),
                  "calls": ("click_to_call", ("click_to_call_url",))}
FETCHED_URL_KEYS = ("url", "api_endpoint", "api_base")        # the server calls these: full SSRF check
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
PHONE = re.compile(r"^\+?[0-9 ()\-]{6,20}$")
MAX_HEADERS = 20
MAX_VALUE_CHARS = 8000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def entry_of(saved: dict, ch_id: str) -> dict:
    return dict(saved.get(channel(ch_id)["integration_id"]) or {})


def active_provider(ch_id: str, entry: dict):
    ch = channel(ch_id)
    if ch["kind"] != "exclusive" or entry.get("mode") == "manual":
        return None
    values = entry.get("values") or {}
    named = hub_provider_for_legacy(ch["integration_id"], values)
    if named:
        return named["id"]
    implied = IMPLIED_ACTIVE.get(ch_id)
    if implied and not values.get("provider") and any(values.get(k) for k in implied[1]):
        return implied[0]
    return None


def record(entry: dict, pid: str) -> dict:
    return dict((entry.get("providers") or {}).get(pid) or {})


def provider_values(ch_id: str, entry: dict, pid: str) -> dict:
    if pid == active_provider(ch_id, entry):
        return {k: v for k, v in (entry.get("values") or {}).items() if k != "provider"}
    return dict(record(entry, pid).get("values") or {})


def is_saved(ch_id: str, entry: dict, pid: str) -> bool:
    return pid in (entry.get("providers") or {}) or pid == active_provider(ch_id, entry)


def effective(provider: dict, values: dict) -> dict:
    return {**defaults(provider), **{k: v for k, v in values.items() if v not in (None, "", [])}}


def missing(provider: dict, values: dict) -> list:
    eff = effective(provider, values)
    return [f["label"] for f in provider["fields"] if f.get("required") and not eff.get(f["key"])]


def oauth_connected(rec: dict) -> bool:
    tokens = rec.get("tokens") or {}
    return bool(tokens.get("refresh_token") or tokens.get("access_token"))


def status(ch_id: str, entry: dict, pid: str) -> str:
    provider = get_provider(ch_id, pid)
    if not is_saved(ch_id, entry, pid):
        return "not_configured"
    rec = record(entry, pid)
    if missing(provider, provider_values(ch_id, entry, pid)) or (provider.get("oauth") and not oauth_connected(rec)):
        return "incomplete"
    last = rec.get("last_test")
    return "error" if last and not last.get("ok") else "configured"


# ---------- showing values (secrets masked) ----------
def shown_values(provider: dict, values: dict) -> dict:
    out = {}
    for f in provider["fields"]:
        value = values.get(f["key"])
        if value in (None, "", []):
            continue
        if f["kind"] == "headers":
            out[f["key"]] = [{"key": h.get("key", ""), "secret": bool(h.get("secret")),
                              "value": mask(h.get("value")) if h.get("secret") and h.get("value") else h.get("value", "")}
                             for h in value if isinstance(h, dict)]
        else:
            out[f["key"]] = mask(value) if f.get("secret") else value
    return out


# ---------- merging and validating what the owner typed ----------
def _merge_headers(old_rows: list, new_rows) -> list:
    if not isinstance(new_rows, list):
        raise ValueError("Headers must be a list of name / value rows.")
    if len(new_rows) > MAX_HEADERS:
        raise ValueError(f"At most {MAX_HEADERS} headers.")
    old = {str(r.get("key", "")).lower(): r.get("value", "") for r in old_rows if isinstance(r, dict)}
    out = []
    for row in new_rows:
        if not isinstance(row, dict) or not str(row.get("key", "")).strip():
            continue
        key, value = str(row["key"]).strip(), str(row.get("value", ""))
        if not HEADER_NAME.match(key):
            raise ValueError(f"Header name '{key[:40]}' may only use letters, numbers and dashes.")
        if is_masked(value):
            if key.lower() not in old:
                raise ValueError(f"Type the value for header {key} again.")
            value = old[key.lower()]
        if "\r" in value or "\n" in value:
            raise ValueError(f"Header {key} cannot contain line breaks.")
        out.append({"key": key, "value": value, "secret": bool(row.get("secret"))})
    return out


def merge(provider: dict, old: dict, new: dict) -> dict:
    """Apply typed values. Empty or masked secrets keep the saved secret; empty plain fields are cleared."""
    out = dict(old)
    for f in provider["fields"]:
        key = f["key"]
        if key not in new or new[key] is None:
            continue
        if f["kind"] == "headers":
            out[key] = _merge_headers(old.get(key) or [], new[key])
            continue
        text = str(new[key]).strip() if not isinstance(new[key], str) else new[key].strip()
        if len(text) > MAX_VALUE_CHARS:
            raise ValueError(f"{f['label']} is too long.")
        if f.get("secret"):
            if text and not is_masked(text):
                out[key] = text
        elif text:
            out[key] = text
        else:
            out.pop(key, None)
    return out


def _check_field(f: dict, value, resolver) -> None:
    kind, label = f["kind"], f["label"]
    if kind == "url":
        try:
            (check_public_url(value, resolver) if f["key"] in FETCHED_URL_KEYS else
             check_https(re.sub(r"\{(name|email)\}", "x", value)))
        except ValueError as exc:
            raise ValueError(f"{label}: {exc}") from None
    elif kind == "email" and not normalise_email(value):
        raise ValueError(f"{label}: enter a valid email address.")
    elif kind == "number" and not (str(value).isdigit() and 0 < int(value) < 65536):
        raise ValueError(f"{label}: enter a number.")
    elif kind == "select" and value not in f.get("options", []):
        raise ValueError(f"{label}: choose one of {', '.join(f.get('options', []))}.")
    elif kind == "phone" and not PHONE.match(str(value)):
        raise ValueError(f"{label}: enter a phone number with country code.")


def validate(provider: dict, values: dict, resolver=None) -> None:
    """Raise ValueError with a plain message when something is missing or wrong."""
    gaps = missing(provider, values)
    if gaps:
        raise ValueError("Please fill in: " + ", ".join(gaps))
    eff = effective(provider, values)
    for f in provider["fields"]:
        if eff.get(f["key"]) not in (None, "", []):
            _check_field(f, eff[f["key"]], resolver)
    if provider["id"] == "click_to_call" and "{phone}" not in eff.get("click_to_call_url", ""):
        raise ValueError("The link must contain {phone} where the number goes.")
    if provider.get("custom") and provider["id"] == "custom_http":
        from .custom_http import validate_template
        validate_template(eff, provider.get("placeholders", ()))


# ---------- writes (each one atomic through store.update) ----------
def _change(store, ch_id: str, fn) -> dict:
    iid = channel(ch_id)["integration_id"]

    def apply(saved: dict) -> dict:
        return {**saved, iid: fn(dict(saved.get(iid) or {}))}
    return store.update("integrations", apply).get(iid, {})


def _with_record(entry: dict, pid: str, ch_id: str, **changes) -> dict:
    providers = dict(entry.get("providers") or {})
    rec = dict(providers.get(pid) or {"values": provider_values(ch_id, entry, pid)})
    rec.update(changes)
    providers[pid] = rec
    return {**entry, "providers": providers}


def save_provider(store, ch_id: str, pid: str, new_values: dict, resolver=None) -> None:
    provider = get_provider(ch_id, pid)
    current = entry_of(store.read("integrations"), ch_id)
    validate(provider, merge(provider, provider_values(ch_id, current, pid), new_values), resolver)
    exclusive = channel(ch_id)["kind"] == "exclusive"

    def fn(entry: dict) -> dict:
        merged = merge(provider, provider_values(ch_id, entry, pid), new_values)
        rec = {k: v for k, v in record(entry, pid).items() if k != "last_test"}
        out = {**entry, "providers": {**(entry.get("providers") or {}), pid: {**rec, "values": merged, "saved_at": now_iso()}}}
        if not exclusive:
            return {**out, "mode": "api"}
        if active_provider(ch_id, entry) in (None, pid):
            out = {**out, "mode": "api", "values": {**merged, "provider": pid}}
        return out
    _change(store, ch_id, fn)


def set_active(store, ch_id: str, pid: str) -> None:
    get_provider(ch_id, pid)
    if channel(ch_id)["kind"] != "exclusive":
        raise ValueError("Every saved provider in this channel is already in use; there is no single active one.")
    if status(ch_id, entry_of(store.read("integrations"), ch_id), pid) not in ("configured", "error"):
        raise ValueError("Finish setting up this provider first.")

    def fn(entry: dict) -> dict:
        current = active_provider(ch_id, entry)
        if current and current != pid:
            entry = _with_record(entry, current, ch_id, values=provider_values(ch_id, entry, current))
        chosen = provider_values(ch_id, entry, pid)
        return {**entry, "mode": "api", "values": {**chosen, "provider": pid}}
    _change(store, ch_id, fn)


def delete_provider(store, ch_id: str, pid: str) -> None:
    get_provider(ch_id, pid)

    def fn(entry: dict) -> dict:
        was_active = active_provider(ch_id, entry) == pid
        providers = {k: v for k, v in (entry.get("providers") or {}).items() if k != pid}
        out = {**entry, "providers": providers}
        if was_active:
            out = {**out, "values": {}, "mode": ""}
        return out
    _change(store, ch_id, fn)


def record_test(store, ch_id: str, pid: str, result: dict) -> dict:
    last = {"ok": bool(result.get("ok")), "message": str(result.get("message", ""))[:300], "at": now_iso()}

    def fn(entry: dict) -> dict:
        return _with_record(entry, pid, ch_id, last_test=last) if is_saved(ch_id, entry, pid) else entry
    _change(store, ch_id, fn)
    return last


def set_tokens(store, ch_id: str, pid: str, tokens: dict) -> None:
    def fn(entry: dict) -> dict:
        old = record(entry, pid).get("tokens") or {}
        merged = {**old, **{k: v for k, v in tokens.items() if v}}
        return _with_record(entry, pid, ch_id, tokens=merged, connected_at=now_iso())
    _change(store, ch_id, fn)


def set_state(store, ch_id: str, pid: str, **changes) -> None:
    def fn(entry: dict) -> dict:
        return _with_record(entry, pid, ch_id, state={**(record(entry, pid).get("state") or {}), **changes})
    _change(store, ch_id, fn)


def webhook_token(store, ch_id: str, pid: str, rotate: bool = False) -> str:
    """The provider's webhook secret, created on first use (kept apart, so creating it does not 'save' the provider)."""
    existing = (entry_of(store.read("integrations"), ch_id).get("webhook_tokens") or {}).get(pid)
    if existing and not rotate:
        return existing

    def fn(entry: dict) -> dict:
        tokens = dict(entry.get("webhook_tokens") or {})
        if rotate or not tokens.get(pid):
            tokens[pid] = new_token()
        return {**entry, "webhook_tokens": tokens}
    return _change(store, ch_id, fn)["webhook_tokens"][pid]


# ---------- reads for adapters and routes ----------
def config_for(store, ch_id: str, pid: str = None):
    """{"provider", "values" (defaults filled), "record"} for a saved, complete provider (active when pid is None)."""
    entry = entry_of(store.read("integrations"), ch_id)
    pid = pid or active_provider(ch_id, entry)
    if not pid or (ch_id, pid) not in BY_KEY or not is_saved(ch_id, entry, pid):
        return None
    provider = get_provider(ch_id, pid)
    values = provider_values(ch_id, entry, pid)
    if missing(provider, values):
        return None
    return {"provider": pid, "values": effective(provider, values), "record": record(entry, pid)}


def describe(saved: dict, legacy_items: list, settings: dict) -> list:
    legacy_by_id = {i["id"]: i for i in legacy_items}
    out = []
    for ch in CHANNELS:
        if ch["kind"] == "legacy":
            provs = [{**p, "fields": legacy_by_id[p["id"]]["fields"], "values": legacy_by_id[p["id"]]["values"],
                      "mode": legacy_by_id[p["id"]]["mode"], "status": legacy_by_id[p["id"]]["status"],
                      "last_test": None, "active": False, "saved": legacy_by_id[p["id"]]["status"] != "not_configured"}
                     for p in providers_for(ch["id"])]
            out.append({**ch, "active": None, "live": None, "status": _best(provs), "providers": provs})
            continue
        entry = entry_of(saved, ch["id"])
        active = active_provider(ch["id"], entry)
        provs = []
        for p in providers_for(ch["id"]):
            rec = record(entry, p["id"])
            provs.append({**p, "values": shown_values(p, provider_values(ch["id"], entry, p["id"])),
                          "status": status(ch["id"], entry, p["id"]), "last_test": rec.get("last_test"),
                          "active": p["id"] == active, "saved": is_saved(ch["id"], entry, p["id"]),
                          "oauth_connected": oauth_connected(rec) if p.get("oauth") else None})
        ch_status = next((p["status"] for p in provs if p["active"]), "not_configured") if ch["kind"] == "exclusive" else _best(provs)
        live = bool(settings.get(ch["live_flag"])) if ch.get("live_flag") else None
        out.append({**ch, "active": active, "live": live, "status": ch_status, "providers": provs})
    return out


def channel_status(saved: dict, ch_id: str) -> str:
    """Status of a hub channel: its active provider (exclusive) or its best saved provider (multi)."""
    entry = entry_of(saved, ch_id)
    if channel(ch_id)["kind"] == "exclusive":
        active = active_provider(ch_id, entry)
        return status(ch_id, entry, active) if active else "not_configured"
    return _best([{"status": status(ch_id, entry, p["id"])} for p in providers_for(ch_id)])


def _best(provs: list) -> str:
    for wanted in ("configured", "error", "incomplete"):
        if any(p["status"] == wanted for p in provs):
            return wanted
    return "not_configured"
