"""Sign in (and sign up) with Google or Microsoft: OpenID Connect, authorization code + PKCE, state + nonce.

Configured on the server only (a button appears only for a configured provider):
  OUTREACH_GOOGLE_CLIENT_ID, OUTREACH_GOOGLE_CLIENT_SECRET
  OUTREACH_MICROSOFT_CLIENT_ID, OUTREACH_MICROSOFT_CLIENT_SECRET, OUTREACH_MICROSOFT_TENANT (default "organizations")
  The redirect URI to register with the provider: <public base URL>/oidc/callback
Flow: start() keeps {state hash, nonce, PKCE verifier, provider, intent} server-side for STATE_MINUTES (single use)
and returns the provider's authorize URL; the browser also keeps the state and checks it on return. finish() swaps
the code (with the verifier and client secret) for an ID token and verifies it: RS256 signature against the provider's
JWKS (cached JWKS_SECONDS, fetched again once for an unknown key id), issuer, audience, expiry, nonce, and a verified
email (Google: email_verified; Microsoft: email_verified or xms_edov, the "email domain owner verified" optional
claim, which must be switched on in the app registration). The verified email links to an existing login only by
that exact email; once linked, the provider's subject id is kept and must match next time.
HTTP goes through HTTP (stdlib urllib; tests replace it with a fake provider).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

STATE_MINUTES = 10
JWKS_SECONDS = 3600
SKEW = 120
KEY = "oidc_pending"
_JWKS: dict = {}


def _http(method: str, url: str, form: Optional[dict] = None) -> dict:
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Accept": "application/json",
                                          **({"Content-Type": "application/x-www-form-urlencoded"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"The sign-in provider refused the request (HTTP {exc.code}).") from None
    except (OSError, ValueError):
        raise ValueError("Could not reach the sign-in provider. Try again.") from None


HTTP = _http


def providers() -> dict:
    """{name: config} for every configured provider."""
    out = {}
    gid, gsec = os.environ.get("OUTREACH_GOOGLE_CLIENT_ID", "").strip(), os.environ.get("OUTREACH_GOOGLE_CLIENT_SECRET", "").strip()
    if gid and gsec:
        out["google"] = {"title": "Google", "client_id": gid, "client_secret": gsec,
                         "authorize": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
                         "jwks": "https://www.googleapis.com/oauth2/v3/certs",
                         "issuers": ("https://accounts.google.com", "accounts.google.com")}
    mid, msec = os.environ.get("OUTREACH_MICROSOFT_CLIENT_ID", "").strip(), os.environ.get("OUTREACH_MICROSOFT_CLIENT_SECRET", "").strip()
    if mid and msec:
        tenant = os.environ.get("OUTREACH_MICROSOFT_TENANT", "").strip() or "organizations"
        base = f"https://login.microsoftonline.com/{urllib.parse.quote(tenant)}"
        out["microsoft"] = {"title": "Microsoft", "client_id": mid, "client_secret": msec, "tenant": tenant,
                            "authorize": f"{base}/oauth2/v2.0/authorize", "token": f"{base}/oauth2/v2.0/token",
                            "jwks": f"{base}/discovery/v2.0/keys", "issuers": ("microsoft",)}
    return out


def public_list() -> list:
    return [{"id": k, "title": v["title"]} for k, v in providers().items()]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(str(text) + "=" * (-len(str(text)) % 4))


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def redirect_uri(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/oidc/callback"


def start(store, provider: str, intent: str, base_url: str, now: Optional[float] = None) -> dict:
    cfg = providers().get(provider)
    if not cfg:
        raise LookupError("This sign-in option is not set up.")
    moment = int(time.time() if now is None else now)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    row = {"state": _h(state), "nonce": nonce, "verifier": verifier, "provider": provider,
           "intent": "signup" if intent == "signup" else "login", "exp": moment + STATE_MINUTES * 60}
    store.update("settings", lambda s: {**s, KEY: [*[r for r in s.get(KEY) or [] if r.get("exp", 0) > moment][-200:], row]})
    query = {"client_id": cfg["client_id"], "response_type": "code", "scope": "openid email profile",
             "redirect_uri": redirect_uri(base_url), "state": state, "nonce": nonce,
             "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256",
             "prompt": "select_account"}
    return {"url": f"{cfg['authorize']}?{urllib.parse.urlencode(query)}", "state": state}


def _take_state(store, state: str, now: float) -> Optional[dict]:
    wanted, found = _h(state or ""), {}

    def apply(s):
        rows = []
        for r in s.get(KEY) or []:
            if hmac.compare_digest(r.get("state", ""), wanted) and r.get("exp", 0) > now and not found:
                found.update(r)
                continue                      # single use: gone the moment it is read
            if r.get("exp", 0) > now:
                rows.append(r)
        return {**s, KEY: rows}
    store.update("settings", apply)
    return found or None


def _jwks(cfg: dict, refresh: bool = False, now: Optional[float] = None) -> list:
    moment = time.time() if now is None else now
    hit = _JWKS.get(cfg["jwks"])
    if hit and not refresh and moment - hit[1] < JWKS_SECONDS:
        return hit[0]
    keys = (HTTP("GET", cfg["jwks"]) or {}).get("keys") or []
    _JWKS[cfg["jwks"]] = (keys, moment)
    return keys


def _verify_signature(cfg: dict, token: str, now: float) -> dict:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
        header, claims = json.loads(_unb64(head_b64)), json.loads(_unb64(body_b64))
    except (ValueError, TypeError):
        raise ValueError("The sign-in provider sent an unreadable ID token.") from None
    if header.get("alg") != "RS256":
        raise ValueError("The ID token is not signed the way we accept (RS256).")
    kid = header.get("kid")
    key = next((k for k in _jwks(cfg, now=now) if k.get("kid") == kid), None) or \
        next((k for k in _jwks(cfg, refresh=True, now=now) if k.get("kid") == kid), None)
    if not key or key.get("kty") != "RSA":
        raise ValueError("The ID token was signed with a key the provider does not publish.")
    public = rsa.RSAPublicNumbers(int.from_bytes(_unb64(key["e"]), "big"), int.from_bytes(_unb64(key["n"]), "big")).public_key()
    try:
        public.verify(_unb64(sig_b64), f"{head_b64}.{body_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise ValueError("The ID token signature is not valid.") from None
    return claims


def _issuer_ok(cfg: dict, claims: dict) -> bool:
    iss = str(claims.get("iss") or "")
    if cfg["issuers"] == ("microsoft",):
        tid = str(claims.get("tid") or "")
        tenant = cfg.get("tenant", "")
        fixed = tenant not in ("common", "organizations", "consumers")
        return bool(tid) and iss == f"https://login.microsoftonline.com/{tid}/v2.0" and (not fixed or tid == tenant or tenant.endswith(".onmicrosoft.com"))
    return iss in cfg["issuers"]


def check_claims(cfg: dict, claims: dict, nonce: str, now: float) -> dict:
    if not _issuer_ok(cfg, claims):
        raise ValueError("The ID token comes from an unexpected issuer.")
    aud = claims.get("aud")
    if (aud != cfg["client_id"]) and not (isinstance(aud, list) and cfg["client_id"] in aud):
        raise ValueError("The ID token was made for another app.")
    try:
        if int(claims["exp"]) < now - SKEW or int(claims.get("iat", now)) > now + SKEW:
            raise ValueError("The ID token has expired. Sign in again.")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(str(exc) if "expired" in str(exc) else "The ID token has no valid expiry.") from None
    if not nonce or not hmac.compare_digest(str(claims.get("nonce") or ""), nonce):
        raise ValueError("The sign-in did not match this browser (nonce). Start again.")
    email = str(claims.get("email") or "").strip().lower()
    verified = claims.get("email_verified") in (True, "true") or claims.get("xms_edov") in (True, "true", 1, "1")
    if not email or not verified:
        raise ValueError("The provider did not confirm that this email address belongs to you, so it cannot be used to sign in.")
    return {"email": email, "name": str(claims.get("name") or "")[:80], "sub": str(claims.get("sub") or "")}


def finish(store, code: str, state: str, base_url: str, now: Optional[float] = None) -> dict:
    """{provider, intent, email, name, sub} from a verified ID token. Raises ValueError with a plain message."""
    moment = time.time() if now is None else now
    pending = _take_state(store, state, moment)
    if not pending:
        raise ValueError("This sign-in has expired or was already used. Start again.")
    cfg = providers().get(pending["provider"])
    if not cfg or not code:
        raise ValueError("This sign-in option is not set up.")
    reply = HTTP("POST", cfg["token"], {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(base_url),
                                         "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                                         "code_verifier": pending["verifier"]})
    id_token = str((reply or {}).get("id_token") or "")
    if not id_token:
        raise ValueError("The sign-in provider did not send an ID token.")
    claims = _verify_signature(cfg, id_token, moment)
    who = check_claims(cfg, claims, pending["nonce"], moment)
    return {"provider": pending["provider"], "intent": pending["intent"], **who}
