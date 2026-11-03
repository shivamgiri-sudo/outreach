"""Next-best-action suggestions shown on the lead screen."""
from .growth_core import DEFAULT_OFFER

DEFAULT_CLAIM_URL = DEFAULT_OFFER.get("claim_url") or "https://bookmyinterview.ai/register"
CALL_RULE = " (PD only, before 18:55)"

_BY_STAGE = {
    "new": ("Check the email and phone, then enrich", "email", "Nothing to say yet. Clean the data first."),
    "verified": ("Find the hiring decision maker", "email", "Look for the HR head or founder before writing."),
    "enriched": ("Send the first human note", "email",
                 "Mention the open role, say in one line what BookMyInterview does, that it is in beta and the first "
                 "role is free, and offer to draft a sample screen."),
    "contacted": ("Wait for a reply, follow up on day 3", "email",
                  "Share one useful tip about screening high-volume roles. No link, no pitch."),
    "engaged": ("Ask permission to send the sample screen on WhatsApp", "email",
                "Ask: 'Can I send the sample screen I drafted for your role on WhatsApp?' Record their yes as consent."),
    "interested": ("Send the sample screen on WhatsApp and alert Priya", "whatsapp",
                   "Thank them for the yes, send the sample screen for their role, offer to set up their first role free."),
    "whatsapp_active": ("Priya to call within 24 hours", "call",
                        "Ask for 2 minutes, ask what is slow in their hiring today, explain screening and "
                        "self-booking in one line, agree one next step."),
    "call_done": ("Send recap and invite them to set up the first role", "email",
                  "Recap the call in 3 lines and link to {claim_url}."),
    "trial": ("Help them publish the first job", "whatsapp", "Offer a 10-minute walkthrough of the screening kit."),
    "customer": ("Ask how the first role went", "email",
                 "Ask what worked and what to improve. If they are happy, ask whether you may quote one line, "
                 "only with their OK."),
    "advocate": ("Ask for a referral", "email", "Ask who else is hiring that they know."),
    "not_now": ("Re-nurture in about 2 months", "email", "Share one useful hiring tip, no ask."),
    "disqualified": ("Do not contact again unless something changes", "none", ""),
}


def suggest_next(stage: str, band: str, claim_url: str = DEFAULT_CLAIM_URL) -> dict:
    """What to do next for a lead; call actions always carry the PD-only rule."""
    if stage == "unsubscribed":
        return {"action": "Do not contact again. They opted out.", "channel": "none", "pitch": "", "priority": "none"}
    action, channel, pitch = _BY_STAGE.get(stage, ("Review lead", "email", ""))
    priority = "high" if band == "hot" else "normal" if band == "warm" else "low"
    action = action + CALL_RULE if channel == "call" else action
    return {"action": action, "channel": channel, "pitch": pitch.format(claim_url=claim_url or DEFAULT_CLAIM_URL),
            "priority": priority}
