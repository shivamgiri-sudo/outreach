"""SMS through the active provider: MSG91 (flow API), Twilio, Fast2SMS or custom HTTP.

send_sms(...) returns "dry_run" unless the channel is connected AND settings.live_sms is true.
In India the text must match a DLT-approved template; MSG91 and Fast2SMS send by template only.
"""
from .config import config_for
from .custom_http import call_custom
from .http import request
from .phone import basic_auth, digits, e164, national, safe_id, values_list

MSG91_URL = "https://control.msg91.com/api/v5/flow"
TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts"
FAST2SMS_URL = "https://www.fast2sms.com/dev/bulkV2"


def _msg91(values, to, text, template_id, variables, transport):
    recipient = {"mobiles": digits(to)}
    if isinstance(variables, dict):
        recipient.update({str(k): str(v) for k, v in variables.items()})
    else:
        recipient.update({f"var{i + 1}": v for i, v in enumerate(values_list(variables))})
    body = {"template_id": template_id or values["template_id"], "short_url": "0", "recipients": [recipient]}
    reply = transport(MSG91_URL, {"authkey": values["authkey"], "accept": "application/json"}, body)
    if str(reply.get("type", "")).lower() != "success":
        raise OSError(f"MSG91 said: {reply.get('message', 'not accepted')}")
    return str(reply.get("message", ""))


def _twilio(values, to, text, template_id, variables, transport):
    if not text:
        raise ValueError("Twilio needs the message text (it must match your DLT-registered template in India).")
    sid = safe_id(values["account_sid"], "Account SID")
    form = {"To": to, "Body": text}
    if values.get("messaging_service_sid"):
        form["MessagingServiceSid"] = values["messaging_service_sid"]
    elif values.get("from_number"):
        form["From"] = values["from_number"]
    else:
        raise ValueError("Add a From number or a Messaging service SID.")
    reply = transport(f"{TWILIO_API}/{sid}/Messages.json", {"Authorization": basic_auth(sid, values["auth_token"])},
                      None, method="POST", form=form)
    return str(reply.get("sid", ""))


def _fast2sms(values, to, text, template_id, variables, transport):
    body = {"route": "dlt", "sender_id": values["sender_id"], "message": template_id or values["message_id"],
            "variables_values": "|".join(values_list(variables)), "numbers": national(to), "flash": "0"}
    reply = transport(FAST2SMS_URL, {"authorization": values["api_key"]}, body)
    if reply.get("return") is not True:
        raise OSError(f"Fast2SMS said: {reply.get('message', 'not accepted')}")
    return str(reply.get("request_id", ""))


ADAPTERS = {"msg91": _msg91, "twilio_sms": _twilio, "fast2sms": _fast2sms}


def deliver_sms(cfg: dict, to_phone: str, text: str = "", template_id: str = None, variables=None, transport=request,
                resolver=None) -> dict:
    pid, values = cfg["provider"], cfg["values"]
    to = e164(to_phone)
    if not to:
        return {"status": "failed", "error": "That phone number is not valid (include the country code)."}
    try:
        if pid == "custom_http":
            result = call_custom(values, {"to": to, "phone": to, "text": text or "", "template": template_id or ""},
                                 transport, resolver)
            return ({"status": "sent", "provider_id": result.get("provider_id", "")} if result["ok"]
                    else {"status": "failed", "error": result.get("error", "failed")})
        if pid not in ADAPTERS:
            return {"status": "failed", "error": f"Unknown SMS provider {pid}"}
        return {"status": "sent", "provider_id": ADAPTERS[pid](values, to, text, template_id, variables, transport)}
    except PermissionError:
        return {"status": "failed", "error": "The provider rejected the key or token."}
    except (OSError, ValueError, KeyError) as exc:
        return {"status": "failed", "error": str(exc)[:300]}


def send_sms(store, to_phone: str, text: str = "", template_id: str = None, variables=None, transport=request,
             resolver=None) -> dict:
    """{"status": "sent"|"failed"|"dry_run", "provider_id"?, "error"?}. Real sends need live_sms on."""
    cfg = config_for(store, "sms")
    if not (cfg and store.read("settings").get("live_sms")):
        return {"status": "dry_run"}
    return deliver_sms(cfg, to_phone, text, template_id, variables, transport, resolver)
