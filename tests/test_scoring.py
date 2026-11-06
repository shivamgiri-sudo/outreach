

def test_score_explains_itself_in_plain_words():
    from app.scoring import score_lead
    # 60 roles = full hiring-volume marks (30) on the log scale, so it outranks the hard-to-fill signal (25).
    out = score_lead({"open_roles": 60, "max_job_age_days": 40, "repost_count": 2, "company_size": 150, "country": "IN"})
    assert out["reasons"][0] == "60 open role(s) right now" and any("hard to fill" in r for r in out["reasons"])
    assert score_lead({})["reasons"] == []
