"""Set stage steps are checked when a flow is saved (not only when it runs or is activated).

A stage can be written as its id ("not_now") or as the word the owner shows for it (Setup > Brand: "Parked"), in any
case; it is stored as the id. A step with no stage, or a word that is not one of the configured stages, is refused.
"""
from .brand import DEFAULT_STAGE_LABELS
from .stages import ALL_STAGES


def stage_words(custom_labels=None) -> dict:
    """{lowercase id or label: stage id} for every stage, with the owner's renamed labels."""
    words = {}
    for stage in ALL_STAGES:
        words[stage.lower()] = stage
        words[DEFAULT_STAGE_LABELS.get(stage, stage).strip().lower()] = stage
    for stage, label in (custom_labels or {}).items():
        if stage in ALL_STAGES and isinstance(label, str) and label.strip():
            words[label.strip().lower()] = stage
    return words


def shown_stages(custom_labels=None) -> str:
    labels = {**DEFAULT_STAGE_LABELS, **{k: v for k, v in (custom_labels or {}).items() if isinstance(v, str) and v.strip()}}
    return ", ".join(labels.get(s, s) for s in ALL_STAGES)


def resolve_set_stage(nodes: list, custom_labels=None) -> tuple:
    """(nodes with each Set stage step's stage turned into its id, first problem message or None)."""
    words, out = stage_words(custom_labels), []
    for node in nodes:
        if node.get("type") != "set_stage":
            out.append(node)
            continue
        config = node.get("config") or {}
        raw = str(config.get("stage") or "").strip()
        title = str(config.get("title") or "").strip() or "Set stage"
        if not raw:
            return nodes, f"The step '{title}' has no stage. Pick the stage to move the lead to."
        stage = words.get(raw.lower())
        if stage is None:
            return nodes, (f"The step '{title}' moves leads to '{raw[:40]}', which is not one of your stages. "
                           f"Pick one of: {shown_stages(custom_labels)}.")
        out.append({**node, "config": {**config, "stage": stage}})
    return out, None
