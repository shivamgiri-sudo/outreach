"""WhatsApp delivery through the active provider: Meta Cloud API, Gupshup, Interakt, WATI, AiSensy or custom HTTP.

send_whatsapp(...) returns "dry_run" unless the channel is connected AND settings.live_whatsapp is true.
It does not check consent or the 24-hour window; app/whatsapp.py does that before calling it.
"""
import json
import re
from urllib.parse import quote

from .config import config_for
from .custom_http import call_custom
from .http import request
from .netguard import check_public_url
from .phone import digits, e164, national, safe_id, values_list
from .registry import parse_template_map

GRAPH = "https://graph.facebook.com"
GUPSHUP_TEMPLATE_URL = "https://api.gupshup.io/wa/api/v1/template/msg"
GUPSHUP_TEXT_URL = "https://api.gupshup.io/wa/api/v1/msg"
INTERAKT_URL = "https://api.interakt.ai/v1/public/message/"
AISENSY_URL = "https://backend.aisensy.com/campaign/t1/api/v2"
GRAPH_VERSION = re.compile(r"^v\d{1,2}\.\d$")


def _meta(values, to, name, lang, params, text, transport):
    version = values.get("graph_version") or "v23.0"
    if not GRAPH_VERSION.match(version):
        raise ValueError("The Graph API version should look like v23.0.")
    url = f"{GRAPH}/{version}/{safe_id(values['phone_number_id'], 'Phone number ID')}/messages"
    if text:
        body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": digits(to), "type": "text",
                "text": {"preview_url": False, "body": text}}
    else:
        template = {"name": name, "language": {"code": lang}}
        if params:
            template["components"] = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]
        body = {"messaging_product": "whatsapp", "to": digits(to), "type": "template", "template": template}
    reply = transport(url, {"Authorization": f"Bearer {values['access_token']}"}, body)
    messages = reply.get("messages") or [{}]
    return messages[0].get("id", "")


def _gupshup(values, to, name, lang, params, text, transport):
    form = {"channel": "whatsapp", "source": digits(values["source_number"]), "destination": digits(to),
            "src.name": values["app_name"]}
    if text:
        url, form = GUPSHUP_TEXT_URL, {**form, "message": json.dumps({"type": "text", "text": text})}
    else:
        url, form = GUPSHUP_TEMPLATE_URL, {**form, "template": json.dumps({"id": name, "params": params})}
    reply = transport(url, {"apikey": values["api_key"]}, None, method="POST", form=form)
    if str(reply.get("status", "")).lower() not in ("submitted", "success"):
        raise OSError(f"Gupshup said: {reply.get('message') or reply.get('status') or 'not accepted'}")
    return str(reply.get("messageId", ""))


def _interakt(values, to, name, lang, params, text, transport):
    cc = values.get("country_code") or "+91"
    body = {"countryCode": cc, "phoneNumber": national(to, cc)}
    if text:
        body.update({"type": "Text", "data": {"message": text}})
    else:
        body.update({"type": "Template", "template": {"name": name, "languageCode": lang, "bodyValues": params}})
    reply = transport(INTERAKT_URL, {"Authorization": f"Basic {values['api_key']}"}, body)
    if reply.get("result") is False:
        raise OSError(f"Interakt said: {reply.get('message', 'not accepted')}")
    return str(reply.get("id", ""))


def _wati(values, to, name, lang, variables, text, transport, resolver):
    base = check_public_url(values["api_endpoint"], resolver).rstrip("/")
    auth = {"Authorization": "Bearer " + values["access_token"].removeprefix("Bearer ").strip()}
    number = digits(to)
    if text:
        reply = transport(f"{base}/api/v1/sendSessionMessage/{number}?messageText={quote(text)}", auth, None, method="POST")
    else:
        items = variables.items() if isinstance(variables, dict) else ((str(i + 1), v) for i, v in enumerate(variables or []))
        body = {"template_name": name, "broadcast_name": "outreach_os",
                "parameters": [{"name": str(k), "value": str(v)} for k, v in items]}
        reply = transport(f"{base}/api/v1/sendTemplateMessage?whatsappNumber={number}", auth, body)
    if reply.get("result") is False:
        raise OSError(f"WATI said: {reply.get('info') or reply.get('message') or 'not accepted'}")
    return str(reply.get("messageId") or reply.get("id") or "")


def _aisensy(values, to, name, lang, params, text, transport):
    if text:
        raise ValueError("AiSensy's API sends approved templates (API campaigns) only, not free text.")
    body = {"apiKey": values["api_key"], "campaignName": name, "destination": digits(to), "userName": "Lead",
            "templateParams": params, "source": "outreach-os"}
    reply = transport(AISENSY_URL, {}, body)
    if str(reply.get("success", "true")).lower() == "false":
        raise OSError(f"AiSensy said: {reply.get('message', 'not accepted')}")
    return str(reply.get("submitted_message_id") or reply.get("id") or "")


def deliver_whatsapp(cfg: dict, to_phone: str, template_name: str = "", language: str = "", variables=None,
                     text: str = None, transport=request, resolver=None) -> dict:
    pid, values = cfg["provider"], cfg["values"]
    to = e164(to_phone)
    if not to:
        return {"status": "failed", "error": "That phone number is not valid (include the country code)."}
    if not text and not template_name:
        return {"status": "failed", "error": "Choose an approved template, or write a message within the 24-hour window."}
    name = parse_template_map(values.get("template_map")).get(template_name, template_name)
    lang = language or values.get("template_language") or "en"
    params = values_list(variables)
    try:
        if pid == "meta_cloud":
            pid_ = _meta(values, to, name, lang, params, text, transport)
        elif pid == "gupshup":
            pid_ = _gupshup(values, to, name, lang, params, text, transport)
        elif pid == "interakt":
            pid_ = _interakt(values, to, name, lang, params, text, transport)
        elif pid == "wati":
            pid_ = _wati(values, to, name, lang, variables, text, transport, resolver)
        elif pid == "aisensy":
            pid_ = _aisensy(values, to, name, lang, params, text, transport)
        elif pid == "custom_http":
            result = call_custom(values, {"to": to, "phone": to, "template": name, "text": text or ""}, transport, resolver)
            if not result["ok"]:
                return {"status": "failed", "error": result.get("error", "failed")}
            pid_ = result.get("provider_id", "")
        else:
            return {"status": "failed", "error": f"Unknown WhatsApp provider {pid}"}
    except PermissionError:
        return {"status": "failed", "error": "The provider rejected the key or token."}
    except (OSError, ValueError, KeyError) as exc:
        return {"status": "failed", "error": str(exc)[:300]}
    return {"status": "sent", "provider_id": pid_}


def send_whatsapp(store, to_phone: str, template_name: str, language: str = "", variables=None, text: str = None,
                  transport=request, resolver=None) -> dict:
    """{"status": "sent"|"failed"|"dry_run", "provider_id"?, "error"?}. Real sends need live_whatsapp on."""
    cfg = config_for(store, "whatsapp")
    if not (cfg and store.read("settings").get("live_whatsapp")):
        return {"status": "dry_run"}
    return deliver_whatsapp(cfg, to_phone, template_name, language, variables, text, transport, resolver)
