"""Heal a saved LinkedIn link that LinkedIn itself has replaced (an /in/ACo... member id, or a renamed vanity slug).

The agent never rewrites a link on its own: when the page it opened shows the lead's name EXACTLY (all name words next to
each other, same order) at another address, it only reports what it saw (observed url + page name). This module decides.
The link is rewritten only when
  (a) the page name matches the lead name exactly (strict, like the agent's namesLead),
  (b) no other lead of the same workspace holds the new vanity address or the old one (a namesake / duplicate),
  (c) the lead's sequence is still bound to its current link (an edit is never laundered), and the binding moves to the new key.
Anything else keeps the refusal ("correct the link"). Nothing here clicks or sends anything.
"""
import re
from typing import Optional

from .activities import iso, now_utc

_HONORIFIC = re.compile(r"^(mr|mrs|ms|miss|dr|prof|shri|smt|er|ca|cs|cma|adv|capt|col|maj)\.?\s+", re.I)
_MEMBER_ID = ("acw", "acoa")


def _words(text: str) -> list:
    head = re.split(r"[,(|]", str(text or ""))[0].strip()
    head = _HONORIFIC.sub("", head).lower()
    return [w for w in re.sub(r"[^\w\s]|\d|_", " ", head).split() if len(w) > 1]


def name_matches_exactly(seen: str, lead_name: str) -> bool:
    """The lead's name words (two or more) appear next to each other, in order, in the name the page shows."""
    want, have = _words(lead_name), _words(seen)
    return len(want) >= 2 and (" " + " ".join(have) + " ").find(" " + " ".join(want) + " ") >= 0


def is_vanity(slug: str) -> bool:
    return bool(slug) and not slug.lower().startswith(_MEMBER_ID)


def refusal(reason: str) -> dict:
    return {"healed": False, "reason": reason}


def heal(store, lead_id: str, observed_url: str, observed_name: str, now=None) -> dict:
    """{"healed": bool, "reason": str, "url": new link when healed}."""
    from . import workspaces
    from .extension_api import profile_slug
    from .linkedin_target import target_changed, target_of
    now = now or now_utc()
    leads = store.read("leads")
    lead = next((l for l in leads if l.get("id") == lead_id), None)
    if lead is None:
        return refusal("no such lead")
    new, old = profile_slug(observed_url), profile_slug(lead.get("linkedin_url") or "")
    if not is_vanity(new):
        return refusal("the page address is not a public profile address")
    if not old or new == old:
        return refusal("nothing to update")
    if not name_matches_exactly(observed_name, lead.get("contact_name") or ""):
        return refusal("the page name is not an exact match of the lead name")
    if target_changed(lead):
        return refusal("the name or link was edited after the sequence started")
    ws = workspaces.ws_of(lead)
    for other in leads:
        if other.get("id") != lead_id and workspaces.ws_of(other) == ws and \
                profile_slug(other.get("linkedin_url") or "") in (new, old):
            return refusal("another lead in this workspace already holds that LinkedIn address")
    url = f"https://www.linkedin.com/in/{new}"

    def change(l: dict) -> Optional[dict]:
        if profile_slug(l.get("linkedin_url") or "") != old or target_changed(l):
            return None                                   # changed under us: leave it
        fresh = {**l, "linkedin_url": url}
        seq = l.get("linkedin_seq")
        if seq and seq.get("target"):
            fresh["linkedin_seq"] = {**seq, "target": target_of(fresh)}
        fresh["timeline"] = [*l.get("timeline", []), {"at": iso(now), "kind": "linkedin",
                             "text": f"LinkedIn link updated to {url} (same person: page name matched)"}]
        return fresh
    saved = store.update_lead(lead_id, change)
    if saved is None or saved.get("linkedin_url") != url:
        return refusal("the lead changed while updating; nothing was rewritten")
    return {"healed": True, "reason": "", "url": url}
