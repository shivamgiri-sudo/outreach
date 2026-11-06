"""WhatsApp: templates and the consent-gated send. Practice mode until a provider is connected."""
import re
from datetime import datetime, timedelta

from .channels.http import request
from .channels.security import server_secret
from .channels.whatsapp_send import send_whatsapp
from .growth_core import ensure_demo
from .privacy import is_blocked
from .sequence_runner import NO_EMAIL_STAGES, get_settings
from .sequences import lead_variables, render
from .template_vars import brand_variables

SESSION_WINDOW = timedelta(hours=24)

OPT_OUT = "Reply STOP to opt out."
# Every template says who is writing, refers honestly to their email reply, and ends with the opt-out line.
TEMPLATES = {
    "first_after_yes": ("{{greeting}} this is {{sender}} from {{my_product}}. Thanks for replying yes to my email. "
                        "Here is the sample screen I drafted for your opening: {{demo_link}} "
                        "Want me to set up your first role free? " + OPT_OUT),
    "nudge_demo_not_opened": ("{{greeting}} {{sender}} from {{my_product}} here. You said yes on email, so I drafted "
                              "a sample screen for your opening: {{demo_link}} Have a look when you have a minute. "
                              + OPT_OUT),
    "nudge_not_published": ("{{greeting}} {{sender}} from {{my_product}}. The role you drafted after our email is "
                            "ready but not published yet. Want me to help publish it? It takes a few minutes. " + OPT_OUT),
    "pilot_offer": ("{{greeting}} {{sender}} from {{my_product}}, following up on your email reply. If you already "
                    "have applicants for this role, I can help you add them to your free first role so you see how "
                    "the screen ranks your own candidates. Want help with that? " + OPT_OUT),
    "first_after_yes_hinglish": ("{{greeting}} main {{sender}}, {{my_product}} se. Email pe yes bolne ke liye thanks. "
                                 "Aapki opening ke liye sample screen yahan hai: {{demo_link}} "
                                 "Kya main aapka first role free mein set up kar doon? " + OPT_OUT),
    "nudge_demo_not_opened_hinglish": ("{{greeting}} {{sender}} here, {{my_product}} se. Aapne email pe yes kaha tha, "
                                       "isliye aapki opening ke liye sample screen banaya hai: {{demo_link}} "
                                       "Jab time mile, ek baar dekh lijiye. " + OPT_OUT),
    "nudge_not_published_hinglish": ("{{greeting}} {{sender}}, {{my_product}} se. Email ke baad aapne jo role draft "
                                     "kiya tha, woh ready hai par abhi publish nahi hua. Publish karne mein help "
                                     "chahiye? Bas kuch minute lagenge. " + OPT_OUT),
    "pilot_offer_hinglish": ("{{greeting}} {{sender}}, {{my_product}} se, aapke email reply ke baare mein. Agar aapke "
                             "paas is role ke applicants pehle se hain, toh main unhe aapke free first role mein add "
                             "karne mein help kar sakti hoon, taaki aap dekh sakein screen aapke candidates ko kaise "
                             "rank karta hai. Help chahiye? " + OPT_OUT),
    # Industry-neutral templates used by the industry presets' flows (app/preset_data): the brand name comes from the
    # Brand settings, nothing here mentions a product.
    "generic_follow_up": ("{{greeting}} this is {{sender}} from {{my_company}}, following up on my email. Would a short "
                          "call this week be useful? " + OPT_OUT),
    "generic_after_yes": ("{{greeting}} this is {{sender}} from {{my_company}}. Thanks for replying to my email. When is a "
                          "good time for a short call? " + OPT_OUT),
}
GENERIC_TEMPLATES = ("generic_follow_up", "generic_after_yes")



class WhatsAppError(Exception):
    """A message that must not be sent, with a reason a person can read."""


# Positional order of the values a provider-approved template can take: {{1}} first name, {{2}} sender, {{3}} company,
# {{4}} demo link. A template that doesn't use a value leaves it out and the rest move up, as the provider requires.
PARAM_ORDER = ("first_name", "sender", "company", "demo_link")
# Brand values a template's value list may also name (Settings: whatsapp_template_params {template: [names]}).
BRAND_PARAMS = ("my_company", "my_product", "my_website", "my_cta", "my_tagline")
NAMED_PARAMS = PARAM_ORDER + BRAND_PARAMS


def template_param_names(template: str, configured=None) -> list:
    """Which values to pass for `template`. `configured` is the optional settings map {template: count | [names]}:
    a count keeps that many of PARAM_ORDER, a list names them in order. Without it a template whose own text numbers
    its values ({{1}}..{{n}}) gets exactly n; the built-in templates name theirs ({{greeting}}) and the approved
    versions follow the four-value convention above, so they and unknown templates keep all four, as before."""
    setting = (configured or {}).get(template) if isinstance(configured, dict) else None
    if isinstance(setting, bool):
        setting = None
    if isinstance(setting, int):
        return list(PARAM_ORDER[:max(0, min(setting, len(PARAM_ORDER)))])
    if isinstance(setting, list) and all(isinstance(n, str) and n in NAMED_PARAMS for n in setting):
        return list(setting)
    declared = [int(n) for n in re.findall(r"\{\{(\d+)\}\}", TEMPLATES.get(template, ""))]
    if declared:
        return list(PARAM_ORDER[:min(max(declared), len(PARAM_ORDER))])
    return list(PARAM_ORDER)


def template_params(template: str, variables: dict, configured=None) -> list:
    defaults = {"first_name": "there"}
    return [variables.get(n) or defaults.get(n, "") for n in template_param_names(template, configured)]


def last_inbound_at(lead: dict):
    """When the lead last sent us a WhatsApp message, or None.

    Meta's 24-hour customer-service window only opens on a WhatsApp message from the person; an email reply or
    a consent noted by a person does not open it.
    """
    stamp = lead.get("whatsapp_last_inbound_at")
    return datetime.fromisoformat(stamp) if stamp else None


def within_session_window(lead: dict, now: datetime) -> bool:
    last = last_inbound_at(lead)
    return bool(last) and now - last <= SESSION_WINDOW


def send(store, lead_id: str, template: str, text: str, now: datetime, base_url: str, transport=None, sender: str = "") -> dict:
    """Queue one WhatsApp message (practice mode until a provider is live) and return the updated lead.

    `sender` is the representative sending it: their name goes into the message, and into the log. Flows and other
    automatic paths pass none and use the team name from Settings."""
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise LookupError("Lead not found")
    lead = ensure_demo(lead, now)
    if lead["stage"] in NO_EMAIL_STAGES:
        raise WhatsAppError(f"Lead is {lead['stage']}; WhatsApp is not allowed")
    if not lead.get("consent"):
        raise WhatsAppError("No recorded consent. Ask for their yes first.")
    if not lead.get("phone"):
        raise WhatsAppError("This lead has no phone number")
    if is_blocked(store, lead.get("email"), lead.get("phone")):
        raise WhatsAppError("This contact is on the do-not-contact list.")
    if template == "custom":
        if not within_session_window(lead, now):
            raise WhatsAppError("Free text is only allowed within 24 hours of their last WhatsApp message. Use a template.")
        raw = text.strip()
        if not raw:
            raise WhatsAppError("Write the message text")
    elif template in TEMPLATES:
        raw = TEMPLATES[template]
    else:
        raise WhatsAppError("Unknown template")
    variables = lead_variables(lead, sender.strip() or get_settings(store)["sender_name"], base_url,
                               {"demo_link": f"{base_url}/demo/{lead['demo_token']}"}, secret=server_secret(store), brand=store)
    body, missing = render(raw, variables)
    if missing:
        raise WhatsAppError(f"This message needs details the lead doesn't have yet: {', '.join(missing)}")
    if template == "custom" and OPT_OUT.lower() not in body.lower():
        body = f"{body} {OPT_OUT}"           # free text carries the opt-out line too, like every template
    # Real delivery through the WhatsApp provider chosen on the Integrations page (dry_run until connected and
    # "WhatsApp for real" is on). Approved templates receive their variables in this order:
    # {{1}} first name, {{2}} sender, {{3}} company, {{4}} demo link.
    params = template_params(template, variables, get_settings(store).get("whatsapp_template_params"))
    result = send_whatsapp(store, lead["phone"], None if template == "custom" else template,
                           "en", params, text=body if template == "custom" else None, transport=transport or request)
    message = {"lead_id": lead_id, "to": lead["phone"], "template": template, "body": body,
               "sent_at": now.isoformat(timespec="seconds"), "status": result.get("status", "dry_run"),
               "provider_id": result.get("provider_id"), "error": result.get("error"), "by": sender.strip()}
    store.update("whatsapp_outbox", lambda rows: [*rows, message])
    if message["status"] == "failed":
        raise WhatsAppError(f"The WhatsApp provider refused the message: {message['error'] or 'unknown error'}")
    event = {"at": message["sent_at"], "kind": "whatsapp", "text": f"{template} — {message['status']}" + (f" (sent by {sender.strip()})" if sender.strip() else ""),
             **({"by": sender.strip()} if sender.strip() else {})}
    demo = {k: lead[k] for k in ("demo_token", "referral_code", "activation") if k in lead}

    def record(latest: dict) -> dict:
        stage = "whatsapp_active" if latest["stage"] in ("interested", "engaged") else latest["stage"]
        return {**demo, **latest, "stage": stage, "timeline": [*latest["timeline"], event]}
    return store.update_lead(lead_id, record)
