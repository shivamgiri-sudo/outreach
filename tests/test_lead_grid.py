"""Leads grid filters (app/lead_grid.py): has phone / mobile only / has email / minimum rating / exclude DND / harvest,
the same on the Leads list (on_dnd) and in the lead downloads ("Download these N")."""
import pytest

from app import lead_grid
from app.lead_model import build_lead
from tests.export_fakes import make_client, read_csv as rows_of


def L(lead_id, **f):
    return {**build_lead({"company": f.pop("company", lead_id.upper())}, "manual", allow_company_key=True), "id": lead_id, **f}


LEADS = [
    L("mob", phone="+919876543210", phone_type="mobile", phone_line_type="mobile", email="a@mob.in", rating=4.6, reviews_count=120,
      harvest_jobs=["j1"], tags=["harvest"]),
    L("mof", phone="+912071234567", phone_type="landline", phone_line_type="mobile_or_fixed", rating="4.0", harvest_jobs=["j2"]),
    L("land", phone="+912040001500", phone_type="landline", phone_line_type="fixed_line", email="b@land.in", rating=3.4),
    L("old", phone="+919123456780", phone_type="mobile", email_guess="guess@old.in", email_guess_status="unconfirmed"),
    L("guess", email_guess="x@guess.in", rating=None),
    L("dnc", phone="+919811111111", phone_type="mobile", phone_line_type="mobile", dnc=True, rating=4.8),
    L("sup", phone="+919822222222", phone_type="mobile", phone_line_type="mobile", phones=["+912012345678"], email="s@sup.in", rating=5),
    L("sup2", phone="+919833333333", phones=["+919844444444"], rating=4.5),
]
SUPPRESSED = {"+919844444444", "+919822222222"}


def picked(**params):
    grid = lead_grid.parse({k: str(v) for k, v in params.items()})
    return sorted(l["id"] for l in LEADS if lead_grid.matches(l, grid, SUPPRESSED))


def test_has_phone_and_mobile_only_edges():
    assert picked(has="phone") == sorted(["mob", "mof", "land", "old", "dnc", "sup", "sup2"])
    # mobile_or_fixed counts as mobile; a fixed line never; an older lead without a line type uses phone_type
    assert picked(has="mobile") == sorted(["mob", "mof", "old", "dnc", "sup"])
    assert "land" not in picked(has="mobile") and "sup2" not in picked(has="mobile")     # no type at all: not a known mobile


def test_has_email_ignores_guesses():
    assert picked(has="email") == sorted(["mob", "land", "sup"])
    assert picked(has="email,mobile") == sorted(["mob", "sup"])


def test_minimum_rating_leaves_out_unrated():
    assert picked(min_rating=4.5) == sorted(["mob", "dnc", "sup", "sup2"])
    assert picked(min_rating=4) == sorted(["mob", "mof", "dnc", "sup", "sup2"])           # "4.0" text rating counts
    assert "guess" not in picked(min_rating=3) and "old" not in picked(min_rating=3)
    with pytest.raises(ValueError):
        lead_grid.parse({"min_rating": "lots"})


def test_exclude_dnd_uses_flag_and_suppression_list():
    assert picked(no_dnd=1) == sorted(["mob", "mof", "land", "old", "guess"])            # dnc flag, main phone and extra phone listed


def test_harvest_any_or_one_job():
    assert picked(harvest="any") == ["mob", "mof"]
    assert picked(harvest="j2") == ["mof"]
    assert picked() == sorted(l["id"] for l in LEADS)                                     # nothing asked: everything


def test_downloads_apply_the_grid_filters(tmp_path):
    c, store = make_client(tmp_path)
    store.write("leads", LEADS)
    store.write("suppressed", ["someone@x.in", *SUPPRESSED])
    ids = lambda q: sorted(r["id"] for r in rows_of(c.get(f"/api/export/leads.csv?{q}")))
    assert ids("has=mobile&no_dnd=1") == ["mob", "mof", "old"]
    assert ids("min_rating=4.5&has=email") == ["mob", "sup"]
    assert ids("harvest=any&no_dnd=1") == ["mob", "mof"]
    assert c.get("/api/export/leads.csv?min_rating=abc").status_code == 400
    assert c.post("/api/export/leads.csv?has=email", json={"ids": ["mob", "land", "old"]}).status_code == 200
    assert sorted(r["id"] for r in rows_of(c.post("/api/export/leads.csv?has=email", json={"ids": ["mob", "land", "old"]}))) == ["land", "mob"]
    filters = next(d for d in c.get("/api/export").json() if d["dataset"] == "leads")["filters"]
    assert {"has", "min_rating", "no_dnd", "harvest"} <= set(filters)


def test_leads_list_marks_numbers_on_the_dnd_list(tmp_path):
    c, store = make_client(tmp_path)
    store.write("leads", LEADS)
    store.write("suppressed", list(SUPPRESSED))
    flags = {l["id"]: l["on_dnd"] for l in c.get("/api/leads").json()}
    assert flags == {"mob": False, "mof": False, "land": False, "old": False, "guess": False, "dnc": True, "sup": True, "sup2": True}
