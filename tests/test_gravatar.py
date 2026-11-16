"""Gravatar public profiles (app/gravatar.py). Fake fetchers only: no network. Addresses are made up."""
from datetime import datetime, timedelta, timezone

from app import gravatar
from app.store import Store

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
PROFILE = {"entry": [{"profileUrl": "https://gravatar.com/asharao", "thumbnailUrl": "https://0.gravatar.com/avatar/abc",
                      "displayName": "asharao", "name": {"givenName": "Asha", "familyName": "Rao", "formatted": "Asha Rao"},
                      "accounts": [{"shortname": "linkedin", "url": "https://www.linkedin.com/in/asha-example"},
                                   {"shortname": "x", "url": "javascript:alert(1)"}],
                      "urls": [{"value": "https://asha.example.in", "title": "Blog"}]}]}


class Fetch:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.urls = reply, error, []

    def __call__(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.reply


def store(tmp_path):
    return Store(tmp_path / "store.json")


def test_hash_is_md5_of_trimmed_lowercase_address():
    assert gravatar.email_hash("  Asha@Gamma-Works.IN ") == gravatar.email_hash("asha@gamma-works.in")
    assert len(gravatar.email_hash("a@b.in")) == 32


def test_hit_returns_name_photo_and_links(tmp_path):
    s, fetch = store(tmp_path), Fetch(PROFILE)
    out = gravatar.lookup(s, "Asha@Gamma-Works.in", fetch, NOW)
    assert out["status"] == "found" and not out["cached"]
    assert fetch.urls == [f"https://gravatar.com/{gravatar.email_hash('asha@gamma-works.in')}.json"]
    p = out["profile"]
    assert p["name"] == "Asha Rao" and p["photo_url"] == "https://0.gravatar.com/avatar/abc"
    assert p["linkedin_url"] == "https://www.linkedin.com/in/asha-example" and p["profile_url"] == "https://gravatar.com/asharao"
    assert [l["url"] for l in p["links"]] == ["https://www.linkedin.com/in/asha-example", "https://asha.example.in"]   # javascript: dropped


def test_v3_reply_shape_is_read_too():
    p = gravatar.parse_profile({"display_name": "Vikram Shah", "avatar_url": "https://0.gravatar.com/avatar/x",
                                "profile_url": "https://gravatar.com/vik", "verified_accounts": [{"service_type": "linkedin", "url": "https://linkedin.com/in/vik"}]})
    assert p["name"] == "Vikram Shah" and p["linkedin_url"] == "https://linkedin.com/in/vik"
    assert gravatar.parse_profile({}) is None and gravatar.parse_profile("x") is None


def test_miss_is_cached_and_not_asked_again_within_30_days(tmp_path):
    s, fetch = store(tmp_path), Fetch(error=gravatar.NoProfile())
    assert gravatar.lookup(s, "nobody@gamma-works.in", fetch, NOW)["status"] == "none"
    again = gravatar.lookup(s, "nobody@gamma-works.in", fetch, NOW + timedelta(days=29))
    assert again == {"status": "none", "profile": None, "cached": True} and len(fetch.urls) == 1
    gravatar.lookup(s, "nobody@gamma-works.in", fetch, NOW + timedelta(days=31))           # stale: asked again
    assert len(fetch.urls) == 2 and len(s.read("gravatar_cache")) == 1


def test_hit_is_cached(tmp_path):
    s, fetch = store(tmp_path), Fetch(PROFILE)
    gravatar.lookup(s, "asha@gamma-works.in", fetch, NOW)
    out = gravatar.lookup(s, "asha@gamma-works.in", fetch, NOW)
    assert out["cached"] and out["profile"]["name"] == "Asha Rao" and len(fetch.urls) == 1


def test_timeout_or_error_never_raises_and_is_not_cached(tmp_path):
    s = store(tmp_path)
    for error in (TimeoutError("timed out"), OSError("HTTP 500"), ValueError("bad json")):
        assert gravatar.lookup(s, "asha@gamma-works.in", Fetch(error=error), NOW) == {"status": "error", "cached": False}
    assert s.read("gravatar_cache") == []
    assert gravatar.lookup(s, "not an email", Fetch(PROFILE), NOW)["status"] == "none"


def test_default_fetch_is_off_in_tests(tmp_path):
    assert gravatar.lookup(store(tmp_path), "asha@gamma-works.in", None, NOW)["status"] == "error"
