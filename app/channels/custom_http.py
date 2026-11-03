"""Custom HTTP connector: send through any provider's API from a template the owner writes.

config = {method, url, headers: [{key, value, secret}], body_type: json|form|none, body_template,
          success_path, success_value, id_path, join_url_path?}
Placeholders look like {{text}}. In a JSON body they are JSON-escaped, in a form body and the URL they are
URL-encoded, and line breaks are stripped from header values. Unknown placeholders are refused, so nothing
goes out half-filled.
"""
import json
import re
from urllib.parse import quote, quote_plus

from .catalog_common import PLACEHOLDERS
from .http import request
from .netguard import check_public_url
from .security import mask

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
METHODS = ("GET", "POST", "PUT", "PATCH")
RESPONSE_PREVIEW_BYTES = 2048
SAMPLE = {
    "to": "+919812345678", "phone": "+919812345678", "email": "test.lead@example.com",
    "subject": "Test from Outreach OS", "text": 'Hello from Outreach OS. A "test" message.', "name": "Test Lead",
    "company": "Example Pvt Ltd", "template": "first_after_yes", "title": "BookMyInterview.ai demo — Example",
    "start": "2026-11-05T10:00:00+05:30", "end": "2026-11-05T10:30:00+05:30", "join_url": "https://meet.example.com/test",
}


def placeholders_in(text) -> set:
    return set(PLACEHOLDER.findall(str(text or "")))


def _all_template_text(cfg: dict) -> str:
    headers = " ".join(str(h.get("value", "")) for h in cfg.get("headers") or [] if isinstance(h, dict))
    return " ".join([str(cfg.get("url", "")), str(cfg.get("body_template", "")), headers])


def _fill(text: str, variables: dict, escape) -> str:
    return PLACEHOLDER.sub(lambda m: escape(str(variables.get(m.group(1)) or "")), str(text or ""))


def _json_escape(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _header_safe(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")


def render_custom(config: dict, variables: dict, allowed=PLACEHOLDERS) -> dict:
    """The exact request that would be sent: {method, url, headers, body}. Raises ValueError when invalid."""
    unknown = placeholders_in(_all_template_text(config)) - set(allowed)
    if unknown:
        raise ValueError("Unknown placeholder(s): " + ", ".join("{{%s}}" % u for u in sorted(unknown)))
    method = str(config.get("method") or "POST").upper()
    if method not in METHODS:
        raise ValueError("Method must be GET, POST, PUT or PATCH.")
    url = _fill(config.get("url", ""), variables, lambda v: quote(v, safe=""))
    headers = {h["key"]: _fill(h.get("value", ""), variables, _header_safe)
               for h in config.get("headers") or [] if isinstance(h, dict) and h.get("key")}
    lower = {k.lower() for k in headers}
    body_type, template = config.get("body_type") or "json", config.get("body_template") or ""
    body = None
    if method != "GET" and body_type != "none" and template.strip():
        if body_type == "form":
            body = _fill(template, variables, quote_plus)
            if "content-type" not in lower:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = _fill(template, variables, _json_escape)
            try:
                json.loads(body)
            except ValueError:
                raise ValueError("The body is not valid JSON once the placeholders are filled in. Check quotes "
                                 "and commas (put text placeholders inside quotes).") from None
            if "content-type" not in lower:
                headers["Content-Type"] = "application/json"
    return {"method": method, "url": url, "headers": headers, "body": body}


def validate_template(config: dict, allowed=PLACEHOLDERS) -> None:
    render_custom(config, SAMPLE, allowed)


def masked_preview(rendered: dict, config: dict) -> dict:
    secret = {str(h.get("key", "")).lower() for h in config.get("headers") or [] if isinstance(h, dict) and h.get("secret")}
    headers = {k: (mask(v) if k.lower() in secret else v) for k, v in rendered["headers"].items()}
    return {**rendered, "headers": headers}


def get_path(data, path: str):
    """Follow a dot path such as data.items.0.id; None when any step is missing."""
    current = data
    for part in [p for p in str(path or "").split(".") if p]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _text(value) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def is_success(reply: dict, config: dict) -> bool:
    path = (config.get("success_path") or "").strip()
    if not path:
        return True
    value = get_path(reply, path)
    expected = (config.get("success_value") or "").strip()
    if expected:
        return value is not None and _text(value).lower() == expected.lower()
    return bool(value)


def response_preview(reply: dict) -> str:
    shown = {k: v for k, v in (reply or {}).items() if k not in ("_headers",)}
    text = json.dumps(shown, ensure_ascii=False, default=str)
    return text[:RESPONSE_PREVIEW_BYTES] + ("… (cut)" if len(text) > RESPONSE_PREVIEW_BYTES else "")


def call_custom(config: dict, variables: dict, transport=request, resolver=None, allowed=PLACEHOLDERS) -> dict:
    """Send one request. Returns {ok, provider_id, join_url, error?, preview (keys hidden), response (≤2 KB)}."""
    try:
        rendered = render_custom(config, variables, allowed)
        check_public_url(rendered["url"], resolver)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "preview": None, "response": ""}
    preview = masked_preview(rendered, config)
    try:
        reply = transport(rendered["url"], rendered["headers"], None, method=rendered["method"],
                          raw=rendered["body"].encode("utf-8") if rendered["body"] is not None else None)
    except PermissionError:
        return {"ok": False, "error": "The provider refused the request (HTTP 401/403). Check the key in the headers.",
                "preview": preview, "response": ""}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"The provider answered with an error: {str(exc)[:300]}", "preview": preview,
                "response": ""}
    ok = is_success(reply, config)
    out = {"ok": ok, "provider_id": _text(get_path(reply, config.get("id_path")) or ""), "preview": preview,
           "response": response_preview(reply)}
    if config.get("join_url_path"):
        out["join_url"] = _text(get_path(reply, config["join_url_path"]) or "")
    if not ok:
        out["error"] = "The reply did not say success. Check the success field and value."
    return out
