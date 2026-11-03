"""QA: the API side of the browser day-in-the-life journey (web/e2e/journey.mjs).

The Playwright script needs Node, Chromium and two servers, so it is not part of pytest. These tests replay the same
three fixture files and the same key steps through the API, so the numbers the browser script asserts (and the bug it
found) stay covered by the normal suite. All people, companies, numbers and domains in the fixtures are made up.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

FIX = Path(__file__).parent / "fixtures" / "browser-journey"


def text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(data_file=tmp_path / "d.json"))


def summary(client, path, csv, source=""):
    r = client.post(path, json={"csv": csv, "source": source} if source else {"csv": csv})
    assert r.status_code == 200, r.text
    return r.json()


def three_imports(client):
    a = summary(client, "/api/import/commit", text("apollo-contacts-export.csv"))
    b = summary(client, "/api/import/commit", text("evaboot-salesnav-hr-leaders.csv"), "sales_navigator")
    c = summary(client, "/api/import/commit", text("team-sheet-leads-tab.tsv"), "bmi_sheet")
    return a, b, c


def by_company(client, company):
    return next(l for l in client.get("/api/leads").json() if l["company"] == company)


def test_three_sources_import_with_the_counts_the_browser_journey_expects(client):
    dry = summary(client, "/api/import/preview", text("apollo-contacts-export.csv"))
    assert dry["format"]["name"] == "Apollo export" and dry["dry_run"] is True
    assert client.get("/api/leads").json() == []                         # a dry run saves nothing
    s = dry["summary"]
    assert (s["rows"], s["create"], s["duplicates_in_file"], s["rejected"]) == (133, 108, 23, 2)
    a, b, c = three_imports(client)
    assert b["format"]["name"].startswith("LinkedIn Sales Navigator export")     # Excel ';' file with a BOM
    assert (b["summary"]["create"], b["summary"]["merge"]) == (41, 12)
    assert c["format"]["name"].startswith("Team sheet")
    assert (c["summary"]["create"], c["summary"]["merge"]) == (32, 10)
    leads = client.get("/api/leads").json()
    assert len(leads) == 181
    chambal = by_company(client, "Chambal Facility Services")
    assert chambal["open_roles"] == 25 and chambal["phone"] == "+919000010300"


def test_an_erasure_request_shows_on_today_even_though_the_lead_is_unsubscribed(client):
    """Regression: Today skipped every task of an unsubscribed lead, and a "delete my data" reply always unsubscribes,
    so the owner's 24-hour DPDP erasure request never reached Today (only the Tasks page)."""
    three_imports(client)
    indus = by_company(client, "Indus Fintech")
    r = client.post(f"/api/leads/{indus['id']}/reply", json={"text": "Please remove me from your list and delete my data."})
    assert r.json()["label"] == "unsubscribe" and r.json()["lead"]["stage"] == "unsubscribed"
    godavari = by_company(client, "Godavari Infra")
    client.post(f"/api/leads/{godavari['id']}/reply", json={"text": "Will check with my team and revert."})
    items = client.get("/api/today").json()["items"]
    erasure = [i for i in items if i["title"] == "Erasure request"]
    assert len(erasure) == 1 and erasure[0]["company"] == "Indus Fintech" and erasure[0]["priority"] == "high"
    assert any(i["title"] == "Read a reply" and i["company"] == "Godavari Infra" for i in items)
    board = client.get("/api/tasks/board").json()                      # the Tasks page dropped it the same way
    open_rows = [t for k in ("overdue", "today", "upcoming", "no_date") for t in board[k]]
    assert [t["company"] for t in open_rows if t.get("title") == "Erasure request"] == ["Indus Fintech"]
    # other tasks of an unsubscribed lead stay hidden: a closed lead stays closed
    client.post("/api/tasks", json={"lead_id": indus["id"], "title": "Send the deck"})
    assert not any(i["title"] == "Send the deck" for i in client.get("/api/today").json()["items"])


def test_erasing_the_lead_clears_the_erasure_request_and_a_reimport_is_left_out(client):
    three_imports(client)
    indus = by_company(client, "Indus Fintech")
    client.post(f"/api/leads/{indus['id']}/reply", json={"text": "Please remove me from your list and delete my data."})
    export = client.get(f"/api/leads/{indus['id']}/export")
    assert export.status_code == 200 and "kavya.iyer@indus-fintech.invalid" in export.text
    assert client.post(f"/api/leads/{indus['id']}/erase", json={"confirm": True}).status_code == 200
    assert not any(i["title"] == "Erasure request" for i in client.get("/api/today").json()["items"])
    again = summary(client, "/api/import/preview", text("apollo-reimport-erased.csv"))
    assert (again["summary"]["create"], again["summary"]["skipped"]) == (0, 1)
    assert "do-not-contact" in again["skipped"][0]["reason"]
