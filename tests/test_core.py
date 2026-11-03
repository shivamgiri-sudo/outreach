import pytest

from app.hygiene import normalise_email, normalise_phone, company_domain, dedupe_key, is_role_address
from app.scoring import score_lead, band
from app.stages import can_move, next_stage, STAGES


def test_normalise_email_lowercases_and_rejects_bad_syntax():
    assert normalise_email(" Priya@Acme.CO.in ") == "priya@acme.co.in"
    assert normalise_email("not-an-email") is None
    assert normalise_email("") is None


def test_normalise_phone_to_e164_india():
    assert normalise_phone("098765 43210") == "+919876543210"
    assert normalise_phone("+91 98765-43210") == "+919876543210"
    assert normalise_phone("12345") is None


def test_company_domain_from_url_or_email():
    assert company_domain("https://www.acme.com/careers") == "acme.com"
    assert company_domain("hr@acme.com") == "acme.com"
    assert company_domain("") is None


def test_role_address_flag():
    assert is_role_address("info@acme.com") is True
    assert is_role_address("priya@acme.com") is False


def test_dedupe_key_prefers_email_then_phone_then_domain():
    assert dedupe_key({"email": "A@x.com"}) == "email:a@x.com"
    assert dedupe_key({"phone": "9876543210"}) == "phone:+919876543210"
    assert dedupe_key({"website": "https://www.x.com"}) == "domain:x.com"
    assert dedupe_key({}) is None


def test_score_hot_lead_has_all_signals():
    lead = {
        "open_roles": 12, "max_job_age_days": 45, "repost_count": 3,
        "company_size": 200, "country": "IN",
        "engagement": {"opens": 3, "clicks": 1, "replies": 1},
        "consent_yes": True,
    }
    result = score_lead(lead)
    assert result["score"] >= 70
    assert band(result["score"]) == "hot"
    assert set(result["signals"]) == {"hiring_volume", "hiring_pain", "fit", "engagement", "intent"}


def test_score_empty_lead_is_cold_and_bounded():
    result = score_lead({})
    assert result["score"] == 0
    assert band(result["score"]) == "cold"


def test_score_never_exceeds_100():
    lead = {"open_roles": 999, "max_job_age_days": 999, "repost_count": 99, "company_size": 100,
            "country": "IN", "engagement": {"opens": 99, "clicks": 99, "replies": 99}, "consent_yes": True}
    assert score_lead(lead)["score"] <= 100


def test_stage_flow_forward_and_exits():
    assert next_stage("new") == "verified"
    assert can_move("contacted", "engaged")
    assert can_move("engaged", "unsubscribed")
    assert not can_move("unsubscribed", "contacted")
    assert not can_move("new", "customer")
    assert "advocate" in STAGES


def test_interested_requires_consent_record():
    assert not can_move("engaged", "interested", has_consent=False)
    assert can_move("engaged", "interested", has_consent=True)
