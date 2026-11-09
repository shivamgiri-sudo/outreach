"""A client account's own WhatsApp provider.

Stored in settings["account_settings"][id]["whatsapp"] = {provider, values, live, saved_at}. The provider catalogue and the
field checks are the hub's (app/channels/registry.py, app/channels/config.py), so a client picks the same providers an
owner does. Secret fields (tokens, API keys) are sealed on disk like the other integration secrets (app/secretbox.py walks
settings["account_settings"]) and are never sent back: the page gets "••••••••" and a blank or masked value keeps the saved one.

Who sends what:
  an account with its own complete provider     sends through it (practice only until its own "live" switch is on)
  an account without one, with a WhatsApp grant sends through the platform's provider (the platform's live switch applies)
  an account with neither                       WhatsApp is held (accounts.hold_reason says so)
  Mas Callnet                                   unchanged: the platform's provider
"""
from typing import Optional

from . import accounts
from .channels import config as hub
from .channels.registry import BY_KEY, providers_for
from .channels.security import is_masked
from .channels.whatsapp_send import deliver_whatsapp, send_whatsapp
from .lead_model import now_iso

CHANNEL = "whatsapp"
HIDDEN = "••••••••"


def own_row(source, account_id: str) -> dict:
    row = accounts.own_settings(source, account_id).get(CHANNEL)
    return row if isinstance(row, dict) else {}


def own_config(source, account_id: str) -> Optional[dict]:
    """{provider, values} when the account saved a complete provider, else None."""
    row = own_row(source, account_id)
    if not row.get("provider") or (CHANNEL, row["provider"]) not in BY_KEY:
        return None
    provider = BY_KEY[(CHANNEL, row["provider"])]
    values = row.get("values") or {}
    if hub.missing(provider, values):
        return None
    return {"provider": row["provider"], "values": hub.effective(provider, values)}


def has_own(source, account_id: str) -> bool:
    return account_id not in (None, "", accounts.MCN) and own_config(source, account_id) is not None


def save(store, account_id: str, provider_id: str, values: dict, live: Optional[bool] = None, resolver=None) -> dict:
    """Validate and keep the account's provider. A masked or blank secret keeps the one already saved."""
    if account_id == accounts.MCN or accounts.find(store, account_id) is None:
        raise ValueError("Mas Callnet uses the platform's WhatsApp connection in Integrations.")
    if (CHANNEL, provider_id) not in BY_KEY:
        raise ValueError("Pick one of the WhatsApp providers in the list.")
    provider = BY_KEY[(CHANNEL, provider_id)]
    old = own_row(store, account_id)
    previous = (old.get("values") or {}) if old.get("provider") == provider_id else {}
    merged = hub.merge(provider, previous, values or {})
    hub.validate(provider, merged, resolver)

    def apply(s: dict) -> dict:
        every = dict(s.get("account_settings") or {})
        mine = dict(every.get(account_id) or {})
        mine[CHANNEL] = {"provider": provider_id, "values": merged, "saved_at": now_iso(),
                         "live": bool(live) if live is not None else bool(old.get("live"))}
        every[account_id] = mine
        return {**s, "account_settings": every}
    store.update("settings", apply)
    return shown(store, account_id)


def remove(store, account_id: str) -> None:
    def apply(s: dict) -> dict:
        every = dict(s.get("account_settings") or {})
        every[account_id] = {k: v for k, v in (every.get(account_id) or {}).items() if k != CHANNEL}
        return {**s, "account_settings": every}
    store.update("settings", apply)


def set_live(store, account_id: str, on: bool) -> None:
    if own_config(store, account_id) is None:
        raise ValueError("Connect your WhatsApp provider first.")

    def apply(s: dict) -> dict:
        every = dict(s.get("account_settings") or {})
        mine = dict(every.get(account_id) or {})
        mine[CHANNEL] = {**mine.get(CHANNEL, {}), "live": bool(on)}
        every[account_id] = {**mine}
        return {**s, "account_settings": every}
    store.update("settings", apply)


def catalogue() -> list:
    return [{"id": p["id"], "title": p["title"], "why": p.get("why", ""), "docs_url": p.get("docs_url", ""),
             "fields": [{k: f.get(k) for k in ("key", "label", "required", "secret", "placeholder", "help", "kind", "options", "default")} for f in p["fields"]]}
            for p in providers_for(CHANNEL) if not p.get("custom")]


def shown(store, account_id: str) -> dict:
    """What the page may see: the provider, its non-secret values, and "••••••••" where a secret is saved. Never a secret."""
    row = own_row(store, account_id)
    provider = BY_KEY.get((CHANNEL, row.get("provider"))) if row.get("provider") else None
    values = {}
    if provider:
        for f in provider["fields"]:
            value = (row.get("values") or {}).get(f["key"])
            if value in (None, "", []) or f["kind"] == "headers":
                continue
            values[f["key"]] = HIDDEN if f.get("secret") else value
    return {"configured": own_config(store, account_id) is not None, "provider": row.get("provider") or "", "values": values,
            "live": bool(row.get("live")), "granted": accounts.granted(store, account_id, "whatsapp"), "hold": accounts.hold_reason(store, account_id, CHANNEL)}


def deliver(store, account_id: str, to_phone: str, template_name: str, language: str, params, text: Optional[str], transport, resolver=None) -> dict:
    """Send (or practise) one WhatsApp message for an account's lead through the right provider."""
    own = own_config(store, account_id) if account_id and account_id != accounts.MCN else None
    if own:
        if not own_row(store, account_id).get("live"):
            return {"status": "dry_run"}
        return deliver_whatsapp(own, to_phone, template_name or "", language or "", params, text, transport, resolver)
    return send_whatsapp(store, to_phone, template_name, language, params, text, transport, resolver)


def is_masked_value(value) -> bool:
    return is_masked(value)
