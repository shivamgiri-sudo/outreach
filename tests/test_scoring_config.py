"""Scoring fix: no size ceiling, log-scaled open roles, owner-editable weights and thresholds (/api/scoring)."""
import pytest

from app import scoring
from app.scoring import DEFAULT_CONFIG, ConfigError, clean_config, score_lead
from tests.flow_helpers import lead, make_client


@pytest.fixture(autouse=True)
def _defaults():
    scoring.use_store(None)          # module-level tests score with the defaults
    yield
    scoring.use_store(None)


def test_big_employers_are_a_size_fit():
    big = score_lead({"company_size": 5000, "country": "IN"})
    assert big["signals"]["fit"] == 15 and score_lead({"company_size": 250000, "country": "IN"})["signals"]["fit"] == 15
    assert score_lead({"company_size": 8, "country": "IN"})["signals"]["fit"] == 6      # tiny: India only


def test_open_roles_are_log_scaled_so_80_beats_10():
    v = lambda roles: score_lead({"open_roles": roles})["signals"]["hiring_volume"]
    assert v(0) == 0 < v(1) < v(5) < v(10) < v(25) < v(80)
    assert v(10) == 18.3 and v(50) == 30 and v(500) == 30
    assert score_lead({"open_roles": 80, "max_job_age_days": 30, "company_size": 5000, "country": "IN"})["band"] == "hot"


def test_clean_config_merges_and_validates():
    assert clean_config({}) == DEFAULT_CONFIG
    cfg = clean_config({"weights": {"intent": 40, "made_up": 9}, "hot": 80})
    assert cfg["weights"]["intent"] == 40 and "made_up" not in cfg["weights"] and cfg["hot"] == 80
    for bad in ({"hot": 30, "warm": 40}, {"weights": {"fit": 101}}, {"weights": {k: 0 for k in DEFAULT_CONFIG["weights"]}},
                {"open_roles_full": 1}, {"hot": "lots"}):
        with pytest.raises(ConfigError):
            clean_config(bad)


def test_owner_changes_weights_and_thresholds_and_leads_rescore(tmp_path):
    c = make_client(tmp_path)
    before = lead(c, "Acme")
    got = c.get("/api/scoring").json()
    assert got["weights"] == DEFAULT_CONFIG["weights"] and got["defaults"]["hot"] == 70
    r = c.put("/api/scoring", json={"weights": {"fit": 50}, "warm": 10, "hot": 20})
    assert r.status_code == 200 and r.json()["weights"]["fit"] == 50 and r.json()["weights"]["intent"] == 10
    after = lead(c, "Acme")
    assert after["signals"]["fit"] == 50 and after["score"] > before["score"] and after["band"] == "hot"
    assert c.put("/api/scoring", json={"warm": 90}).status_code == 400            # warm must stay below hot (20)
    assert c.put("/api/scoring", json={"weights": {"hiring_pain": 5}}).json()["weights"]["fit"] == 50   # partial keeps the rest
    saved = c.app.state.store.read("settings")["scoring"]
    assert saved["updated_by"] == "This computer" and saved["weights"]["hiring_pain"] == 5


def test_a_broken_saved_config_falls_back_to_defaults(tmp_path):
    c = make_client(tmp_path)
    c.app.state.store.update("settings", lambda s: {**s, "scoring": {"hot": -5}})
    scoring.reset_cache()
    assert c.get("/api/scoring").json()["hot"] == 70 and lead(c, "Acme")["score"] >= 0
