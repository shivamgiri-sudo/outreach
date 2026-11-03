"""Lead-capture connectors: create, change, rotate and look up.

They live in the integrations store under `lead_capture.connectors`; the secrets sit in each connector's `tokens`
container, which the store encrypts at rest and backups leave out. No API returns a secret after it was first shown:
create and rotate answer with the full values once, every later read shows them masked.
"""
import re
import secrets
import uuid
from datetime import datetime, timezone

from .capture_catalogue import BY_ID, TYPES
from .channels.security import is_masked, mask, new_token, safe_equal
from .growth_core import public_base_url

KEY = "lead_capture"
MAX_CONNECTORS = 50
SECRET_FIELDS = ("app_secret", "page_access_token")
PLAIN_CONFIG = ("graph_version", "allowed_origins", "min_fill_seconds", "thanks_text", "sheet_url", "assume_consent")
MAX_TAGS = 10


class ConnectorError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def all_connectors(store) -> list:
    return list((store.read("integrations").get(KEY) or {}).get("connectors") or [])


def get(store, connector_id: str):
    return next((c for c in all_connectors(store) if c.get("id") == connector_id), None)


def _save(store, change) -> None:
    def apply(saved: dict) -> dict:
        entry = dict(saved.get(KEY) or {})
        entry["connectors"] = change(list(entry.get("connectors") or []))
        return {**saved, KEY: entry}
    store.update("integrations", apply)


def update_connector(store, connector_id: str, change) -> None:
    _save(store, lambda rows: [change(dict(c)) if c.get("id") == connector_id else c for c in rows])


def bump(store, connector_id: str, outcome: str) -> None:
    def change(c: dict) -> dict:
        counters = dict(c.get("counters") or {})
        counters[outcome] = counters.get(outcome, 0) + 1
        counters["total"] = counters.get("total", 0) + 1
        return {**c, "counters": counters, "last_capture_at": now_iso()}
    update_connector(store, connector_id, change)


def urls(store, connector: dict, base_default: str) -> dict:
    base = public_base_url(store, base_default)
    cid, mode = connector["id"], BY_ID.get(connector["type"], {}).get("mode", "generic")
    out = {"capture": f"{base}/api/inbound/{cid}"}
    if mode == "meta":
        out = {"callback": f"{base}/api/inbound/meta/{cid}"}
    elif mode == "google":
        out = {"webhook": f"{base}/api/inbound/google/{cid}"}
    elif mode == "form":
        out["embed"] = f"{base}/api/inbound/{cid}/embed.js"
    elif mode == "sheet":
        out = {"sync": f"{base}/api/inbound/{cid}/sync"}
    elif mode == "whatsapp":
        out = {}
    return out


def _lines(value) -> list:
    items = value if isinstance(value, list) else re.split(r"[\n,]+", str(value or ""))
    return [i.strip().rstrip("/") for i in items if str(i).strip()][:20]


def public_view(store, connector: dict, base_default: str = "", reveal: bool = False) -> dict:
    """The connector as the owner's screen sees it: secrets masked unless this is the one-time reveal."""
    tokens = connector.get("tokens") or {}
    spec = BY_ID.get(connector["type"], {})
    secrets_out = {}
    for name in ("capture", *SECRET_FIELDS):
        if tokens.get(name):
            secrets_out[name] = tokens[name] if reveal and name == "capture" else mask(tokens[name])
    view = {k: connector.get(k) for k in ("id", "type", "name", "enabled", "created_at", "last_capture_at", "field_map",
                                          "default_owner", "default_tags", "counters", "form_key", "enroll_flow_id",
                                          "call_task", *PLAIN_CONFIG)}
    view.update(title=spec.get("title", connector["type"]), source_id=spec.get("source_id", "other_inbound"),
                mode=spec.get("mode", "generic"), urls=urls(store, connector, base_default), secrets=secrets_out,
                verify_token=tokens.get("verify") if spec.get("mode") == "meta" else None,
                configured=configured(connector), counters=connector.get("counters") or {})
    if reveal:
        view["token"] = tokens.get("capture")
    return view


def configured(connector: dict) -> bool:
    spec, tokens = BY_ID.get(connector["type"], {}), connector.get("tokens") or {}
    if spec.get("mode") == "meta":
        return bool(tokens.get("app_secret") and tokens.get("page_access_token"))
    if spec.get("mode") == "sheet":
        return bool(connector.get("sheet_url"))
    return True


def _apply_config(connector: dict, body: dict) -> dict:
    tokens = dict(connector.get("tokens") or {})
    for name in SECRET_FIELDS:
        value = body.get(name)
        if isinstance(value, str) and value.strip() and not is_masked(value):
            tokens[name] = value.strip()[:500]
    out = {**connector, "tokens": tokens}
    if "name" in body:
        name = str(body["name"] or "").strip()[:80]
        if not name:
            raise ConnectorError("Give the connector a name.")
        out["name"] = name
    if "enabled" in body:
        out["enabled"] = bool(body["enabled"])
    if "call_task" in body:
        out["call_task"] = bool(body["call_task"])
    if "default_owner" in body:
        out["default_owner"] = str(body["default_owner"] or "").strip()[:60]
    if "default_tags" in body:
        tags = body["default_tags"] if isinstance(body["default_tags"], list) else re.split(r"[,\n]+", str(body["default_tags"] or ""))
        out["default_tags"] = [str(t).strip()[:40] for t in tags if str(t).strip()][:MAX_TAGS]
    if "enroll_flow_id" in body:
        out["enroll_flow_id"] = str(body["enroll_flow_id"] or "").strip()[:60]
    if "field_map" in body:
        fm = body["field_map"]
        if not isinstance(fm, dict) or len(fm) > 40:
            raise ConnectorError("The field map must be a list of lead field to payload field pairs.")
        out["field_map"] = {str(k)[:60]: str(v)[:120] for k, v in fm.items() if str(k).strip() and str(v).strip()}
    for key in PLAIN_CONFIG:
        if key in body:
            value = body[key]
            if key == "allowed_origins":
                value = _lines(value)
                if any(not re.match(r"^https?://[^\s/]+$", v) for v in value):
                    raise ConnectorError("Each allowed website must look like https://www.example.com (no path).")
            elif key == "min_fill_seconds":
                try:
                    value = max(0, min(120, int(value or 0)))
                except (TypeError, ValueError):
                    raise ConnectorError("Minimum fill time must be a number of seconds.") from None
            elif key == "assume_consent":
                value = bool(value)
            elif key == "sheet_url":
                value = str(value or "").strip()[:1000]
                if value and not value.startswith("https://"):
                    raise ConnectorError("The sheet link must start with https://")
            else:
                value = str(value or "").strip()[:200]
            out[key] = value
    return out


def create(store, body: dict) -> dict:
    kind = str(body.get("type") or "")
    spec = BY_ID.get(kind)
    if spec is None:
        raise ConnectorError("Unknown connector type.")
    if len(all_connectors(store)) >= MAX_CONNECTORS:
        raise ConnectorError("That is as many connectors as this app keeps. Delete one you no longer use.")
    base = {"id": "cap" + uuid.uuid4().hex[:8], "type": kind, "name": str(body.get("name") or spec["title"]).strip()[:80],
            "enabled": True, "created_at": now_iso(), "last_capture_at": None, "counters": {},
            "field_map": dict(spec["preset"].get("field_map") or {}), "id_path": spec["preset"].get("id_path", ""),
            "default_owner": "", "default_tags": [], "call_task": kind != "sheet_sync",
            "tokens": {"capture": new_token()}, "form_key": "pk_" + secrets.token_urlsafe(12)}
    if spec["mode"] == "meta":
        base["tokens"]["verify"] = new_token()
    connector = _apply_config(base, {k: v for k, v in body.items() if k != "type"})
    _save(store, lambda rows: [*rows, connector])
    return connector


def change(store, connector_id: str, body: dict) -> dict:
    current = get(store, connector_id)
    if current is None:
        raise LookupError("Connector not found")
    updated = _apply_config(current, body)
    update_connector(store, connector_id, lambda c: updated)
    return updated


def remove(store, connector_id: str) -> bool:
    if get(store, connector_id) is None:
        return False
    _save(store, lambda rows: [c for c in rows if c.get("id") != connector_id])
    return True


def rotate(store, connector_id: str, which: str = "capture") -> dict:
    current = get(store, connector_id)
    if current is None:
        raise LookupError("Connector not found")
    if which not in ("capture", "verify"):
        raise ConnectorError("Rotate the capture token or the verify token.")
    if which == "verify" and BY_ID[current["type"]]["mode"] != "meta":
        raise ConnectorError("Only Meta connectors have a verify token.")
    token = new_token()
    update_connector(store, connector_id, lambda c: {**c, "tokens": {**(c.get("tokens") or {}), which: token}})
    return get(store, connector_id)


def token_ok(connector: dict, given: str, name: str = "capture") -> bool:
    return safe_equal(given, (connector.get("tokens") or {}).get(name))


def catalogue(store, base_default: str) -> dict:
    return {"base_url": public_base_url(store, base_default),
            "types": [{k: v for k, v in t.items() if k != "preset"} | {"has_preset": bool(t["preset"]), "preset_map": t["preset"].get("field_map", {})}
                      for t in TYPES],
            "standard_fields": ["name", "email", "phone", "company", "title", "city", "website", "message", "country"]}
