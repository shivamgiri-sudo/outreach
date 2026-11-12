"""Who is the LinkedIn sequence really for? Identity binding, wrong-person checks and inbox matching.

- A running sequence is bound to the person it started for (profile key + name). If the name or link is edited later the
  sequence is not carried over to "the new person" (audit D4).
- check_target(): what the agent SAW (profile name / url / chat title) against the lead. Positive evidence of another
  person = mismatch (the account is paused by linkedin_agent.record_send); missing evidence = "unverified" only, so an
  old agent that sends nothing keeps working.
- match_inbox(): a reply is attributed by profile link when the agent sends one; a name-only match never stops a
  sequence (it raises a task for a person), because strangers share names (audit D5).
"""
import re
from datetime import timedelta
from typing import Optional

from .activities import add_task, iso, now_utc

_HONORIFIC = re.compile(r"^(mr|mrs|ms|miss|dr|prof|shri|smt|er|ca|adv)\.?\s+", re.I)


def _name_key(name: str) -> str:
    words = re.sub(r"[^a-z\s]", " ", _HONORIFIC.sub("", (name or "").strip()).lower()).split()
    return " ".join([words[0], words[-1]]) if len(words) >= 2 else (words[0] if words else "")


def _tokens(name: str) -> list:
    text = re.sub(r"\([^)]*\)", " ", str(name or ""))                 # "(He/Him)"
    text = _HONORIFIC.sub("", text.strip()).split(",")[0].split("|")[0]
    return re.sub(r"[^a-z\s]", " ", text.lower()).split()


def names_match(seen: str, lead_name: str) -> Optional[bool]:
    """True / False when both sides have a usable name; None when there is nothing to compare (never a mismatch)."""
    want, got = _tokens(lead_name), set(_tokens(seen))
    if not want or not got:
        return None
    return want[0] in got and want[-1] in got


def slug_of(url: str) -> str:
    from .extension_api import profile_slug
    return profile_slug(url or "")


def target_of(lead: dict) -> dict:
    from .linkedin_dupes import profile_key
    return {"key": profile_key(lead.get("linkedin_url") or ""), "name": _name_key(lead.get("contact_name"))}


def target_changed(lead: dict) -> bool:
    """The name or profile link differs from the one the sequence started for. Old rows (no binding) are allowed."""
    t = (lead.get("linkedin_seq") or {}).get("target")
    if not t:
        return False
    now = target_of(lead)
    return t.get("key") != now["key"] or t.get("name") != now["name"]


def on_identity_edit(before: dict, after: dict) -> Optional[dict]:
    """Called by edit_contact with the lead before / after an edit. Returns the lead to save: the sequence is reset when the
    person is another one (other profile link, or another first name), re-bound when only the spelling changed."""
    seq = before.get("linkedin_seq")
    if not seq:
        return None
    a, b = target_of(before), target_of(after)
    if a == b:
        return None
    other = a["key"] != b["key"] or (a["name"].split(" ")[:1] != b["name"].split(" ")[:1])
    if not other:
        return {**after, "linkedin_seq": {**seq, "target": b}}
    return {**after, "linkedin_seq": None, "linkedin_attempts": [], "linkedin_lease": None,
            "timeline": [*after["timeline"], {"at": iso(now_utc()), "kind": "linkedin",
                                              "text": "LinkedIn sequence reset: the name or profile link was edited, so this is treated as another person. "
                                                      "Start the sequence again when the details are right."}]}


def check_target(lead: dict, proof: dict, kind: str, expected_text: str = "") -> dict:
    """What the agent saw vs the lead. mismatch only on positive evidence; absent evidence is 'unverified'."""
    bad = []
    seen_name = names_match(proof.get("observed_name", ""), lead.get("contact_name", "")) if proof.get("observed_name") else None
    if seen_name is False:
        bad.append(f"profile shows {proof['observed_name']}")
    want_slug, got_slug = slug_of(lead.get("linkedin_url", "")), slug_of(proof.get("observed_url", ""))
    if proof.get("observed_url") and want_slug and got_slug and got_slug != want_slug:
        bad.append(f"page is {proof['observed_url']}")
    chat = names_match(proof.get("chat_title", ""), lead.get("contact_name", "")) if (kind == "message" and proof.get("chat_title")) else None
    if chat is False:
        bad.append(f"chat is with {proof['chat_title']}")
    seen = bool(proof.get("observed_name") or proof.get("observed_url")) and (kind != "message" or bool(proof.get("chat_title")))
    text = (proof.get("text_sent") or "").strip()
    return {"mismatch": bool(bad), "mismatch_why": "; ".join(bad), "unverified": not seen,
            "text_matches": (not text) or text[:60] == (expected_text or "").strip()[:60]}


def match_inbox(store, account: str, items: list, now=None) -> list:
    """The agent read the LinkedIn messaging list: a conversation whose last message is from a person on the sequence
    (owned by this account or by no account yet) is their reply. Matched by the profile link the agent read from the
    thread; a name alone only raises a "check this" task. Returns [{lead_id, name, text}] marked replied."""
    from . import linkedin_seq as li, workspaces
    now = now or now_utc()
    active = [l for l in store.read("leads") if (l.get("linkedin_seq") or {}).get("status") in ("waiting_for_acceptance", "messaging")
              and (l["linkedin_seq"].get("account") or "") in ("", account) and l.get("stage") not in li.CLOSED
              and workspaces.visible(l)]                     # the agent's own client account only (its token's scope)
    by_key, by_slug = {}, {}
    for l in active:
        k = _name_key(l.get("contact_name"))
        if k:
            by_key.setdefault(k, []).append(l)
        s = slug_of(l.get("linkedin_url", ""))
        if s:
            by_slug.setdefault(s, []).append(l)
    replied, asked = [], set()
    for item in items:
        if not item.get("from_them"):
            continue
        slug = slug_of(item.get("profile_url") or "")
        found = by_slug.get(slug, []) if slug else []
        if len(found) != 1:
            key = _name_key(item.get("name"))
            cand = by_key.get(key, []) if key and not slug else []
            if len(cand) == 1 and cand[0]["id"] not in asked:      # name only: do NOT stop the sequence, ask a person
                asked.add(cand[0]["id"])
                add_task(store, cand[0]["id"], "follow_up", f"Possible LinkedIn reply from '{item.get('name')}': check before the next step.",
                         due_at=now + timedelta(hours=1), owner=li._owner(store, cand[0]), priority="high",
                         title="Check a possible LinkedIn reply", source="linkedin_sequence")
            continue
        if any(r["lead_id"] == found[0]["id"] for r in replied):
            continue
        text = str(item.get("text") or "").strip()[:2000]
        li.mark_replied(store, found[0]["id"], text, now)
        replied.append({"lead_id": found[0]["id"], "name": found[0].get("contact_name") or "", "text": text})
    return replied
