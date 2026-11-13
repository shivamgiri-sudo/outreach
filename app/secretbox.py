"""Saved keys encrypted at rest in store.json (Fernet: AES-128-CBC + HMAC-SHA256, from the `cryptography` package).

With OUTREACH_SECRET_KEY set, the store encrypts on every save and decrypts on load, so the rest of the app only
ever sees plain values in memory and nothing else has to change. What is encrypted:
  - integration fields marked secret in the catalogues (API keys, passwords, tokens, signing secrets),
  - every value under `tokens` (OAuth) and `webhook_tokens`, and header rows marked secret (custom connector),
  - settings.server_secret and settings.webhook_secret.
Encrypted values look like "enc:v1:<token>". Plain values found on load are encrypted at the next save (and the
store saves once at start-up to migrate them, backups included).

The key may be a Fernet key or any long random string (it is stretched with SHA-256). Without the key, values stay
in plain text and Setup shows a warning. With a missing or wrong key, encrypted values are kept as they are (never
lost or overwritten) and Setup says so.
"""
import base64
import hashlib
import os
from functools import lru_cache

PREFIX = "enc:v1:"
ENV_KEY = "OUTREACH_SECRET_KEY"
SECRET_SETTINGS = ("server_secret", "webhook_secret")
CONTAINERS = ("tokens", "webhook_tokens")       # every string inside is a secret
CT_SECRET_KEYS = ("api_key", "webhook_secret")  # Call Tracker connections (ct_connections: {workspace: {...}})
EXTRA_SECRET_KEYS = ("access_token", "refresh_token", "id_token")


@lru_cache(maxsize=1)
def secret_keys() -> frozenset:
    """Field names marked secret in the Integrations hub and Setup catalogues."""
    from . import integrations
    from .channels.registry import CHANNELS, providers_for
    keys = set(EXTRA_SECRET_KEYS)
    for ch in CHANNELS:
        for provider in providers_for(ch["id"]):
            keys.update(f["key"] for f in provider.get("fields", []) if f.get("secret"))
    for item in integrations.CATALOGUE + integrations.SOURCE_CATALOGUE:
        keys.update(f["key"] for f in item["fields"] if f.get("secret"))
    return frozenset(keys)


def _fernet(key_text: str):
    from cryptography.fernet import Fernet
    try:
        return Fernet(key_text.encode())
    except (ValueError, TypeError):
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key_text.encode("utf-8")).digest()))


class SecretBox:
    def __init__(self, key_text: str = None):
        key = os.environ.get(ENV_KEY, "") if key_text is None else key_text
        self._fernet = _fernet(key) if key else None
        self._cache = {}               # plain -> sealed, so unchanged values don't get a new ciphertext on every save
        self.failures = 0              # encrypted values that could not be opened (missing or wrong key)

    @property
    def enabled(self) -> bool:
        return self._fernet is not None

    # ---------- single values ----------
    def seal_value(self, value):
        if not self.enabled or not isinstance(value, str) or not value or value.startswith(PREFIX):
            return value
        if value not in self._cache:
            self._cache[value] = PREFIX + self._fernet.encrypt(value.encode("utf-8")).decode()
        return self._cache[value]

    def open_value(self, value):
        if not (isinstance(value, str) and value.startswith(PREFIX)):
            return value
        if not self.enabled:
            self.failures += 1
            return value
        from cryptography.fernet import InvalidToken
        try:
            plain = self._fernet.decrypt(value[len(PREFIX):].encode()).decode("utf-8")
        except (InvalidToken, ValueError):
            self.failures += 1
            return value
        self._cache[plain] = value
        return plain

    # ---------- the store ----------
    def _walk(self, node, fn, inside_secret=False):
        if isinstance(node, dict):
            out = {}
            secret_row = node.get("secret") is True and "key" in node and "value" in node
            for key, value in node.items():
                if key in CONTAINERS and isinstance(value, dict):
                    out[key] = {k: self._walk(v, fn, True) for k, v in value.items()}
                elif (key in secret_keys() or (secret_row and key == "value") or inside_secret) and isinstance(value, str):
                    out[key] = fn(value)
                else:
                    out[key] = self._walk(value, fn, inside_secret)
            return out
        if isinstance(node, list):
            return [self._walk(item, fn, inside_secret) for item in node]
        return fn(node) if inside_secret and isinstance(node, str) else node

    def _apply(self, data: dict, fn) -> dict:
        out = dict(data)
        if isinstance(data.get("integrations"), dict):
            out["integrations"] = self._walk(data["integrations"], fn)
        if isinstance(data.get("ct_connections"), dict):
            out["ct_connections"] = {ws: ({k: (fn(v) if k in CT_SECRET_KEYS and isinstance(v, str) else v) for k, v in conn.items()}
                                          if isinstance(conn, dict) else conn) for ws, conn in data["ct_connections"].items()}
        if isinstance(data.get("settings"), dict):
            out["settings"] = {k: (fn(v) if k in SECRET_SETTINGS else self._walk(v, fn) if k == "account_settings" and isinstance(v, dict) else v)
                               for k, v in data["settings"].items()}      # a client account's own keys (sending, WhatsApp) too
        return out

    def seal(self, data: dict) -> dict:
        return self._apply(data, self.seal_value) if self.enabled else data

    def unseal(self, data: dict) -> dict:
        return self._apply(data, self.open_value)

    def plain_secrets(self, data: dict) -> int:
        """How many secret values are stored unencrypted (what the next save would encrypt)."""
        found = []
        self._apply(data, lambda v: found.append(v) or v if isinstance(v, str) and v and not v.startswith(PREFIX) else v)
        return len(found)
