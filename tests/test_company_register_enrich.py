"""Find & enrich's free company-register step (app/find_enrich_free.register_step): high matches only, empty fields only,
switchable in the spend settings. Fake transports only (no network)."""
from fastapi.testclient import TestClient

from app import company_register as R, company_register_csv as C
from app import prospect_waterfall as W
from app.main import create_app
from tests.campaign_fakes import FakeIO
from tests.test_find_leads import add_leads, go

LEADS = ("company,website,contact_name,state\n"
         "Acme Technologies Pvt Ltd,https://acme-tech.in,Kiran Desai,\n"
         "Zeta Foods,https://zeta-foods.in,Asha Rao,\n"
         "Beta Works,https://beta-works.in,Om Puri,Goa\n")


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "store.json", campaign_transports=FakeIO(pages={}).transports()))
    repo = R.repo_for(c.app.state.store)
    repo.upsert([C.map_record(r) for r in (
        {"CIN": "U72200MH2010PTC123456", "COMPANY_NAME": "ACME TECHNOLOGIES PRIVATE LIMITED", "COMPANY_STATUS": "Active",
         "REGISTERED_STATE": "Maharashtra", "REGISTERED_OFFICE_ADDRESS": "12 MG Road, Mumbai"},
        {"CIN": "U15400KA2015PTC111111", "COMPANY_NAME": "ZETA FOODS PRIVATE LIMITED", "REGISTERED_STATE": "Karnataka"},
        {"CIN": "U15400DL2016PTC222222", "COMPANY_NAME": "ZETA FOODS LIMITED", "REGISTERED_STATE": "Delhi"},
        {"CIN": "U15400MH2016PTC333333", "COMPANY_NAME": "BETA WORKS LIMITED", "REGISTERED_STATE": "Maharashtra"})])
    return c


def lead(c, company):
    return next(l for l in c.app.state.store.read("leads") if l["company"] == company)


def test_high_match_attaches_medium_is_left_and_filled_fields_carry_provenance(tmp_path):
    c = make(tmp_path)
    add_leads(c, LEADS)
    go(c, mode="enrich_existing", titles=["HR Head"])
    acme = lead(c, "Acme Technologies Pvt Ltd")
    assert acme["registry"]["cin"] == "U72200MH2010PTC123456" and acme["registry"]["matched_by"] == "Find & enrich"
    assert acme["company_legal"] == "ACME TECHNOLOGIES PRIVATE LIMITED" and acme["provenance"]["company_legal"] == "mca-master-data"
    assert acme["address"] == "12 MG Road, Mumbai" and acme["state"] == "Maharashtra"
    assert "registry" not in lead(c, "Zeta Foods")                     # two identical names: left for a person
    beta = lead(c, "Beta Works")
    assert "registry" not in beta and beta["state"] == "Goa"           # registered elsewhere: state disagrees, not attached
    R.forget(c.app.state.store)


def test_step_shows_in_spend_settings_and_can_be_switched_off(tmp_path):
    c = make(tmp_path)
    ids = {p["id"]: p for p in W.provider_config(c.app.state.store)}
    assert ids["company_register"]["enabled"] and ids["company_register"]["cost"] == 0
    c.app.state.store.update("settings", lambda s: {**s, "prospect_db": {"providers": {"company_register": {"enabled": False}}}})
    add_leads(c, LEADS)
    go(c, mode="enrich_existing", titles=["HR Head"])
    assert "registry" not in lead(c, "Acme Technologies Pvt Ltd")
    R.forget(c.app.state.store)


def test_no_register_imported_is_quiet(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "store.json", campaign_transports=FakeIO(pages={}).transports()))
    add_leads(c, LEADS)
    run = go(c, mode="enrich_existing", titles=["HR Head"])
    assert "registry" not in lead(c, "Acme Technologies Pvt Ltd")
    assert "Company register" not in str(run.get("errors") or "")
    R.forget(c.app.state.store)
