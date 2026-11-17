"""Company name normalisation and register matching (app/company_match.py). Pure functions, no database."""
import pytest

from app import company_match as M


@pytest.mark.parametrize("raw, want", [
    ("Acme Technologies Pvt Ltd", "acme technologies"),
    ("ACME TECHNOLOGIES PRIVATE LIMITED", "acme technologies"),
    ("Acme Technologies Pvt. Ltd.", "acme technologies"),
    ("M/s. Acme Technologies (OPC) Private Limited", "acme technologies"),
    ("Acme Technologies OPC Pvt Ltd", "acme technologies"),
    ("Zeta Foods India Private Limited", "zeta foods"),
    ("Zeta Foods (India) Pvt Ltd", "zeta foods"),
    ("Brightpath Advisors LLP", "brightpath advisors"),
    ("Mahindra & Mahindra Limited", "mahindra and mahindra"),
    ("Tata Sons & Co.", "tata sons"),
    ("The Indian Hotels Company Limited", "indian hotels"),
    ("Globex Inc", "globex"), ("Globex LLC", "globex"), ("Globex Corporation", "globex"), ("Globex Corp.", "globex"),
    ("  Infosys   Ltd  ", "infosys"),
    ("India", "india"),                         # never stripped to nothing
    ("Limited", "limited"),
    ("", ""),
])
def test_normalise(raw, want):
    assert M.normalise(raw) == want


def test_tok_key_prefers_a_telling_word_over_generic_ones():
    assert M.tok_key("acme technologies") == "acme"
    assert M.tok_key("global services solutions") == "solutions"      # all generic: longest
    assert M.distinctive("sunrise infotech services") == ["sunrise", "infotech", "services"]


def test_states_codes_aliases_and_cin():
    assert M.state_key("MH") == M.state_key("Maharashtra") == "maharashtra"
    assert M.state_key("Orissa") == "odisha" and M.state_key("NCT of Delhi") == "delhi"
    assert M.state_key("Atlantis") == ""
    assert M.state_from_cin("U72200MH2010PTC123456") == "maharashtra"
    assert M.state_from_cin("AAB-1234") == ""


def row(cin, name, state="Maharashtra", status="Active", address="", roc="RoC-Mumbai"):
    return {"cin": cin, "name": name, "name_key": M.normalise(name), "state": state, "status": status, "address": address, "roc": roc}


def test_name_similarity_bounds():
    assert M.name_similarity("acme technologies", "acme technologies") == 1.0
    assert M.name_similarity("info edge", "infoedge") == 0.98
    assert 0.8 < M.name_similarity("acme technologies", "acme technology") < 1.0
    assert M.name_similarity("acme", "zeta foods") < 0.4
    assert M.name_similarity("", "acme") == 0.0


def test_exact_unique_match_is_high_and_alternatives_are_listed():
    rows = [row("U1MH", "ACME TECHNOLOGIES PRIVATE LIMITED"), row("U2MH", "ACME TECHNO SERVICES PRIVATE LIMITED"),
            row("U3KA", "ACME FOODS PRIVATE LIMITED", "Karnataka"), row("U4DL", "ACME HOLDINGS LIMITED", "Delhi"),
            row("U5DL", "ACME LOGISTICS LIMITED", "Delhi")]
    out = M.rank({"company": "Acme Technologies Pvt Ltd", "state": "Maharashtra"}, rows)
    assert out["confidence"] == "high" and out["best"]["cin"] == "U1MH"
    assert len(out["alternatives"]) == 3 and all(a["cin"] != "U1MH" for a in out["alternatives"])
    assert out["best"]["match"]["state"] == "agree"


def test_state_agreement_breaks_a_tie_between_same_names():
    rows = [row("U1MH", "Acme Technologies Private Limited"), row("U1KA", "Acme Technologies Limited", "Karnataka", roc="RoC-Bangalore")]
    tie = M.rank({"company": "Acme Technologies"}, rows)
    assert tie["confidence"] == "medium"                  # two identical names and no state: ask a person
    picked = M.rank({"company": "Acme Technologies", "state": "Karnataka"}, rows)
    assert picked["best"]["cin"] == "U1KA" and picked["confidence"] == "high"
    by_city = M.rank({"company": "Acme Technologies", "city": "Bengaluru"}, rows)
    assert by_city["best"]["cin"] == "U1KA" and by_city["best"]["match"]["city"] is True


def test_state_conflict_is_never_high():
    out = M.rank({"company": "Acme Technologies", "state": "Gujarat"}, [row("U1MH", "Acme Technologies Pvt Ltd")])
    assert out["best"]["match"]["state"] == "conflict" and out["confidence"] == "medium"


def test_active_company_preferred_over_struck_off():
    rows = [row("U1", "Acme Technologies Private Limited", status="Strike Off"), row("U2", "Acme Technologies LLP", status="Active")]
    out = M.rank({"company": "Acme Technologies", "state": "MH"}, rows)
    assert out["best"]["cin"] == "U2" and out["best"]["match"]["active"] is True
    assert M.is_inactive("Strike Off") and M.is_inactive("Under Liquidation") and not M.is_inactive("Active")


def test_weak_name_is_low_and_rejected_cins_are_skipped():
    out = M.rank({"company": "Acme"}, [row("U1", "Zenith Acme Logistics Private Limited")])
    assert out["confidence"] == "low"
    gone = M.rank({"company": "Acme Technologies"}, [row("U1", "Acme Technologies Pvt Ltd")], rejected={"U1"})
    assert gone == {"confidence": "none", "best": None, "alternatives": []}


def test_very_short_names_are_never_high():
    assert M.rank({"company": "ABC Ltd"}, [row("U1", "ABC Private Limited")])["confidence"] == "medium"
