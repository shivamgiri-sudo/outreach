"""Open / click tracking (signed pixel and redirect), alerts on replies / yes / meetings / calls, and response times."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app import sla, tracking
from app.main import create_app

CSV = ("company,contact_name,email,phone,website\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com\n"
       "Beta Ltd,Sita Rao,sita@beta.in,91234 56789,https://beta.in\n")


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    store = c.app.state.store
    return c, store, {l["company"]: l["id"] for l in store.read("leads")}


def sent_row(store, lead_id, minutes_ago=10, key="k1"):
    at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    store.update("outbox", lambda rows: [*rows, {"lead_id": lead_id, "idempotency_key": key, "status": "sent", "sent_at": at,
                                                 "subject": "Hiring help", "body": "Hi\n\nSee https://example.com/demo\n\nNot relevant? Unsubscribe: https://x.test/u/abc"}])


def test_decorate_adds_pixel_and_signed_links_but_not_unsubscribe(tmp_path):
    c, store, ids = make(tmp_path)
    html = '<html><body><a href="https://example.com/demo?a=1&amp;b=2">x</a><a href="https://x.test/u/abc">u</a></body></html>'
    out = tracking.decorate(store, html, {"idempotency_key": "k1", "lead_id": ids["Acme"]}, "https://app.test")
    assert "/api/track/o/" in out and out.count("/api/track/c/") == 1 and 'href="https://x.test/u/abc"' in out
    store.update("settings", lambda s: {**s, "track_email": False})
    assert tracking.decorate(store, html, {"idempotency_key": "k1", "lead_id": ids["Acme"]}, "https://app.test") == html


def test_open_and_click_count_on_the_lead_and_alert_on_click(tmp_path):
    c, store, ids = make(tmp_path)
    sent_row(store, ids["Acme"])
    token = tracking.make_token(store, "k1")
    r = c.get(f"/api/track/o/{token}.gif")
    assert r.status_code == 200 and r.headers["content-type"] == "image/gif"
    html = tracking.decorate(store, '<a href="https://example.com/demo">x</a>', {"idempotency_key": "k1", "lead_id": ids["Acme"]}, "https://app.test")
    href = html.split('href="')[1].split('"')[0].replace("&amp;", "&")
    q = urlparse(href)
    r = c.get(q.path, params={k: v[0] for k, v in parse_qs(q.query).items()}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://example.com/demo"
    lead = next(l for l in store.read("leads") if l["id"] == ids["Acme"])
    assert lead["engagement"]["opens"] == 1 and lead["engagement"]["clicks"] == 1
    assert {"email_open", "email_click"} <= {e["kind"] for e in lead["timeline"]}
    row = store.read("outbox")[-1]
    assert row["opens"] == 1 and row["clicks"] == 1 and row["clicked_urls"] == ["https://example.com/demo"]
    assert any(n["kind"] == "alert:click" for n in store.read("notifications"))


def test_bad_tokens_and_forged_links_do_nothing(tmp_path):
    c, store, ids = make(tmp_path)
    sent_row(store, ids["Acme"])
    token = tracking.make_token(store, "k1")
    assert c.get("/api/track/o/garbage.gif").status_code == 200
    r = c.get(f"/api/track/c/{token}", params={"u": "https://evil.example", "s": "deadbeef"}, follow_redirects=False)
    assert r.status_code == 302 and "evil" not in r.headers["location"]
    lead = next(l for l in store.read("leads") if l["id"] == ids["Acme"])
    assert lead["engagement"]["clicks"] == 0


def test_scanner_opens_right_after_sending_are_ignored(tmp_path):
    c, store, ids = make(tmp_path)
    sent_row(store, ids["Acme"], minutes_ago=0)
    assert tracking.record(store, "k1", "open") is None


def test_reply_and_yes_and_call_raise_alerts_once(tmp_path):
    c, store, ids = make(tmp_path)
    store.update("settings", lambda s: {**s, "alert_managers": ["Boss"]})
    c.post(f"/api/leads/{ids['Acme']}/reply", json={"text": "Can you share pricing?"})
    kinds = [(n["kind"], n["to"]) for n in store.read("notifications")]
    assert ("alert:reply", "Sales team") in kinds and ("alert:reply", "Boss") not in kinds
    c.post(f"/api/leads/{ids['Acme']}/reply", json={"text": "Another question please?"})
    assert sum(1 for n in store.read("notifications") if n["kind"] == "alert:reply") == 1     # not repeated within 10 minutes
    c.post(f"/api/leads/{ids['Beta Ltd']}/call-outcome", json={"outcome": "interested", "notes": "wants a demo"})
    to = {n["to"] for n in store.read("notifications") if n["kind"] == "alert:call_interested"}
    assert to == {"Sales team", "Boss"}


def test_response_times_and_breach_alerts(tmp_path):
    c, store, ids = make(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat(timespec="seconds")
    store.update("leads", lambda rows: [{**l, "created_at": old} for l in rows])
    c.post(f"/api/leads/{ids['Acme']}/call-outcome", json={"outcome": "no_answer"})
    earlier = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat(timespec="seconds")
    store.update("activities", lambda rows: [{**a, "at": earlier} for a in rows])          # the call came before the reply
    store.update_lead(ids["Acme"], lambda l: {**l, "timeline": [{**e, "at": earlier} if e.get("kind") == "call" else e for e in l["timeline"]]})
    replied = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(timespec="seconds")
    store.update_lead(ids["Acme"], lambda l: {**l, "timeline": [*l["timeline"], {"at": replied, "kind": "reply", "text": "[question] (email, pasted) price?"}]})
    data = c.get("/api/reports/sla", params={"days": 7}).json()
    assert data["first_touch"]["measured"] == 1 and data["first_touch"]["breaches"] == 1
    assert data["untouched"][0]["company"] == "Beta Ltd"
    assert data["reply"]["breaches"] == 1 and data["waiting"][0]["company"] == "Acme"
    assert sla.check(store) == 2 and sla.check(store) == 0                     # one alert per breach
    assert sum(1 for n in store.read("notifications") if n["kind"] == "alert:sla") == 2
    assert c.put("/api/reports/sla-targets", json={"first_touch_hours": 48, "reply_hours": 2}).json()["first_touch_hours"] == 48
    assert c.get("/api/reports/sla", params={"days": 7}).json()["first_touch"]["breaches"] == 0
