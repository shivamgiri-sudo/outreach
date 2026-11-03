"""Apollo person match. NOT yet verified against the live Apollo API; the CSV route is the safe path.

Request and response shapes are coded from Apollo's public API docs. The key goes in the `x-api-key` header
(never in the URL).
"""
from typing import Optional

from .http_util import post_json

APOLLO_BASE = "https://api.apollo.io/api/v1"
APOLLO_MATCH_URL = f"{APOLLO_BASE}/people/match"
APOLLO_USAGE_URL = f"{APOLLO_BASE}/usage_stats/api_usage_stats"


def _headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "Accept": "application/json"}


def match_person(api_key: str, first_name: str, last_name: str, domain: str, organization: str,
                 transport=post_json) -> Optional[dict]:
    body = {"first_name": first_name, "last_name": last_name, "domain": domain or "", "organization_name": organization or ""}
    return transport(APOLLO_MATCH_URL, _headers(api_key), body).get("person")


def match_person_by_id(api_key: str, person_id: str, transport=post_json) -> Optional[dict]:
    """People Match by the Apollo person id a search returned. Business email only (personal emails not asked for)."""
    body = {"id": person_id, "reveal_personal_emails": False}
    return transport(APOLLO_MATCH_URL, _headers(api_key), body).get("person")


def check_usage(api_key: str, transport=post_json) -> dict:
    """Apollo's API usage stats: proves the key works without spending a credit."""
    return transport(APOLLO_USAGE_URL, _headers(api_key), {})
