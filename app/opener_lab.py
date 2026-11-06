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
    unknown = sorted({p for p in PLACEHOLDER.findall(text) if p != "first_name"})
    rendered = text.replace("{first_name}", LONG_NAME)
    if not text:
        return {"score": 0, "reasons": ["Empty note."], "length": 0, "ok": False}
    if unknown:
        return {"score": 0, "reasons": [f"Unknown placeholder {{{unknown[0]}}}; only {{first_name}} is filled in."], "length": len(rendered), "ok": False}
    if len(rendered) > NOTE_LIMIT:
        return {"score": 0, "reasons": [f"Too long: {len(rendered)} characters with a long name; LinkedIn allows {NOTE_LIMIT}."], "length": len(rendered), "ok": False}
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


def candidates(store, context: str = "", n: int = 4, transport=post_json) -> dict:
    """Up to n scored candidate notes, best first. Claude writes them when connected; otherwise the built-in set."""
    n = max(2, min(int(n or 4), 6))
    cfg = ai._llm_config(store)
    notes, source = [], "built_in"
    if cfg:
        user = f"Write {n} notes." + (f"\n<context>\n{ai._untrusted(context)[:500]}\n</context>" if context.strip() else "")
        try:
            raw = ai._call_llm(cfg, WRITER_SYSTEM, user, 900, transport)
            found = re.search(r"\{.*\}", raw, re.S)
            notes = [str(x).strip() for x in (json.loads(found.group(0)).get("notes") or [])] if found else []
            source = "claude" if notes else source
        except (*ai._AI_ERRORS, json.JSONDecodeError):
            notes = []
    if not notes:
        notes = BUILT_IN[:n]
    out = [{"text": t[:600], **score_note(t)} for t in notes[:n] if t]
    out.sort(key=lambda r: -r["score"])
    return {"source": source, "candidates": out}


def current(store) -> dict:
    from .linkedin_seq import CONNECT_VARIANTS
    saved = store.read("settings").get("linkedin_connect_variants") or {}
    notes = {v: saved.get(v) or CONNECT_VARIANTS[v] for v in ("A", "B")}
    return {"notes": {v: {"text": t, **score_note(t)} for v, t in notes.items()},
            "changed_at": saved.get("changed_at"), "changed_by": saved.get("changed_by")}


def save(store, a: Optional[str], b: Optional[str], by: str, stamp: str) -> dict:
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
    store.update("settings", lambda s: {**s, "linkedin_connect_variants": {**(s.get("linkedin_connect_variants") or {}), **changes,
                                                                           "changed_at": stamp, "changed_by": by}})
    return current(store)
