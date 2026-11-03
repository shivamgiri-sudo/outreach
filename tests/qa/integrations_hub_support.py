"""Shared helpers for the Integrations hub QA tests (tests/qa/test_integrations-hub*.py).

Everything external is faked: HTTP through tests.channel_fakes.FakeHTTP, SMTP / IMAP through their fakes, DNS through a
resolver that answers a public documentation-free address. Fixture data is fictional.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

from app.channels import registry
from app.channels.security import mask
from app.store import Store
from tests.channel_fakes import FakeHTTP, make_client

FIXTURES = Path(__file__).parent / "fixtures" / "integrations-hub"
PUBLIC_IP = "93.184.216.34"
PUBLIC = lambda host: [PUBLIC_IP]                                   # noqa: E731
HUB_CARDS = sorted(k for k in registry.BY_KEY if registry.is_hub_channel(k[0]))
OAUTH_CARDS = {("calendar", "google"), ("calendar", "microsoft")}


def fixture_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


VALUES = {k: v for k, v in fixture_json("provider_values.json").items() if not k.startswith("_")}


def values_for(channel: str, pid: str) -> dict:
    return json.loads(json.dumps(VALUES[f"{channel}/{pid}"]))


class Recorder:
    """Stands in for app.inbound.process_inbound_message / process_delivery_event."""

    def __init__(self):
        self.items = []

    def __call__(self, store, item):
        self.items.append(item)
        return {"matched": False}


def make(tmp_path, http=None, **kw):
    return make_client(tmp_path, http or FakeHTTP(), resolver=kw.pop("resolver", PUBLIC), **kw)


def store_of(client) -> Store:
    return client.app.state.store


def channel_view(client, channel: str) -> dict:
    return next(c for c in client.get("/api/channels").json()["channels"] if c["id"] == channel)


def card(client, channel: str, pid: str) -> dict:
    return next(p for p in channel_view(client, channel)["providers"] if p["id"] == pid)


def save(client, channel: str, pid: str, values=None, **extra):
    return client.put(f"/api/channels/{channel}/{pid}", json={"values": values_for(channel, pid) if values is None else values, **extra})


def secret_values(channel: str, pid: str, values: dict) -> list:
    """Every real secret in a card's values: secret fields and header rows ticked 'secret'."""
    provider = registry.get_provider(channel, pid)
    out = [str(values[f["key"]]) for f in provider["fields"] if f.get("secret") and values.get(f["key"])]
    for f in provider["fields"]:
        if f["kind"] == "headers":
            out += [h["value"] for h in values.get(f["key"]) or [] if h.get("secret")]
    return out


def ui_form(provider_view: dict) -> dict:
    """What the Integrations wizard sends back on 'Save' after opening a saved card (web/src/pages/integrations/Fields.jsx
    initialValues): secrets empty, header rows as shown (masked), plain fields as shown or their default."""
    out = {}
    for f in provider_view["fields"]:
        shown = (provider_view.get("values") or {}).get(f["key"])
        if f["kind"] == "headers":
            out[f["key"]] = shown or []
        elif f.get("secret"):
            out[f["key"]] = ""
        else:
            out[f["key"]] = shown if shown is not None else f.get("default", "")
    return out


def hook(client, channel: str, pid: str) -> tuple:
    """(path?token=…, secret) of a provider's webhook address, as listed for pasting into the provider."""
    row = client.get(f"/api/channels/webhooks?channel={channel}&provider={pid}").json()["webhooks"][0]
    parts = urlsplit(row["url"])
    return f"{parts.path}?{parts.query}", row["secret"]


def masked(value: str) -> str:
    return mask(value)
