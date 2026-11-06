"""Buying signals from public news: watchlist, feed parsing, de-duplication, safe parsing, article -> lead."""
import pytest
from fastapi.testclient import TestClient

from app import signals
from app.main import create_app

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Alpha BPO to hire 500 in Noida - Economic Times</title><link>https://news.example/a1</link>
<pubDate>Mon, 02 Nov 2026 08:00:00 GMT</pubDate><source url="https://et.example">Economic Times</source></item>
<item><title>Beta Services opens new centre - Mint</title><link>https://news.example/b2</link><pubDate>Sun, 01 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>No link here</title><link>javascript:alert(1)</link></item>
</channel></rss>"""


def make(tmp_path, fetch=None, ai=None):
    urls = []

    def feed(url):
        urls.append(url)
        if isinstance(fetch, Exception):
            raise fetch
        return fetch or RSS

    c = TestClient(create_app(data_file=tmp_path / "d.json", signal_fetch=feed, ai_transport=ai or (lambda *a, **k: {"content": []})))
    return c, urls


def test_parse_feed_keeps_only_web_links_and_finds_the_source():
    rows = signals.parse_feed(RSS)
    assert [r["link"] for r in rows] == ["https://news.example/a1", "https://news.example/b2"]
    assert rows[0]["source"] == "Economic Times" and rows[1]["source"] == "Mint" and rows[0]["published"].startswith("2026-11-02")


def test_feed_with_entities_is_refused():
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title><link>https://x.y</link></item></channel></rss>'
    with pytest.raises(ValueError):
        signals.parse_feed(bomb)
    with pytest.raises(ValueError):
        signals.parse_feed(b"not xml")


def test_only_the_fixed_news_host_is_ever_fetched():
    with pytest.raises(OSError):
        signals.default_fetch("http://169.254.169.254/latest/meta-data")
    with pytest.raises(OSError):
        signals.default_fetch("https://evil.example/rss")


def test_watchlist_rules(tmp_path):
    c, _ = make(tmp_path)
    assert c.post("/api/signals/watch", json={"kind": "keyword", "text": "BPO hiring Noida"}).status_code == 200
    assert c.post("/api/signals/watch", json={"kind": "keyword", "text": "bpo hiring noida"}).status_code == 400      # duplicate
    assert c.post("/api/signals/watch", json={"kind": "person", "text": "someone"}).status_code == 400
    assert c.post("/api/signals/watch", json={"kind": "company", "text": "x"}).status_code == 400
    for i in range(19):
        assert c.post("/api/signals/watch", json={"kind": "company", "text": f"Firm number {i}"}).status_code == 200
    assert c.post("/api/signals/watch", json={"kind": "company", "text": "One too many"}).status_code == 400
    wid = c.get("/api/signals").json()["watch"][0]["id"]
    assert c.delete(f"/api/signals/watch/{wid}").status_code == 200 and c.delete(f"/api/signals/watch/{wid}").status_code == 404


def test_run_collects_new_articles_once_and_survives_a_bad_feed(tmp_path):
    c, urls = make(tmp_path)
    c.post("/api/signals/watch", json={"kind": "keyword", "text": "BPO hiring"})
    first = c.post("/api/signals/run").json()
    assert first["new"] == 2 and first["errors"] == [] and urls[0].startswith("https://news.google.com/rss/search?q=")
    assert c.post("/api/signals/run").json()["new"] == 0                       # same links are not added again
    assert len(c.get("/api/signals").json()["items"]) == 2
    (tmp_path / "b").mkdir()
    bad, _ = make(tmp_path / "b", fetch=OSError("boom"))
    bad.post("/api/signals/watch", json={"kind": "company", "text": "Gamma Co"})
    r = bad.post("/api/signals/run").json()
    assert r["new"] == 0 and r["errors"][0]["watch"] == "Gamma Co"


def test_article_becomes_a_lead_and_is_not_duplicated(tmp_path):
    c, _ = make(tmp_path)
    c.post("/api/signals/watch", json={"kind": "keyword", "text": "BPO hiring"})
    c.post("/api/signals/run")
    item = c.get("/api/signals").json()["items"][0]
    assert c.post(f"/api/signals/items/{item['id']}/lead", json={"company": "x"}).status_code == 400
    r = c.post(f"/api/signals/items/{item['id']}/lead", json={"company": "Alpha BPO"}).json()
    assert r["new"] is True
    lead = next(l for l in c.get("/api/leads").json() if l["id"] == r["lead_id"])
    assert lead["company"] == "Alpha BPO" and lead["source"].startswith("news_signal") and any(e["kind"] == "signal" for e in lead["timeline"])
    other = c.get("/api/signals").json()["items"][1]
    again = c.post(f"/api/signals/items/{other['id']}/lead", json={"company": "alpha bpo"}).json()
    assert again["new"] is False and again["lead_id"] == r["lead_id"]
    assert [i["status"] for i in c.get("/api/signals").json()["items"]] == ["added", "added"]
    assert c.post(f"/api/signals/items/{item['id']}/status", json={"status": "bogus"}).status_code == 400


def test_ai_names_the_company_when_connected(tmp_path):
    def ai(url, headers, body):
        return {"content": [{"type": "text", "text": '{"company": "Alpha BPO"}'}]}
    c, _ = make(tmp_path, ai=ai)
    c.put("/api/integrations/llm", json={"mode": "api", "values": {"provider": "anthropic", "api_key": "k"}})
    c.post("/api/signals/watch", json={"kind": "keyword", "text": "BPO hiring"})
    c.post("/api/signals/run")
    assert {i["company"] for i in c.get("/api/signals").json()["items"]} == {"Alpha BPO"}


def test_article_about_a_company_already_found_from_its_job_pages_joins_that_lead(tmp_path):
    """QA: a Wipro lead made by Read job pages is keyed by wipro.com; adding a Wipro article made a second Wipro lead."""
    c, _ = make(tmp_path)
    c.post("/api/jobs/ingest", json={"csv": "company,title,location,date_posted,website\nWipro Limited,Analyst,Pune,2026-11-01,https://wipro.com\n"})
    c.post("/api/signals/watch", json={"kind": "keyword", "text": "BPO hiring"})
    c.post("/api/signals/run")
    item = c.get("/api/signals").json()["items"][0]
    r = c.post(f"/api/signals/items/{item['id']}/lead", json={"company": "Wipro"}).json()
    assert r["new"] is False
    assert [l["company"] for l in c.get("/api/leads").json()] == ["Wipro Limited"]
