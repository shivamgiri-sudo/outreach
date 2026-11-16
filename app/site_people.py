"""Pair a person's NAME with their email on a company's own pages (team, leadership, press pages ...).

Two signals, both on one page:
  * schema.org Person data (JSON-LD) that carries both a name and an email (read by app/sources/site_discover.parse_page);
  * a mailto: link inside the same "card" as a name (a heading, bold text or an element whose class says name). A card
    starts at the nearest <li>, <article>, <tr>, <section> or an element whose class says card / member / person / team.
    When no card edge is found, the name must also show up in the address (asha.rao@ next to "Asha Rao").
Shared inboxes (info@, press@ ...) and addresses meant for complaints or legal matters (privacy@, security@, abuse@, dpo@,
legal@, postmaster@, noreply@ ...) are never paired: they are not a person and not for sales use.
"""
import re

from . import email_patterns as EP
from .hygiene import is_role_address, normalise_email
from .scraper import outreach_ok
from .sources.builtin_verifier import is_role
from .sources.site_discover import parse_page

MAILTO_RE = re.compile(r'href=["\']mailto:([^"\'?>\s]+)', re.I)
CARD_START = re.compile(r"<(?:li|article|tr|section)\b|<\w+[^>]*class=[\"'][^\"']*(?:card|member|person|profile|team-item|staff)", re.I)
CARD_END = re.compile(r"</(?:li|article|tr|section)>", re.I)
NAME_TAG = re.compile(r"<(h[1-6]|strong|b)\b[^>]*>(.*?)</\1>|<\w+[^>]*class=[\"'][^\"']*name[^\"']*[\"'][^>]*>(.*?)</\w+>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
NOT_NAME_WORDS = frozenset("contact email mail us our team office press media sales support help careers jobs about write reach "
                           "enquiries enquiry info investor investors relations partner partners news events speakers leadership "
                           "management board directors department desk head office".split())
WINDOW = 600


def looks_like_name(text: str) -> bool:
    words = text.split()
    if not 2 <= len(words) <= 4 or len(text) > 60:
        return False
    for w in words:
        bare = w.strip(".,'-")
        if not bare or not bare.replace("-", "").replace("'", "").isalpha() or bare.lower() in NOT_NAME_WORDS:
            return False
    return words[0][0].isupper()


def _person_address(email: str) -> bool:
    return bool(email) and outreach_ok(email) and not is_role_address(email) and not is_role(email)


def _name_in_address(name: str, local: str) -> bool:
    if EP.pattern_of(name, local):
        return True
    parts = EP.parse_name(name)
    return any(len(p) >= 3 and p in re.sub(r"[^a-z]", "", local) for p in (parts["first"], parts["last"]))


def _card_name(html: str, at: int, local: str) -> str:
    window = html[max(0, at - WINDOW):at]
    edges = [m.end() for m in CARD_END.finditer(window)] + [m.start() for m in CARD_START.finditer(window)]
    edge = max(edges) if edges else None
    names = []
    for m in NAME_TAG.finditer(window[edge or 0:]):
        text = " ".join(TAG_RE.sub(" ", m.group(2) or m.group(3) or "").split())
        if looks_like_name(text):
            names.append(text)
    if not names:
        return ""
    name = names[-1]
    return name if edge is not None or _name_in_address(name, local) else ""


def pairs(html: str, url: str) -> list:
    """[{"name", "email", "title", "source"}] for the people on one page whose name and email sit together."""
    out, seen = [], set()
    try:
        people = parse_page(html, url)["people"]
    except Exception:                          # noqa: BLE001 - a broken page never stops the crawl
        people = []
    for p in people:
        email = normalise_email(p.get("email"))
        if email and _person_address(email) and looks_like_name(p.get("name") or "") and email not in seen:
            seen.add(email)
            out.append({"name": p["name"].strip(), "email": email, "title": (p.get("title") or "").strip()[:120], "source": url})
    for m in MAILTO_RE.finditer(html):
        email = normalise_email(m.group(1).replace("%40", "@"))
        if not email or email in seen or not _person_address(email):
            continue
        name = _card_name(html, m.start(), email.split("@", 1)[0])
        if name:
            seen.add(email)
            out.append({"name": name, "email": email, "title": "", "source": url})
    return out
