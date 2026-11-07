"""Backups and logins: password hashes and token hashes leave only with the secrets; a restore never wipes logins."""
from app import auth, backup

from .export_fakes import make_client, seed

OLD_HASH, NEW_HASH = "scrypt$16384$8$1$b2xk$b2xk", "scrypt$16384$8$1$bmV3$bmV3"


def _with_logins(store, users, **extra):
    store.update("settings", lambda s: {**s, "users": users, **extra})


def _team(store):
    return {u["email"]: u for u in store.read("settings").get("users") or []}


def test_backup_without_secrets_strips_password_and_token_hashes(tmp_path):
    _, store = make_client(tmp_path)
    seed(store)
    _with_logins(store, [{"email": "o@x.in", "name": "O", "role": "owner", "password_hash": OLD_HASH}],
                 extension_tokens=[{"id": "t1", "hash": "abc", "email": "o@x.in"}],
                 linkedin_runners=[{"id": "r1", "status": "active", "hash": "def"}],
                 linkedin_runner_pairing={"hash": "ghi", "expires_at": "2099-01-01T00:00:00+00:00"})
    payload = backup.build_backup(store, include_secrets=False)
    settings = payload["data"]["settings"]
    assert payload["includes_secrets"] is False and "password hashes" in payload["note"]
    assert settings["users"] == [{"email": "o@x.in", "name": "O", "role": "owner"}]
    assert "extension_tokens" not in settings and "linkedin_runner_pairing" not in settings and "server_secret" not in settings
    assert settings["linkedin_runners"] == [{"id": "r1", "status": "active"}]
    assert OLD_HASH not in str(payload) and "'def'" not in str(payload)


def test_backup_with_secrets_keeps_hashes_and_says_so(tmp_path):
    _, store = make_client(tmp_path)
    _with_logins(store, [{"email": "o@x.in", "role": "owner", "password_hash": OLD_HASH}],
                 extension_tokens=[{"id": "t1", "hash": "abc"}])
    payload = backup.build_backup(store, include_secrets=True)
    assert payload["includes_secrets"] is True and "password hashes" in payload["note"]
    assert payload["data"]["settings"]["users"][0]["password_hash"] == OLD_HASH
    assert payload["data"]["settings"]["extension_tokens"] == [{"id": "t1", "hash": "abc"}]


def test_restore_without_hashes_keeps_current_passwords(tmp_path):
    _, store = make_client(tmp_path)
    _with_logins(store, [{"email": "o@x.in", "name": "Owner", "role": "owner", "password_hash": OLD_HASH},
                         {"email": "c@x.in", "role": "caller", "password_hash": OLD_HASH}],
                 linkedin_runners=[{"id": "r1", "status": "active", "hash": "def"}])
    payload = backup.build_backup(store, include_secrets=False)
    payload["data"]["settings"]["users"] += [{"email": "new@x.in", "role": "viewer"}]
    payload["data"]["settings"]["users"] = [u for u in payload["data"]["settings"]["users"] if u["email"] != "c@x.in"]
    payload["data"]["settings"]["users"][0]["name"] = "Owner (from backup)"
    _with_logins(store, [{"email": "o@x.in", "name": "Owner", "role": "owner", "password_hash": NEW_HASH},
                         {"email": "c@x.in", "role": "caller", "password_hash": OLD_HASH}],
                 linkedin_runners=[{"id": "r1", "status": "active", "hash": "def"}])
    plan = backup.summary(store, payload)
    assert plan["logins"] == {"kept": ["o@x.in"], "no_password": ["new@x.in"], "only_here": ["c@x.in"]}
    assert any("keep their current password" in n and "new@x.in" in n for n in plan["notes"])
    result = backup.restore(store, payload)
    team = _team(store)
    assert team["o@x.in"]["password_hash"] == NEW_HASH and team["o@x.in"]["name"] == "Owner (from backup)"
    assert team["c@x.in"]["password_hash"] == OLD_HASH            # not in the backup: kept, nobody is locked out
    assert "password_hash" not in team["new@x.in"]                # must be given a password by the owner
    assert not auth.check_password(team["new@x.in"].get("password_hash", ""), "")
    assert store.read("settings")["linkedin_runners"][0]["hash"] == "def"
    assert result["logins"]["no_password"] == ["new@x.in"] and any("Team page" in n for n in result["notes"])


def test_restore_with_secrets_uses_backup_passwords(tmp_path):
    _, store = make_client(tmp_path)
    _with_logins(store, [{"email": "o@x.in", "role": "owner", "password_hash": OLD_HASH}])
    payload = backup.build_backup(store, include_secrets=True)
    _with_logins(store, [{"email": "o@x.in", "role": "owner", "password_hash": NEW_HASH}])
    plan = backup.summary(store, payload, keep_secrets=False)
    assert plan["logins"] is None and any("come from the backup" in n for n in plan["notes"])
    backup.restore(store, payload)
    assert _team(store)["o@x.in"]["password_hash"] == OLD_HASH
