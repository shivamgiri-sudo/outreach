"""Shared email-format code (app/email_patterns.py). Pure functions: no network. Names and domains are made up."""
from app import email_patterns as EP


def test_parse_name_handles_titles_accents_initials_and_compound_surnames():
    assert EP.parse_name("Dr. José Müller") == {"first": "jose", "last": "muller", "last_alts": []}
    assert EP.parse_name("Shri Ramesh") == {"first": "ramesh", "last": "", "last_alts": []}
    assert EP.parse_name("A. K. Sharma")["first"] == "a" and EP.parse_name("A. K. Sharma")["last"] == "sharma"
    assert EP.parse_name("Maria de la Cruz") == {"first": "maria", "last": "delacruz", "last_alts": ["cruz"]}
    assert EP.parse_name("Ana García-López") == {"first": "ana", "last": "garcialopez", "last_alts": ["garcia", "lopez"]}
    assert EP.parse_name("Sharma, Rahul") == {"first": "rahul", "last": "sharma", "last_alts": []}
    assert EP.parse_name("Rahul Sharma, PhD")["last"] == "sharma"
    assert EP.parse_name("Strauß Zoë")["first"] == "strauss"


def test_which_pattern_reads_each_format():
    cases = {"asha.rao": "first.last", "arao": "flast", "asha": "first", "asharao": "firstlast", "asha_rao": "first_last",
             "rao.asha": "last.first", "a.rao": "f.last", "ashar": "firstl", "asha.r": "first.l", "raoa": "lastf"}
    for local, pid in cases.items():
        assert EP.which_pattern("Asha", "Rao", local) == pid, local
    assert EP.which_pattern("Asha", "Rao", "sales") is None


def test_pattern_of_tries_surname_variants_and_ignores_single_names():
    assert EP.pattern_of("Maria de la Cruz", "maria.cruz") == "first.last"
    assert EP.pattern_of("José Müller", "jose.muller") == "first.last"
    assert EP.pattern_of("Ravi", "ravi") is None


def test_learn_counts_formats_with_confidence():
    pairs = [("Asha Rao", "asha.rao"), ("Vikram Shah", "vikram.shah"), ("Neha Iyer", "neha.iyer"), ("Om Puri", "opuri"), ("Ravi", "ravi")]
    rows = EP.learn(pairs)
    assert rows[0] == {"pattern": "first.last", "count": 3, "share": 0.75, "confidence": 0.75}
    assert rows[1]["pattern"] == "flast" and rows[1]["confidence"] == round(0.25 / 3, 2)
    assert EP.confidence_label(0.75) == "high" and EP.confidence_label(0.33) == "medium" and EP.confidence_label(0.1) == "low"
    assert EP.learn([("Asha Rao", "asha.rao")])[0]["confidence"] == 0.33        # one example is never high confidence


def test_candidates_put_learned_format_first_then_defaults():
    learned = EP.learn([("Om Puri", "opuri"), ("Neha Iyer", "niyer")])
    out = EP.candidates("Kiran Desai", "gamma-works.in", learned)
    assert out[0] == {"email": "kdesai@gamma-works.in", "pattern": "flast", "basis": "the company's own format (2 known addresses)",
                      "confidence": 0.67}
    assert [c["pattern"] for c in out[1:]] == ["first.last", "first", "firstlast", "f.last"]
    assert len(out) == EP.MAX_CANDIDATES


def test_candidates_for_single_name_and_accents():
    assert [c["email"] for c in EP.candidates("Ramesh", "acme.in")] == ["ramesh@acme.in"]
    assert EP.candidates("Zoë Ångström", "acme.in")[0]["email"] == "zoe.angstrom@acme.in"
    assert EP.candidates("", "acme.in") == [] and EP.candidates("Asha Rao", "") == []
