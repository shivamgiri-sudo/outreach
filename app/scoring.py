"""Five buying signals -> 0-100 score.

Weights, the warm/hot thresholds and the two size knobs are editable by the owner (settings key "scoring",
GET/PUT /api/scoring); DEFAULT_CONFIG is used for anything not saved. The app registers its store with
`use_store`, so every caller of `score_lead` (lead list, campaigns, flows, inbox, MCP) scores the same way.
"""
import math
import time
from typing import Optional

from .places import clean_country, is_indian_place

SIGNALS = ("hiring_volume", "hiring_pain", "fit", "engagement", "intent")
DEFAULT_WEIGHTS = {
    "hiring_volume": 30,
    "hiring_pain": 25,
    "fit": 15,
    "engagement": 20,
    "intent": 10,
}
HOT_THRESHOLD = 70
WARM_THRESHOLD = 40
# Open roles count on a log scale: 1 role is a weak signal, 10 a fair one (61%), 25 a strong one (83%), 50+ full marks.
OPEN_ROLES_FULL = 50
JOB_AGE_FULL_DAYS = 30
REPOST_FULL = 2
# Companies from this size up are a size fit. There is no upper limit: a 5,000-staff BPO hires the most.
FIT_SIZE_MIN = 20
ENGAGEMENT_FULL_POINTS = 6
DEFAULT_CONFIG = {"weights": DEFAULT_WEIGHTS, "hot": HOT_THRESHOLD, "warm": WARM_THRESHOLD,
                  "open_roles_full": OPEN_ROLES_FULL, "fit_size_min": FIT_SIZE_MIN}
SETTINGS_KEY = "scoring"
CACHE_SECONDS = 5

_state = {"store": None, "config": None, "at": 0.0}


class ConfigError(ValueError):
    """A scoring setting is out of range; the message says which in plain words."""


def _int(raw, name: str, low: int, high: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a whole number.") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}.")
    return value


def clean_config(raw: Optional[dict]) -> dict:
    """Merge a (partial) saved config over the defaults and check every value. Raises ConfigError."""
    raw = raw if isinstance(raw, dict) else {}
    weights = {**DEFAULT_WEIGHTS, **{k: v for k, v in (raw.get("weights") or {}).items() if k in SIGNALS}}
    weights = {k: _int(v, f"Weight for {k.replace('_', ' ')}", 0, 100) for k, v in weights.items()}
    if not sum(weights.values()):
        raise ConfigError("At least one weight must be above 0.")
    hot = _int(raw.get("hot", HOT_THRESHOLD), "Hot from", 1, 100)
    warm = _int(raw.get("warm", WARM_THRESHOLD), "Warm from", 0, 100)
    if warm >= hot:
        raise ConfigError("Warm must start below hot.")
    return {"weights": weights, "hot": hot, "warm": warm,
            "open_roles_full": _int(raw.get("open_roles_full", OPEN_ROLES_FULL), "Open roles for full marks", 2, 100000),
            "fit_size_min": _int(raw.get("fit_size_min", FIT_SIZE_MIN), "Smallest company size that fits", 0, 1000000)}


def use_store(store) -> None:
    """Called once by the app: read the owner's scoring settings from this store."""
    _state.update(store=store, config=None, at=0.0)


def reset_cache() -> None:
    _state.update(config=None, at=0.0)


def active_config() -> dict:
    store = _state["store"]
    if store is None:
        return DEFAULT_CONFIG
    if _state["config"] is None or time.monotonic() - _state["at"] > CACHE_SECONDS:
        try:
            config = clean_config(store.read("settings").get(SETTINGS_KEY))
        except ConfigError:
            config = DEFAULT_CONFIG               # a bad saved value never breaks scoring
        _state.update(config=config, at=time.monotonic())
    return _state["config"]


def _ratio(value, full) -> float:
    return max(0.0, min(1.0, (value or 0) / full))


def _volume(open_roles, full: int) -> float:
    roles = max(0, open_roles or 0)
    return min(1.0, math.log1p(roles) / math.log1p(full))


def _hiring_pain(lead: dict) -> float:
    age = _ratio(lead.get("max_job_age_days"), JOB_AGE_FULL_DAYS)
    repost = _ratio(lead.get("repost_count"), REPOST_FULL)
    return max(age, repost)


def _in_india(lead: dict) -> bool:
    """'India', 'IND' and 'in' count as IN; with no country, an Indian city or a +91 number does."""
    country = clean_country(lead.get("country"))
    if country:
        return country == "IN"
    phones = [lead.get("phone"), *(lead.get("phones") or [])]
    return is_indian_place(lead.get("city") or "", lead.get("state") or "") or any(
        str(p).replace(" ", "").startswith("+91") for p in phones if p)


def _fit(lead: dict, size_min: int = FIT_SIZE_MIN) -> float:
    size = lead.get("company_size") or 0
    size_ok = 0.6 if size and size >= size_min else 0.0
    india = 0.4 if _in_india(lead) else 0.0
    return size_ok + india


def _engagement(lead: dict) -> float:
    e = lead.get("engagement") or {}
    points = (e.get("opens") or 0) * 1 + (e.get("clicks") or 0) * 2 + (e.get("replies") or 0) * 3
    return _ratio(points, ENGAGEMENT_FULL_POINTS)


def _intent(lead: dict) -> float:
    return 1.0 if lead.get("consent_yes") else 0.0


def score_lead(lead: dict, weights: Optional[dict] = None, config: Optional[dict] = None) -> dict:
    cfg = config or active_config()
    weights = weights or cfg["weights"]
    parts = {
        "hiring_volume": _volume(lead.get("open_roles"), cfg["open_roles_full"]),
        "hiring_pain": _hiring_pain(lead),
        "fit": _fit(lead, cfg["fit_size_min"]),
        "engagement": _engagement(lead),
        "intent": _intent(lead),
    }
    signals = {name: round(parts[name] * weights[name], 1) for name in parts}
    score = min(100, round(sum(signals.values())))
    return {"score": score, "signals": signals, "band": band(score, cfg), "reasons": reasons(lead, parts, weights)}


def band(score: float, config: Optional[dict] = None) -> str:
    cfg = config or active_config()
    if score >= cfg["hot"]:
        return "hot"
    return "warm" if score >= cfg["warm"] else "cold"


def reasons(lead: dict, parts: dict, weights: Optional[dict] = None) -> list:
    """Plain-words 'why this lead', strongest signal first; only signals that actually fired."""
    w = weights or DEFAULT_WEIGHTS
    out = []
    if parts["hiring_volume"]:
        out.append((parts["hiring_volume"] * w["hiring_volume"], f"{lead.get('open_roles')} open role(s) right now"))
    if parts["hiring_pain"]:
        age, repost = lead.get("max_job_age_days") or 0, lead.get("repost_count") or 0
        text = f"a role open {age} days" + (f", reposted {repost}x" if repost else "") if age else f"a role reposted {repost}x"
        out.append((parts["hiring_pain"] * w["hiring_pain"], text + " (hard to fill)"))
    if parts["fit"]:
        out.append((parts["fit"] * w["fit"], "right size and in India" if parts["fit"] >= 1 else "partly in our target size/location"))
    if parts["engagement"]:
        out.append((parts["engagement"] * w["engagement"], "has opened, clicked or replied"))
    if parts["intent"]:
        out.append((parts["intent"] * w["intent"], "said yes to being contacted"))
    return [text for _w, text in sorted(out, key=lambda item: -item[0])]
