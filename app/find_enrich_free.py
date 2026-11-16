"""The free steps of Find & enrich that run before any paid lookup (app/find_enrich.py calls these).

  team_pages     the company's team / leadership / press / investor / partner / event pages besides the usual contact
                 pages, pairing a name with an email when both sit on one card (app/enrich.py, app/site_people.py)
  pattern_guess  an email GUESS from the company's own format, checked without sending (app/pattern_guess.py); stored
                 as `email_guess`, never as `email`, so nothing is ever sent to it
  gravatar       a public Gravatar profile for the lead's email (or its guess): name, photo, LinkedIn (app/gravatar.py)

Each step fills empty fields only, records where every value came from, and has an on/off switch in Prospect search >
Spend (settings.prospect_db.providers.<id>.enabled, on by default).
"""
from . import gravatar
from . import pattern_guess as PG
from .email_patterns import parse_name

STEPS = {
    "team_pages": "Team and press pages (free)",
    "pattern_guess": "Email format guess (free, never sent to)",
    "gravatar": "Gravatar profiles (free)",
}
NOTE = "free step in Find & enrich"


def config_rows(over: dict) -> list:
    """Rows for prospect_waterfall.provider_config, so the owner can switch each free step off."""
    return [{"id": sid, "title": title, "cost": 0.0, "priority": int((over.get(sid) or {}).get("priority", 0)),
             "enabled": (over.get(sid) or {}).get("enabled", True) is not False, "connected": True, "finds": False,
             "verifies": False, "free": True, "note": NOTE} for sid, title in STEPS.items()]


class FreeTools:
    """What the free steps share over one run. The pattern book and checker are built on first use."""

    def __init__(self, store, transports: dict, now=None):
        self.store, self.transports, self.now = store, transports or {}, now
        self._book = self._checker = None

    @property
    def book(self):
        if self._book is None:
            self._book = PG.PatternBook(self.store)
        return self._book

    @property
    def checker(self):
        if self._checker is None:
            self._checker = PG.make_checker(self.store, self.transports, self.book.history)
        return self._checker

    def gravatar(self, email: str) -> dict:
        return gravatar.lookup(self.store, email, self.transports.get("gravatar"), self.now)


def on(ctx, step: str) -> bool:
    return getattr(ctx, "free", None) is not None and (ctx.meter is None or ctx.meter.enabled(step))


def _set(work: dict, out, field: str, value, source: str) -> bool:
    if value in (None, "") or work.get(field):
        return False
    work[field] = out.changes[field] = value
    out.prov[field] = source
    return True


def _same_person(a: str, b: str) -> bool:
    pa, pb = parse_name(a), parse_name(b)
    return bool(pa["first"]) and pa["first"] == pb["first"] and (not pa["last"] or not pb["last"] or pa["last"] == pb["last"])


def site_people(work: dict, out, people: list, emails: list) -> list:
    """Attach names to crawled emails; when the lead already names someone found on a card, use only that address.
    The other named people become other contacts. Returns the emails to choose from."""
    named = {p["email"]: p for p in people}
    emails = [{**e, "name": named[e["value"]]["name"], "title": named[e["value"]]["title"]} if e["value"] in named else e for e in emails]
    match = next((p for p in people if work.get("contact_name") and _same_person(work["contact_name"], p["name"])), None)
    if match:
        emails = [e for e in emails if e["value"] == match["email"]]
    for p in people:
        if p is not match:
            out.others.append({"contact_name": p["name"], "title": p["title"], "email": p["email"], "linkedin_url": "",
                               "found_at": f"website:{p['source']}", "company": work.get("company", "")})
    return emails


def pattern_step(work: dict, out, domain: str, ctx) -> None:
    """Guess the lead's email from the company's format when it has a name but no email and no earlier guess."""
    if (not on(ctx, "pattern_guess") or work.get("email") or work.get("email_guess") or work.get("email_guess_status")
            or not (work.get("contact_name") or "").strip() or not domain):
        return
    try:
        fields = PG.guess(work["contact_name"], domain, ctx.free.book, ctx.free.checker)
    except Exception as exc:                                 # noqa: BLE001 - a failed guess never stops the paid steps
        ctx.error("pattern_guess", f"Some email guesses could not be checked (last: {str(exc)[:120]}).")
        return
    if not fields:
        return
    for field, value in fields.items():
        _set(work, out, field, value, fields.get("email_guess_source") or "pattern")
    if fields.get("email_guess"):
        out.notes.append(f"email guess {fields['email_guess']} ({fields['email_guess_status'].replace('_', '-')}, never sent to)")


def gravatar_step(work: dict, out, ctx) -> None:
    """Fill an empty name, photo and LinkedIn link from the public Gravatar profile of the lead's email or its guess."""
    address = work.get("email") or work.get("email_guess")
    if not on(ctx, "gravatar") or not address or address in out.looked_up:
        return
    out.looked_up.add(address)
    found = ctx.free.gravatar(address)
    profile = found.get("profile") or {}
    if found.get("status") != "found" or not profile:
        return
    added = []
    if len((profile.get("name") or "").split()) >= 2 and _set(work, out, "contact_name", profile["name"], "gravatar"):
        added.append("name")
    for field, label in (("photo_url", "photo"), ("linkedin_url", "LinkedIn"), ("gravatar_url", "profile")):
        if _set(work, out, field, profile.get("profile_url" if field == "gravatar_url" else field), "gravatar"):
            added.append(label)
    if address == work.get("email_guess") and not work.get("email") and "email_guess_reason" in out.changes:
        work["email_guess_reason"] = out.changes["email_guess_reason"] = (
            out.changes["email_guess_reason"] + "; a public Gravatar profile uses this address")
    if added:
        out.notes.append("Gravatar profile (" + ", ".join(added) + ")")
