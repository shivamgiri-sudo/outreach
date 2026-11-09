"""Full backup and restore: header, secrets, dry run, atomic swap, pre-restore copy, sending off, refusals, CLI."""
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import backup, sequence_runner

from .export_fakes import SECRETS, make_client, seed

ROOT = Path(__file__).resolve().parent.parent
SAME_AFTER_RESTORE = ("leads", "outbox", "inbox", "whatsapp_outbox", "transactional_outbox", "tasks", "activities",
                      "campaigns", "suppressed")


@pytest.fixture()
def seeded(tmp_path):
    client, store = make_client(tmp_path)
    seed(store)
    return client, store


def unpack(response) -> dict:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/gzip"
    return json.loads(gzip.decompress(response.content))


def test_backup_header_counts_and_name(seeded, monkeypatch):
    client, store = seeded
    monkeypatch.setenv("OUTREACH_COMMIT", "abc1234")
    r = client.get("/api/backup")
    payload = unpack(r)
    assert (payload["format"], payload["version"], payload["app_commit"]) == ("outreach-os-backup", 1, "abc1234")
    assert payload["created_at"].endswith("+00:00") and payload["created_at_ist"].endswith("IST")
    assert payload["counts"]["leads"] == 4 and payload["counts"]["outbox"] == 3 and payload["counts"]["campaigns"] == 1
    assert payload["data"]["leads"] == store.read("leads")
    assert 'filename="outreach-backup-' in r.headers["content-disposition"] and "WITH-SECRETS" not in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"


def test_secrets_are_stripped_by_default(seeded):
    client, _ = seeded
    payload = unpack(client.get("/api/backup?include_secrets=0"))
    text = json.dumps(payload)
    integration_secrets = SECRETS[:7]
    assert not [s for s in integration_secrets if s in text]
    assert payload["includes_secrets"] is False and "enter the connections again" in payload["note"]
    data = payload["data"]
    assert "server_secret" not in data["settings"] and "webhook_secret" not in data["settings"]
    assert data["settings"]["caller_name"] == "Priya"
    sending = data["integrations"]["sending_domain"]
    assert sending["mode"] == "api" and sending["values"]["domain"] == "mail.bmi.in" and "api_key" not in sending["values"]
    assert "tokens" not in data["integrations"]["calendar"]["providers"]["google"]
    assert "client_secret" not in data["integrations"]["calendar"]["providers"]["google"]["values"]
    assert "webhook_tokens" not in data["integrations"]["calendar"]
    assert data["integrations"]["sms"]["values"]["headers"] == [{"key": "Authorization", "value": "", "secret": True}]
    assert "password" not in data["integrations"]["inbound_email"]["providers"]["imap"]["values"]


def test_backup_with_secrets_keeps_them(seeded):
    client, store = seeded
    r = client.get("/api/backup?include_secrets=1")
    payload = unpack(r)
    assert payload["includes_secrets"] is True and "plain text" in payload["note"]
    assert payload["data"]["settings"] == store.read("settings") and payload["data"]["integrations"] == store.read("integrations")
    assert "WITH-SECRETS" in r.headers["content-disposition"]


def test_round_trip_dry_run_then_confirm(seeded, tmp_path):
    client, store = seeded
    original = store.snapshot()
    file = client.get("/api/backup").content
    store.update("leads", lambda leads: leads[:1])
    store.update("outbox", lambda rows: [])
    store.update("settings", lambda s: {**s, "live_sending": True, "live_sms": True})

    plan = client.post("/api/backup/restore", content=file, headers={"Content-Type": "application/gzip"})
    assert plan.status_code == 200 and plan.json()["dry_run"] is True
    body = plan.json()
    collections = {c["name"]: c for c in body["collections"]}
    assert (collections["leads"]["now"], collections["leads"]["after"]) == (1, 4)
    assert (collections["outbox"]["now"], collections["outbox"]["after"]) == (0, 3)
    assert "Nothing has been changed yet" in body["message"] and any("OFF" in n for n in body["notes"])
    assert len(store.read("leads")) == 1                                            # the dry run changed nothing
    assert not list(tmp_path.glob("store.json.pre-restore-*"))

    done = client.post("/api/backup/restore?confirm=1", content=file)
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["restored"] is True and result["live_sending"] is False and "OFF" in result["message"]
    for key in SAME_AFTER_RESTORE:
        assert store.read(key) == original[key], key
    settings = store.read("settings")
    assert settings["live_sending"] is False and settings["live_whatsapp"] is False and settings["live_sms"] is False
    assert settings["server_secret"] == SECRETS[2]                                  # this server keeps its own
    assert "webhook_secret" not in settings and settings["caller_name"] == "Priya"
    assert "api_key" not in store.read("integrations")["sending_domain"]["values"]
    copies = list(tmp_path.glob("store.json.pre-restore-*"))
    assert [c.name for c in copies] == [result["pre_restore_copy"]]
    kept = json.loads(copies[0].read_text(encoding="utf-8"))
    assert len(kept["leads"]) == 1 and kept["settings"]["live_sending"] is True    # the state just before the restore
    on_disk = json.loads((tmp_path / "store.json").read_text(encoding="utf-8"))
    assert len(on_disk["leads"]) == 4 and on_disk["settings"]["live_sending"] is False
    assert client.get("/api/sending/status").json()["mode"] == "practice"


def test_dry_run_flag_wins_over_confirm(seeded):
    client, store = seeded
    file = client.get("/api/backup").content
    store.update("leads", lambda leads: [])
    assert client.post("/api/backup/restore?dry_run=1&confirm=1", content=file).json()["dry_run"] is True
    assert store.read("leads") == []
    assert client.post("/api/backup/restore?dry_run=0&confirm=1", content=file).json()["restored"] is True
    assert len(store.read("leads")) == 4


def test_secrets_backup_from_another_server_restores_without_secrets(tmp_path):
    source_client, source = make_client(tmp_path / "a")
    seed(source)
    file = source_client.get("/api/backup?include_secrets=1").content
    client, store = make_client(tmp_path / "b")
    store.write("settings", {"server_secret": "server-B"})
    plan = client.post("/api/backup/restore", content=file).json()          # signed by server A, not this one
    assert plan["backup"]["includes_secrets"] is False and any("not signed" in n for n in plan["notes"])
    done = client.post("/api/backup/restore?confirm=1&keep_secrets=1", content=file).json()
    assert done["server_secret"] == "kept from this server" and store.read("settings")["server_secret"] == "server-B"
    assert "api_key" not in store.read("integrations")["sending_domain"]["values"]
    assert "webhook_secret" not in store.read("settings") and store.read("settings")["live_sending"] is False


def test_secrets_backup_from_this_server_restores_keys_but_never_the_server_secret(tmp_path):
    client, store = make_client(tmp_path)
    seed(store)
    file = client.get("/api/backup?include_secrets=1").content
    store.update("settings", lambda s: {**s, "webhook_secret": "changed"})
    store.update("integrations", lambda i: {**i, "sending_domain": {"values": {}}})
    plan = client.post("/api/backup/restore", content=file).json()
    assert plan["backup"]["includes_secrets"] is True and plan["backup"]["signed"] is True
    done = client.post("/api/backup/restore?confirm=1&keep_secrets=1", content=file).json()
    assert done["server_secret"] == "kept from this server" and store.read("settings")["server_secret"] == SECRETS[2]
    assert store.read("integrations")["sending_domain"]["values"]["api_key"] == SECRETS[0]
    assert store.read("settings")["webhook_secret"] == SECRETS[1]


def test_keep_secrets_ignored_for_a_backup_without_secrets(seeded):
    client, store = seeded
    file = client.get("/api/backup").content
    store.update("settings", lambda s: {**s, "server_secret": "fresh"})
    client.post("/api/backup/restore?confirm=1&keep_secrets=1", content=file)
    assert store.read("settings")["server_secret"] == "fresh"


def test_multipart_upload_works(seeded):
    client, _ = seeded
    file = client.get("/api/backup").content
    r = client.post("/api/backup/restore", files={"file": ("outreach-backup.json.gz", file, "application/gzip")})
    assert r.status_code == 200 and r.json()["dry_run"] is True
    plain = gzip.decompress(file)
    assert client.post("/api/backup/restore", content=plain, headers={"Content-Type": "application/json"}).status_code == 200


def _gz(obj) -> bytes:
    return gzip.compress(json.dumps(obj).encode())


@pytest.mark.parametrize("raw,words", [
    (b"", "empty"),
    (b"just some text", "not readable JSON"),
    (b"\x1f\x8b\x08\x00broken", "could not be unpacked"),
    (_gz({"leads": [], "settings": {}}), "not an Outreach OS backup"),                       # a raw store.json
    (_gz({"format": "other-app-backup", "version": 1, "data": {}}), "not an Outreach OS backup"),
    (_gz({"format": "outreach-os-backup", "version": 99, "data": {}}), "newer version"),
    (_gz({"format": "outreach-os-backup", "version": "1", "data": {}}), "version"),
    (_gz({"format": "outreach-os-backup", "version": 1}), "no data"),
    (_gz({"format": "outreach-os-backup", "version": 1, "data": {"leads": {"a": 1}}}), "wrong shape"),
    (_gz({"format": "outreach-os-backup", "version": 1, "data": {"leads": [{"company": "x"}]}}), "no id"),
    (_gz({"format": "outreach-os-backup", "version": 1, "data": {"leads": [{"id": "a", "stage": "new", "timeline": "x"}]}}), "history"),
    (_gz({"format": "outreach-os-backup", "version": 1, "data": {"leads": [{"id": "a", "timeline": []}]}}), "stage"),
    (json.dumps([1, 2]).encode(), "not an Outreach OS backup"),
])
def test_bad_or_foreign_files_are_refused(seeded, raw, words):
    client, store = seeded
    before = store.snapshot()
    for query in ("", "?confirm=1"):
        r = client.post(f"/api/backup/restore{query}", content=raw)
        assert r.status_code == 400 and words in r.json()["detail"], r.text
    assert store.snapshot() == before


def test_multipart_without_a_file_is_refused(seeded):
    client, _ = seeded
    r = client.post("/api/backup/restore", data={"note": "hi"}, files={"other": ("", b"", "text/plain")})
    assert r.status_code == 400 and "No file" in r.json()["detail"]


def test_restore_waits_for_a_running_send_and_gives_up_politely(seeded, monkeypatch):
    client, store = seeded
    file = client.get("/api/backup").content
    store.update("leads", lambda leads: [])
    monkeypatch.setattr(backup, "SEND_RUN_WAIT_SECONDS", 0.05)
    assert sequence_runner._RUN_LOCK.acquire(blocking=False)
    try:
        r = client.post("/api/backup/restore?confirm=1", content=file)
    finally:
        sequence_runner._RUN_LOCK.release()
    assert r.status_code == 409 and "being sent" in r.json()["detail"]
    assert store.read("leads") == []
    assert client.post("/api/backup/restore?confirm=1", content=file).status_code == 200


def test_unknown_collections_from_a_newer_app_are_kept(seeded):
    client, store = seeded
    payload = json.loads(gzip.decompress(client.get("/api/backup").content))
    payload["data"]["users"] = [{"name": "Owner", "role": "owner", "password_hash": "x"}]
    r = client.post("/api/backup/restore?confirm=1", content=_gz(payload))
    assert r.status_code == 200 and store.read("users")[0]["name"] == "Owner"
    stripped = json.loads(gzip.decompress(client.get("/api/backup").content))
    assert stripped["data"]["users"] == [{"name": "Owner", "role": "owner"}]


def _cli(*args, cwd):
    env = {**os.environ, "OUTREACH_FSYNC": "0"}
    env.pop("OUTREACH_DATA_FILE", None)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "backup.py"), *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=60)


def test_cli_export_dry_run_and_restore(tmp_path):
    _, store = make_client(tmp_path)
    seed(store)
    data_file, out = tmp_path / "store.json", tmp_path / "b.json.gz"
    made = _cli("export", "--data-file", str(data_file), "--out", str(out), cwd=tmp_path)
    assert made.returncode == 0, made.stderr
    assert "leads 4" in made.stdout and "left out" in made.stdout
    payload = json.loads(gzip.decompress(out.read_bytes()))
    assert payload["format"] == "outreach-os-backup" and SECRETS[0] not in json.dumps(payload)

    store.update("leads", lambda leads: [])
    dry = _cli("restore", "--data-file", str(data_file), "--file", str(out), cwd=tmp_path)
    assert dry.returncode == 0 and "Nothing changed" in dry.stdout and "leads" in dry.stdout
    assert json.loads(data_file.read_text(encoding="utf-8"))["leads"] == []

    done = _cli("--data-file", str(data_file), "restore", "--file", str(out), "--confirm", cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert "OFF" in done.stdout
    saved = json.loads(data_file.read_text(encoding="utf-8"))
    assert len(saved["leads"]) == 4 and saved["settings"]["live_sending"] is False
    assert saved["settings"]["server_secret"] == SECRETS[2]
    assert list(tmp_path.glob("store.json.pre-restore-*"))

    secret = _cli("export", "--data-file", str(data_file), "--out", str(tmp_path / "s.json.gz"), "--include-secrets", cwd=tmp_path)
    assert secret.returncode == 0 and "plain text" in secret.stdout

    (tmp_path / "bad.json").write_text('{"leads": []}', encoding="utf-8")
    bad = _cli("restore", "--data-file", str(data_file), "--file", str(tmp_path / "bad.json"), "--confirm", cwd=tmp_path)
    assert bad.returncode != 0 and "not an Outreach OS backup" in bad.stderr
    missing = _cli("export", "--data-file", str(tmp_path / "nope.json"), "--out", str(out), cwd=tmp_path)
    assert missing.returncode != 0 and "No data file" in missing.stderr
