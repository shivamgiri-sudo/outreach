"""Company health: each free source parsed, namesakes on Wikidata ignored, failures reported not guessed, score explained."""
import json

from app import company_health as ch

HOME = ('<html><head><title>Acme Retail | Stores</title><meta name="description" content="Retail chain"></head><body>'
        '<a href="/careers">Careers</a> <a href="https://linkedin.com/company/acme-retail">in</a> <a href="https://x.com/acme">x</a>'
        '<script src="https://www.googletagmanager.com/gtag.js"></script> © 2019-2026 Acme</body></html>')
RSS = ('<rss><channel><item><title>Acme Retail raises Series B to expand stores</title><link>https://n/1</link><pubDate>Mon, 02 Nov 2026</pubDate>'
       '<source>ET</source></item><item><title>Acme Retail announces layoffs</title><link>https://n/2</link></item>'
       '<item><title>Something unrelated</title><link>https://n/3</link></item></channel></rss>')


def fake_get(url):
    if "rdap.org" in url:
        return json.dumps({"events": [{"eventAction": "registration", "eventDate": "2009-05-01T00:00:00Z"}]})
    if "wbsearchentities" in url:
        return json.dumps({"search": [{"id": "Q1"}, {"id": "Q2"}]})
    if "wbgetentities" in url:
        site = lambda u: {"P856": [{"mainsnak": {"datavalue": {"value": u}}}]}
        return json.dumps({"entities": {
            "Q1": {"claims": site("https://namesake.example"), "labels": {"en": {"value": "Acme (band)"}}},
            "Q2": {"claims": {**site("https://www.acme.test/"), "P571": [{"mainsnak": {"datavalue": {"value": {"time": "+2009-00-00T00:00:00Z"}}}}],
                              "P1128": [{"mainsnak": {"datavalue": {"value": {"amount": "+1200"}}}}]},
                   "labels": {"en": {"value": "Acme Retail"}}, "descriptions": {"en": {"value": "Indian retailer"}}}}})
    if "news.google.com" in url:
        return RSS
    raise OSError("unexpected " + url)


def fake_page(url):
    if url == "https://acme.test/":
        return HOME
    raise OSError("unreachable")


LEAD = {"company": "Acme Retail", "domain": "acme.test", "open_roles": 6}
SRC = dict(get=fake_get, get_page=fake_page, mx=lambda d: ["aspmx.l.google.com"], txt=lambda n: ["v=spf1 include:_spf.google.com ~all"] if n == "acme.test" else ["v=DMARC1; p=quarantine"])


def test_report_reads_every_source_and_explains_the_score():
    r = ch.build(LEAD, **SRC)
    assert r["website"]["ok"] and r["website"]["careers_page"] and r["website"]["copyright_year"] == 2026
    assert set(r["website"]["social"]) == {"linkedin", "twitter"} and "Google Analytics" in r["website"]["tools"]
    assert r["email"]["provider"] == "Google Workspace" and r["email"]["spf"] and r["email"]["dmarc_policy"] == "quarantine"
    assert r["domain_age"]["ok"] and r["domain_age"]["years"] > 15
    assert r["wikidata"]["ok"] and r["wikidata"]["id"] == "Q2" and r["wikidata"]["employees"] == "1200"   # the namesake is skipped
    assert r["news"]["count"] == 2 and r["news"]["growth"] == 1 and r["news"]["risk"] == 1
    assert 0 < r["score"] <= 100 and r["band"] in ("strong", "steady", "weak / unknown")
    assert any(x["points"] < 0 and "risk" in x["why"] for x in r["reasons"]) and sum(x["points"] for x in r["reasons"]) == r["score"]


def test_failed_sources_are_reported_not_guessed():
    boom = lambda *a: (_ for _ in ()).throw(OSError("down"))
    r = ch.build({"company": "Nowhere", "domain": "nowhere.test"}, get=boom, get_page=boom, mx=lambda d: [], txt=lambda n: [])
    assert not r["website"]["ok"] and not r["domain_age"]["ok"] and not r["wikidata"]["ok"] and not r["news"]["ok"]
    assert r["score"] == 0 and r["band"] == "weak / unknown"


def test_feed_with_entities_is_refused():
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>Acme &a;</title></item></channel></rss>'
    assert ch.news("Acme", lambda u: evil)["ok"] is False


def test_endpoint_needs_a_domain_and_caches(tmp_path, monkeypatch):
    from tests.flow_helpers import lead, make_client, patch_lead
    c = make_client(tmp_path)
    a = lead(c, "Acme")["id"]
    monkeypatch.setattr(ch, "build", lambda l, **k: {"domain": l.get("domain"), "built_at": ch.now_utc().isoformat(), "score": 50, "band": "steady"})
    r = c.post(f"/api/leads/{a}/company-health")
    assert r.status_code == 200 and r.json()["report"]["score"] == 50
    assert c.get(f"/api/leads/{a}/company-health").json()["report"]["score"] == 50
    assert c.post(f"/api/leads/{a}/company-health").json()["report"].get("from_cache") is True
    patch_lead(c, a, domain="", website="", email="")
    assert c.post(f"/api/leads/{a}/company-health").status_code == 400
