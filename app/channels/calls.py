"""Calling: the dialler's click-to-call link, or a bridged call (agent first, then the lead) via Exotel,
Knowlarity, Twilio Voice or a custom HTTP API. Nothing is dialled unless a calls provider is connected."""
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

from .config import config_for
from .custom_http import call_custom
from .http import request
from .phone import basic_auth, e164, safe_id

EXOTEL_HOSTS = ("api.exotel.com", "api.in.exotel.com")
KNOWLARITY_URL = "https://kpi.knowlarity.com/Basic/v1/account/call/makecall"
TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts"


def _exotel(values, agent, lead, transport):
    host = values.get("subdomain")
    if host not in EXOTEL_HOSTS:
        raise ValueError("Choose your Exotel region.")
    sid = safe_id(values["account_sid"], "Account SID")
    form = {"From": agent, "To": lead, "CallerId": values["caller_id"]}
    reply = transport(f"https://{host}/v1/Accounts/{sid}/Calls/connect.json",
                      {"Authorization": basic_auth(values["api_key"], values["api_token"])}, None, method="POST", form=form)
    return str((reply.get("Call") or {}).get("Sid", ""))


def _knowlarity(values, agent, lead, transport):
    body = {"k_number": values["k_number"], "agent_number": agent, "customer_number": lead}
    if values.get("caller_id"):
        body["caller_id"] = values["caller_id"]
    reply = transport(KNOWLARITY_URL, {"x-api-key": values["x_api_key"], "Authorization": values["sr_api_key"]}, body)
    if "error" in reply:
        raise OSError(f"Knowlarity said: {reply['error']}")
    return str((reply.get("success") or {}).get("call_id", ""))


def _twilio(values, agent, lead, transport):
    sid = safe_id(values["account_sid"], "Account SID")
    twiml = f"<Response><Dial callerId={quoteattr(values['from_number'])}><Number>{escape(lead)}</Number></Dial></Response>"
    reply = transport(f"{TWILIO_API}/{sid}/Calls.json", {"Authorization": basic_auth(sid, values["auth_token"])}, None,
                      method="POST", form={"To": agent, "From": values["from_number"], "Twiml": twiml})
    return str(reply.get("sid", ""))


ADAPTERS = {"exotel": _exotel, "knowlarity": _knowlarity, "twilio_voice": _twilio}


def place_call(cfg: dict, agent_phone: str, lead_phone: str, transport=request, resolver=None) -> dict:
    pid, values = cfg["provider"], cfg["values"]
    if pid == "click_to_call":
        return {"status": "link", "url": values["click_to_call_url"].replace("{phone}", quote(str(lead_phone or ""), safe="+"))}
    lead, agent = e164(lead_phone), e164(agent_phone or values.get("agent_phone"))
    if not lead:
        return {"status": "failed", "error": "The lead's phone number is not valid."}
    try:
        if pid == "custom_http":
            result = call_custom(values, {"to": lead, "phone": lead, "text": agent or ""}, transport, resolver)
            return ({"status": "started", "provider_id": result.get("provider_id", "")} if result["ok"]
                    else {"status": "failed", "error": result.get("error", "failed")})
        if not agent:
            return {"status": "failed", "error": "Add the agent's phone number."}
        if pid not in ADAPTERS:
            return {"status": "failed", "error": f"Unknown calls provider {pid}"}
        return {"status": "started", "provider_id": ADAPTERS[pid](values, agent, lead, transport)}
    except PermissionError:
        return {"status": "failed", "error": "The provider rejected the key or token."}
    except (OSError, ValueError, KeyError) as exc:
        return {"status": "failed", "error": str(exc)[:300]}


def start_call(store, agent_phone: str, lead_phone: str, transport=request, resolver=None) -> dict:
    """{"status": "started"|"link"|"failed"|"dry_run", ...}. dry_run when no calls provider is connected."""
    cfg = config_for(store, "calls")
    if not cfg:
        return {"status": "dry_run"}
    return place_call(cfg, agent_phone, lead_phone, transport, resolver)
