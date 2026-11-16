"""app/phones.py: libphonenumber-based E.164 + line type, Indian formats, invalid numbers dropped."""
import pytest

from app import phones


@pytest.mark.parametrize("raw", ["98220 12345", "+91 98220 12345", "+91-98220-12345", "098220-12345", "919822012345", "(+91) 9822 012 345",
                                 "0091 98220 12345"])
def test_indian_mobiles_in_every_common_spelling(raw):
    assert phones.parse(raw) == {"e164": "+919822012345", "type": "mobile", "region": "IN"}


@pytest.mark.parametrize("raw,e164", [("011 2345 6789", "+911123456789"), ("080-4000-1500", "+918040001500"),
                                      ("022 6600 1234", "+912266001234"), ("0124 456 7890", "+911244567890"),
                                      ("+91 20 2553 0001", "+912025530001")])
def test_landlines_with_std_codes_are_fixed_lines(raw, e164):
    found = phones.parse(raw)
    assert found["e164"] == e164 and found["type"] == "fixed_line"
    assert phones.compat_type(found) == "landline"            # the older sending rules: never WhatsApp / SMS


@pytest.mark.parametrize("raw", ["1800 123 4567", "1800-11-0011", "1800 11 0011"])
def test_toll_free_1800(raw):
    found = phones.parse(raw)
    assert found["type"] == "toll_free" and found["e164"].startswith("+911800")
    assert phones.compat_type(found) == "landline"


@pytest.mark.parametrize("raw", ["", None, "12345", "98220", "abc", "+91 12345", "0000000000", "9822012345678901"])
def test_invalid_or_incomplete_numbers_are_dropped(raw):
    assert phones.parse(raw) is None
    assert phones.e164(raw) == ""


def test_country_override_and_international():
    uk = phones.parse("020 7946 0958", country="GB")
    assert uk["e164"] == "+442079460958" and uk["region"] == "GB" and uk["type"] == "fixed_line"
    assert phones.parse("+44 7911 123456")["type"] == "mobile"
    assert phones.compat_type(phones.parse("+44 7911 123456")) == "intl"
    assert phones.parse("98220 12345", country="bad!")["e164"] == "+919822012345"      # unknown country -> India


def test_parse_many_splits_dedupes_and_best_prefers_mobile():
    found = phones.parse_many("020 2553 0001 / 98220 12345; +91 98220 12345, 1800 11 0011 or 123")
    assert [n["e164"] for n in found] == ["+912025530001", "+919822012345", "+911800110011"]
    assert phones.best(found)["e164"] == "+919822012345"
    assert phones.best([]) is None
    assert phones.parse_many(["98220 12345", "", None, "bad"]) == [phones.parse("98220 12345")]


def test_line_types_listed():
    assert set(phones.LINE_TYPES) >= {"mobile", "fixed_line", "toll_free", "voip", "mobile_or_fixed", "unknown"}
