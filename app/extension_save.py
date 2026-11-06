"""Chrome assistant "Save to portal": a rep has ONE LinkedIn profile open and presses the button in the side panel.

The extension sends that profile's link plus the name, headline and current company the rep saw (and could edit) on
that one page. Nothing here visits LinkedIn or collects anything by itself: one click = one profile.

- The link must be a single person's profile (https://www.linkedin.com/in/<slug>, normalised by lead_model).
- An existing lead is matched by profile slug, then by name + company (extension_api.find_lead, never a guess). A match
  only gets its EMPTY fields filled; nothing the team already has is overwritten.
- Otherwise a new lead is made (source "linkedin_manual", owned by the rep) in the rep's workspace.
- People on the do-not-contact / suppression lists, erased people, and leads in a workspace the rep does not work in
  are refused with a plain message. Every save and refusal writes an audit row.
- Human pace: at most SAVES_PER_HOUR saves per pairing token.
"""
import re
import threading
import time
from collections import deque
from typing import Optional

from . import audit, extension_api as ext, workspaces
from .lead_model import build_lead, event, linkedin_profile_url, now_iso
from .privacy import is_blocked, linkedin_hash
from .rep_workflow import actor_name
from .source_names import add_touch

SOURCE = "linkedin_manual"
SAVES_PER_HOUR = 60
NAME_MAX, FIELD_MAX = 120, 200
FILL_LABELS = {"contact_name": "Name", "title": "Title", "company": "Company", "linkedin_url": "LinkedIn profile",
               "city": "City", "state": "State"}
CONTROL = re.compile(r"[\x00-\x1f\x7f​-‏ -‮⁦-⁩]")


class SaveRefused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class HourlyLimit:
    """Sliding one-hour window per key (the pairing token). In memory: a restart forgets it, which is fine for pacing."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key: str, per_hour: int) -> bool:
        with self._lock:
            now = self.clock()
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= 3600:
                hits.popleft()
            if len(hits) >= per_hour:
                return False
            hits.append(now)
            return True


def clean(raw, limit: int) -> str:
    return " ".join(CONTROL.sub(" ", str(raw or "")).split())[:limit]


def _place(location: str) -> dict:
    """'Bengaluru, Karnataka, India' -> city Bengaluru, state Karnataka. Only the city when there is no state part."""
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if not parts:
        return {}
    out = {"city": parts[0][:80]}
    if len(parts) >= 3:
        out["state"] = parts[1][:80]
    return out


def _do_not_contact(store, lead: dict) -> str:
    if lead.get("do_not_contact"):
        return "This person is on the do-not-contact list. Nothing was saved."
    if lead.get("stage") in ("unsubscribed", "disqualified"):
        return f"This person is {lead['stage']} (do-not-contact). Nothing was saved."
    if is_blocked(store, lead.get("email"), lead.get("phone")):
        return "This person's contact details are on the suppression list. Nothing was saved."
    return ""


def _match(store, url: str, name: str, company: str) -> Optional[dict]:
    lead = ext.find_lead(store, url, name, company)
    if lead is None:
        return None
    theirs = ext.profile_slug(lead.get("linkedin_url", ""))
    if theirs and theirs != ext.profile_slug(url):
        return None                        # same name at the same company, but a different profile: another person
    return lead


def save_profile(store, user: dict, body: dict, account: str = "") -> dict:
    """{ok, lead_id, result: created|updated|unchanged, changed: [labels], match: describe(...)}. Raises SaveRefused."""
    rep = actor_name(store, user)
    url = linkedin_profile_url(body.get("profile_url"))
    if not url:
        raise SaveRefused(400, "That is not one person's LinkedIn profile link (linkedin.com/in/…). Open a profile and try again.")
    name = clean(body.get("name"), NAME_MAX)
    if not name:
        raise SaveRefused(400, "The name is empty. Type the person's name as shown on their profile.")
    title, company, location = (clean(body.get(k), FIELD_MAX) for k in ("title", "company", "location"))

    if linkedin_hash(url) in set(store.read("suppressed_hashes")):
        audit.record(store, user, "extension.save_profile", "", "refused: this person's data was erased on request")
        raise SaveRefused(409, "This person asked for their data to be erased, so they cannot be saved again.")

    values = {"contact_name": name, "title": title, "company": company, "linkedin_url": url, **_place(location)}
    lead = _match(store, url, name, company)
    if lead is not None:
        return _fill(store, user, rep, lead, values, account)
    return _create(store, user, rep, values, account)


def _fill(store, user, rep: str, lead: dict, values: dict, account: str) -> dict:
    if not workspaces.visible(lead):
        audit.record(store, user, "extension.save_profile", lead["id"], "refused: lead is in a workspace this person does not work in")
        raise SaveRefused(409, "This person is already a lead in a workspace you do not work in. Ask the owner to share it.")
    why = _do_not_contact(store, lead)
    if why:
        audit.record(store, user, "extension.save_profile", lead.get("company") or lead["id"], f"refused (do-not-contact): lead {lead['id']}")
        raise SaveRefused(409, why)
    changed = []

    def apply(current: dict):
        filled = {k: v for k, v in values.items() if v and not str(current.get(k) or "").strip()}
        if not filled:
            return None
        changed.extend(filled)
        merged = {**current, **filled, "field_sources": {**(current.get("field_sources") or {}), **{k: SOURCE for k in filled}}}
        add_touch(merged, SOURCE)
        text = f"Details added from LinkedIn by {rep}: " + ", ".join(FILL_LABELS[k] for k in filled)
        merged["timeline"] = [*current["timeline"], {**event("linkedin", text), "by": rep}]
        return merged
    saved = store.update_lead(lead["id"], apply) or lead
    result = "updated" if changed else "unchanged"
    labels = [FILL_LABELS[k] for k in changed]
    audit.record(store, user, "extension.save_profile", saved.get("company") or saved["id"],
                 f"{result} lead {saved['id']}" + (f"; filled {', '.join(labels)}" if labels else "; nothing was empty"))
    return {"ok": True, "lead_id": saved["id"], "result": result, "changed": labels, "match": ext.describe(store, saved, account)}


def _create(store, user, rep: str, values: dict, account: str) -> dict:
    lead = build_lead(values, SOURCE, fallback_key=f"linkedin:{values['linkedin_url'].lower()}")
    if lead is None:                                   # cannot happen with the fallback key; kept as a guard
        raise SaveRefused(400, "Could not make a lead from this profile.")
    stamp = now_iso()
    lead["timeline"] = [{**event("created", f"Saved from LinkedIn by {rep}"), "by": rep}]
    lead["owner"], lead["owner_at"] = rep, stamp
    lead["field_sources"] = {k: SOURCE for k, v in values.items() if v}
    added = store.add_leads([lead])
    if not added:
        twin = next((l for l in store.read("leads") if l.get("dedupe_key") == lead["dedupe_key"]), None)
        if twin is not None:                           # saved a moment ago (double click / two tabs): fill it instead
            return _fill(store, user, rep, twin, values, account)
        raise SaveRefused(409, "This person cannot be saved (they are on the do-not-contact list).")
    saved = added[0]
    audit.record(store, user, "extension.save_profile", saved.get("company") or saved["id"],
                 f"created lead {saved['id']} (workspace {workspaces.ws_of(saved)})")
    return {"ok": True, "lead_id": saved["id"], "result": "created", "changed": [FILL_LABELS[k] for k, v in values.items() if v],
            "match": ext.describe(store, saved, account)}
