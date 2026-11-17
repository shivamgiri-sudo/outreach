"""Public GitHub contacts (app/github_profiles.py): org matched by website domain, only public on-domain emails, noreply
excluded, ambiguous skipped, rate-limit back-off, per-run cap, default OFF in Find & enrich. Fake GitHub only."""
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import find_enrich_free as FE, github_profiles as GH
from app.main import create_app
from tests.campaign_fakes import FakeIO
from tests.test_domain_signals import Store
from tests.test_find_leads import add_leads, go

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
OK = {"x-ratelimit-remaining": "50", "x-ratelimit-reset": str(int((NOW + timedelta(minutes=30)).timestamp()))}


class Hub:
    """A fake api.github.com: {path: json}; unknown paths 404."""

    def __init__(self, routes: dict, status: int = 200, headers: dict = None):
        self.routes, self.status, self.headers, self.asked = routes, status, headers or OK, []

    def __call__(self, url):
        path = url.replace(GH.API, "")
        self.asked.append(path)
        if self.status != 200:
            return self.status, self.headers, "{}"
        if path not in self.routes:
            return 404, self.headers, "{}"
        return 200, self.headers, json.dumps(self.routes[path])


ACME = {                                                              # the domain acme-hr.in -> the org name "acme-hr"
    "/search/users?q=acme-hr+type:org&per_page=3": {"items": [{"login": "acme-hr"}, {"login": "acme-labs"}, {"login": "acmefans"}]},
    "/orgs/acme-hr": {"login": "acme-hr", "name": "Acme", "blog": "https://www.acme-hr.in", "email": "hello@acme-hr.in",
                      "html_url": "https://github.com/acme-hr"},
    "/orgs/acme-labs": {"login": "acme-labs", "blog": "https://acmelabs.example"},
    "/orgs/acmefans": {"login": "acmefans", "blog": ""},
    "/orgs/acme-hr/public_members?per_page=8": [{"login": "asha"}, {"login": "bot"}, {"login": "om"}, {"login": "lata"}, {"login": "sec"}],
    "/users/asha": {"login": "asha", "name": "Asha Rao", "email": "asha.rao@acme-hr.in", "html_url": "https://github.com/asha"},
    "/users/bot": {"login": "bot", "email": "123+bot@users.noreply.github.com"},
    "/users/om": {"login": "om", "name": "Om Puri", "email": "om@gmail.com"},
    "/users/lata": {"login": "lata", "name": "Lata", "email": None},
    "/users/sec": {"login": "sec", "email": "security@acme-hr.in"},
}


def test_org_matched_by_website_and_only_public_on_domain_emails():
    st, hub = Store(), Hub(ACME)
    r = GH.lookup(st, "acme-hr.in", hub, NOW, "Acme HR")
    assert r["status"] == "found" and not r["cached"]
    emails = [(p["email"], p["source"], p["profile_url"]) for p in r["people"]]
    assert emails == [("hello@acme-hr.in", "github", "https://github.com/acme-hr"), ("asha.rao@acme-hr.in", "github", "https://github.com/asha")]
    assert not any("noreply" in p or "gmail" in p or "security@" in p for p, _, _ in emails)
    assert all("/commits" not in a and "/events" not in a for a in hub.asked)        # profile fields only, never commits


def test_cached_thirty_days():
    st, hub = Store(), Hub(ACME)
    GH.lookup(st, "acme-hr.in", hub, NOW)
    n = len(hub.asked)
    assert GH.lookup(st, "acme-hr.in", hub, NOW + timedelta(days=29))["cached"] and len(hub.asked) == n
    GH.lookup(st, "acme-hr.in", hub, NOW + timedelta(days=31))
    assert len(hub.asked) > n


def test_ambiguous_match_is_skipped():
    routes = {"/search/users?q=twin+type:org&per_page=3": {"items": [{"login": "twin"}, {"login": "twin-in"}]},
              "/orgs/twin": {"login": "twin", "blog": "twin.in", "email": "a@twin.in"},
              "/orgs/twin-in": {"login": "twin-in", "blog": "https://twin.in/about", "email": "b@twin.in"}}
    r = GH.lookup(Store(), "twin.in", Hub(routes), NOW)
    assert r["status"] == "ambiguous" and r["people"] == []


def test_no_matching_org():
    routes = {"/search/users?q=solo+type:org&per_page=3": {"items": [{"login": "solo"}]}, "/orgs/solo": {"login": "solo", "blog": "solo.example"}}
    assert GH.lookup(Store(), "solo.in", Hub(routes), NOW)["status"] == "none"


def test_rate_limit_backs_off_and_later_calls_skip():
    st = Store()
    for status in (403, 429):
        st = Store()
        hub = Hub(ACME, status=status, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int((NOW + timedelta(hours=2)).timestamp()))})
        r = GH.lookup(st, "acme-hr.in", hub, NOW)
        assert r["status"] == "paused" and len(hub.asked) == 1
        until = datetime.fromisoformat(st.data["settings"][GH.BACKOFF_KEY])
        assert until == NOW + timedelta(hours=2)
        again = Hub(ACME)
        assert GH.lookup(st, "other.in", again, NOW + timedelta(minutes=5))["status"] == "paused" and again.asked == []
        assert GH.lookup(st, "acme-hr.in", again, NOW + timedelta(hours=3))["status"] == "found"


def test_low_remaining_quota_stops_early_with_at_least_an_hour():
    hub = Hub(ACME, headers={"x-ratelimit-remaining": "1", "x-ratelimit-reset": str(int((NOW + timedelta(minutes=1)).timestamp()))})
    st = Store()
    assert GH.lookup(st, "acme-hr.in", hub, NOW)["status"] == "paused" and len(hub.asked) == 1
    assert datetime.fromisoformat(st.data["settings"][GH.BACKOFF_KEY]) == NOW + timedelta(seconds=GH.BACKOFF_SECONDS)


def test_network_error_is_not_cached():
    st = Store()

    def down(url):
        raise OSError("down")
    assert GH.lookup(st, "acme-hr.in", down, NOW)["status"] == "error"
    assert GH.CACHE_KEY not in st.data


def test_public_email_filter():
    assert GH.public_email("Asha@Acme.in", "acme.in") == "asha@acme.in"
    assert GH.public_email("x@mail.acme.in", "acme.in") == "x@mail.acme.in"
    for bad in ("1+bot@users.noreply.github.com", "noreply@acme.in", "privacy@acme.in", "a@acme.in.evil.com", "a@notacme.in", "", None, "nope"):
        assert GH.public_email(bad, "acme.in") == "", bad


# ---------- Find & enrich ----------
def make(tmp_path, hub):
    io = FakeIO()
    return TestClient(create_app(data_file=tmp_path / "store.json", campaign_transports={**io.transports(), "github": hub}))


CSV = "company,website,contact_name\nAcme HR,https://acme-hr.in,Kiran Desai\n"


def test_step_is_off_by_default(tmp_path):
    hub = Hub(ACME)
    c = make(tmp_path, hub)
    add_leads(c, CSV)
    go(c, mode="enrich_existing", titles=["HR Head"])
    assert hub.asked == []
    assert "public_profiles" in FE.DEFAULT_OFF


def test_step_when_switched_on_adds_other_contacts(tmp_path):
    hub = Hub(ACME)
    c = make(tmp_path, hub)
    c.app.state.store.update("settings", lambda s: {**s, "prospect_db": {"providers": {"public_profiles": {"enabled": True}}}})
    add_leads(c, CSV)
    go(c, mode="enrich_existing", titles=["HR Head"])
    lead = next(l for l in c.app.state.store.read("leads") if l["company"] == "Acme HR")
    gh = [o for o in lead.get("other_contacts") or [] if o.get("source") == "github"]
    assert {o["email"] for o in gh} == {"hello@acme-hr.in", "asha.rao@acme-hr.in"}
    assert all(o["profile_url"].startswith("https://github.com/") for o in gh)
    assert not lead.get("email")                                   # other contacts only; the lead's own email is untouched


class Ctx:
    def __init__(self, store, hub):
        self.free = FE.FreeTools(store, {"github": hub}, NOW)
        self.meter, self.errors = None, []

    def error(self, source, message):
        self.errors.append((source, message))


class Out:
    def __init__(self):
        self.others, self.notes = [], []


def test_per_run_domain_cap(monkeypatch):
    monkeypatch.setattr(FE, "DEFAULT_OFF", frozenset())
    monkeypatch.setattr(GH, "MAX_DOMAINS", 2)
    hub = Hub({})
    ctx = Ctx(Store(), hub)
    for i in range(5):
        FE.profiles_step({"company": "X"}, Out(), f"d{i}.in", ctx)
    assert {a.split("q=")[1].split("+")[0] for a in hub.asked if a.startswith("/search")} == {"d0", "d1"}


def test_pause_is_reported_once_per_lead(monkeypatch):
    monkeypatch.setattr(FE, "DEFAULT_OFF", frozenset())
    st = Store()
    st.data["settings"][GH.BACKOFF_KEY] = (NOW + timedelta(hours=1)).isoformat()
    ctx = Ctx(st, Hub(ACME))
    FE.profiles_step({"company": "X"}, Out(), "acme-hr.in", ctx)
    assert ctx.errors and "hourly limit" in ctx.errors[0][1]
