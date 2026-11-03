"""OAuth for calendars: Google and Microsoft (authorization-code web flow), Zoom (server-to-server).

Tokens are stored server-side in the provider's record (never in `values`, never returned, never logged).
"""
import re
import time
from urllib.parse import urlencode

from .config import config_for, set_tokens
from .http import request
from .phone import basic_auth

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.freebusy"
MS_BASE = "https://login.microsoftonline.com"
MS_SCOPES = "offline_access User.Read Calendars.ReadWrite OnlineMeetings.ReadWrite"
ZOOM_TOKEN = "https://zoom.us/oauth/token"
TENANT = re.compile(r"^[A-Za-z0-9.\-]{1,100}$")
REFRESH_MARGIN_SECONDS = 120
OAUTH_PROVIDERS = ("google", "microsoft")


def redirect_uri(base_url: str, provider: str) -> str:
    return f"{base_url.rstrip('/')}/api/oauth/{provider}/callback"


def _tenant(values: dict) -> str:
    tenant = values.get("tenant") or "common"
    if not TENANT.match(tenant):
        raise ValueError("The tenant should be 'common' or your tenant ID.")
    return tenant


def authorize_url(provider: str, values: dict, redirect: str, state: str) -> str:
    if provider == "google":
        query = {"client_id": values["client_id"], "redirect_uri": redirect, "response_type": "code",
                 "scope": GOOGLE_SCOPES, "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
                 "state": state}
        return f"{GOOGLE_AUTH}?{urlencode(query)}"
    if provider == "microsoft":
        query = {"client_id": values["client_id"], "redirect_uri": redirect, "response_type": "code",
                 "response_mode": "query", "scope": MS_SCOPES, "state": state}
        return f"{MS_BASE}/{_tenant(values)}/oauth2/v2.0/authorize?{urlencode(query)}"
    raise ValueError("This provider does not use a sign-in window.")


def _token_url(provider: str, values: dict) -> str:
    return GOOGLE_TOKEN if provider == "google" else f"{MS_BASE}/{_tenant(values)}/oauth2/v2.0/token"


def _tokens(reply: dict, now: float) -> dict:
    if not reply.get("access_token"):
        raise OSError("The provider did not return an access token.")
    return {"access_token": reply["access_token"], "refresh_token": reply.get("refresh_token", ""),
            "expires_at": int(now + int(reply.get("expires_in") or 3600)), "scope": reply.get("scope", "")}


def exchange_code(provider: str, values: dict, code: str, redirect: str, transport=request, now: float = None) -> dict:
    form = {"code": code, "client_id": values["client_id"], "client_secret": values["client_secret"],
            "redirect_uri": redirect, "grant_type": "authorization_code"}
    if provider == "microsoft":
        form["scope"] = MS_SCOPES
    reply = transport(_token_url(provider, values), {"Accept": "application/json"}, None, method="POST", form=form)
    return _tokens(reply, now or time.time())


def refresh_tokens(provider: str, values: dict, refresh_token: str, transport=request, now: float = None) -> dict:
    form = {"refresh_token": refresh_token, "client_id": values["client_id"], "client_secret": values["client_secret"],
            "grant_type": "refresh_token"}
    if provider == "microsoft":
        form["scope"] = MS_SCOPES
    try:
        reply = transport(_token_url(provider, values), {"Accept": "application/json"}, None, method="POST", form=form)
    except OSError as exc:
        if "invalid_grant" in str(exc):      # refresh token revoked or expired: only a new sign-in helps
            raise PermissionError("The calendar sign-in expired or was revoked. Reconnect it.") from None
        raise
    return _tokens(reply, now or time.time())


def access_token(store, provider: str, transport=request, now: float = None) -> str:
    """A valid access token, refreshed (and saved) when it is about to expire."""
    now = now or time.time()
    cfg = config_for(store, "calendar", provider)
    if not cfg:
        raise ValueError("Save the client ID and secret first.")
    tokens = cfg["record"].get("tokens") or {}
    if tokens.get("access_token") and tokens.get("expires_at", 0) > now + REFRESH_MARGIN_SECONDS:
        return tokens["access_token"]
    if not tokens.get("refresh_token"):
        raise PermissionError("Not connected yet. Click Connect and allow access.")
    fresh = refresh_tokens(provider, cfg["values"], tokens["refresh_token"], transport, now)
    set_tokens(store, "calendar", provider, fresh)
    return fresh["access_token"]


def zoom_token(store, transport=request, now: float = None) -> str:
    """Server-to-server OAuth: a fresh account token, cached until shortly before it expires."""
    now = now or time.time()
    cfg = config_for(store, "calendar", "zoom")
    if not cfg:
        raise ValueError("Save the Zoom Account ID, Client ID and Client secret first.")
    tokens = cfg["record"].get("tokens") or {}
    if tokens.get("access_token") and tokens.get("expires_at", 0) > now + REFRESH_MARGIN_SECONDS:
        return tokens["access_token"]
    values = cfg["values"]
    # Zoom requires the account id in the query string; it is an identifier, not a secret.
    url = f"{ZOOM_TOKEN}?{urlencode({'grant_type': 'account_credentials', 'account_id': values['account_id']})}"
    reply = transport(url, {"Authorization": basic_auth(values["client_id"], values["client_secret"])}, None, method="POST")
    fresh = _tokens(reply, now)
    set_tokens(store, "calendar", "zoom", fresh)
    return fresh["access_token"]
