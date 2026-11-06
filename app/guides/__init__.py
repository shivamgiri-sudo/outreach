"""Plain-English "How do I get this?" guides for every credential or connection in the app.

`GUIDES` is every guide; `ATTACH` maps a screen key (see common.py) to the guide ids shown there, best first.
`required_keys()` lists every key the catalogues need a guide for; tests fail when one is missing.
"""
from . import app_setup, calendar, capture, email_inbound, email_send, lead_data, lead_sources, messaging

APP_FORMS = ("public_url", "team_logins", "workspaces", "backup", "linkedin_agent", "chrome_extension")
GUIDES = [*email_send.GUIDES, *email_inbound.GUIDES, *messaging.GUIDES, *calendar.GUIDES, *lead_data.GUIDES,
          *lead_sources.GUIDES, *capture.GUIDES, *app_setup.GUIDES]
BY_ID = {g["id"]: g for g in GUIDES}
assert len(BY_ID) == len(GUIDES), "two guides share an id"


def _attach_index() -> dict:
    out = {}
    for g in GUIDES:
        for key in g["attach"]:
            out.setdefault(key, []).append(g["id"])
    return out


ATTACH = _attach_index()


def required_keys() -> set:
    """Every screen key that must have a guide: hub cards, Setup connector cards, capture types and app forms."""
    from .. import capture_catalogue, integrations
    from ..channels import registry
    keys = {f"hub:{c}:{p['id']}" for c, plist in registry.PROVIDERS.items() for p in plist}
    keys |= {f"integration:{i['id']}" for i in integrations.CATALOGUE + integrations.SOURCE_CATALOGUE}
    keys |= {f"capture:{t['id']}" for t in capture_catalogue.TYPES}
    keys |= {f"app:{name}" for name in APP_FORMS}
    return keys


def missing(extra=()) -> list:
    return sorted(k for k in required_keys() | set(extra) if k not in ATTACH)


def payload() -> dict:
    return {"guides": GUIDES, "attach": ATTACH}
