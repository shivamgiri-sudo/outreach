"""Apollo person match. NOT yet verified against the live Apollo API; the CSV route is the safe path."""
from typing import Optional

from .http_util import post_json

APOLLO_MATCH_URL = "https://api.apollo.io/api/v1/people/match"


def match_person(api_key: str, first_name: str, last_name: str, domain: str, organization: str,
                 transport=post_json) -> Optional[dict]:
    body = {"first_name": first_name, "last_name": last_name, "domain": domain or "", "organization_name": organization or ""}
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    return transport(APOLLO_MATCH_URL, headers, body).get("person")
