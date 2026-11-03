"""Lead nurturing stage machine."""
from typing import Optional

STAGES = (
    "new", "verified", "enriched", "contacted", "engaged", "interested",
    "whatsapp_active", "call_done", "trial", "customer", "advocate",
)
EXIT_STAGES = ("not_now", "unsubscribed", "disqualified")
ALL_STAGES = STAGES + EXIT_STAGES
TERMINAL = frozenset({"unsubscribed"})
CONSENT_REQUIRED = frozenset({"interested"})


def next_stage(stage: str) -> Optional[str]:
    if stage not in STAGES or stage == STAGES[-1]:
        return None
    return STAGES[STAGES.index(stage) + 1]


def can_move(current: str, target: str, has_consent: bool = False) -> bool:
    if current not in ALL_STAGES or target not in ALL_STAGES or current in TERMINAL:
        return False
    if target in CONSENT_REQUIRED and not has_consent:
        return False
    if target in EXIT_STAGES:
        return True
    if current in EXIT_STAGES:
        return current == "not_now" and target == "new"
    return STAGES.index(target) - STAGES.index(current) == 1 or (
        STAGES.index(target) > STAGES.index(current) and target in ("engaged", "interested", "trial")
    )
