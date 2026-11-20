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
MAX_FORM_IDS = 20
MAIN_WS = "main"
PULL_VIEW = ("last_pull_at", "last_count", "last_fetched", "total_pulled", "last_error", "last_error_at", "next_pull_at",
             "fail_count", "forms_found", "pending")


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


def workspace_of(connector: dict) -> str:
    """The workspace every lead this connector captures goes into (missing = Main, as before workspaces were per connector)."""
    return str((connector or {}).get("workspace") or MAIN_WS)


def no_auto_messages(connector: dict) -> bool:
    """"No automatic messages": its leads are never started on a flow, sequence or campaign, nor sent a rule's WhatsApp.
    On by default for Meta Lead Ads (the business already sends its own onboarding message), off for the rest."""
    value = (connector or {}).get("no_auto_messages")
    if value is None:
        return (connector or {}).get("type") == "meta_ads"
    return bool(value)


def pull_configured(connector: dict) -> bool:
    """A Meta connector pulls leads itself once it has a page access token and form ids (or a page id to find them)."""
    tokens = (connector or {}).get("tokens") or {}
    return (BY_ID.get((connector or {}).get("type"), {}).get("mode") == "meta" and bool(tokens.get("page_access_token"))
            and bool(connector.get("form_ids") or connector.get("page_id")))


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
                                          "call_task", "page_id", *PLAIN_CONFIG)}
    view.update(workspace=workspace_of(connector), no_auto_messages=no_auto_messages(connector),
                form_ids=list(connector.get("form_ids") or []))
    if spec.get("mode") == "meta":
        state = connector.get("pull_state") or {}
        view["pull"] = {"configured": pull_configured(connector), **{k: state.get(k) for k in PULL_VIEW},
                        "forms": [{"id": f.get("id"), "name": f.get("name", "")} for f in state.get("forms") or []][:MAX_FORM_IDS * 5]}
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
        return bool(tokens.get("app_secret") and tokens.get("page_access_token")) or pull_configured(connector)
    if spec.get("mode") == "sheet":
        return bool(connector.get("sheet_url"))
    return True


def _check_workspace(store, value, user) -> str:
    """The workspace a connector may send leads into: one that exists, and that the person saving may use."""
    from . import workspaces
    ws = str(value or MAIN_WS).strip()[:40] or MAIN_WS
    if store is not None and ws not in {w["id"] for w in workspaces.all_workspaces(store)}:
        raise ConnectorError("That workspace does not exist. Pick one from the list.")
    allowed = workspaces.allowed_of(user) if user is not None else None
    if allowed is not None and ws not in allowed:
        raise ConnectorError("You cannot send leads into that workspace.")
    return ws


def _ids(value, what: str) -> list:
    items = value if isinstance(value, list) else re.split(r"[\s,]+", str(value or ""))
    out = [str(i).strip() for i in items if str(i).strip()]
    if any(not i.isdigit() or len(i) > 30 for i in out):
        raise ConnectorError(f"Each {what} is a number Meta shows (digits only).")
    return list(dict.fromkeys(out))[:MAX_FORM_IDS]


def _apply_config(connector: dict, body: dict, store=None, user=None) -> dict:
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
    if "workspace" in body:
        out["workspace"] = _check_workspace(store, body["workspace"], user)
    if "no_auto_messages" in body:
        out["no_auto_messages"] = bool(body["no_auto_messages"])
    if ("no_auto_messages" in body or "enroll_flow_id" in body) and no_auto_messages(out) and out.get("enroll_flow_id"):
        raise ConnectorError("This connector is set to No automatic messages, so it cannot start a flow. Clear one of the two.")
    if "page_id" in body:
        out["page_id"] = (_ids(body["page_id"], "page id") or [""])[0]
    if "form_ids" in body:
        out["form_ids"] = _ids(body["form_ids"], "form id")
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


def create(store, body: dict, user=None) -> dict:
    kind = str(body.get("type") or "")
    spec = BY_ID.get(kind)
    if spec is None:
        raise ConnectorError("Unknown connector type.")
    if len(all_connectors(store)) >= MAX_CONNECTORS:
        raise ConnectorError("That is as many connectors as this app keeps. Delete one you no longer use.")
    base = {"id": "cap" + uuid.uuid4().hex[:8], "type": kind, "name": str(body.get("name") or spec["title"]).strip()[:80],
            "enabled": True, "created_at": now_iso(), "last_capture_at": None, "counters": {},
            "field_map": dict(spec["preset"].get("field_map") or {}), "id_path": spec["preset"].get("id_path", ""),
            "default_owner": "", "default_tags": [], "call_task": kind != "sheet_sync", "workspace": MAIN_WS,
            "no_auto_messages": kind == "meta_ads",
            "tokens": {"capture": new_token()}, "form_key": "pk_" + secrets.token_urlsafe(12)}
    if spec["mode"] == "meta":
        base["tokens"]["verify"] = new_token()
    connector = _apply_config(base, {k: v for k, v in body.items() if k != "type"}, store, user)
    _save(store, lambda rows: [*rows, connector])
    return connector


def change(store, connector_id: str, body: dict, user=None) -> dict:
    current = get(store, connector_id)
    if current is None:
        raise LookupError("Connector not found")
    updated = _apply_config(current, body, store, user)
    keep = ("counters", "last_capture_at", "pull_state", "pull_seen", "row_hashes", "last_sync_at")
    # Settings only: counters, pull cursors and sync state written meanwhile (a background pull) are kept as they are now.
    update_connector(store, connector_id, lambda c: {**updated, **{k: c[k] for k in keep if k in c}})
    return get(store, connector_id)


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
