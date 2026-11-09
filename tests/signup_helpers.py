"""Shared set-up for the sign-up and super-admin console tests: an app with a super admin, the dev outbox (emails with
their links), a fake OpenID provider (its own RSA key and JWKS; no network) and quick sign-up helpers."""
import base64
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app import oidc, signup
from app.main import create_app
from tests.two_step_helpers import sign_in

ADMIN = "admin-pass-123"
SUPER = "boss@mcn.example"
SUPER_PW = "boss-long-password-1"


def make(tmp_path, monkeypatch, ip="203.0.113.9", open_signup=True):
    outbox = tmp_path / "dev_outbox.jsonl"
    monkeypatch.setenv("OUTREACH_DEV_OUTBOX", str(outbox))
    monkeypatch.setenv("OUTREACH_SUPER_ADMIN", SUPER)
    monkeypatch.setattr(signup, "MIN_FILL_SECONDS", 0)
    app = create_app(data_file=tmp_path / "s.json", admin_password=ADMIN)
    c = TestClient(app, client=(ip, 50000))
    ha = {"Authorization": f"Bearer {c.post('/api/login', json={'password': ADMIN}).json()['token']}"}
    assert c.post("/api/users", json={"email": SUPER, "name": "Boss", "role": "owner", "password": SUPER_PW}, headers=ha).status_code == 200
    hs = {"Authorization": f"Bearer {sign_in(c, SUPER, SUPER_PW)}"}
    if open_signup:                          # public sign-up is closed by default; most tests exercise it open
        assert c.put("/api/console/signup-open", json={"on": True}, headers=hs).status_code == 200
    return c, ha, hs, outbox


def mails(outbox, to=None):
    if not outbox.exists():
        return []
    rows = [json.loads(line) for line in outbox.read_text().splitlines() if line.strip()]
    return [r for r in rows if to is None or r["to"] == to]


def link_token(outbox, to, kind="verify"):
    body = [m for m in mails(outbox, to) if f"#{kind}?token=" in m["body"]][-1]["body"]
    return re.search(rf"#{kind}\?token=([A-Za-z0-9_.\-]+)", body).group(1)


def form(c, **over):
    ticket = c.get("/api/signup/form").json()["ticket"]
    return {"company": "Delta Freight", "name": "Dev Patel", "email": "dev@deltafreight.example", "password": "delta-long-password-1",
            "phone": "+91 98100 11111", "accept": True, "ticket": ticket, **over}


def signed_up(c, outbox, **over):
    """Sign up and verify; returns (headers of the new pending admin, account id, email)."""
    body = form(c, **over)
    r = c.post("/api/signup", json=body)
    if r.status_code == 400 and "challenge" in str(r.json().get("detail")):          # a second sign-up from this address
        ch = r.json()["detail"]["challenge"]
        a, b = [int(x) for x in ch["question"].replace("What is ", "").rstrip("?").split(" + ")]
        body = {**body, "challenge_token": ch["token"], "challenge_answer": str(a + b)}
        r = c.post("/api/signup", json=body)
    assert r.status_code == 200, r.text
    v = c.post("/api/signup/verify", json={"token": link_token(outbox, body["email"])})
    assert v.status_code == 200, v.text
    me = c.get("/api/platform/status", headers={"Authorization": f"Bearer {v.json()['token']}"}).json()
    assert me["account_status"] == "pending"
    from app import auth
    user = auth.find_user(c.app.state.store, body["email"])
    return {"Authorization": f"Bearer {v.json()['token']}"}, user["account_id"], body["email"]


# ---------------------------------------------------------------- a fake OpenID provider
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class FakeProvider:
    def __init__(self, monkeypatch, client_id="gid-123"):
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.client_id = client_id
        self.claims = {}
        self.calls = []
        self.forge = False
        monkeypatch.setenv("OUTREACH_GOOGLE_CLIENT_ID", client_id)
        monkeypatch.setenv("OUTREACH_GOOGLE_CLIENT_SECRET", "gsecret")
        monkeypatch.setattr(oidc, "HTTP", self.http)
        oidc._JWKS.clear()
        self.nonce = ""

    def jwk(self):
        n = self.key.public_key().public_numbers()
        return {"kty": "RSA", "kid": "k1", "alg": "RS256", "n": _b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")),
                "e": _b64(n.e.to_bytes(3, "big"))}

    def id_token(self, **claims):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        now = int(time.time())
        body = {"iss": "https://accounts.google.com", "aud": self.client_id, "exp": now + 600, "iat": now, "nonce": self.nonce,
                "sub": "g-1", "email": "dev@deltafreight.example", "email_verified": True, "name": "Dev Patel", **claims}
        head = _b64(json.dumps({"alg": "RS256", "kid": "k1", "typ": "JWT"}).encode())
        payload = _b64(json.dumps(body).encode())
        signer = self.other if self.forge else self.key
        sig = signer.sign(f"{head}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{payload}.{_b64(sig)}"

    def http(self, method, url, form=None):
        self.calls.append((method, url, dict(form or {})))
        if url.endswith("/certs"):
            return {"keys": [self.jwk()]}
        assert form["code_verifier"] and form["client_secret"] == "gsecret"
        return {"id_token": self.id_token(**self.claims)}

    def run(self, c, intent="login", **claims):
        """start -> (provider) -> callback; returns the callback response."""
        self.claims = claims
        start = c.get(f"/api/oidc/google/start?intent={intent}").json()
        q = parse_qs(urlsplit(start["url"]).query)
        assert q["code_challenge_method"] == ["S256"] and q["state"] == [start["state"]]
        self.nonce = q["nonce"][0]
        return c.post("/api/oidc/callback", json={"code": "auth-code-1", "state": start["state"]}), start["state"]
