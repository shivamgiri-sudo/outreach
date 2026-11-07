"""Auth gate, roles, token rotation, secrets at rest and rate limits for the lead-capture addresses."""
import json

from fastapi.testclient import TestClient

from app.main import create_app

from .test_security_auth import ADMIN, add_user, bearer, login

GENERIC = {"name": "Asha", "email": "asha@acme.in", "phone": "9810012345"}


def secured(tmp_path, **kwargs):
    app = create_app(data_file=tmp_path / "d.json", admin_password=ADMIN, **kwargs)
    c = TestClient(app, client=("203.0.113.9", 50000))
    return c, app.state.store, login(c)


def make(c, owner, kind="generic", **extra):
    r = c.post("/api/capture/connectors", json={"type": kind, "name": "Conn", **extra}, headers=bearer(owner))
    assert r.status_code == 200, r.text
    return r.json()


def test_capture_addresses_are_public_but_need_their_proof(tmp_path):
    c, store, owner = secured(tmp_path)
    conn = make(c, owner)
    assert c.post(f"/api/inbound/{conn['id']}", json=GENERIC).status_code == 401           # no login, no token
    assert c.post(f"/api/inbound/{conn['id']}", json=GENERIC, headers={"X-Capture-Token": "nope"}).status_code == 401
    assert c.post(f"/api/inbound/{conn['id']}?token={conn['token']}", json=GENERIC).status_code == 200      # no login needed with the token
    assert c.post(f"/api/inbound/{conn['id']}", json={**GENERIC, "email": "b@acme.in"}, headers={"X-Capture-Token": conn["token"]}).status_code == 200


def test_everything_else_under_inbound_needs_a_login_and_the_owner_role(tmp_path):
    c, store, owner = secured(tmp_path)
    conn = make(c, owner)
    add_user(c, owner, "cal@example.com", "caller")
    add_user(c, owner, "vie@example.com", "viewer")
    caller, viewer = login(c, "long-password-1", "cal@example.com"), login(c, "long-password-1", "vie@example.com")
    for method, path in (("get", "/api/inbound/log"), ("post", f"/api/inbound/{conn['id']}/test"), ("post", f"/api/inbound/{conn['id']}/sync"),
                         ("get", "/api/capture/connectors"), ("get", "/api/capture/catalogue"), ("post", "/api/capture/connectors"),
                         ("put", f"/api/capture/connectors/{conn['id']}"), ("delete", f"/api/capture/connectors/{conn['id']}"),
                         ("post", f"/api/capture/connectors/{conn['id']}/rotate-token")):
        assert getattr(c, method)(path).status_code == 401, path
        for token in (caller, viewer):
            assert getattr(c, method)(path, headers=bearer(token)).status_code == 403, (method, path)
    assert c.get("/api/inbound/log", headers=bearer(owner)).status_code == 200
    assert c.get("/api/lead-sources", headers=bearer(viewer)).status_code == 200            # the source list is plain reading
    assert c.post(f"/api/inbound/{conn['id']}/embed.js").status_code in (401, 405)          # wrong method on the public script
    # the capture token does not open the owner screens
    assert c.get("/api/inbound/log", headers={"X-Capture-Token": conn["token"]}).status_code == 401
    assert c.get("/api/capture/connectors", headers={"X-Capture-Token": conn["token"]}).status_code == 401


def test_rotating_the_token_cuts_off_the_old_one(tmp_path):
    c, store, owner = secured(tmp_path)
    conn = make(c, owner)
    old = conn["token"]
    rotated = c.post(f"/api/capture/connectors/{conn['id']}/rotate-token", headers=bearer(owner)).json()
    assert rotated["token"] and rotated["token"] != old
    assert c.post(f"/api/inbound/{conn['id']}", json=GENERIC, headers={"X-Capture-Token": old}).status_code == 401
    assert c.post(f"/api/inbound/{conn['id']}", json=GENERIC, headers={"X-Capture-Token": rotated["token"]}).status_code == 200
    later = c.get(f"/api/capture/connectors/{conn['id']}", headers=bearer(owner)).json()
    assert "token" not in later and later["secrets"]["capture"].startswith("••••") and rotated["token"] not in json.dumps(later)


def test_meta_verify_token_rotates_separately(tmp_path):
    c, store, owner = secured(tmp_path)
    conn = make(c, owner, "meta_ads")
    old = conn["verify_token"]
    new = c.post(f"/api/capture/connectors/{conn['id']}/rotate-token", json={"which": "verify"}, headers=bearer(owner)).json()
    assert new["verify_token"] != old
    q = lambda t: {"hub.mode": "subscribe", "hub.verify_token": t, "hub.challenge": "9"}
    assert c.get(f"/api/inbound/meta/{conn['id']}", params=q(old)).status_code == 401
    assert c.get(f"/api/inbound/meta/{conn['id']}", params=q(new["verify_token"])).text == "9"
    assert c.post(f"/api/capture/connectors/{conn['id']}/rotate-token", json={"which": "nonsense"}, headers=bearer(owner)).status_code == 400


def test_secrets_are_masked_in_every_read_and_encrypted_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_SECRET_KEY", "a-long-random-key-for-tests-1234567890")
    c, store, owner = secured(tmp_path)
    conn = make(c, owner, "meta_ads", app_secret="APP-SECRET-VALUE", page_access_token="PAGE-TOKEN-VALUE")
    assert conn["secrets"]["app_secret"] == "••••ALUE" and conn["secrets"]["page_access_token"] == "••••ALUE"
    listing = json.dumps(c.get("/api/capture/connectors", headers=bearer(owner)).json())
    assert "APP-SECRET-VALUE" not in listing and "PAGE-TOKEN-VALUE" not in listing and conn["token"] not in listing
    assert "APP-SECRET-VALUE" not in json.dumps(c.get("/api/integrations", headers=bearer(owner)).json())
    on_disk = (tmp_path / "d.json").read_text()
    for secret in ("APP-SECRET-VALUE", "PAGE-TOKEN-VALUE", conn["token"], conn["verify_token"]):
        assert secret not in on_disk
    assert "enc:v1:" in on_disk
    # saving the masked value back does not overwrite the real one
    c.put(f"/api/capture/connectors/{conn['id']}", json={"app_secret": "••••ALUE", "name": "Renamed"}, headers=bearer(owner))
    assert store.read("integrations")["lead_capture"]["connectors"][0]["tokens"]["app_secret"] == "APP-SECRET-VALUE"
    # an install restart reads them back
    again = create_app(data_file=tmp_path / "d.json", admin_password=ADMIN).state.store
    assert again.read("integrations")["lead_capture"]["connectors"][0]["tokens"]["capture"] == conn["token"]


def test_connector_validation(tmp_path):
    c, store, owner = secured(tmp_path)
    h = bearer(owner)
    assert c.post("/api/capture/connectors", json={"type": "nope"}, headers=h).status_code == 400
    assert c.post("/api/capture/connectors", json={"type": "website_form", "allowed_origins": ["not a url"]}, headers=h).status_code == 400
    assert c.post("/api/capture/connectors", json={"type": "sheet_sync", "sheet_url": "http://plain.example/x.csv"}, headers=h).status_code == 400
    assert c.put("/api/capture/connectors/capdeadbeef", json={}, headers=h).status_code == 404
    cat = c.get("/api/capture/catalogue", headers=h).json()
    assert {t["id"] for t in cat["types"]} >= {"generic", "website_form", "meta_ads", "google_ads", "whatsapp_click", "sheet_sync",
                                               "linkedin_leadgen", "indiamart", "justdial", "tradeindia", "tiktok_ads"}
    assert all(t["steps"] and t["title"] for t in cat["types"]) and cat["base_url"].startswith("http")
    conn = make(c, owner, name="To delete")
    assert c.delete(f"/api/capture/connectors/{conn['id']}", headers=h).json()["ok"]
    assert c.get("/api/capture/connectors", headers=h).json() == []
    assert c.post(f"/api/inbound/{conn['id']}", json=GENERIC, headers={"X-Capture-Token": conn["token"]}).status_code == 401


def test_urls_use_the_saved_public_address(tmp_path):
    c, store, owner = secured(tmp_path)
    c.put("/api/settings", json={"public_base_url": "https://outreach.example.in"}, headers=bearer(owner))
    conn = make(c, owner)
    assert conn["urls"]["capture"] == f"https://outreach.example.in/api/inbound/{conn['id']}"
    assert c.get("/api/capture/catalogue", headers=bearer(owner)).json()["base_url"] == "https://outreach.example.in"


def test_capture_addresses_are_rate_limited_per_address(tmp_path):
    app = create_app(data_file=tmp_path / "d.json")
    one, other = TestClient(app, client=("203.0.113.1", 1)), TestClient(app, client=("203.0.113.2", 1))
    path = "/api/inbound/capdeadbeef"
    codes = [one.post(path, json={}).status_code for _ in range(120)]
    assert 429 not in codes
    blocked = one.post(path, json={})
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"
    assert other.post(path, json={}).status_code != 429
