

def test_score_explains_itself_in_plain_words():
    from app.scoring import score_lead
    out = score_lead({"open_roles": 12, "max_job_age_days": 40, "repost_count": 2, "company_size": 150, "country": "IN"})
    assert out["reasons"][0] == "12 open role(s) right now" and any("hard to fill" in r for r in out["reasons"])
    assert score_lead({})["reasons"] == []
