"""WhatsApp: templates and the consent-gated send. Practice mode until a provider is connected."""
from datetime import datetime, timedelta

from .growth_core import ensure_demo
from .sequence_runner import NO_EMAIL_STAGES, get_settings
from .sequences import lead_variables, render

SESSION_WINDOW = timedelta(hours=24)

OPT_OUT = "Reply STOP to opt out."
# Every template says who is writing, refers honestly to their email reply, and ends with the opt-out line.
TEMPLATES = {
    "first_after_yes": ("{{greeting}} this is {{sender}} from BookMyInterview. Thanks for replying yes to my email. "
                        "Here is the sample screen I drafted for your opening: {{demo_link}} "
                        "Want me to set up your first role free? " + OPT_OUT),
    "nudge_demo_not_opened": ("{{greeting}} {{sender}} from BookMyInterview here. You said yes on email, so I drafted "
                              "a sample screen for your opening: {{demo_link}} Have a look when you have a minute. "
                              + OPT_OUT),
    "nudge_not_published": ("{{greeting}} {{sender}} from BookMyInterview. The role you drafted after our email is "
                            "ready but not published yet. Want me to help publish it? It takes a few minutes. " + OPT_OUT),
    "pilot_offer": ("{{greeting}} {{sender}} from BookMyInterview, following up on your email reply. If you already "
                    "have applicants for this role, I can help you add them to your free first role so you see how "
                    "the screen ranks your own candidates. Want help with that? " + OPT_OUT),
    "first_after_yes_hinglish": ("{{greeting}} main {{sender}}, BookMyInterview se. Email pe yes bolne ke liye thanks. "
                                 "Aapki opening ke liye sample screen yahan hai: {{demo_link}} "
                                 "Kya main aapka first role free mein set up kar doon? " + OPT_OUT),
    "nudge_demo_not_opened_hinglish": ("{{greeting}} {{sender}} here, BookMyInterview se. Aapne email pe yes kaha tha, "
                                       "isliye aapki opening ke liye sample screen banaya hai: {{demo_link}} "
                                       "Jab time mile, ek baar dekh lijiye. " + OPT_OUT),
    "nudge_not_published_hinglish": ("{{greeting}} {{sender}}, BookMyInterview se. Email ke baad aapne jo role draft "
                                     "kiya tha, woh ready hai par abhi publish nahi hua. Publish karne mein help "
                                     "chahiye? Bas kuch minute lagenge. " + OPT_OUT),
    "pilot_offer_hinglish": ("{{greeting}} {{sender}}, BookMyInterview se, aapke email reply ke baare mein. Agar aapke "
                             "paas is role ke applicants pehle se hain, toh main unhe aapke free first role mein add "
                             "karne mein help kar sakti hoon, taaki aap dekh sakein screen aapke candidates ko kaise "
                             "rank karta hai. Help chahiye? " + OPT_OUT),
}


class WhatsAppError(Exception):
    """A message that must not be sent, with a reason a person can read."""


def last_inbound_at(lead: dict):
    """When the lead last wrote to us (a reply or a recorded yes), or None."""
    stamps = [e["at"] for e in lead.get("timeline", []) if e["kind"] in ("reply", "consent")]
    return datetime.fromisoformat(max(stamps)) if stamps else None


def within_session_window(lead: dict, now: datetime) -> bool:
    last = last_inbound_at(lead)
    return bool(last) and now - last <= SESSION_WINDOW


def send(store, lead_id: str, template: str, text: str, now: datetime, base_url: str) -> dict:
    """Queue one WhatsApp message (practice mode) and return the updated lead."""
    leads = store.read("leads")
    index = next((i for i, lead in enumerate(leads) if lead["id"] == lead_id), None)
    if index is None:
        raise LookupError("Lead not found")
    lead = ensure_demo(leads[index], now)
    if lead["stage"] in NO_EMAIL_STAGES:
        raise WhatsAppError(f"Lead is {lead['stage']}; WhatsApp is not allowed")
    if not lead.get("consent"):
        raise WhatsAppError("No recorded consent. Ask for their yes first.")
    if not lead.get("phone"):
        raise WhatsAppError("This lead has no phone number")
    if template == "custom":
        if not within_session_window(lead, now):
            raise WhatsAppError("Free text is only allowed within 24 hours of their last message. Use a template.")
        raw = text.strip()
        if not raw:
            raise WhatsAppError("Write the message text")
    elif template in TEMPLATES:
        raw = TEMPLATES[template]
    else:
        raise WhatsAppError("Unknown template")
    variables = lead_variables(lead, get_settings(store)["sender_name"], base_url,
                               {"demo_link": f"{base_url}/demo/{lead['demo_token']}"})
    body, _ = render(raw, variables)
    message = {"lead_id": lead_id, "to": lead["phone"], "template": template, "body": body,
               "sent_at": now.isoformat(timespec="seconds"), "status": "dry_run"}
    store.write("whatsapp_outbox", [*store.read("whatsapp_outbox"), message])
    event = {"at": message["sent_at"], "kind": "whatsapp", "text": f"{template} — dry_run"}
    stage = "whatsapp_active" if lead["stage"] in ("interested", "engaged") else lead["stage"]
    leads[index] = {**lead, "stage": stage, "timeline": [*lead["timeline"], event]}
    store.write("leads", leads)
    return leads[index]
