"""Shared helpers for the lead-capture tests."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def make_client(tmp_path: Path, **kwargs):
    app = create_app(data_file=tmp_path / "store.json", **kwargs)
    return TestClient(app), app.state.store


def add_connector(client, kind="generic", name="Test connector", **extra):
    r = client.post("/api/capture/connectors", json={"type": kind, "name": name, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def post_lead(client, connector, payload, token=None, headers=None, **kwargs):
    h = {"X-Capture-Token": token or connector["token"], **(headers or {})}
    return client.post(f"/api/inbound/{connector['id']}", json=payload, headers=h, **kwargs)


def leads(store):
    return store.read("leads")


def lead_by_email(store, email):
    return next(l for l in store.read("leads") if l.get("email") == email)


def tasks_for(store, lead_id):
    return [t for t in store.read("tasks") if t.get("lead_id") == lead_id]
