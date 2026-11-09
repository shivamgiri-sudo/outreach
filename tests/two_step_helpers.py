"""Signing in owners and super admins, who must use two-step login (app/super_admin.py).

sign_in(client, email, password) returns the token: it sets two-step login up on the first sign-in (remembering the
secret per client) and types a fresh code afterwards."""
from app import totp

_SECRETS = {}


_USED = {}
_RECOVERY = {}


def _code(secret: str, used: dict, key=None) -> str:
    """A code for a step newer than the last one used (one code works only once; one step ahead is accepted).
    When both are used up within 30 seconds, one of the login's recovery codes."""
    step = max(totp.step_of(), used.get(secret, -1) + 1)
    if step > totp.step_of() + totp.DRIFT_STEPS and _RECOVERY.get(key):
        return _RECOVERY[key].pop()
    used[secret] = step
    return totp.hotp(totp._key(secret), step)


def sign_in(client, email: str, password: str) -> str:
    out = client.post("/api/login", json={"email": email, "password": password})
    assert out.status_code == 200, out.text
    body = out.json()
    if body.get("token"):
        return body["token"]
    key = (id(client.app), email.lower())
    if body["two_step"] == "setup":
        secret = client.post("/api/login/setup/start", json={"ticket": body["ticket"]}).json()["secret"]
        _SECRETS[key] = secret
        done = client.post("/api/login/setup/confirm", json={"ticket": body["ticket"], "code": _code(secret, _USED)})
        assert done.status_code == 200, done.text
        _RECOVERY[key] = list(done.json().get("recovery_codes") or [])
    else:
        done = client.post("/api/login/code", json={"ticket": body["ticket"], "code": _code(_SECRETS[key], _USED, key)})
    assert done.status_code == 200, done.text
    return done.json()["token"]


def secret_of(client, email: str) -> str:
    return _SECRETS[(id(client.app), email.lower())]


def fresh_code(client, email: str) -> str:
    """A fresh authenticator code (never a recovery code)."""
    return _code(secret_of(client, email), _USED)
