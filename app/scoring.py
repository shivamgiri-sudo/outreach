"""Five buying signals -> 0-100 score. Weights are editable via DEFAULT_WEIGHTS."""
from .places import clean_country, is_indian_place

DEFAULT_WEIGHTS = {
    "hiring_volume": 30,
    "hiring_pain": 25,
    "fit": 15,
    "engagement": 20,
    "intent": 10,
}
HOT_THRESHOLD = 70
WARM_THRESHOLD = 40
OPEN_ROLES_FULL = 10
JOB_AGE_FULL_DAYS = 30
REPOST_FULL = 2
FIT_SIZE_MIN, FIT_SIZE_MAX = 20, 2000
ENGAGEMENT_FULL_POINTS = 6


def _ratio(value, full) -> float:
    return max(0.0, min(1.0, (value or 0) / full))


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


def _fit(lead: dict) -> float:
    size = lead.get("company_size") or 0
    size_ok = 0.6 if FIT_SIZE_MIN <= size <= FIT_SIZE_MAX else 0.0
    india = 0.4 if _in_india(lead) else 0.0
    return size_ok + india


def _engagement(lead: dict) -> float:
    e = lead.get("engagement") or {}
    points = (e.get("opens") or 0) * 1 + (e.get("clicks") or 0) * 2 + (e.get("replies") or 0) * 3
    return _ratio(points, ENGAGEMENT_FULL_POINTS)


def _intent(lead: dict) -> float:
    return 1.0 if lead.get("consent_yes") else 0.0


def score_lead(lead: dict, weights: dict = DEFAULT_WEIGHTS) -> dict:
    parts = {
        "hiring_volume": _ratio(lead.get("open_roles"), OPEN_ROLES_FULL),
        "hiring_pain": _hiring_pain(lead),
        "fit": _fit(lead),
        "engagement": _engagement(lead),
        "intent": _intent(lead),
    }
    signals = {name: round(parts[name] * weights[name], 1) for name in parts}
    score = min(100, round(sum(signals.values())))
    return {"score": score, "signals": signals, "band": band(score)}


def band(score: float) -> str:
    if score >= HOT_THRESHOLD:
        return "hot"
    return "warm" if score >= WARM_THRESHOLD else "cold"
