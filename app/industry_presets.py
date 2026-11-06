"""Industry presets: a ready set of words and settings for one kind of business, applied in one click.

A preset holds an email sequence, LinkedIn steps and personas, a call script with objection answers, email and
WhatsApp snippets, ideal-customer and scoring hints, the word for the lead's number field ("Open roles", "Properties"),
suggested stage labels and two starter flows. The content lives in app/preset_data/; the brand words come from the
{{my_*}} placeholders, so no preset makes a claim the owner hasn't written.

apply(store, preset_id, by, overwrite=False):
  * the email sequence is always created as a NEW saved sequence (never the built-in default), once per preset;
  * starter flows are created as drafts, once per preset;
  * LinkedIn steps/personas, the call script, snippets, scoring hints, ICP text, lead term and stage labels are only
    replaced while they are still at their defaults (never saved) or when overwrite=true;
  * applying the same preset again changes nothing (idempotent). The result lists what changed and what was kept.
"""
import copy
import uuid
from datetime import datetime, timezone

from .preset_data import BUILDERS
from .preset_data.common import flows as default_flows

DEFAULT_ID = "recruitment"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build(builder) -> dict:
    preset = builder()
    if not preset.get("flows"):
        steps = preset["sequence"]["steps"]
        first, second = steps[0], steps[1]
        preset["flows"] = default_flows(preset["name"], first, second,
                                        f"They said yes. Call them, listen to what they need and agree the next step ({preset['name']}).")
    return preset


def all_presets() -> list:
    return [_build(b) for b in BUILDERS]


def get(preset_id: str):
    return next((copy.deepcopy(p) for p in all_presets() if p["id"] == preset_id), None)


def summaries() -> list:
    return [{"id": p["id"], "name": p["name"], "description": p["description"], "default": bool(p.get("default")),
             "lead_term": p["lead_term"], "email_steps": len(p["sequence"]["steps"]),
             "linkedin_steps": len(p["linkedin"]["steps"]), "personas": len(p["linkedin"]["personas"]),
             "snippets": len(p["snippets"]), "flows": [f["name"] for f in p["flows"]],
             "target_titles": p["icp"].get("target_titles", [])} for p in all_presets()]


def validate(preset: dict) -> list:
    """Every problem with a preset's content, in plain words (empty = valid). Used by the tests and before applying."""
    from . import flows as flow_mod, linkedin_config, rep_workflow, sequence_library
    problems = []

    def check(what, fn):
        try:
            fn()
        except ValueError as exc:
            problems.append(f"{preset.get('id')}: {what}: {exc}")
    check("email sequence", lambda: sequence_library.clean_steps(preset["sequence"]["steps"]))
    li = preset["linkedin"]
    holder = {}
    check("personas", lambda: holder.update(p=linkedin_config.clean_personas(li["personas"], li["default_hook"])))
    longest = max([p["hook"] for p in li["personas"]] + [li["default_hook"]], key=len)
    check("LinkedIn steps", lambda: linkedin_config.clean_steps(li["steps"], longest))
    if linkedin_config.stand_in_length(li.get("variant_b") or "", longest) > 300:
        problems.append(f"{preset.get('id')}: connection note B is over 300 characters with a long name")
    check("connection note B", lambda: linkedin_config.check_placeholders(li.get("variant_b") or "", "Note B"))
    check("call script", lambda: rep_workflow.clean_script(preset["call_script"]["script"], preset["call_script"]["objections"]))
    for s in preset["snippets"]:
        check(f"snippet {s.get('name')}", lambda s=s: sequence_library.clean_snippet(s))
    for f in preset["flows"]:
        nodes, edges = flow_mod.normalise_graph(f["nodes"], f["edges"])
        problems += [f"{preset.get('id')}: flow {f['name']}: {p['message']}" for p in flow_mod.validate_graph(nodes, edges)]
    if not preset.get("lead_term"):
        problems.append(f"{preset.get('id')}: no lead term")
    return problems


# ---------------------------------------------------------------- apply
def _sequence(store, preset: dict, by: str, out: dict) -> None:
    from . import sequence_library as lib
    existing = next((s for s in lib._read(store, "sequences") if s.get("preset") == preset["id"] and not s.get("archived")), None)
    if existing:
        out["unchanged"].append("sequence")
        out["sequence_id"] = existing["id"]
        return
    row = lib.create_sequence(store, {"name": preset["sequence"]["name"], "notes": f"From the {preset['name']} industry preset.",
                                      "steps": copy.deepcopy(preset["sequence"]["steps"])}, by)
    lib._update(store, "sequences", lambda rows: [{**r, "preset": preset["id"]} if r["id"] == row["id"] else r for r in rows])
    out["changed"].append("sequence")
    out["sequence_id"] = row["id"]


def _linkedin(store, preset: dict, by: str, overwrite: bool, out: dict) -> None:
    from . import linkedin_config
    settings = store.read("settings")
    li = preset["linkedin"]
    cfg = linkedin_config.current(settings)
    notes = linkedin_config.variants(settings)
    same = ([(s["kind"], s["after"], s["days"], s["text"]) for s in cfg["steps"]] ==
            [(s["kind"], s["after"], s["days"], s["text"]) for s in li["steps"]]
            and cfg["personas"] == li["personas"] and cfg["default_hook"] == li["default_hook"] and notes["B"] == li["variant_b"])
    if same:
        out["unchanged"].append("linkedin")
        return
    saved_notes = settings.get("linkedin_connect_variants") or {}
    if not overwrite and not (cfg.get("builtin") and not saved_notes.get("A") and not saved_notes.get("B")):
        out["kept"].append({"what": "linkedin", "why": "You have edited the LinkedIn steps or notes; apply with overwrite to replace them."})
        return
    linkedin_config.save(store, {"steps": li["steps"], "personas": li["personas"], "default_hook": li["default_hook"],
                                 "variant_b": li["variant_b"]}, by)
    out["changed"].append("linkedin")


def _setting(store, key: str, value, overwrite: bool, out: dict, label: str, is_default) -> None:
    current = store.read("settings").get(key)
    if current == value:
        out["unchanged"].append(label)
        return
    if not overwrite and not is_default(current):
        out["kept"].append({"what": label, "why": f"You have your own {label.replace('_', ' ')}; apply with overwrite to replace it."})
        return
    store.update("settings", lambda s: {**s, key: value})
    out["changed"].append(label)


def _snippets(store, preset: dict, by: str, overwrite: bool, out: dict) -> None:
    from . import sequence_library as lib
    rows = lib._read(store, "snippets")
    mine = {(r.get("name"), r.get("channel")) for r in rows}
    wanted = [s for s in preset["snippets"] if (s["name"], s["channel"]) not in mine]
    others = [r for r in rows if r.get("preset") and r.get("preset") != preset["id"]]
    if not wanted and not (overwrite and others):
        out["unchanged"].append("snippets")
        return
    if rows and not overwrite and any(not r.get("preset") for r in rows):
        out["kept"].append({"what": "snippets", "why": "You have your own snippets; apply with overwrite to add the preset's."})
        return
    stamp = _now()
    new = [{"id": uuid.uuid4().hex[:10], **lib.clean_snippet(s), "preset": preset["id"], "created_by": by,
            "created_by_email": "", "created_at": stamp, "updated_at": stamp} for s in wanted]
    drop = {r["id"] for r in others} if overwrite else set()      # only snippets an earlier preset made; never yours
    lib._update(store, "snippets", lambda current: [*[r for r in current if r["id"] not in drop], *new])
    out["changed"].append("snippets")


def _flows(store, preset: dict, out: dict) -> None:
    from . import flows as flow_mod
    flow_mod.ensure_collections(store)
    have = {f.get("preset_flow") for f in store.read("flows")}
    made = []
    for spec in preset["flows"]:
        key = f"{preset['id']}:{spec['key']}"
        if key in have:
            continue
        flow = {**flow_mod.build_flow(spec["name"], spec["description"], copy.deepcopy(spec["nodes"]), copy.deepcopy(spec["edges"]),
                                      spec["settings"]), "preset_flow": key}
        made.append(flow)
    if made:
        store.update("flows", lambda rows: [*rows, *made])
        out["changed"].append("flows")
        out["flow_ids"] = [f["id"] for f in made]
    else:
        out["unchanged"].append("flows")


def _icp(store, preset: dict, overwrite: bool, out: dict) -> None:
    from .icp import get_icp, set_icp
    text = preset["icp"]["text"]
    current = get_icp(store)["text"]
    if current == text:
        out["unchanged"].append("icp")
    elif current and not overwrite:
        out["kept"].append({"what": "icp", "why": "You have your own ideal-customer description; apply with overwrite to replace it."})
    else:
        set_icp(store, {"text": text})
        out["changed"].append("icp")


def apply(store, preset_id: str, by: str, overwrite: bool = False) -> dict:
    """Apply one preset. Raises LookupError for an unknown id, ValueError when its content does not validate."""
    preset = get(preset_id)
    if preset is None:
        raise LookupError("Unknown preset")
    problems = validate(preset)
    if problems:
        raise ValueError(problems[0])
    out = {"preset": preset_id, "name": preset["name"], "overwrite": bool(overwrite), "changed": [], "unchanged": [], "kept": [],
           "sequence_id": None, "flow_ids": []}
    _sequence(store, preset, by, out)
    _linkedin(store, preset, by, overwrite, out)
    script = {**preset["call_script"]}
    current_script = store.read("settings").get("call_script") or {}
    if {"script": current_script.get("script"), "objections": current_script.get("objections")} == script:
        out["unchanged"].append("call_script")
    elif current_script and not overwrite:
        out["kept"].append({"what": "call_script", "why": "You have edited the call script; apply with overwrite to replace it."})
    else:
        store.update("settings", lambda s: {**s, "call_script": {**script, "updated_by": by, "updated_at": _now()}})
        out["changed"].append("call_script")
    _snippets(store, preset, by, overwrite, out)
    from .scoring import SETTINGS_KEY as SCORING_KEY, reset_cache
    scoring_now = store.read("settings").get(SCORING_KEY) or {}
    _setting(store, SCORING_KEY, {**scoring_now, **preset["scoring"]}, overwrite, out, "scoring", lambda c: not c)
    reset_cache()
    _setting(store, "icp_hints", {k: v for k, v in preset["icp"].items() if k != "text"}, overwrite, out, "icp_hints", lambda c: not c)
    _icp(store, preset, overwrite, out)
    terms = {**(store.read("settings").get("lead_terms") or {}), "open_roles": preset["lead_term"]}
    _setting(store, "lead_terms", terms, overwrite, out, "lead_term",
             lambda c: not c or (c or {}).get("open_roles") in (None, "", "Open roles"))
    if preset["stage_labels"] or overwrite:
        _setting(store, "stage_labels", dict(preset["stage_labels"]), overwrite, out, "stage_labels", lambda c: not c)
    _flows(store, preset, out)
    store.update("settings", lambda s: {**s, "industry_preset": {"id": preset_id, "at": _now(), "by": by}})
    return out
