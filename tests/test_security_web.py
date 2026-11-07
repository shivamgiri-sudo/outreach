"""Rate limits, security headers, API docs switch and signed unsubscribe links."""
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import unsubscribe, web_security
from app.main import create_app
from app.sequences import lead_variables
from app.unsubscribe import unsub_path

CSV = "company,contact_name,email,website\nAcme Retail,Ravi Kumar,ravi@acme-retail.in,https://acme-retail.in\n"


def make(tmp_path, **kw):
    return TestClient(create_app(data_file=tmp_path / "d.json", **kw))


def lead_id(c):
    c.post("/api/leads/import", json={"csv": CSV, "source": "test"})
    return c.get("/api/leads").json()[0]["id"]


# ---------- rate limiter ----------
def test_token_bucket_refills_and_forgets_old_callers():
    now = [0.0]
    limiter = web_security.RateLimiter(2, max_keys=3, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    now[0] += 30                                   # 2 per minute = one more after 30 seconds
    assert limiter.allow("a") and not limiter.allow("a")
    for i in range(10):
        limiter.allow(f"ip{i}")
    assert len(limiter) == 3


@pytest.mark.parametrize("path,method,limit", [
    ("/u/nope.sig", "get", 30),
    ("/demo/nope", "get", 60),
    ("/api/webhooks/whatsapp/meta_cloud", "post", 120),
    ("/api/activation/event", "post", 120),
])
def test_public_routes_are_rate_limited_per_address(tmp_path, monkeypatch, path, method, limit):
    monkeypatch.setattr("app.web_security.time.monotonic", lambda: 1000.0)   # no refill mid-test on a slow machine
    app = create_app(data_file=tmp_path / "d.json")
    one, other = TestClient(app, client=("203.0.113.1", 1)), TestClient(app, client=("203.0.113.2", 1))
    codes = [getattr(one, method)(path).status_code for _ in range(limit)]
    assert 429 not in codes
    blocked = getattr(one, method)(path)
    assert blocked.status_code == 429 and blocked.text.startswith("Too many requests") and blocked.headers["retry-after"] == "60"
    assert blocked.headers["x-content-type-options"] == "nosniff"          # headers even on a 429
    assert getattr(other, method)(path).status_code != 429                   # other callers unaffected


def test_login_is_limited_to_five_a_minute(tmp_path):
    c = make(tmp_path, admin_password="admin-pass-123")
    codes = [c.post("/api/login", json={"password": "admin-pass-123"}).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]


def test_client_ip_trusts_forwarded_for_only_from_the_local_proxy():
    class Req:
        def __init__(self, peer, xff):
            self.client = type("C", (), {"host": peer})()
            self.headers = {"x-forwarded-for": xff}
    assert web_security.client_ip(Req("127.0.0.1", "6.6.6.6, 203.0.113.7")) == "203.0.113.7"   # last hop = added by Caddy
    assert web_security.client_ip(Req("127.0.0.1", "not-an-ip")) == "127.0.0.1"
    assert web_security.client_ip(Req("198.51.100.3", "203.0.113.7")) == "198.51.100.3"


# ---------- headers ----------
def test_security_headers_on_api_responses(tmp_path):
    r = make(tmp_path).get("/api/auth/status")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert r.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in r.headers


def test_hsts_only_when_https_came_through_the_trusted_proxy(tmp_path):
    app = create_app(data_file=tmp_path / "d.json")
    via_proxy = TestClient(app, client=("127.0.0.1", 1)).get("/api/auth/status", headers={"X-Forwarded-Proto": "https"})
    assert via_proxy.headers["strict-transport-security"].startswith("max-age=31536000")
    spoofed = TestClient(app, client=("203.0.113.9", 1)).get("/api/auth/status", headers={"X-Forwarded-Proto": "https"})
    assert "strict-transport-security" not in spoofed.headers
    direct = TestClient(app, base_url="https://testserver").get("/api/auth/status")
    assert "strict-transport-security" in direct.headers


def test_public_pages_get_a_page_policy_and_the_demo_beacon_a_nonce(tmp_path):
    c = make(tmp_path)
    lid = lead_id(c)
    page = c.get(unsub_path(c.app.state.store, lid))
    assert "script-src 'none'" in page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    token = c.post(f"/api/leads/{lid}/demo-link").json()["token"]
    demo = c.get(f"/demo/{token}")
    csp = demo.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert f'<script nonce="{nonce}">' in demo.text and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert c.get(f"/demo/{token}").headers["content-security-policy"] != csp      # a new nonce every time
    assert unsub_path(c.app.state.store, lid) in demo.text                         # stop link is signed too


def test_api_docs_are_off_unless_switched_on(tmp_path, monkeypatch):
    c = make(tmp_path)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404
    monkeypatch.setenv("OUTREACH_ENABLE_DOCS", "1")
    on = TestClient(create_app(data_file=tmp_path / "e.json"))
    assert on.get("/openapi.json").status_code == 200 and on.get("/docs").status_code == 200
    assert "content-security-policy" not in on.get("/docs").headers


# ---------- signed unsubscribe links ----------
def test_signed_unsubscribe_link_works_and_tampering_fails(tmp_path):
    c = make(tmp_path)
    store = c.app.state.store
    lid = lead_id(c)
    link = unsub_path(store, lid)
    assert re.fullmatch(rf"/u/{lid}\.[A-Za-z0-9_-]{{22}}", link)
    assert c.get(link[:-1] + ("A" if link[-1] != "A" else "B")).status_code == 404
    assert c.post(f"/u/{lid}.").status_code == 404
    other = "ffffffffff"
    assert c.get(f"/u/{other}.{unsubscribe.signature(store.read('settings')['server_secret'], other)}").status_code == 404
    assert c.post(link).status_code == 200
    assert c.get(f"/api/leads/{lid}").json()["stage"] == "unsubscribed"


def test_unsigned_links_are_refused_unless_a_real_email_went_out_before_signing(tmp_path):
    c = make(tmp_path)
    store = c.app.state.store
    lid = lead_id(c)
    assert c.get(f"/u/{lid}").status_code == 404                         # no older email: unsigned link is useless
    cutover = datetime.fromisoformat(store.read("settings")["unsub_signed_since"])
    sent = (cutover - timedelta(days=3)).isoformat(timespec="seconds")
    store.update("outbox", lambda rows: [*rows, {"lead_id": lid, "status": "dry_run", "sent_at": sent}])
    assert c.get(f"/u/{lid}").status_code == 404                         # practice emails were never delivered
    store.update("outbox", lambda rows: [*rows, {"lead_id": lid, "status": "sent", "sent_at": sent}])
    assert c.get(f"/u/{lid}").status_code == 200
    assert c.post(f"/u/{lid}").status_code == 200
    saved = c.get(f"/api/leads/{lid}").json()
    assert saved["stage"] == "unsubscribed" and "older unsigned link" in saved["timeline"][-1]["text"]


def test_unsigned_link_grace_ends_sixty_days_after_the_email(tmp_path):
    c = make(tmp_path)
    store = c.app.state.store
    lid = lead_id(c)
    cutover = datetime.fromisoformat(store.read("settings")["unsub_signed_since"])
    store.update("outbox", lambda rows: [*rows, {"lead_id": lid, "status": "sent",
                                                 "sent_at": (cutover - timedelta(days=1)).isoformat()}])
    assert unsubscribe.legacy_allowed(store, lid, cutover + timedelta(days=58))
    assert not unsubscribe.legacy_allowed(store, lid, cutover + timedelta(days=60))
    store.update("outbox", lambda rows: [{**r, "sent_at": (cutover + timedelta(minutes=5)).isoformat()} for r in rows])
    assert not unsubscribe.legacy_allowed(store, lid, cutover + timedelta(days=1))   # sent after signing started


def test_cutover_is_recorded_once(tmp_path):
    make(tmp_path)
    first = make(tmp_path).app.state.store.read("settings")["unsub_signed_since"]
    assert make(tmp_path).app.state.store.read("settings")["unsub_signed_since"] == first


def test_template_variable_and_practice_email_carry_the_signed_link(tmp_path):
    c = make(tmp_path)
    store = c.app.state.store
    lid = lead_id(c)
    secret = store.read("settings").get("server_secret") or unsubscribe.server_secret(store)
    assert lead_variables({"id": lid}, "Priya", "https://x.in", secret=secret)["unsub_link"] == "https://x.in" + unsub_path(store, lid)
    c.post(f"/api/leads/{lid}/enroll")
    c.post("/api/sequences/run", json={"now": "2026-11-04T05:00:00+00:00"})
    body = c.get("/api/outbox").json()[0]["body"]
    assert unsub_path(store, lid) in body


def test_unsubscribe_pages_are_readable_for_the_recipient(tmp_path):
    # QA sweep: a wrong link showed raw JSON, the page named the product instead of the company in the email footer,
    # and reopening the link after unsubscribing asked again.
    c = make(tmp_path)
    store = c.app.state.store
    store.update("settings", lambda s: {**s, "brand": {"company_name": "Mas Callnet", "product_name": "Bulk hiring and calling teams"}})
    lid = lead_id(c)
    link = unsub_path(store, lid)
    bad = c.get(link[:-1] + ("A" if link[-1] != "A" else "B"))
    assert bad.status_code == 404 and bad.headers["content-type"].startswith("text/html") and "not valid" in bad.text and "detail" not in bad.text
    assert c.post(f"/u/{lid}.").headers["content-type"].startswith("text/html")
    first = c.get(link)
    assert "Stop all emails from Mas Callnet?" in first.text and "Yes, unsubscribe me" in first.text
    assert "You are unsubscribed" in c.post(link).text
    again = c.get(link)
    assert again.status_code == 200 and "already unsubscribed" in again.text and "Yes, unsubscribe me" not in again.text
