"""Shared helpers for the hygiene / scoring / Today / Insights QA tests: the 500-lead store, a frozen clock, metrics."""
import importlib.util
import itertools
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import hygiene_report, main, routes_today
from app.main import create_app

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hygiene-scoring-insights"
NOW = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)          # Tue 11:30 IST, the generator's "now"


def load_500() -> tuple:
    """(store collections, planted truth) from the deterministic generator."""
    spec = importlib.util.spec_from_file_location("hsi_build_db", FIXTURES / "build_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate()


def freeze(monkeypatch, when: datetime) -> None:
    """Pin 'now' for Today, the lead presenter (trial nudges) and the stale-lead count."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return when if tz else when.replace(tzinfo=None)
    for module in (routes_today, main, hygiene_report):
        monkeypatch.setattr(module, "datetime", Frozen)


def seeded(tmp_path, monkeypatch, collections: dict, when: datetime = NOW) -> TestClient:
    freeze(monkeypatch, when)
    client = TestClient(create_app(data_file=tmp_path / "qa.json"))
    store = client.app.state.store
    for key, rows in collections.items():
        store.write(key, rows)
    return client


def pairs(ids) -> set:
    return {tuple(sorted(p)) for p in itertools.combinations(ids, 2)}


def cluster_pairs(clusters: list) -> set:
    out = set()
    for cluster in clusters:
        out |= pairs(cluster["lead_ids"])
    return out


def truth_pairs(truth: dict, *expects: str) -> set:
    out = set()
    for group in truth["groups"]:
        if group["expect"] in expects:
            out |= pairs(group["lead_ids"])
    return out
