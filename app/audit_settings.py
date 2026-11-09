"""Settings changes in the change log: who, which keys, old -> new, with secrets masked.

Use around any save of settings (or integrations):

    with audit_settings.watch(store, request, "Settings", action="settings.save"):
        store.update("settings", ...)

or call `settings_change(store, user, area, before, after)` with two snapshots. Only keys whose value changed are
written. Nested dicts are compared one level down ("brand.name"). A key whose name looks like a secret (password,
token, api key, webhook secret, cookie …) is shown as "(hidden)" / "(empty)", never its value.
"""
import json
import re
from contextlib import contextmanager
from typing import Optional

from . import audit

SECRET = re.compile(r"(secret|password|passwd|(^|_)pass$|token|api_?key|apikey|_key$|^key$|credential|cookie|li_at|session|"
                    r"private|(^|_)auth|signature|(^|_)otp|(^|_)pin$)", re.I)
IGNORED = {"updated_at", "updated_by", "changed_at", "changed_by", "history", "at"}
MAX_VALUE = 120
MAX_CHANGES = 60


def is_secret(key: str) -> bool:
    return any(SECRET.search(part) for part in str(key or "").split("."))


def shown(value) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return "(empty)"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    text = " ".join(text.split())
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE - 1] + "…"


def _masked(key: str, old, new, all_secret: bool = False) -> tuple:
    if not (all_secret or is_secret(key)):
        return shown(old), shown(new)
    empty = lambda v: v in (None, "", [], {})
    return ("(empty)" if empty(old) else "(hidden)"), ("(empty)" if empty(new) else "(hidden, changed)")


def diff(before: Optional[dict], after: Optional[dict], prefix: str = "", depth: int = 0, all_secret: bool = False) -> list:
    """[{key, old, new}] for every changed key (secrets masked), at most MAX_CHANGES."""
    before, after = before or {}, after or {}
    out = []
    for key in sorted(set(before) | set(after), key=str):
        if key in IGNORED:
            continue
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        name = f"{prefix}{key}"
        if depth == 0 and isinstance(old, dict) and isinstance(new, dict) and not is_secret(name):
            out.extend(diff(old, new, f"{name}.", depth + 1, all_secret))
            continue
        old_text, new_text = _masked(name, old, new, all_secret and name.split(".")[-1] not in ("mode", "active", "enabled"))
        if old_text == new_text and not (all_secret or is_secret(name)):
            continue
        out.append({"key": name, "old": old_text, "new": new_text})
    return out[:MAX_CHANGES]


def describe(changes: list) -> str:
    return "; ".join(f"{c['key']}: {c['old']} -> {c['new']}" for c in changes)


def settings_change(store, user, area: str, before: Optional[dict], after: Optional[dict], action: str = "settings.change",
                    ignore: tuple = (), all_secret: bool = False) -> Optional[dict]:
    """One change-log row for the keys that changed between the two snapshots; None when nothing changed.
    `all_secret` masks every value (connections: keys, ids and tokens), except mode / active / enabled."""
    changes = [c for c in diff(before, after, all_secret=all_secret) if c["key"].split(".")[0] not in ignore]
    if not changes:
        return None
    return audit.record(store, user, action, area, describe(changes), {"changes": changes})


def _user(who):
    return getattr(getattr(who, "state", None), "user", None) if hasattr(who, "state") else who


@contextmanager
def watch(store, who, area: str, action: str = "settings.change", collection: str = "settings", key: Optional[str] = None,
          ignore: tuple = (), all_secret: bool = False):
    """Snapshot before, diff after (also when the block raises after a partial save). `who` is the request or the user.
    `key` limits the diff to one entry of the collection (e.g. the LinkedIn agent's config)."""
    pick = (lambda data: (data or {}).get(key) or {}) if key else (lambda data: data or {})
    before = pick(store.read(collection))
    try:
        yield
    finally:
        after = pick(store.read(collection))
        settings_change(store, _user(who), area, before, after, action, ignore, all_secret)
