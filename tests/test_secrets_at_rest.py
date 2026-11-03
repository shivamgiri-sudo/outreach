"""Saved keys are encrypted in store.json when OUTREACH_SECRET_KEY is set; the API still masks them."""
import json

import shutil

from app.channels import config
from app.channels.security import server_secret
from app.secretbox import PREFIX, SecretBox
from app.store import Store
from tests.channel_fakes import make_client

KEY = "a-long-random-server-key-for-tests-0123456789"
CUSTOM = {"method": "POST", "url": "https://api.sms.example/v1/send", "body_type": "json",
          "body_template": '{"to": "{{phone}}", "msg": "{{text}}"}',
          "headers": [{"key": "Authorization", "value": "Bearer custom-secret-9876", "secret": True},
                      {"key": "X-Team", "value": "bmi"}]}
SECRETS = ("ap_live_secret_123", "re_secret_value_456", "custom-secret-9876", "refresh-token-abc", "webhook-secret-xyz")


def fill(c):
    c.put("/api/integrations/apollo", json={"mode": "api", "values": {"api_key": "ap_live_secret_123"}})
    r = c.put("/api/channels/email_send/resend", json={"values": {"api_key": "re_secret_value_456", "from_email": "priya@mail.example.in",
                                                                  "from_name": "Priya", "domain": "mail.example.in"}, "make_active": True})
    assert r.status_code == 200, r.text
    assert c.put("/api/channels/sms/custom_http", json={"values": CUSTOM}).status_code == 200
    store = c.app.state.store
    config.save_provider(store, "calendar", "google", {"client_id": "cid", "client_secret": "csecret-1"}, resolver=lambda h: ["93.184.216.34"])
    config.set_tokens(store, "calendar", "google", {"refresh_token": "refresh-token-abc", "access_token": "at-1"})
    c.put("/api/settings", json={"webhook_secret": "webhook-secret-xyz"})
    config.webhook_token(store, "whatsapp", "meta_cloud")
    server_secret(store)
    return store


def test_keys_are_encrypted_on_disk_and_plain_in_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_SECRET_KEY", KEY)
    c = make_client(tmp_path)
    store = fill(c)
    raw = (tmp_path / "d.json").read_text()
    for secret in (*SECRETS, store.read("settings")["server_secret"]):
        assert secret not in raw, secret
    assert raw.count(PREFIX) >= 8 and '"X-Team"' in raw and '"bmi"' in raw          # non-secret values stay readable
    assert store.read("integrations")["apollo"]["values"]["api_key"] == "ap_live_secret_123"
    again = Store(tmp_path / "d.json")                                                 # restart: same key opens them
    assert again.read("integrations")["calendar"]["providers"]["google"]["tokens"]["refresh_token"] == "refresh-token-abc"
    assert again.read("settings")["webhook_secret"] == "webhook-secret-xyz" and again.secret_errors == 0
    shown = c.get("/api/integrations").text + c.get("/api/channels").text
    for secret in SECRETS:
        assert secret not in shown and PREFIX not in shown
    assert "••••_123" in shown and "••••9876" in shown


def test_unchanged_keys_keep_their_ciphertext_between_saves(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_SECRET_KEY", KEY)
    c = make_client(tmp_path)
    fill(c)
    before = json.loads((tmp_path / "d.json").read_text())["integrations"]["apollo"]["values"]["api_key"]
    c.post("/api/stories", json={"title": "x"})
    after = json.loads((tmp_path / "d.json").read_text())["integrations"]["apollo"]["values"]["api_key"]
    assert before == after and before.startswith(PREFIX)


def test_plain_keys_are_migrated_on_first_start_with_a_key_backups_too(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTREACH_SECRET_KEY", raising=False)
    c = make_client(tmp_path)
    fill(c)
    path = tmp_path / "d.json"
    assert "ap_live_secret_123" in path.read_text()
    shutil.copy(path, tmp_path / "d.json.2")                       # an older backup with the key in plain text
    monkeypatch.setenv("OUTREACH_SECRET_KEY", KEY)
    store = Store(path)
    assert "ap_live_secret_123" not in path.read_text() and PREFIX in path.read_text()
    for backup in tmp_path.glob("d.json.*"):
        assert "ap_live_secret_123" not in backup.read_text(), backup
    assert store.read("integrations")["apollo"]["values"]["api_key"] == "ap_live_secret_123"


def test_missing_or_wrong_key_keeps_the_ciphertext_and_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_SECRET_KEY", KEY)
    fill(make_client(tmp_path))
    sealed = json.loads((tmp_path / "d.json").read_text())["integrations"]["apollo"]["values"]["api_key"]
    for env in (None, "the-wrong-key-entirely-000000000000"):
        if env is None:
            monkeypatch.delenv("OUTREACH_SECRET_KEY")
        else:
            monkeypatch.setenv("OUTREACH_SECRET_KEY", env)
        c = make_client(tmp_path)
        store = c.app.state.store
        assert store.secret_errors > 0
        assert store.read("integrations")["apollo"]["values"]["api_key"] == sealed       # never lost
        c.post("/api/stories", json={"title": "save something"})
        assert json.loads((tmp_path / "d.json").read_text())["integrations"]["apollo"]["values"]["api_key"] == sealed
        item = next(i for i in c.get("/api/readiness").json()["items"] if i["id"] == "secret_key")
        assert item["status"] != "done" and "missing or wrong" in item["detail"]


def test_without_a_key_setup_suggests_setting_one(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTREACH_SECRET_KEY", raising=False)
    c = make_client(tmp_path)
    item = next(i for i in c.get("/api/readiness").json()["items"] if i["id"] == "secret_key")
    assert item["status"] == "optional" and item["detail"] == "Set OUTREACH_SECRET_KEY to encrypt saved keys."
    monkeypatch.setenv("OUTREACH_SECRET_KEY", KEY)
    c2 = make_client(tmp_path / "x")
    assert next(i for i in c2.get("/api/readiness").json()["items"] if i["id"] == "secret_key")["status"] == "done"


def test_box_round_trip_and_fernet_keys():
    from cryptography.fernet import Fernet
    for key in (KEY, Fernet.generate_key().decode()):
        box = SecretBox(key)
        data = {"integrations": {"x": {"values": {"api_key": "k1", "from_email": "a@b.in"}, "tokens": {"access_token": "t1"},
                                       "webhook_tokens": {"meta": "w1"}}},
                "settings": {"server_secret": "s1", "daily_cap": 20}}
        sealed = box.seal(data)
        assert sealed["integrations"]["x"]["values"]["from_email"] == "a@b.in" and sealed["settings"]["daily_cap"] == 20
        assert all(v.startswith(PREFIX) for v in (sealed["integrations"]["x"]["values"]["api_key"], sealed["settings"]["server_secret"],
                                                   sealed["integrations"]["x"]["tokens"]["access_token"],
                                                   sealed["integrations"]["x"]["webhook_tokens"]["meta"]))
        assert SecretBox(key).unseal(sealed) == data
        assert box.plain_secrets(data) == 4 and box.plain_secrets(sealed) == 0
