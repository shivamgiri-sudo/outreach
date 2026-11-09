"""The owner's own LinkedIn sequence: steps (connection note + follow-up messages, delays, "after" rules) and personas
(job-title pattern -> hook line), saved in settings "linkedin_sequence". Without a saved one, the built-in steps and
personas in app/linkedin_seq.py are used unchanged.

    {"steps": [{n, kind, after, days, purpose, text, needs?}], "personas": [{key, pattern, hook}], "default_hook",
     "version", "history": [{version, steps, replaced_at}], "updated_at", "updated_by"}

Versions, as in the email sequence library: a lead remembers `linkedin_seq.steps_version` when it starts. Changing the
shape of the sequence (number of steps, kind, after rule, days, needs) while leads are part-way through saves the old
steps in `history` and bumps `version`, so those leads finish on the steps they started with; wording-only edits keep
the version and apply to every lead's next message. Leads started before this existed are on version 1 (the built-in).

Per workspace (app/ws_templates.py): a workspace other than main may have its own steps / personas and notes, saved in
settings "workspace_templates"[ws]; its leads follow them, and main's (or the built-in) ones while it has none. Every
function below that takes `settings` reads the workspace's view when given ws_templates.settings_for(settings, ws).

Message text placeholders: {first_name}, {hook}, {company}, {case_study_url} (single braces, as before) and every
email placeholder in double braces ({{my_company}}, {{custom.budget}} ...).
"""
import copy
import re
from datetime import datetime, timezone

SETTINGS_KEY = "linkedin_sequence"
MAX_STEPS, MAX_TEXT, MAX_PURPOSE, MAX_DAYS = 10, 2000, 120, 60
MAX_PERSONAS, MAX_PATTERN, MAX_HOOK = 20, 300, 400
SINGLE = ("first_name", "hook", "company", "case_study_url")
NEEDS = ("case_study_url",)
LONG_NAME = "Venkataraghavan"
LONG_COMPANY = "Venkateshwara Constructions Pvt Ltd"
_SINGLE = re.compile(r"(?<!\{)\{(\w+)\}(?!\})")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,29}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_config() -> dict:
    from .linkedin_seq import DEFAULT_HOOK, PERSONAS, STEPS
    return {"steps": copy.deepcopy(STEPS), "personas": [{"key": k, "pattern": p, "hook": h} for k, p, h in PERSONAS],
            "default_hook": DEFAULT_HOOK, "version": 1, "history": [], "builtin": True}


def current(settings: dict) -> dict:
    saved = (settings or {}).get(SETTINGS_KEY)
    if not isinstance(saved, dict) or not saved.get("steps"):
        return default_config()
    base = default_config()
    return {**base, **saved, "builtin": False}


def is_default(settings: dict) -> bool:
    return current(settings).get("builtin", False)


def steps_for(state: dict, settings: dict) -> list:
    """The steps a lead on the LinkedIn sequence follows: its own version, or the current steps."""
    cfg = current(settings)
    version = (state or {}).get("steps_version") or 1
    if version == cfg.get("version", 1):
        return cfg["steps"]
    old = next((h for h in cfg.get("history") or [] if h.get("version") == version), None)
    if old:
        return old["steps"]
    if version == 1:
        return default_config()["steps"]
    return cfg["steps"]


def personas(settings: dict) -> tuple:
    cfg = current(settings)
    return [(p["key"], p["pattern"], p["hook"]) for p in cfg.get("personas") or []], cfg.get("default_hook") or ""


def persona_of(title: str, settings: dict) -> tuple:
    found, default_hook = personas(settings)
    text = (title or "").lower()
    for key, pattern, hook in found:
        try:
            if re.search(pattern, text):
                return key, hook
        except re.error:
            continue
    return "general", default_hook


def variants(settings: dict, steps: list = None) -> dict:
    """The two connection notes the A/B test shares out: saved notes, else step 1 of the sequence and the built-in B."""
    from .linkedin_seq import CONNECT_VARIANTS
    saved = (settings or {}).get("linkedin_connect_variants") or {}
    first = (steps or current(settings)["steps"])[0]["text"]
    return {"A": saved.get("A") or first, "B": saved.get("B") or CONNECT_VARIANTS["B"]}


# ---------------------------------------------------------------- filling a message
def fill(template: str, lead: dict, settings: dict, first: str, hook: str) -> str:
    from .sequences import clean_company, lead_variables, render
    from .template_vars import brand_variables
    variables = lead_variables({**lead, "id": lead.get("id") or "x"}, (settings or {}).get("sender_name") or "", "",
                               brand_variables(settings or {}))
    text, _missing = render(template, variables)
    values = {"first_name": first, "hook": hook, "case_study_url": (settings or {}).get("case_study_url", ""),
              "company": clean_company(lead.get("company")) or lead.get("domain") or "your company"}
    return _SINGLE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def stand_in_length(text: str, hook: str = "") -> int:
    """Length of a note filled with a long name and company (and long stand-ins for any {{...}}), for the 300 limit."""
    from .template_vars import VAR
    filled = VAR.sub(lambda m: "x" * (12 if m.group(1).startswith("custom.") else 24), text)
    values = {"first_name": LONG_NAME, "hook": hook, "case_study_url": "https://example.com/case-study", "company": LONG_COMPANY}
    return len(_SINGLE.sub(lambda m: values.get(m.group(1), m.group(0)), filled))


# ---------------------------------------------------------------- validation
def _text(value, limit: int, what: str, required: bool = True) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if required and not text:
        raise ValueError(f"{what} is empty.")
    if len(text) > limit:
        raise ValueError(f"{what} is longer than {limit} characters.")
    return text


def check_placeholders(text: str, where: str) -> None:
    from .sequence_library import KNOWN_PLACEHOLDERS
    from .template_vars import VAR, is_custom_placeholder
    bad_single = sorted({m.group(1) for m in _SINGLE.finditer(text)} - set(SINGLE))
    if bad_single:
        raise ValueError(f"{where}: unknown placeholder {{{bad_single[0]}}}. Use {', '.join('{' + s + '}' for s in SINGLE)}.")
    allowed = set(KNOWN_PLACEHOLDERS) - {"unsub_link"}
    bad = sorted(n for n in {m.group(1) for m in VAR.finditer(text)} if n not in allowed and not is_custom_placeholder(n))
    if bad:
        raise ValueError(f"{where}: unknown placeholder {{{{{bad[0]}}}}}.")


def clean_personas(raw, default_hook) -> tuple:
    if not isinstance(raw, list) or len(raw) > MAX_PERSONAS:
        raise ValueError(f"Give 0 to {MAX_PERSONAS} personas.")
    out, keys = [], set()
    for i, p in enumerate(raw):
        where = f"Persona {i + 1}"
        if not isinstance(p, dict):
            raise ValueError(f"{where} must be an object.")
        key = str(p.get("key") or "").strip().lower()
        if not _KEY.match(key) or key in keys or key == "general":
            raise ValueError(f"{where}: the key must be unique, start with a letter and use a-z, 0-9, _ (not 'general').")
        keys.add(key)
        pattern = _text(p.get("pattern"), MAX_PATTERN, f"{where} title pattern")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"{where}: the title pattern is not valid ({exc}).") from None
        hook = _text(p.get("hook"), MAX_HOOK, f"{where} hook")
        check_placeholders(hook, f"{where} hook")
        out.append({"key": key, "pattern": pattern, "hook": hook})
    hook = _text(default_hook, MAX_HOOK, "The hook for everyone else")
    check_placeholders(hook, "The hook for everyone else")
    return out, hook


def clean_steps(raw, longest_hook: str = "") -> list:
    from .linkedin_seq import NOTE_LIMIT
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_STEPS:
        raise ValueError(f"The LinkedIn sequence needs 1 to {MAX_STEPS} steps.")
    steps = []
    for i, s in enumerate(raw):
        n, where = i + 1, f"Step {i + 1}"
        if not isinstance(s, dict):
            raise ValueError(f"{where} must be an object.")
        kind = "connect" if i == 0 else "message"
        if str(s.get("kind") or kind) != kind:
            raise ValueError(f"{where}: the first step is the connection request and every later step is a message.")
        after = "start" if i == 0 else str(s.get("after") or ("accepted" if i == 1 else "previous"))
        if i == 1 and after != "accepted":
            raise ValueError("Step 2: the first message waits for the connection to be accepted.")
        if i > 1 and after not in ("accepted", "previous"):
            raise ValueError(f"{where}: send it after the previous message or after acceptance.")
        try:
            days = 0 if i == 0 else int(s.get("days"))
        except (TypeError, ValueError):
            raise ValueError(f"{where}: the delay must be a whole number of days.") from None
        if isinstance(s.get("days"), bool) or not 0 <= days <= MAX_DAYS:
            raise ValueError(f"{where}: the delay must be 0 to {MAX_DAYS} days.")
        text = _text(s.get("text"), MAX_TEXT, f"{where} message")
        check_placeholders(text, where)
        if kind == "connect":
            length = stand_in_length(text, longest_hook)
            if length > NOTE_LIMIT:
                raise ValueError(f"Step 1: the connection note is {length} characters with a long name; LinkedIn allows {NOTE_LIMIT}.")
        needs = s.get("needs") or None
        if needs and needs not in NEEDS:
            raise ValueError(f"{where}: unknown condition '{needs}'.")
        step = {"n": n, "kind": kind, "after": after, "days": days,
                "purpose": _text(s.get("purpose") or ("Connection request" if i == 0 else f"Message {i}"), MAX_PURPOSE, f"{where} purpose"),
                "text": text}
        if needs or "{case_study_url}" in text:
            step["needs"] = "case_study_url"        # a step that shares the case study waits until a real link is set
        steps.append(step)
    return steps


def _shape(steps: list) -> list:
    return [(s["kind"], s["after"], s["days"], s.get("needs")) for s in steps]


def leads_in_flight(store, ws=None) -> list:
    """Leads part-way through the LinkedIn sequence: all of them, or (ws given) the ones a save for `ws` affects: a
    workspace's own leads, or for main every lead whose workspace has no sequence of its own."""
    rows = [l for l in store.read("leads") if (l.get("linkedin_seq") or {}).get("status") not in (None, "stopped", "finished")]
    if ws is None:
        return rows
    from . import ws_templates as wst
    from .workspaces import ws_of
    if ws != wst.MAIN:
        return [l for l in rows if ws_of(l) == ws]
    settings = store.read("settings")
    return [l for l in rows if wst.linkedin_ws(settings, ws_of(l)) == wst.MAIN]


def save(store, body: dict, by: str, ws: str = "main") -> dict:
    """Validate and save steps / personas (either may be left out) of one workspace (main by default). Returns the
    saved config plus new_version. A workspace's first save carries on main's version numbers and history, so its
    leads already part-way through main's steps keep them."""
    from . import ws_templates as wst
    settings = store.read("settings")
    cfg = current(wst.settings_for(settings, ws))
    new_personas, new_hook = cfg["personas"], cfg["default_hook"]
    if body.get("personas") is not None or body.get("default_hook") is not None:
        new_personas, new_hook = clean_personas(body.get("personas") if body.get("personas") is not None else cfg["personas"],
                                                body.get("default_hook") if body.get("default_hook") is not None else cfg["default_hook"])
    longest = max([p["hook"] for p in new_personas] + [new_hook], key=len)
    steps = clean_steps(body["steps"], longest) if body.get("steps") is not None else cfg["steps"]
    variant_b = body.get("variant_b")
    if variant_b is not None and str(variant_b).strip():
        variant_b = _text(variant_b, 600, "Connection note B")
        check_placeholders(variant_b, "Connection note B")
        if stand_in_length(variant_b, longest) > 300:
            raise ValueError("Connection note B is over 300 characters with a long name.")
    version, history = cfg.get("version", 1), list(cfg.get("history") or [])
    new_version = _shape(steps) != _shape(cfg["steps"]) and bool(leads_in_flight(store, ws))
    if new_version:
        history = [*history, {"version": version, "steps": cfg["steps"], "replaced_at": _now()}][-20:]
        version += 1
    saved = {"steps": steps, "personas": new_personas, "default_hook": new_hook, "version": version, "history": history,
             "updated_at": _now(), "updated_by": by}

    def apply(s: dict) -> dict:
        notes = dict(wst.settings_for(s, ws).get("linkedin_connect_variants") or {})
        if body.get("steps") is not None:
            notes["A"] = " ".join(steps[0]["text"].split())       # the A/B lab's note A is step 1
        if variant_b is not None and str(variant_b).strip():
            notes["B"] = " ".join(str(variant_b).split())
        if notes != (s.get("linkedin_connect_variants") or {}):
            notes.update(changed_at=_now(), changed_by=by)
        if ws != wst.MAIN:
            return wst.put_own(s, ws, {SETTINGS_KEY: saved, "linkedin_connect_variants": notes})
        return {**s, SETTINGS_KEY: saved, "linkedin_connect_variants": notes}
    store.update("settings", apply)
    return {**current(wst.settings_for(store.read("settings"), ws)), "new_version": new_version}


def copy_from_main(store, ws: str, by: str) -> dict:
    """A workspace's own copy of main's steps, personas and notes, word for word (versions and history too)."""
    from . import ws_templates as wst
    settings = store.read("settings")
    cfg = current(settings)
    saved = {**{k: copy.deepcopy(cfg.get(k)) for k in ("steps", "personas", "default_hook", "version", "history")},
             "updated_at": _now(), "updated_by": by}
    notes = copy.deepcopy(settings.get("linkedin_connect_variants") or {})
    store.update("settings", lambda s: wst.put_own(s, ws, {SETTINGS_KEY: saved, "linkedin_connect_variants": notes}))
    return current(wst.settings_for(store.read("settings"), ws))


def reset(store, ws: str = "main") -> dict:
    """Back to the built-in steps and personas (leads part-way keep their version through history). For a workspace:
    back to main's (its own copy is dropped, or replaced by main's steps when its leads would lose their version)."""
    from . import ws_templates as wst
    if ws != wst.MAIN:
        settings = store.read("settings")
        if not any(wst.own(settings, ws).get(k) for k in (SETTINGS_KEY, "linkedin_connect_variants")):
            return current(settings)
        mine, main_cfg = current(wst.settings_for(settings, ws)), current(settings)
        if leads_in_flight(store, ws) and (_shape(mine["steps"]) != _shape(main_cfg["steps"]) or mine.get("version") != main_cfg.get("version")):
            return save(store, {k: main_cfg[k] for k in ("steps", "personas", "default_hook")}, "reset", ws)
        store.update("settings", lambda s: wst.put_own(s, ws, {SETTINGS_KEY: None, "linkedin_connect_variants": None}))
        return current(store.read("settings"))
    settings = store.read("settings")
    cfg = current(settings)
    if cfg.get("builtin"):
        return cfg
    default = default_config()
    if (_shape(default["steps"]) != _shape(cfg["steps"]) or cfg.get("version", 1) > 1) and leads_in_flight(store):
        return save(store, {"steps": default["steps"], "personas": default["personas"], "default_hook": default["default_hook"]}, "reset")
    store.update("settings", lambda s: {**{k: v for k, v in s.items() if k not in (SETTINGS_KEY, "linkedin_connect_variants")}})
    return default_config()
