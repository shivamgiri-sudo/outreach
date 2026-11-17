"""Lead drawer helpers for a person to research and reach the right contact (nothing here fetches or sends anything).

search_links(lead)   "Search the web" links a person opens in a new tab: the exact email in quotes on Google, the
                     company's own site for the contact's name, a LinkedIn people search (app/linkedin_finder.search_url)
                     and the company's careers page. Every value is URL-encoded; the server never opens these links.

ask_contact          "Ask who handles hiring": a short, polite email to a company's shared address (info@, hr@,
                     contact@ ...) asking who looks after hiring / HR. It is a built-in email snippet ("Ask who handles
                     hiring"), seeded into the main snippet library once (settings.builtin_snippets) the first time the
                     drawer asks for it, so an owner can reword it in Templates; a deleted copy is never re-seeded (the
                     built-in text is used instead). The drawer only DRAFTS it into the one-off email composer; the person
                     reads it and presses Send, and the normal one-off send path (app/manual_send.py) adds the sender
                     identity and the unsubscribe line and refuses suppressed / unsubscribed / bounced / invalid addresses.
                     The same refusals are checked here first, so the button is not offered for such a lead.
"""
import uuid
from datetime import datetime, timezone
from urllib.parse import quote_plus

from . import email_domain, email_gate
from .hygiene import business_domain, is_role_address, normalise_email
from .linkedin_finder import search_url
from .privacy import is_blocked

SNIPPET_KEY = "ask_contact"
SEEDED_KEY = "builtin_snippets"
ASK_SNIPPET = {
    "name": "Ask who handles hiring",
    "channel": "email",
    "subject": "Who looks after hiring at {{company}}?",
    "body": ("Hello,\n\n"
             "I hope this reaches the right desk. Could you tell me who looks after hiring or HR at {{company}}? "
             "A name and email address would be perfect, and I will write to them directly.\n\n"
             "If you would rather not share this, no problem at all. Thank you for reading.\n\n"
             "{{sign_off}}"),
}
CLOSED_STAGES = ("unsubscribed", "disqualified")


def google(q: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(q)


def search_links(lead: dict) -> list:
    """[{id, label, url}] for the links that make sense for this lead (each needs its own input)."""
    email = normalise_email(lead.get("email")) or normalise_email(lead.get("email_guess"))
    name = " ".join(str(lead.get("contact_name") or "").split())[:120]
    company = " ".join(str(lead.get("company") or "").split())[:120]
    domain = lead.get("domain") or business_domain(lead.get("website")) or ""
    out = []
    if email:
        out.append({"id": "email", "label": "This exact email on Google", "url": google(f'"{email}"')})
    if domain and name:
        out.append({"id": "site", "label": f"{name} on {domain}", "url": google(f"site:{domain} {name} contact OR email")})
    if name or company:
        out.append({"id": "linkedin", "label": "LinkedIn people search", "url": search_url({"contact_name": name, "company": company})})
    if company or domain:
        out.append({"id": "careers", "label": "Careers page on Google", "url": google(f"{company or domain} careers")})
    return out


# ------------------------------------------------------------------ ask for the right contact
def generic_only(lead: dict) -> bool:
    """The lead has only a shared / role address, or no named contact."""
    email = normalise_email(lead.get("email")) or ""
    return bool(email) and (is_role_address(email) or not str(lead.get("contact_name") or "").strip())


def refusal(store, lead: dict) -> str:
    """Why this lead may not get the email ('' = it may). The send path checks the same again."""
    email = normalise_email(lead.get("email"))
    if not email:
        return "This lead has no email address."
    if lead.get("stage") in CLOSED_STAGES or is_blocked(store, email) or email in set(store.read("suppressed")):
        return "This address may not be emailed (unsubscribed, bounced, erased or on the do-not-contact list)."
    if (lead.get("email_check") or {}).get("status") == "invalid":
        return "A mailbox check said this address does not exist."
    if (bad := email_domain.problem(email)):
        return f"Not possible: {bad}."
    return ""


def gate_note(store, lead: dict) -> str:
    """A plain warning when the verify-before-first-email rule is on and this address has not passed it."""
    cfg = email_gate.settings_of(store)
    if not cfg.get("enabled"):
        return ""
    verdict, why = email_gate.decision(lead, False, cfg.get("approve_risky", False))
    return "" if verdict == "send" else f"Check before sending: {why}."


def _snippets(store) -> list:
    try:
        return store.read("snippets")
    except KeyError:
        return []


def ensure_snippet(store, by: str = "") -> dict:
    """The library copy of the built-in snippet, seeded into main once. Returns the row, or the built-in text when the
    owner deleted the copy (never seeded again)."""
    row = next((r for r in _snippets(store) if r.get("builtin") == SNIPPET_KEY), None)
    if row:
        return row
    if SNIPPET_KEY in ((store.read("settings") or {}).get(SEEDED_KEY) or []):
        return {**ASK_SNIPPET, "builtin": SNIPPET_KEY, "id": ""}
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = {"id": uuid.uuid4().hex[:10], **ASK_SNIPPET, "builtin": SNIPPET_KEY, "created_by": by or "built-in",
           "created_by_email": "", "created_at": stamp, "updated_at": stamp}

    def add(rows):
        return rows if any(r.get("builtin") == SNIPPET_KEY for r in rows) else [*rows, row]
    try:
        store.update("snippets", add)
    except KeyError:
        store.write("snippets", [row])
    store.update("settings", lambda s: {**s, SEEDED_KEY: sorted({*(s.get(SEEDED_KEY) or []), SNIPPET_KEY})})
    return next((r for r in _snippets(store) if r.get("builtin") == SNIPPET_KEY), row)


def ask_contact(store, lead: dict, by: str = "") -> dict:
    """{offer, reason, gate_note, to, subject, body}: whether the drawer offers the action, and the draft. Never sends."""
    why = refusal(store, lead)
    if not why and not generic_only(lead):
        why = "This lead already has a named contact with a personal address."
    if why:
        return {"offer": False, "reason": why, "gate_note": "", "to": lead.get("email") or "", "subject": "", "body": ""}
    snippet = ensure_snippet(store, by)
    return {"offer": True, "reason": "", "gate_note": gate_note(store, lead), "to": lead["email"],
            "subject": snippet.get("subject") or ASK_SNIPPET["subject"], "body": snippet.get("body") or ASK_SNIPPET["body"],
            "snippet_id": snippet.get("id", "")}
