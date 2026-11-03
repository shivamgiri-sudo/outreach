"""Test connection for every hub provider. Returns {ok, message, ...}; never raises for provider problems."""
import json
from urllib.parse import quote

from .calendar import test_calendar
from .calls import place_call
from .config import config_for, entry_of, is_saved
from .custom_http import SAMPLE, call_custom
from .email_send import test_email
from .imap_poll import check_mailbox
from .inbound_parse import custom_messages, delivery_events
from .netguard import check_public_url
from .phone import basic_auth, safe_id
from .registry import get_provider
from .sms_send import deliver_sms
from .whatsapp_send import GRAPH, deliver_whatsapp

SAVE_FIRST = {"ok": False, "message": "Save all the required details first."}


def _custom_send_test(cfg: dict, body: dict, deps: dict) -> dict:
    to = (body.get("to") or "").strip()
    sample = {**SAMPLE, **({"to": to, "email": to} if "@" in to else {"to": to, "phone": to} if to else {})}
    result = call_custom(cfg["values"], sample, deps["transport"], deps["resolver"],
                         get_provider(body["_channel"], "custom_http").get("placeholders"))
    message = "The provider said success." if result["ok"] else result.get("error", "It did not work.")
    return {"ok": result["ok"], "message": message, "preview": result.get("preview"), "response": result.get("response")}


def _custom_webhook_test(store, ch_id: str, body: dict) -> dict:
    provider = get_provider(ch_id, "custom_webhook")
    cfg = config_for(store, ch_id, "custom_webhook")
    mapping = {**(cfg["values"] if cfg else {}), **{k: v for k, v in (body.get("mapping") or {}).items() if isinstance(v, str)}}
    try:
        payload = json.loads(body.get("sample") or "")
    except ValueError:
        return {"ok": False, "message": "Paste a sample payload in JSON format.", "extracted": []}
    if provider["mapping"] == "events":
        found = delivery_events("custom_webhook", payload, mapping)
    else:
        found = custom_messages("email" if ch_id == "inbound_email" else ch_id, provider["mapping"], mapping, payload)
    if not found:
        return {"ok": False, "message": "Nothing could be read with these fields. Check the field paths against the sample.",
                "extracted": []}
    return {"ok": True, "message": f"Read {len(found)} item(s) from the sample.", "extracted": found[:5]}


def _whatsapp_test(cfg: dict, body: dict, deps: dict) -> dict:
    to, transport = (body.get("to") or "").strip(), deps["transport"]
    values, pid = cfg["values"], cfg["provider"]
    if to:
        template = (body.get("template") or "").strip() or ("hello_world" if pid == "meta_cloud" else "")
        if not template and pid != "custom_http":
            return {"ok": False, "message": "Type the name of an approved template to send the test with."}
        language = "en_US" if template == "hello_world" else ""
        result = deliver_whatsapp(cfg, to, template, language, body.get("variables") or [], None, transport, deps["resolver"])
        return ({"ok": True, "message": f"Test message sent to {to}. Check WhatsApp on that phone."} if result["status"] == "sent"
                else {"ok": False, "message": f"Sending failed: {result.get('error')}"})
    if pid == "meta_cloud":
        version = values.get("graph_version") or "v23.0"
        url = f"{GRAPH}/{version}/{safe_id(values['phone_number_id'], 'Phone number ID')}?fields=display_phone_number,verified_name"
        reply = transport(url, {"Authorization": f"Bearer {values['access_token']}"}, None, method="GET")
        return {"ok": True, "message": f"Connected: {reply.get('verified_name', 'your business')} "
                                       f"({reply.get('display_phone_number', 'number hidden')})."}
    if pid == "wati":
        base = check_public_url(values["api_endpoint"], deps["resolver"]).rstrip("/")
        transport(f"{base}/api/v1/getContacts?pageSize=1", {"Authorization": "Bearer " + values["access_token"].removeprefix("Bearer ").strip()},
                  None, method="GET")
        return {"ok": True, "message": "WATI accepted the token."}
    return {"ok": True, "message": "Settings look complete. Enter your own number to send a real test message."}


def _sms_test(cfg: dict, body: dict, deps: dict) -> dict:
    to, transport, values, pid = (body.get("to") or "").strip(), deps["transport"], cfg["values"], cfg["provider"]
    if to:
        text = (body.get("text") or "").strip() or "Test message from Outreach OS."
        result = deliver_sms(cfg, to, text, None, body.get("variables") or [], transport, deps["resolver"])
        return ({"ok": True, "message": f"Test SMS sent to {to}."} if result["status"] == "sent"
                else {"ok": False, "message": f"Sending failed: {result.get('error')}"})
    if pid == "twilio_sms":
        sid = safe_id(values["account_sid"], "Account SID")
        reply = transport(f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
                          {"Authorization": basic_auth(sid, values["auth_token"])}, None, method="GET")
        return {"ok": True, "message": f"Twilio account '{reply.get('friendly_name', sid)}' is {reply.get('status', 'reachable')}."}
    if pid == "fast2sms":
        reply = transport("https://www.fast2sms.com/dev/wallet", {"authorization": values["api_key"]}, None, method="GET")
        return {"ok": reply.get("return") is True, "message": f"Fast2SMS wallet balance: {reply.get('wallet', 'unknown')}."}
    return {"ok": True, "message": "Settings look complete. Enter your own number to send a real test SMS."}


def _calls_test(cfg: dict, body: dict, deps: dict) -> dict:
    to, values, pid = (body.get("to") or "").strip(), cfg["values"], cfg["provider"]
    if pid == "click_to_call":
        ok = "{phone}" in values.get("click_to_call_url", "")
        return {"ok": ok, "message": "Looks right. Phone numbers will be inserted at {phone}." if ok
                else "The link must contain {phone} where the number goes."}
    if to:
        result = place_call(cfg, None, to, deps["transport"], deps["resolver"])
        return ({"ok": True, "message": "Call started: the agent phone rings first, then your number."}
                if result["status"] == "started" else {"ok": False, "message": f"Call failed: {result.get('error')}"})
    if pid == "twilio_voice":
        sid = safe_id(values["account_sid"], "Account SID")
        deps["transport"](f"https://api.twilio.com/2010-04-01/Accounts/{quote(sid)}.json",
                          {"Authorization": basic_auth(sid, values["auth_token"])}, None, method="GET")
        return {"ok": True, "message": "Twilio accepted the account details."}
    return {"ok": True, "message": "Settings look complete. Enter your own number to place a real test call."}


def run_test(store, ch_id: str, pid: str, body: dict, deps: dict) -> dict:
    """deps: transport, smtp_factory, imap_factory, resolver, webhook_url(ch, pid), webhook_secret(ch, pid)."""
    provider = get_provider(ch_id, pid)
    body = {**body, "_channel": ch_id}
    if pid == "custom_webhook":
        return _custom_webhook_test(store, ch_id, body)
    cfg = config_for(store, ch_id, pid)
    if not cfg:
        return SAVE_FIRST
    try:
        if pid == "custom_http" and ch_id != "calendar":
            return _custom_send_test(cfg, body, deps)
        if ch_id == "email_send":
            return test_email(store, pid, body.get("to", ""), deps["transport"], deps["smtp_factory"], deps["resolver"])
        if ch_id == "inbound_email" and pid == "imap":
            return check_mailbox(store, deps["imap_factory"])
        if ch_id in ("inbound_email", "email_events"):
            saved = is_saved(ch_id, entry_of(store.read("integrations"), ch_id), pid)
            return {"ok": saved, "message": "Saved. Paste the webhook address from step 3 into the provider; "
                                            "items appear here as they arrive." if saved else "Save this card first."}
        if ch_id == "whatsapp":
            return _whatsapp_test(cfg, body, deps)
        if ch_id == "sms":
            return _sms_test(cfg, body, deps)
        if ch_id == "calls":
            return _calls_test(cfg, body, deps)
        if ch_id == "calendar":
            secret = deps["webhook_secret"](ch_id, pid) if provider.get("webhook") else ""
            url = deps["webhook_url"](ch_id, pid) if provider.get("webhook") else ""
            return test_calendar(store, pid, deps["transport"], deps["resolver"], url, secret)
    except PermissionError as exc:
        detail = str(exc) if str(exc) and not str(exc).startswith("HTTP") else "The provider rejected the key or token."
        return {"ok": False, "message": detail}
    except (OSError, ValueError, KeyError) as exc:
        return {"ok": False, "message": f"Could not complete the test: {str(exc)[:300]}"}
    return {"ok": False, "message": "No automatic test for this provider yet."}
