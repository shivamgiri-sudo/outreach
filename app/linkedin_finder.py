"""Find a person's LinkedIn profile when the lead has none, then start the designed sequence.

Start LinkedIn on a lead with a name and a company but no profile link puts it on a lookup list (lead["linkedin_lookup"]).
The LinkedIn agent (the user's own logged-in session on the office runner) takes ONE queued lead per cycle, opens the people
search for "<name> <company>" once, reads the first page's visible result cards and reports them here. THE SERVER DECIDES:

  - found       exactly one candidate has the exact (normalised) name AND its headline mentions the company: the link is saved
                (with a timeline line saying how it was found) and the LinkedIn sequence starts.
  - ambiguous   anything else with plausible people (up to 5 kept): a person picks one with one click, never the program.
  - not_found   no plausible card: a prefilled "Search on LinkedIn" link and a field to paste the link.
  - manual      a person skipped it, or the agent could not read the search page twice.

Status values: queued | found | ambiguous | not_found | manual. The owner switch is "Find profiles automatically"
(agent setting find_profiles, default on); off = a lead without a link is refused as before.
"""
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote_plus

from . import linkedin_seq as li
from .activities import iso, now_utc

STATUSES = ("queued", "found", "ambiguous", "not_found", "manual")
MAX_CANDIDATES = 5
MAX_ATTEMPTS = 2                        # unreadable search page this many times -> a person takes over
HAND_OUT_HOLD = timedelta(hours=1)      # a lead handed to an agent is not handed out again within this time
NO_LINK = "Add the person's LinkedIn profile link first."
_HONORIFIC = re.compile(r"^(mr|mrs|ms|miss|dr|prof|shri|smt|er|ca|adv)\.?\s+", re.I)
_LEGAL = {"pvt", "private", "ltd", "limited", "llp", "inc", "incorporated", "co", "corp", "corporation", "company", "llc", "plc", "the"}
_PROFILE = re.compile(r"^https?://([a-z0-9-]+\.)?linkedin\.com/in/([^/?#\s]+)", re.I)


def _plain(text: str) -> str:
    """Lower-case, accents removed, anything but letters and digits turned into one space."""
    folded = unicodedata.normalize("NFKD", str(text or "")).casefold()
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded).split())


def norm_name(name: str) -> str:
    """A person's name for an exact comparison: no honorific, no credentials after a comma ("Ravi Kumar, MBA")."""
    head = _HONORIFIC.sub("", str(name or "").strip()).split(",")[0]
    return _plain(head)


def norm_company(name: str) -> str:
    return " ".join(w for w in _plain(name).split() if w not in _LEGAL)


_FORMER = re.compile(r"\b(ex|former|formerly|previously|past)\b")
_SEP = re.compile(r"\bat\b|@|,|\s[-\u2013\u2014]\s", re.I)
_JUNIOR = re.compile(r"\b(intern|interns|trainee|student|fresher|junior|apprentice)\b")


def mentions_company(headline: str, company: str) -> bool:
    """The company is the person's EMPLOYER in the headline: the whole company name after "at" / "@" / "," / " - " or as a whole
    segment ("Tata" is not "Tata Motors"; "Ex-Infosys" is a former employer)."""
    c = norm_company(company)
    if not c:
        return False
    for seg in re.split(r"\s*[|\u00b7\u2022;]\s*", str(headline or "")):
        if _FORMER.search(_plain(seg)):
            continue
        if norm_company(seg) == c or any(norm_company(seg[m.end():]) == c for m in _SEP.finditer(seg)):
            return True
    return False


def _title_clash(lead: dict, card: dict) -> bool:
    """A junior title on the card for a lead whose title is not junior (an intern is not the CHRO)."""
    want = _plain(lead.get("title"))
    return bool(want) and bool(_JUNIOR.search(_plain(card.get("headline")))) and not _JUNIOR.search(want)


def profile_url(raw: str) -> str:
    """https://www.linkedin.com/in/<slug> for a LinkedIn profile link, else ''."""
    m = _PROFILE.match(str(raw or "").strip())
    return f"https://www.linkedin.com/in/{m.group(2)}" if m else ""


def search_url(lead: dict) -> str:
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(f"{lead.get('contact_name') or ''} {lead.get('company') or ''}".strip())


def clean_candidates(raw) -> list:
    """The cards the agent read, reduced to name / headline / location / profile link (only /in/ links), no duplicates."""
    seen, out = set(), []
    for c in (raw if isinstance(raw, list) else [])[:25]:
        if not isinstance(c, dict):
            continue
        url = profile_url(c.get("url") or c.get("profile_url"))
        if not url or url.lower() in seen:
            continue
        seen.add(url.lower())
        out.append({"name": str(c.get("name") or "").strip()[:120], "headline": str(c.get("headline") or "").strip()[:240],
                    "location": str(c.get("location") or "").strip()[:120], "url": url})
    return out


def decide(lead: dict, candidates: list) -> dict:
    """The one rule. {"status": found|ambiguous|not_found, "pick": candidate or None, "candidates": [up to 5]}.
    Accept only when exactly one card has the exact normalised name and its headline mentions the company."""
    cards = clean_candidates(candidates)
    want = norm_name(lead.get("contact_name"))
    exact = [c for c in cards if want and norm_name(c["name"]) == want]
    if len(exact) == 1 and mentions_company(exact[0]["headline"], lead.get("company")) and not _title_clash(lead, exact[0]):
        return {"status": "found", "pick": exact[0], "candidates": [exact[0]]}
    tokens = want.split()
    ends = {tokens[0], tokens[-1]} if tokens else set()
    plausible = [c for c in cards if c in exact or ends & set(norm_name(c["name"]).split())]
    plausible.sort(key=lambda c: (c not in exact, not mentions_company(c["headline"], lead.get("company"))))
    if not plausible:
        return {"status": "not_found", "pick": None, "candidates": []}
    return {"status": "ambiguous", "pick": None, "candidates": plausible[:MAX_CANDIDATES]}


# ---------------------------------------------------------------- settings
def account_of(store, lead: dict) -> str:
    from . import accounts
    return accounts.account_of_lead_in(store, lead)


def enabled(store, lead: dict) -> bool:
    from . import linkedin_agent
    return bool(linkedin_agent.config(store, account_of(store, lead)).get("find_profiles", True))


def refusal(store, lead: dict) -> str:
    """Why this lead cannot be looked up ('' = it can)."""
    if not enabled(store, lead):
        return NO_LINK
    if len(norm_name(lead.get("contact_name")).split()) < 2:
        return "Add the person's full name (first and last) so their LinkedIn profile can be found, or add the profile link."
    if not norm_company(lead.get("company")):
        return "Add the person's company so their LinkedIn profile can be found, or add the profile link."
    return ""


# ---------------------------------------------------------------- the lookup list
def _set(store, lead_id: str, lookup: dict, line: Optional[dict] = None):
    return store.update_lead(lead_id, lambda l: {**l, "linkedin_lookup": lookup, **({"timeline": [*l["timeline"], line]} if line else {})})


def queue_lookup(store, lead: dict, now: Optional[datetime] = None) -> dict:
    """Start LinkedIn on a lead without a link: put it on the lookup list. Raises ValueError with the reason when it cannot."""
    now = now or now_utc()
    why = refusal(store, lead)
    if why:
        raise ValueError(why)
    if lead.get("stage") in li.CLOSED:
        raise ValueError(f"Lead is {lead['stage']}; no LinkedIn outreach.")
    if lead.get("linkedin_seq"):
        raise ValueError("This lead is already on the LinkedIn sequence; it is never restarted.")
    if (lead.get("linkedin_lookup") or {}).get("status") == "queued":
        return {"lead": lead, "task": None, "lookup": "queued"}
    saved = _set(store, lead["id"], {"status": "queued", "queued_at": iso(now), "tried_at": None, "attempts": 0, "candidates": [],
                                     "search_url": search_url(lead)},
                 {"at": iso(now), "kind": "linkedin", "text": "No LinkedIn link: looking for the profile (name and company)"})
    return {"lead": saved, "task": None, "lookup": "queued"}


def next_for(store, account: str, now: Optional[datetime] = None, user: Optional[dict] = None) -> dict:
    """The oldest queued lead this agent may look up ({} when none). Marked as handed out so it is not given twice."""
    from . import extension_api as ext
    from . import workspaces
    now = now or now_utc()
    settings = store.read("settings")
    rows = []
    for l in store.read("leads"):
        look = l.get("linkedin_lookup") or {}
        if look.get("status") != "queued" or l.get("linkedin_seq") or l.get("stage") in li.CLOSED or not workspaces.visible(l):
            continue
        if account and ext.owner_of(l, settings) not in ("", account):
            continue
        if user is not None:                       # ownership: never offer a lead this person may not send to
            from . import lead_access
            if not lead_access.can_send(store, user, l):
                continue
        if look.get("handed_at") and datetime.fromisoformat(look["handed_at"]) > now - HAND_OUT_HOLD:
            continue
        rows.append(l)
    if not rows:
        return {}
    lead = min(rows, key=lambda l: l["linkedin_lookup"].get("queued_at") or "")
    store.update_lead(lead["id"], lambda l: {**l, "linkedin_lookup": {**l["linkedin_lookup"], "handed_at": iso(now)}})
    return {"lead_id": lead["id"], "name": lead.get("contact_name") or "", "company": lead.get("company") or "", "search_url": search_url(lead)}


def apply_result(store, lead_id: str, candidates, account: str = "", error: str = "", now: Optional[datetime] = None) -> dict:
    """The agent reports the first page of results (or that it could not read it). Decide and act."""
    now = now or now_utc()
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise LookupError("Lead not found")
    look = lead.get("linkedin_lookup") or {}
    if look.get("status") != "queued" or lead.get("linkedin_seq"):
        raise ValueError("This lead is not waiting for a profile lookup.")
    if error:
        attempts = int(look.get("attempts") or 0) + 1
        nxt = {**look, "attempts": attempts, "tried_at": iso(now), "handed_at": None, "note": error[:200]}
        if attempts >= MAX_ATTEMPTS:
            nxt["status"] = "manual"
        _set(store, lead_id, nxt)
        return {"status": nxt["status"]}
    verdict = decide(lead, candidates)
    base = {**look, "tried_at": iso(now), "handed_at": None, "search_url": search_url(lead), "attempts": int(look.get("attempts") or 0) + 1}
    if verdict["status"] != "found":
        _set(store, lead_id, {**base, "status": verdict["status"], "candidates": verdict["candidates"]},
             {"at": iso(now), "kind": "linkedin", "text": "LinkedIn profile search: " + (
                 f"{len(verdict['candidates'])} possible people, a person must pick one" if verdict["status"] == "ambiguous"
                 else "nobody matched; a person can search or paste the link")})
        return {"status": verdict["status"]}
    _accept(store, lead_id, verdict["pick"], base, now, how="found automatically, matched name and company", account=account)
    return {"status": "found", "url": verdict["pick"]["url"]}


def _origin(store, lead: dict, account: str) -> dict:
    from . import provenance
    return provenance.build(store, "linkedin_agent", True, lead, provenance.system(account=account),
                            {"step": "profile lookup"}, None, "linkedin")


def _accept(store, lead_id: str, cand: dict, base: dict, now: datetime, how: str, account: str = "", by: Optional[dict] = None) -> None:
    """Save the link on the lead (with how it was found), then start the designed sequence."""
    lead = next(l for l in store.read("leads") if l["id"] == lead_id)
    line = {"at": iso(now), "kind": "linkedin", "text": f"LinkedIn profile {how}: {cand['url']}",
            **({"by": by["name"], "origin": {"type": "manual", "trigger": "linkedin_by_hand", "actor": by, "recorded_at": iso(now)}} if by
               else {"origin": _origin(store, lead, account)})}
    store.update_lead(lead_id, lambda l: {**l, "linkedin_url": cand["url"], "linkedin_lookup": {**base, "status": "found", "picked": cand["url"],
                                                                                            "candidates": [cand] if cand.get("name") else [],
                                                                                            "found_at": iso(now), "via": "person" if by else "agent"},
                                          "timeline": [*l["timeline"], line]})
    li.start(store, lead_id, now)


def person_choice(store, lead_id: str, action: str, url: str = "", by: Optional[dict] = None, now: Optional[datetime] = None) -> dict:
    """A person settles the lookup: pick a candidate ("pick"), paste a link ("link") or give up ("skip")."""
    now = now or now_utc()
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise LookupError("Lead not found")
    look = lead.get("linkedin_lookup") or {}
    if lead.get("linkedin_seq") or look.get("status") not in ("queued", "ambiguous", "not_found", "manual"):
        raise ValueError("This lead is not waiting for a profile.")
    by = by or {"name": "A person", "email": ""}
    if action == "skip":
        _set(store, lead_id, {**look, "status": "manual", "skipped_by": by["name"], "handed_at": None},
             {"at": iso(now), "kind": "linkedin", "text": f"LinkedIn profile lookup skipped by {by['name']}", "by": by["name"]})
        return {"status": "manual"}
    clean = profile_url(url)
    if not clean:
        raise ValueError("That is not a LinkedIn profile link (it should look like https://www.linkedin.com/in/name).")
    if action == "pick":
        cand = next((c for c in look.get("candidates") or [] if c.get("url", "").lower() == clean.lower()), None)
        if cand is None:
            raise ValueError("That person is not one of the people found.")
        how = f"picked by {by['name']}"
    elif action == "link":
        cand, how = {"url": clean, "name": "", "headline": ""}, f"pasted by {by['name']}"
    else:
        raise ValueError("Unknown action.")
    if lead.get("stage") in li.CLOSED:
        raise ValueError(f"Lead is {lead['stage']}; no LinkedIn outreach.")
    _accept(store, lead_id, cand, look, now, how, by=by)
    return {"status": "found", "url": clean}


def waiting(leads) -> list:
    """Leads in the lookup list that a person or the agent still has to settle (for the LinkedIn queue and the lead drawer)."""
    out = []
    for l in leads:
        look = l.get("linkedin_lookup") or {}
        listed = look.get("status") in ("queued", "ambiguous", "not_found") or (look.get("status") == "manual" and not look.get("skipped_by"))
        if listed and not l.get("linkedin_seq") and l.get("stage") not in li.CLOSED:
            out.append(summary(l))
    return sorted(out, key=lambda r: r.get("queued_at") or "")


def summary(lead: dict) -> dict:
    look = lead.get("linkedin_lookup") or {}
    return {"lead_id": lead["id"], "company": lead.get("company"), "name": lead.get("contact_name"), "status": look.get("status"),
            "candidates": look.get("candidates") or [], "search_url": look.get("search_url") or search_url(lead),
            "queued_at": look.get("queued_at"), "tried_at": look.get("tried_at")}
