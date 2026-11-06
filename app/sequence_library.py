"""Editable email sequences (several of them), per-step A/B tests with an automatic winner, and the snippet library.

A sequence is saved in the "sequences" collection as
    {id, name, notes, workspace, steps: [{day, name, subject, body, variants: [{name, subject?, body?}], winner?}],
     version, history: [{version, steps}], created_by, updated_at, archived}
The built-in DEFAULT_SEQUENCE is shown as the read-only entry "default" ("Default (6 emails)"). It is never saved, so a
lead without `sequence.sequence_id` (every lead enrolled before this existed) keeps getting exactly the old emails.

Versions: a lead remembers `sequence.sequence_id` and `sequence.version`. Changing the step days (or the number of
steps) of a sequence that leads are part-way through saves the old steps in `history` and bumps `version`, so those
leads carry on with the days they started on; new enrolments get the new version. Wording-only edits keep the version.

A/B rule (documented in the UI too): a step with variants sends the step's own text as variant "A" and each extra
variant by its name. A lead's variant is fixed by a hash of lead id + sequence + step day, so assignment is even and a
retry always sends the same text. Once every variant of the step has MIN_SENDS_PER_VARIANT real (live) sends, the
variant with the best reply rate wins when it has at least MIN_WINNER_REPLIES replies, beats the runner-up by at least
MIN_LEAD_POINTS percentage points AND by at least WIN_RATIO times its rate. The winner is saved on the step and from then
on every new send of that step uses only the winner. Opens are shown for information but never decide the winner.
"""
import hashlib
import re
import uuid
from datetime import datetime, timezone

from .sequences import DEFAULT_SEQUENCE

DEFAULT_ID = "default"
DEFAULT_NAME = "Default (6 emails)"
MAX_STEPS, MAX_SUBJECT, MAX_BODY, MAX_NAME, MAX_NOTES, MAX_VARIANTS = 10, 200, 6000, 120, 500, 3
MAX_SEQUENCES, MAX_SNIPPETS = 100, 500
# Placeholders the sequence engine fills (sequences.lead_variables + the runner's extras).
KNOWN_PLACEHOLDERS = ("greeting", "first_name", "company", "role", "sender", "unsub_link", "offer_line", "demo_link",
                      # brand placeholders (app/brand.py), filled the same way in every channel
                      "my_company", "my_product", "my_tagline", "my_value_1", "my_value_2", "my_value_3", "my_cta",
                      "my_website", "sign_off")
UNSUB_TOKEN = "{{unsub_link}}"
UNSUB_LINE = "\n\nNot relevant? Unsubscribe: {{unsub_link}}"
SNIPPET_CHANNELS = ("email", "whatsapp", "linkedin", "sms")
MIN_SENDS_PER_VARIANT = 30
MIN_WINNER_REPLIES = 3
MIN_LEAD_POINTS = 2.0
WIN_RATIO = 1.5
_VAR = re.compile(r"\{\{(\w+)\}\}")   # same as sequences.render: no spaces inside
_REPLY_SKIP = ("[auto_reply]", "[bounce]")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(store, key: str) -> list:
    try:
        return store.read(key)
    except KeyError:                       # data files saved before this collection existed
        return []


def _update(store, key: str, change) -> list:
    try:
        return store.update(key, change)
    except KeyError:
        store.write(key, [])
        return store.update(key, change)


# ---- the built-in sequence ----
def default_entry() -> dict:
    return {"id": DEFAULT_ID, "name": DEFAULT_NAME, "notes": "The built-in executive sequence. Read-only: duplicate it to change the words.",
            "workspace": "", "steps": [dict(s) for s in DEFAULT_SEQUENCE], "version": 1, "history": [], "builtin": True,
            "archived": False, "created_by": "built in", "updated_at": ""}


def all_sequences(store, include_archived: bool = False) -> list:
    saved = [s for s in _read(store, "sequences") if include_archived or not s.get("archived")]
    return [default_entry(), *sorted(saved, key=lambda s: s.get("name", "").lower())]


def get_sequence(store, seq_id: str):
    if not seq_id or seq_id == DEFAULT_ID:
        return default_entry()
    return next((s for s in _read(store, "sequences") if s["id"] == seq_id), None)


def steps_for(store, seq_state: dict):
    """The steps a lead's sequence uses: the default for old leads, else the saved sequence at the lead's version.

    None when the lead points at a sequence that no longer exists (the runner then holds the lead, never guesses)."""
    seq_id = (seq_state or {}).get("sequence_id") or DEFAULT_ID
    if seq_id == DEFAULT_ID:
        return DEFAULT_SEQUENCE
    saved = get_sequence(store, seq_id)
    if saved is None:
        return None
    version = (seq_state or {}).get("version") or saved.get("version", 1)
    if version == saved.get("version", 1):
        return saved["steps"]
    old = next((h for h in saved.get("history") or [] if h.get("version") == version), None)
    return old["steps"] if old else None


def leads_in_flight(store, seq_id: str) -> list:
    """Leads part-way through this sequence (enrolled, not stopped)."""
    return [l for l in store.read("leads") if (l.get("sequence") or {}).get("sequence_id") == seq_id
            and not l["sequence"].get("stopped")]


# ---- validation ----
def unknown_placeholders(text: str) -> list:
    return sorted({m.group(1) for m in _VAR.finditer(text or "")} - set(KNOWN_PLACEHOLDERS))


def _text(value, limit: int, what: str, required: bool = True) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if required and not text:
        raise ValueError(f"{what} is empty.")
    if len(text) > limit:
        raise ValueError(f"{what} is longer than {limit} characters.")
    return text


def _check_placeholders(text: str, where: str) -> None:
    bad = unknown_placeholders(text)
    if bad:
        raise ValueError(f"{where}: unknown placeholder(s) " + ", ".join("{{" + b + "}}" for b in bad)
                         + ". Use only " + ", ".join("{{" + k + "}}" for k in KNOWN_PLACEHOLDERS) + ".")
    if "{{" in _VAR.sub("", text) or "}}" in _VAR.sub("", text):
        raise ValueError(f"{where}: write placeholders exactly like {{{{company}}}} (no spaces, both braces).")


def _clean_variant(raw: dict, where: str, taken: set) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: a variant must be an object.")
    name = _text(raw.get("name"), 40, f"{where} variant name")
    if name.upper() == "A" or name in taken:
        raise ValueError(f"{where}: variant name '{name}' is used already (A is the step's own text).")
    subject = _text(raw.get("subject"), MAX_SUBJECT, f"{where} variant {name} subject", required=False)
    body = _text(raw.get("body"), MAX_BODY, f"{where} variant {name} body", required=False)
    if not subject and not body:
        raise ValueError(f"{where}: variant {name} changes nothing (give a subject or a body).")
    if body and UNSUB_TOKEN not in body:
        raise ValueError(f"{where}: variant {name} body must contain {UNSUB_TOKEN}.")
    for text, part in ((subject, "subject"), (body, "body")):
        _check_placeholders(text, f"{where} variant {name} {part}")
    return {"name": name, **({"subject": subject} if subject else {}), **({"body": body} if body else {})}


def clean_steps(raw_steps) -> list:
    """Validated steps, or ValueError with a plain reason."""
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_STEPS:
        raise ValueError(f"A sequence needs 1 to {MAX_STEPS} steps.")
    steps, last_day = [], None
    for i, raw in enumerate(raw_steps):
        where = f"Step {i + 1}"
        if not isinstance(raw, dict):
            raise ValueError(f"{where} must be an object.")
        try:
            day = int(raw.get("day"))
        except (TypeError, ValueError):
            raise ValueError(f"{where}: the day must be a whole number.") from None
        if isinstance(raw.get("day"), bool) or (last_day is None and day != 0) or (last_day is not None and day <= last_day) or day > 365:
            raise ValueError(f"{where}: days must start at 0 (the first email) and go up step by step (got {day}).")
        last_day = day
        name = _text(raw.get("name") or f"Email {i + 1}", MAX_NAME, f"{where} name")
        subject = _text(raw.get("subject"), MAX_SUBJECT, f"{where} subject")
        body = _text(raw.get("body"), MAX_BODY, f"{where} body")
        if UNSUB_TOKEN not in body:
            raise ValueError(f"{where}: the body must contain {UNSUB_TOKEN} (the unsubscribe link every cold email carries).")
        _check_placeholders(subject, f"{where} subject")
        _check_placeholders(body, f"{where} body")
        variants, taken = [], set()
        for v in (raw.get("variants") or [])[:MAX_VARIANTS + 1]:
            cleaned = _clean_variant(v, where, taken)
            taken.add(cleaned["name"])
            variants.append(cleaned)
        if len(variants) > MAX_VARIANTS:
            raise ValueError(f"{where}: at most {MAX_VARIANTS} extra variants.")
        step = {"day": day, "name": name, "subject": subject, "body": body, "variants": variants}
        # Legacy role/link variants of a duplicated default step stay (the engine picks them by the lead's data).
        for key in ("subject_no_role", "body_no_role", "body_with_link", "body_with_link_no_role"):
            if raw.get(key):
                extra = _text(raw[key], MAX_BODY, f"{where} {key}")
                _check_placeholders(extra, f"{where} {key}")
                if key.startswith("body") and UNSUB_TOKEN not in extra:
                    raise ValueError(f"{where}: {key} must contain {UNSUB_TOKEN}.")
                step[key] = extra
        if raw.get("winner") and (raw["winner"] == "A" or raw["winner"] in taken):
            step["winner"] = raw["winner"]
            step["winner_at"] = raw.get("winner_at") or _now_iso()
        steps.append(step)
    return steps


def _days(steps: list) -> list:
    return [s["day"] for s in steps]


# ---- create / edit ----
def create_sequence(store, body: dict, by: str) -> dict:
    name = _text(body.get("name"), MAX_NAME, "The name")
    if len(_read(store, "sequences")) >= MAX_SEQUENCES:
        raise ValueError(f"At most {MAX_SEQUENCES} sequences; archive or reuse one.")
    row = {"id": uuid.uuid4().hex[:10], "name": name, "notes": _text(body.get("notes"), MAX_NOTES, "Notes", required=False),
           "workspace": _text(body.get("workspace"), 40, "Workspace", required=False),
           "steps": clean_steps(body.get("steps")), "version": 1, "history": [], "created_by": by,
           "created_at": _now_iso(), "updated_at": _now_iso(), "updated_by": by, "archived": False}
    _update(store, "sequences", lambda rows: [*rows, row])
    return row


def duplicate_sequence(store, seq_id: str, by: str, name: str = "") -> dict:
    source = get_sequence(store, seq_id)
    if source is None:
        raise LookupError("Sequence not found")
    # A copy starts a fresh A/B test, and step names lose a "(Day N)" ending since the days may now change.
    steps = [{**{k: v for k, v in s.items() if k not in ("winner", "winner_at")},
              "name": re.sub(r"\s*\(Day \d+\)\s*$", "", s.get("name") or "")} for s in source["steps"]]
    notes = "Copy of the built-in executive sequence." if source.get("builtin") else source.get("notes", "")
    return create_sequence(store, {"name": name or f"Copy of {source['name']}"[:MAX_NAME], "notes": notes,
                                   "workspace": source.get("workspace", ""), "steps": steps}, by)


def update_sequence(store, seq_id: str, body: dict, by: str) -> dict:
    """Edit a saved sequence. Day changes on a sequence in use create a new version (old leads keep theirs)."""
    if seq_id == DEFAULT_ID:
        raise PermissionError("The default sequence is read-only. Duplicate it to make your own.")
    current = get_sequence(store, seq_id)
    if current is None:
        raise LookupError("Sequence not found")
    changes = {}
    if "name" in body and body["name"] is not None:
        changes["name"] = _text(body["name"], MAX_NAME, "The name")
    if "notes" in body and body["notes"] is not None:
        changes["notes"] = _text(body["notes"], MAX_NOTES, "Notes", required=False)
    if "workspace" in body and body["workspace"] is not None:
        changes["workspace"] = _text(body["workspace"], 40, "Workspace", required=False)
    new_version = None
    if body.get("steps") is not None:
        steps = clean_steps(body["steps"])
        changes["steps"] = steps
        if _days(steps) != _days(current["steps"]) and leads_in_flight(store, seq_id):
            new_version = current.get("version", 1) + 1
    result = {}

    def apply(rows):
        out = []
        for row in rows:
            if row["id"] == seq_id:
                if new_version:
                    history = [*(row.get("history") or []), {"version": row.get("version", 1), "steps": row["steps"],
                                                             "replaced_at": _now_iso()}]
                    row = {**row, "history": history[-20:], "version": new_version}
                row = {**row, **changes, "updated_at": _now_iso(), "updated_by": by}
                result["row"] = row
            out.append(row)
        return out
    _update(store, "sequences", apply)
    return {**result["row"], "new_version": bool(new_version)}


def set_archived(store, seq_id: str, archived: bool, by: str) -> dict:
    if seq_id == DEFAULT_ID:
        raise PermissionError("The default sequence cannot be archived.")
    if get_sequence(store, seq_id) is None:
        raise LookupError("Sequence not found")
    saved = _update(store, "sequences", lambda rows: [{**r, "archived": bool(archived), "updated_at": _now_iso(), "updated_by": by}
                                                      if r["id"] == seq_id else r for r in rows])
    return next(r for r in saved if r["id"] == seq_id)


# ---- A/B ----
def variant_names(step: dict) -> list:
    return ["A", *[v["name"] for v in step.get("variants") or []]]


def assign_variant(lead_id: str, seq_id: str, step: dict) -> str:
    """The variant this lead gets for this step: the saved winner, else an even hash split (stable across retries)."""
    names = variant_names(step)
    if step.get("winner") in names:
        return step["winner"]
    if len(names) == 1:
        return "A"
    digest = hashlib.sha256(f"{lead_id}:{seq_id}:{step['day']}".encode()).hexdigest()
    return names[int(digest, 16) % len(names)]


def apply_variant(step: dict, name: str) -> dict:
    """A copy of the step with that variant's subject/body (the role/link fallbacks are dropped for what it replaces)."""
    out = {k: v for k, v in step.items() if k not in ("variants",)}
    variant = next((v for v in step.get("variants") or [] if v["name"] == name), None)
    if variant:
        if variant.get("subject"):
            out = {k: v for k, v in out.items() if not k.startswith("subject_")}
            out["subject"] = variant["subject"]
        if variant.get("body"):
            out = {k: v for k, v in out.items() if not k.startswith("body_")}
            out["body"] = variant["body"]
    out["ab_variant"] = name
    return out


def prepare_step(step: dict, lead_id: str, seq_id: str) -> dict:
    """The step the runner sends for a saved (non-default) sequence: A/B applied, unsubscribe line guaranteed."""
    chosen = apply_variant(step, assign_variant(lead_id, seq_id, step)) if step.get("variants") else {**step, "ab_variant": "A"}
    for key in [k for k in chosen if k == "body" or k.startswith("body_")]:
        if UNSUB_TOKEN not in chosen[key]:
            chosen[key] = chosen[key].rstrip() + UNSUB_LINE   # validation requires it; this is the second lock
    return chosen


def _parse(ts):
    try:
        dt = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def step_stats(store, seq_id: str) -> dict:
    """{step_day: {variant: {sent, practice, opens, replies, reply_rate}}} from the outbox and the leads' replies.

    A reply counts for the latest message of this sequence the lead got before replying."""
    rows = [m for m in store.read("outbox") if m.get("sequence_id") == seq_id and m.get("step_day") is not None]
    leads = {l["id"]: l for l in store.read("leads")}
    by_lead = {}
    for m in rows:
        if m.get("status") == "sent":
            by_lead.setdefault(m["lead_id"], []).append(m)
    replied = set()
    for lead_id, msgs in by_lead.items():
        msgs.sort(key=lambda m: _parse(m.get("sent_at")) or datetime.min.replace(tzinfo=timezone.utc))
        lead = leads.get(lead_id) or {}
        for ev in lead.get("timeline") or []:
            if ev.get("kind") != "reply" or str(ev.get("text", "")).startswith(_REPLY_SKIP):
                continue
            at = _parse(ev.get("at"))
            before = [m for m in msgs if at and (_parse(m.get("sent_at")) or at) <= at]
            if before:
                replied.add(id(before[-1]))
        if (lead.get("engagement") or {}).get("opens", 0) > 0 and msgs:
            msgs[-1]["_lead_opened"] = True
    stats = {}
    for m in rows:
        cell = stats.setdefault(m["step_day"], {}).setdefault(m.get("variant") or "A",
                                                             {"sent": 0, "practice": 0, "opens": 0, "replies": 0})
        if m.get("status") == "sent":
            cell["sent"] += 1
            cell["opens"] += 1 if (m.get("opened_at") or m.get("opens") or m.get("_lead_opened")) else 0
            cell["replies"] += 1 if id(m) in replied else 0
        elif m.get("status") == "dry_run":
            cell["practice"] += 1
    for cells in stats.values():
        for cell in cells.values():
            cell["reply_rate"] = round(100.0 * cell["replies"] / cell["sent"], 1) if cell["sent"] else 0.0
    return stats


def pick_winner(cells: dict, names: list):
    """The winning variant name by the documented rule, or None while it is too early or too close."""
    if len(names) < 2 or any(cells.get(n, {}).get("sent", 0) < MIN_SENDS_PER_VARIANT for n in names):
        return None
    rate = lambda n: cells[n]["replies"] / cells[n]["sent"]
    ranked = sorted(names, key=rate, reverse=True)
    best, second = ranked[0], ranked[1]
    if cells[best]["replies"] < MIN_WINNER_REPLIES:
        return None
    if (rate(best) - rate(second)) * 100 < MIN_LEAD_POINTS or rate(best) < WIN_RATIO * rate(second):
        return None
    return best


def evaluate_winners(store, seq_ids=None) -> list:
    """Save a winner on every A/B step that has a clear one. Returns [(seq_id, step_day, winner)] newly decided."""
    decided = []
    for seq in _read(store, "sequences"):
        if seq_ids is not None and seq["id"] not in seq_ids:
            continue
        open_steps = [s for s in seq["steps"] if s.get("variants") and not s.get("winner")]
        if not open_steps:
            continue
        stats = step_stats(store, seq["id"])
        for step in open_steps:
            winner = pick_winner(stats.get(step["day"], {}), variant_names(step))
            if winner:
                decided.append((seq["id"], step["day"], winner))
    if decided:
        marks = {(s, d): w for s, d, w in decided}

        def apply(rows):
            return [{**r, "steps": [{**st, "winner": marks[(r["id"], st["day"])], "winner_at": _now_iso()}
                                    if (r["id"], st["day"]) in marks and not st.get("winner") else st for st in r["steps"]]}
                    for r in rows]
        _update(store, "sequences", apply)
    return decided


# ---- snippets ----
def clean_snippet(body: dict) -> dict:
    channel = str(body.get("channel") or "email").lower()
    if channel not in SNIPPET_CHANNELS:
        raise ValueError("Channel must be one of: " + ", ".join(SNIPPET_CHANNELS))
    name = _text(body.get("name"), MAX_NAME, "The name")
    subject = _text(body.get("subject"), MAX_SUBJECT, "The subject", required=False) if channel == "email" else ""
    text = _text(body.get("body"), MAX_BODY, "The text")
    for part, where in ((subject, "Subject"), (text, "Text")):
        _check_placeholders(part, where)
    return {"name": name, "channel": channel, "subject": subject, "body": text}


def list_snippets(store, channel: str = "") -> list:
    rows = _read(store, "snippets")
    return sorted([r for r in rows if not channel or r.get("channel") == channel], key=lambda r: r.get("name", "").lower())


def create_snippet(store, body: dict, by: dict) -> dict:
    if len(_read(store, "snippets")) >= MAX_SNIPPETS:
        raise ValueError(f"At most {MAX_SNIPPETS} snippets.")
    row = {"id": uuid.uuid4().hex[:10], **clean_snippet(body), "created_by": by.get("name", ""),
           "created_by_email": by.get("email", ""), "created_at": _now_iso(), "updated_at": _now_iso()}
    _update(store, "snippets", lambda rows: [*rows, row])
    return row


def _may_change(row: dict, by: dict) -> bool:
    if by.get("role") == "owner":
        return True
    return bool(by.get("email")) and row.get("created_by_email") == by.get("email")


def update_snippet(store, snippet_id: str, body: dict, by: dict) -> dict:
    row = next((r for r in _read(store, "snippets") if r["id"] == snippet_id), None)
    if row is None:
        raise LookupError("Snippet not found")
    if not _may_change(row, by):
        raise PermissionError("Only the person who made this snippet, or the owner, can change it.")
    cleaned = clean_snippet({**row, **{k: v for k, v in body.items() if v is not None}})
    saved = _update(store, "snippets", lambda rows: [{**r, **cleaned, "updated_at": _now_iso()} if r["id"] == snippet_id else r for r in rows])
    return next(r for r in saved if r["id"] == snippet_id)


def delete_snippet(store, snippet_id: str, by: dict) -> None:
    row = next((r for r in _read(store, "snippets") if r["id"] == snippet_id), None)
    if row is None:
        raise LookupError("Snippet not found")
    if not _may_change(row, by):
        raise PermissionError("Only the person who made this snippet, or the owner, can delete it.")
    _update(store, "snippets", lambda rows: [r for r in rows if r["id"] != snippet_id])
