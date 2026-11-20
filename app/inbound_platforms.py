"""Platform-specific parts of lead capture: Meta Lead Ads (signed webhook plus a Graph API fetch) and Google Ads lead forms.

The Graph API call goes through the app's guarded HTTP client (public addresses only, size cap, timeout) and is
injectable, so tests never touch the network. The page access token is sent in the address as Meta documents and is
never logged or returned.
"""
import hashlib
import hmac
import json
from urllib.parse import quote, urlencode

from .channels.netguard import guarded_request
from .inbound_fields import clip, extract

GRAPH_HOST = "https://graph.facebook.com"
DEFAULT_GRAPH_VERSION = "v19.0"
META_ATTRIBUTION = ("campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name", "form_id", "form_name")


class PlatformError(RuntimeError):
    """The platform could not be asked; the message is safe to show and log. `status` / `code` are Meta's HTTP status and
    error code when it answered with an error (for back-off and retry decisions), else None."""

    def __init__(self, message: str, status: int = None, code: int = None):
        super().__init__(message)
        self.status, self.code = status, code


def _graph_error(status: int, body: bytes) -> PlatformError:
    """A Graph API error as a plain message. Only Meta's numeric error code is read; its own text is not repeated (it can
    quote request details)."""
    code = None
    try:
        err = json.loads(body.decode("utf-8")).get("error") or {}
        code = int(err.get("code")) if str(err.get("code", "")).lstrip("-").isdigit() else None
    except (ValueError, AttributeError, UnicodeDecodeError):
        pass
    if code == 190:
        text = "Meta refused the page access token (expired, revoked or wrong). Paste a new one."
    elif code in (10,) or (code is not None and 200 <= code < 300):
        text = "The page access token is missing a permission (leads_retrieval, pages_show_list, pages_read_engagement or ads_management)."
    elif code in (4, 17, 32, 613, 80001):
        text = "Meta says too many requests for now; the next pull waits longer."
    elif code == 100:
        text = "Meta did not accept a page or form id (check the ids belong to this page and token)."
    else:
        text = f"Meta answered with error {status} (check the page access token and permissions)."
    return PlatformError(text, status, code)


def graph_get(url: str) -> dict:
    """Default transport: one guarded GET that returns the parsed JSON. Errors say only what failed, never the address."""
    try:
        resp = guarded_request(url, "GET", {"Accept": "application/json"}, timeout=10, max_bytes=500_000)
    except (OSError, ValueError) as exc:
        raise PlatformError("Meta could not be reached.") from exc
    if resp.status >= 400:
        raise _graph_error(resp.status, resp.body or b"")
    try:
        data = json.loads(resp.body.decode("utf-8"))
    except ValueError as exc:
        raise PlatformError("Meta did not answer with JSON.") from exc
    if not isinstance(data, dict):
        raise PlatformError("Meta did not answer with a lead.")
    return data


def meta_signature_ok(app_secret: str, header: str, body: bytes) -> bool:
    if not app_secret or not header:
        return False
    expected = "sha256=" + hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header.encode("utf-8", "replace"), expected.encode("utf-8"))


def meta_events(payload) -> list:
    """One dict per lead notification: Meta's entry[].changes[].value, or a payload that is already a lead."""
    if not isinstance(payload, dict):
        return []
    events = []
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value")
            if isinstance(value, dict) and (change.get("field") in (None, "leadgen")) and (value.get("leadgen_id") or value.get("field_data")):
                events.append(value)
    if not events and (payload.get("field_data") or payload.get("leadgen_id") or payload.get("email")):
        events.append(payload)           # Zapier / Make Meta modules and hand-made tests post the lead itself
    return events[:25]


def meta_lead(event: dict, app_token: str, version: str, transport) -> dict:
    """Parsed lead for one notification, fetching the answers from Meta when the notification has only ids."""
    data = dict(event)
    if not data.get("field_data") and not data.get("email"):
        leadgen_id = clip(data.get("leadgen_id"), 60)
        if not leadgen_id.isdigit():
            raise PlatformError("The notification has no valid lead id.")
        if not app_token:
            raise PlatformError("The page access token is not saved yet.")
        version = version if version and version.replace("v", "").replace(".", "").isdigit() else DEFAULT_GRAPH_VERSION
        url = f"{GRAPH_HOST}/{quote(version)}/{leadgen_id}?{urlencode({'access_token': app_token})}"
        fetched = transport(url)
        data = {**data, **{k: v for k, v in fetched.items() if k != "id"}}
    extra = {k: data[k] for k in META_ATTRIBUTION if data.get(k)}
    if data.get("adgroup_id"):
        extra.setdefault("adset_id", data["adgroup_id"])
    extra["platform"] = "meta"
    parsed = extract(data, extra_attribution=extra)
    parsed["platform_id"] = clip(event.get("leadgen_id") or data.get("leadgen_id") or data.get("id"), 100) or parsed["platform_id"]
    parsed["ids"] = {k: clip(event[k], 100) for k in ("leadgen_id", "form_id", "page_id", "ad_id", "adgroup_id") if event.get(k)}
    return parsed


def google_lead(payload: dict) -> dict:
    """Parsed lead from a Google Ads lead-form webhook body (user_column_data plus campaign ids)."""
    extra = {"platform": "google", "campaign_id": payload.get("campaign_id"), "adset_id": payload.get("adgroup_id"),
             "ad_id": payload.get("creative_id"), "form_id": payload.get("form_id"), "click_id": payload.get("gcl_id")}
    parsed = extract(payload, extra_attribution={k: v for k, v in extra.items() if v})
    parsed["platform_id"] = clip(payload.get("lead_id"), 100) or parsed["platform_id"]
    parsed["ids"] = {k: clip(payload[k], 100) for k in ("lead_id", "form_id", "campaign_id", "adgroup_id", "creative_id") if payload.get(k)}
    parsed["is_test"] = payload.get("is_test") in (True, "true", "True", 1, "1")
    return parsed
