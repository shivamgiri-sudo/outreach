"""Domain trust signals (app/domain_signals.py): parsing, failures not guessed, cache window, batch cap, Find & enrich step,
re-crawl piggy-back, Leads grid filters (server and the same rule in web/src/pages/domainSignals.js). Fake DNS / RDAP only."""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import domain_signals as DS, lead_grid, routes_lead_research
from app.main import create_app
from tests.campaign_fakes import FakeIO
from tests.test_find_leads import add_leads, go

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)


def rdap(date: str):
    return lambda url: json.dumps({"events": [{"eventAction": "registration", "eventDate": date}]})


def txt_of(spf="v=spf1 include:_spf.google.com ~all", dmarc="v=DMARC1; p=reject"):
    return lambda name: [dmarc] if name.startswith("_dmarc.") and dmarc else ([spf] if spf and not name.startswith("_dmarc.") else [])


def boom(*_a):
    raise OSError("timeout")


GOOD = dict(mx=lambda d: ["aspmx.l.google.com"], txt=txt_of(), get=rdap("2010-03-01T00:00:00Z"))


# ---------- parsing ----------
def test_full_signals_from_dns_and_rdap():
    s = DS.check("acme.test", now=NOW, **GOOD)
    assert s == {"mx": "Google Workspace", "spf": "present", "dmarc": "reject", "age_days": (NOW.date() - datetime(2010, 3, 1).date()).days,
                 "registered_at": "2010-03-01", "checked_at": NOW.isoformat(timespec="seconds")}
    assert not DS.is_young(s) and not DS.no_mail_auth(s) and DS.known(s)


def test_spf_missing_and_dmarc_policies():
    for record, word in (("v=DMARC1; p=none; rua=mailto:x@y", "none"), ("v=DMARC1; p=quarantine", "quarantine"),
                         ("v=DMARC1; p=reject", "reject"), ("v=DMARC1; rua=mailto:x@y", "none"), ("", "missing")):
        s = DS.check("acme.test", mx=lambda d: [], txt=txt_of(spf="", dmarc=record), get=boom, now=NOW)
        assert s["dmarc"] == word, record
        assert s["spf"] == "missing" and s["mx"] == "none"
    assert DS.no_mail_auth(DS.check("acme.test", mx=lambda d: [], txt=txt_of(dmarc=""), get=boom, now=NOW))


def test_young_domain_from_rdap_date():
    s = DS.check("new.test", now=NOW, mx=GOOD["mx"], txt=GOOD["txt"], get=rdap("2026-08-01T10:00:00Z"))
    assert s["age_days"] == 101 and s["registered_at"] == "2026-08-01" and DS.is_young(s)


def test_failures_are_unknown_never_guessed():
    s = DS.check("down.test", mx=boom, txt=boom, get=boom, now=NOW)
    assert (s["mx"], s["spf"], s["dmarc"], s["age_days"], s["registered_at"]) == ("unknown", "unknown", "unknown", None, None)
    assert not DS.known(s) and not DS.is_young(s) and not DS.no_mail_auth(s)
    odd = DS.check("odd.test", mx=GOOD["mx"], txt=GOOD["txt"], get=lambda u: json.dumps({"events": [{"eventAction": "registration", "eventDate": "soon"}]}), now=NOW)
    assert odd["age_days"] is None and odd["spf"] == "present"
    no_reg = DS.check("x.test", mx=GOOD["mx"], txt=GOOD["txt"], get=lambda u: json.dumps({"events": []}), now=NOW)
    assert no_reg["age_days"] is None


def test_offline_by_default_in_tests():
    assert not DS.known(DS.check("acme.test", now=NOW))          # LIVE is off: no real DNS, nothing guessed


# ---------- cache ----------
class Store:
    def __init__(self):
        self.data = {"settings": {}, "leads": []}

    def read(self, k):
        return json.loads(json.dumps(self.data[k]))

    def write(self, k, v):
        self.data[k] = v

    def update(self, k, fn):
        self.data[k] = fn(self.read(k))
        return self.data[k]

    def update_lead(self, lead_id, fn):
        self.data["leads"] = [fn(l) if l["id"] == lead_id else l for l in self.data["leads"]]


def test_cache_window_thirty_days_and_unknown_not_cached():
    st, calls = Store(), []

    def mx(d):
        calls.append(d)
        return ["aspmx.l.google.com"]
    res = dict(GOOD, mx=mx)
    DS.lookup(st, "acme.test", NOW, **res)
    DS.lookup(st, "acme.test", NOW + timedelta(days=29), **res)
    assert len(calls) == 1
    DS.lookup(st, "acme.test", NOW + timedelta(days=31), **res)
    assert len(calls) == 2 and len(st.data[DS.CACHE_KEY]) == 1
    DS.lookup(st, "down.test", NOW, mx=boom, txt=boom, get=boom)
    assert [r["d"] for r in st.data[DS.CACHE_KEY]] == ["acme.test"]           # a failed check is tried again next time


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(DS, "MAX_CACHED", 3)
    st = Store()
    for i in range(5):
        DS.lookup(st, f"d{i}.test", NOW, **GOOD)
    assert [r["d"] for r in st.data[DS.CACHE_KEY]] == ["d2.test", "d3.test", "d4.test"]


# ---------- batch ----------
def test_batch_pick_is_capped_and_skips_recent_and_closed():
    leads = [{"id": f"l{i}", "domain": f"d{i}.test", "stage": "new"} for i in range(DS.BATCH_MAX + 20)]
    leads += [{"id": "fresh", "domain": "fresh.test", "stage": "new", "domain_signals": {"checked_at": NOW.isoformat()}},
              {"id": "gone", "domain": "gone.test", "stage": "unsubscribed"}, {"id": "nodomain", "domain": "", "stage": "new"}]
    work = DS.pick(leads, 10_000, NOW)
    assert len(work) == DS.BATCH_MAX
    assert not {"fresh.test", "gone.test", ""} & {d for d, _ in work}
    assert len(DS.pick(leads, 5, NOW)) == 5


def make(tmp_path, res=GOOD, **extra):
    io = FakeIO()
    return TestClient(create_app(data_file=tmp_path / "store.json", campaign_transports={**io.transports(), "domain_signals": res, **extra}))


CSV = ("company,website,contact_name,email\nAcme,https://acme.test,Asha Rao,asha@acme.test\n"
       "Acme Two,https://acme.test,Om Puri,om@acme.test\nNew Co,https://new.test,,info@new.test\n")
NO_EMAIL = "company,website,contact_name\nFresh Co,https://fresh-co.in,Kiran Desai\n"


def leads_of(c):
    return {l["company"]: l for l in c.app.state.store.read("leads")}


def test_owner_batch_endpoint_writes_signals(tmp_path, monkeypatch):
    monkeypatch.setattr(routes_lead_research, "BACKGROUND", False)
    c = make(tmp_path)
    add_leads(c, CSV)
    r = c.post("/api/domain-signals/run", json={"limit": 500})
    assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["total"] == 2
    assert c.post("/api/domain-signals/run", json={"limit": 501}).status_code == 422
    rows = leads_of(c)
    assert rows["Acme"]["domain_signals"]["spf"] == "present" and rows["Acme Two"]["domain_signals"]["mx"] == "Google Workspace"
    assert c.get("/api/domain-signals/run").json()["done"] == 2
    again = c.post("/api/domain-signals/run", json={}).json()
    assert again["total"] == 0 and "30 days" in again["note"]


def test_single_lead_check_and_research_view(tmp_path):
    c = make(tmp_path, dict(GOOD, txt=txt_of(dmarc=""), get=rdap("2026-06-01T00:00:00Z")))
    add_leads(c, CSV)
    lid = leads_of(c)["New Co"]["id"]
    r = c.post(f"/api/leads/{lid}/domain-signals").json()
    assert r["known"] and r["domain_signals"]["dmarc"] == "missing"
    view = c.get(f"/api/leads/{lid}/research").json()
    assert view["young"] and view["no_mail_auth"] and "bounce" in view["why"]


# ---------- Find & enrich step, re-crawl ----------
def test_find_enrich_step_on_by_default_and_switchable(tmp_path):
    c = make(tmp_path)
    add_leads(c, NO_EMAIL)
    go(c, mode="enrich_existing", titles=["HR Head"])
    assert leads_of(c)["Fresh Co"]["domain_signals"]["dmarc"] == "reject"
    from app import prospect_waterfall as W
    rows = {p["id"]: p for p in W.provider_config(c.app.state.store)}
    assert rows["domain_signals"]["enabled"] and rows["domain_signals"]["free"] and not rows["public_profiles"]["enabled"]


def test_find_enrich_step_off(tmp_path):
    c = make(tmp_path)
    c.app.state.store.update("settings", lambda s: {**s, "prospect_db": {"providers": {"domain_signals": {"enabled": False}}}})
    add_leads(c, NO_EMAIL)
    go(c, mode="enrich_existing", titles=["HR Head"])
    assert not any(l.get("domain_signals") for l in leads_of(c).values())


def test_recrawl_refreshes_signals_for_domains_it_reads(monkeypatch):
    from app import recrawl
    st = Store()
    st.data["leads"] = [{"id": "a", "domain": "acme.test"}]
    monkeypatch.setattr(DS, "resolvers", lambda: GOOD)
    recrawl._domain_signals(st, "acme.test", NOW)
    assert st.data["leads"][0]["domain_signals"]["dmarc"] == "reject"
    monkeypatch.setattr(DS, "refresh_domain", boom)
    recrawl._domain_signals(st, "acme.test", NOW)                 # a failure never stops the re-crawl


# ---------- Leads grid filters: server and UI agree ----------
SIGNALS = [
    {"age_days": 100, "spf": "present", "dmarc": "reject"},
    {"age_days": 365, "spf": "present", "dmarc": "none"},
    {"age_days": 2000, "spf": "missing", "dmarc": "reject"},
    {"age_days": None, "spf": "present", "dmarc": "missing"},
    {"age_days": None, "spf": "unknown", "dmarc": "unknown"},
    None,
]


def test_grid_filters_young_domain_and_no_mail_auth():
    leads = [{"id": str(i), "domain_signals": s} for i, s in enumerate(SIGNALS)]
    pick = lambda has: [l["id"] for l in leads if lead_grid.matches(l, lead_grid.parse({"has": has}), set())]
    assert pick("young_domain") == ["0"]
    assert pick("no_mail_auth") == ["2", "3"]
    assert pick("young_domain,no_mail_auth") == []


def test_ui_rule_matches_server(tmp_path):
    script = tmp_path / "t.mjs"
    script.write_text(f'import {{ isYoungDomain, noMailAuth }} from "{ROOT}/web/src/pages/domainSignals.js";\n'
                      f'const cases = {json.dumps(SIGNALS)};\n'
                      'console.log(JSON.stringify(cases.map((s) => [isYoungDomain({ domain_signals: s }), noMailAuth({ domain_signals: s })])));\n',
                      encoding="utf-8")
    out = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    want = [[DS.is_young(s), DS.no_mail_auth(s)] for s in SIGNALS]
    assert json.loads(out.stdout) == want


def test_saved_view_and_download_keep_the_filters(tmp_path):
    c = make(tmp_path)
    saved = c.post("/api/lead-views", json={"name": "Risky domains", "filters": {"has": ["young_domain", "no_mail_auth", "bogus"]}})
    assert saved.status_code == 200 and saved.json()["filters"]["has"] == ["young_domain", "no_mail_auth"]
