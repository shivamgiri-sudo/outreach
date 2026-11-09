"""LinkedIn connection-note lab: score a note, have Claude write candidates (or use built-in ones), save notes A and B.

Scores are simple, explainable rules (length, personal greeting, no pitch, no link, about their world), not a promise of
results. The real test is the A/B accept rate on the Intelligence page; the score only filters out weak drafts early.
"""
import json
import re
from typing import Optional

from . import ai
from .http_util import post_json

NOTE_LIMIT = 300
LONG_NAME = "Venkataraghavan"            # notes are checked with a long first name so real ones never overflow
PITCH = re.compile(r"\b(demo|pricing|price|offer|free trial|discount|buy|sale|book a call|schedule a call|limited time|guarantee)\b", re.I)
THEIR_WORLD = re.compile(r"\b(hiring|hire|team|talent|recruit\w*|interview\w*|candidates?|roles?|people)\b", re.I)
PLACEHOLDER = re.compile(r"\{(\w+)\}")

BUILT_IN = [
    "Hi {first_name}, I follow how growing companies in India handle hiring at scale. Your work with teams caught my eye, "
    "so I'd like to connect and learn from your posts.",
    "Hi {first_name}, I spend most days talking to HR leaders about what slows hiring down. Would be glad to have you in my "
    "network and swap notes now and then.",
    "Hi {first_name}, noticed you lead people and hiring decisions. I work on the interview side of recruitment and enjoy "
    "comparing notes with people who build teams. Happy to connect.",
    "Hi {first_name}, connecting with leaders who care about getting the right people in the right roles. No pitch, just "
    "keen to follow what you share.",
]


def score_note(text: str) -> dict:
    text = str(text or "").strip()
    reasons, score = [], 0
    if not text:
        return {"score": 0, "reasons": ["Empty note."], "length": 0, "ok": False}
    # The same placeholders the LinkedIn steps editor accepts and the sequence fills in ({company}, {hook}, {{my_company}}...).
    # Only {first_name} was allowed here, so a note B saved fine in "Steps & personas" was refused in this lab.
    from .linkedin_config import check_placeholders, stand_in_length
    try:
        check_placeholders(text, "Note")
    except ValueError as exc:
        return {"score": 0, "reasons": [str(exc)], "length": len(text), "ok": False}
    length = stand_in_length(text)
    rendered = "x" * length                  # only its length is used below: filled with a long name and company
    if len(rendered) > NOTE_LIMIT:
        return {"score": 0, "reasons": [f"Too long: {len(rendered)} characters with a long name and company; LinkedIn allows {NOTE_LIMIT}."], "length": len(rendered), "ok": False}
    if 110 <= len(rendered) <= 260:
        score += 25; reasons.append("Good length.")
    else:
        score += 10; reasons.append("Very short or close to the limit." if len(rendered) < 110 else "Close to the 300-character limit.")
    if "{first_name}" in text:
        score += 15; reasons.append("Greets them by name.")
    else:
        reasons.append("Add {first_name} so it feels personal.")
    if PITCH.search(text):
        reasons.append(f"Sounds like a pitch (“{PITCH.search(text).group(0)}”); connection notes accept better without one.")
    else:
        score += 20; reasons.append("No sales pitch.")
    if re.search(r"https?://|www\.", text, re.I):
        reasons.append("Has a link; links in connection notes look like spam.")
    else:
        score += 10
    if THEIR_WORLD.search(text):
        score += 10; reasons.append("About their world (hiring, teams).")
    else:
        reasons.append("Mention their world: hiring, teams or talent.")
    body = re.sub(r"^hi\s+\{first_name\},?\s*", "", text, flags=re.I)
    if len(re.findall(r"\bI\b", body)) <= 2 and not body.startswith("I "):
        score += 10
    else:
        reasons.append("Talks about yourself a lot; turn it towards them.")
    if text.count("!") <= 1 and not re.search(r"\b[A-Z]{4,}\b", text):
        score += 10
    else:
        reasons.append("Too many exclamation marks or capital letters.")
    return {"score": min(score, 100), "reasons": reasons, "length": len(rendered), "ok": True}


WRITER_SYSTEM = "\n".join([
    "You write LinkedIn connection-request notes for an Indian B2B hiring-tech company (BookMyInterview.ai) reaching HR",
    "leaders, talent heads and founders. Each note: at most 240 characters, starts with 'Hi {first_name},', warm and plain,",
    "about the reader's world (hiring, teams), NO pitch, NO product name, NO link, NO invented facts, numbers or customers,",
    "no flattery that pretends you read something specific. Write each note in a different style.",
    'Return only JSON: {"notes": ["...", "..."]}',
])


def writer_system(store) -> str:
    """The note writer's brief, describing the seller from the brand and offer (app/brand.py); the default brand
    keeps the original wording."""
    from .brand import brand_is_default, get_brand, get_offer_profile, prompt_profile
    settings = store.read("settings")
    if brand_is_default(settings):
        return WRITER_SYSTEM
    brand, offer, profile = get_brand(settings), get_offer_profile(settings), prompt_profile(settings)
    return "\n".join([
        f"You write LinkedIn connection-request notes for {profile['seller']} ({brand['product_name']}) reaching "
        f"{offer['who'] or 'business owners and decision makers'}.",
        "Each note: at most 240 characters, starts with 'Hi {first_name},', warm and plain,",
        f"about the reader's world ({offer['topic'] or 'their work'}), NO pitch, NO product name, NO link, NO invented facts, "
        "numbers or customers,",
        "no flattery that pretends you read something specific. Write each note in a different style.",
        'Return only JSON: {"notes": ["...", "..."]}',
    ])


def candidates(store, context: str = "", n: int = 4, transport=post_json) -> dict:
    """Up to n scored candidate notes, best first. Claude writes them when connected; otherwise the built-in set."""
    n = max(2, min(int(n or 4), 6))
    cfg = ai._llm_config(store)
    notes, source = [], "built_in"
    if cfg:
        user = f"Write {n} notes." + (f"\n<context>\n{ai._untrusted(context)[:500]}\n</context>" if context.strip() else "")
        try:
            raw = ai._call_llm(cfg, writer_system(store), user, 900, transport)
            found = re.search(r"\{.*\}", raw, re.S)
            notes = [str(x).strip() for x in (json.loads(found.group(0)).get("notes") or [])] if found else []
            source = "claude" if notes else source
        except (*ai._AI_ERRORS, json.JSONDecodeError):
            notes = []
    if not notes:
        notes = BUILT_IN[:n]
    out = [{"text": t[:600], **score_note(t)} for t in notes[:n] if t]
    out.sort(key=lambda r: -r["score"])
    if source == "claude":                       # remembered so a note saved from (an edit of) one is marked AI-drafted
        keep = [_norm(r["text"]) for r in out]
        store.update("settings", lambda s: {**s, AI_KEY: [*(s.get(AI_KEY) or []), *keep][-AI_KEEP:]})
    return {"source": source, "candidates": out}


AI_KEY, AI_KEEP = "linkedin_ai_note_candidates", 40


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _from_ai(store, text: str) -> bool:
    """The saved note is an AI candidate or a light edit of one (same opening and mostly the same words)."""
    mine = _norm(text)
    words = set(mine.split())
    for cand in store.read("settings").get(AI_KEY) or []:
        theirs = set(cand.split())
        if mine == cand or (mine[:40] == cand[:40]) or (words and len(words & theirs) / max(len(words | theirs), 1) >= 0.7):
            return True
    return False


def ai_note(settings: dict, lead: dict) -> str:
    """Who approved the AI-drafted connection note this lead gets ("" when it is not AI-drafted)."""
    from .ws_templates import for_lead
    variant = (lead.get("linkedin_seq") or {}).get("variant") or "A"
    return ((for_lead(settings, lead).get("linkedin_connect_variants") or {}).get("ai") or {}).get(variant, "")


def current(store, ws: str = "main") -> dict:
    """Notes A and B of one workspace (its own, else main's; app/ws_templates.py)."""
    from .linkedin_config import variants
    from .ws_templates import settings_for
    settings = settings_for(store.read("settings"), ws)
    saved = settings.get("linkedin_connect_variants") or {}
    notes = variants(settings)                  # saved notes, else step 1 of the (editable) sequence and the built-in B
    return {"notes": {v: {"text": t, **score_note(t)} for v, t in notes.items()},
            "changed_at": saved.get("changed_at"), "changed_by": saved.get("changed_by")}


def save(store, a: Optional[str], b: Optional[str], by: str, stamp: str, ws: str = "main") -> dict:
    changes = {}
    for key, text in (("A", a), ("B", b)):
        if text is None:
            continue
        result = score_note(text)
        if not result["ok"]:
            raise ValueError(f"Note {key}: {result['reasons'][0]}")
        changes[key] = " ".join(text.split())
    if not changes:
        raise ValueError("Nothing to save.")
    ai_by = {k: by for k, t in changes.items() if _from_ai(store, t)}

    from . import ws_templates as wst

    def apply(s):
        old = wst.settings_for(s, ws).get("linkedin_connect_variants") or {}
        if ws != wst.MAIN and not wst.own(s, ws).get("linkedin_connect_variants") and not wst.own(s, ws).get("linkedin_sequence"):
            from .linkedin_config import variants
            old = {**old, **variants(s)}            # a workspace's first notes: start from what it saw (main's A and B)
        ai = {k: v for k, v in (old.get("ai") or {}).items() if k not in changes}
        notes = {**old, **changes, "ai": {**ai, **ai_by}, "changed_at": stamp, "changed_by": by}
        if ws != wst.MAIN:
            return wst.put_own(s, ws, {"linkedin_connect_variants": notes})
        return {**s, "linkedin_connect_variants": notes}
    store.update("settings", apply)
    return current(store, ws)
