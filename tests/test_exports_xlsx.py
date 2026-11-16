"""Excel downloads (app/exports_xlsx.py): typed cells, IST dates, phone as text, bold frozen header, autofilter, formula
neutralising, the same filters and secrets rules as the CSV, owner-only, audit-logged, workspace-scoped; harvest .xlsx."""
import io
from datetime import datetime

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import exports, exports_xlsx
from app.main import create_app
from tests.export_fakes import SECRETS, make_client, read_csv, seed
from tests.flow_helpers import make_client as flow_client

REQUIRED = {"activity": {"lead_id": "l1"}, "campaign_leads": {"campaign_id": "c1"}, "campaign_runs": {"campaign_id": "c1"}}


def book(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == exports_xlsx.MEDIA_TYPE
    return openpyxl.load_workbook(io.BytesIO(response.content)).active


def table(sheet) -> list:
    rows = list(sheet.iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:]]


@pytest.fixture()
def seeded(tmp_path):
    client, store = make_client(tmp_path)
    seed(store)
    return client, store


def test_leads_xlsx_types_header_and_filter(seeded):
    client, _ = seeded
    r = client.get("/api/export/leads.xlsx")
    assert 'filename="outreach-leads-' in r.headers["content-disposition"] and r.headers["content-disposition"].endswith('.xlsx"')
    sheet = book(r)
    assert sheet.freeze_panes == "A2" and sheet.auto_filter.ref.startswith("A1:")
    assert all(c.font.b for c in sheet[1])
    rows = {row["id"]: row for row in table(sheet)}
    l1 = rows["l1"]
    assert isinstance(l1["score"], int) and l1["open_roles"] == 60 and l1["company_size"] == 200 and l1["emails_sent"] == 2
    assert l1["consent"] is True
    assert l1["consent_at_ist"] == datetime(2026, 11, 2, 12, 0)                    # 06:30 UTC = 12:00 IST, a real date
    assert l1["consent_at"] == "2026-11-02T06:30:00+00:00"                          # the raw ISO stays text, as in the CSV
    assert l1["phone"] == "+919876543210"                                           # text, the + kept
    header = [c.value for c in sheet[1]]
    phone_cell = sheet.cell(row=2, column=header.index("phone") + 1)
    assert phone_cell.data_type == "s" and phone_cell.number_format == "@"
    widths = [sheet.column_dimensions[openpyxl.utils.get_column_letter(i + 1)].width for i in range(len(header))]
    assert all(exports_xlsx.MIN_WIDTH <= w <= exports_xlsx.MAX_WIDTH for w in widths)
    assert header == list(read_csv(client.get("/api/export/leads.csv"))[0])        # the same columns as the CSV


def test_formulas_are_neutralised(seeded):
    client, _ = seeded
    rows = {row["id"]: row for row in table(book(client.get("/api/export/leads.xlsx")))}
    assert rows["l1"]["company"].startswith("'=HYPERLINK")
    assert rows["l2"]["contact_name"] == "'@admin" and rows["l3"]["contact_name"] == "'-Neha"
    assert rows["l2"]["company"] == "Śrī Gaṇeśa Traders ₹"
    sheet = book(client.get("/api/export/leads.xlsx"))
    assert not any(c.data_type == "f" for row in sheet.iter_rows() for c in row)
    assert exports_xlsx.safe_text("+1 idea") == "'+1 idea" and exports_xlsx.safe_text("bad\x01char") == "badchar"


def test_xlsx_filters_match_the_csv(seeded):
    client, _ = seeded
    for q in ("stage=new", "tag=vip", "q=gamma", "has=phone", "source=manual"):
        x = sorted(r["id"] for r in table(book(client.get(f"/api/export/leads.xlsx?{q}"))))
        c = sorted(r["id"] for r in read_csv(client.get(f"/api/export/leads.csv?{q}")))
        assert x == c, q
    picked = table(book(client.post("/api/export/leads.xlsx", json={"ids": ["l2", "l3"]})))
    assert sorted(r["id"] for r in picked) == ["l2", "l3"]


def test_no_secret_in_any_xlsx(seeded):
    client, _ = seeded
    for dataset in exports.DATASETS:
        sheet = book(client.get(f"/api/export/{dataset}.xlsx", params={**REQUIRED.get(dataset, {}), "include_body": "1"}))
        text = " ".join(str(v) for row in sheet.iter_rows(values_only=True) for v in row if v is not None)
        assert not [s for s in SECRETS if s in text], dataset


def test_xlsx_is_owner_only_and_audited(tmp_path):
    client, store = make_client(tmp_path)
    seed(store)
    client.get("/api/export/leads.xlsx?stage=new")
    row = next(a for a in reversed(store.read("audit")) if a.get("action") == "export.download")
    assert "leads.xlsx" in str(row) and "stage=new" in str(row)
    app = create_app(data_file=tmp_path / "store.json")

    @app.middleware("http")
    async def as_member(request, call_next):
        request.state.role = "member"
        return await call_next(request)
    member = TestClient(app)
    assert member.get("/api/export/leads.xlsx").status_code == 403
    assert member.post("/api/export/leads.xlsx", json={"ids": ["l1"]}).status_code == 403
    assert member.get("/api/export/tasks.xlsx").status_code == 200


def test_xlsx_keeps_to_the_selected_workspace(tmp_path):
    c = flow_client(tmp_path)
    w = c.post("/api/workspaces", json={"name": "Client One"}).json()
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nClientCo,Neha,neha@clientco.test\n", "source": "apollo"},
           headers={"X-Workspace": w["id"]})
    mine = [r["company"] for r in table(book(c.get("/api/export/leads.xlsx", headers={"X-Workspace": w["id"]})))]
    main = [r["company"] for r in table(book(c.get("/api/export/leads.xlsx", headers={"X-Workspace": "main"})))]
    assert mine == ["ClientCo"] and "ClientCo" not in main and main
    assert [r["company"] for r in read_csv(c.get("/api/export/leads.csv", headers={"X-Workspace": w["id"]}))] == ["ClientCo"]


def test_big_list_streams_in_chunks():
    columns = ["id", "phone", "score"]
    rows = [{"id": f"l{i}", "phone": "+9198" + str(10000000 + i), "score": i} for i in range(3000)]
    pieces = list(exports_xlsx.chunks(columns, rows))
    assert len(pieces) >= 1 and all(len(p) <= exports_xlsx.CHUNK for p in pieces)
    sheet = openpyxl.load_workbook(io.BytesIO(b"".join(pieces))).active
    assert sheet.max_row == 3001 and sheet.cell(3001, 3).value == 2999


def test_harvest_job_xlsx(tmp_path, monkeypatch):
    from app import harvest
    from tests.harvest_fakes import PlacesFake, fake_http, place
    from tests.test_harvest_routes import connect_places, rig, run
    monkeypatch.setattr(harvest, "KICK", False)
    monkeypatch.setattr(harvest, "_last_call", {})
    http = fake_http(PlacesFake({("dentist in Pune", ""): {"places": [place(1, displayName={"text": "=HYPERLINK(\"x\")"}), place(2)]}}))
    c, owner, store, login_as, _ = rig(tmp_path, http)
    connect_places(store)
    job = c.post("/api/harvest/jobs", json={"keywords": "dentist", "locations": "Pune", "sources": ["google_places"], "start": True},
                 headers=owner).json()
    run(store)
    r = c.get(f"/api/harvest/jobs/{job['id']}/export.xlsx", headers=owner)
    assert r.headers["content-disposition"].endswith('.xlsx"')
    sheet = book(r)
    rows = table(sheet)
    assert [c.value for c in sheet[1]][:3] == ["company", "category", "phone"] and sheet.freeze_panes == "A2"
    assert any(str(x["company"]).startswith("'=HYPERLINK") for x in rows)
    phoned = [x for x in rows if x["phone"]]
    assert phoned and all(str(x["phone"]).startswith("+") for x in phoned)
    assert all(isinstance(x["rating"], (int, float)) for x in rows if x["rating"] is not None)
    assert any(a["action"] == "export.download" and "harvest.xlsx" in str(a) for a in store.read("audit"))
    caller = login_as("cal@example.com", "caller")
    assert c.get(f"/api/harvest/jobs/{job['id']}/export.xlsx", headers=caller).status_code == 403


def test_harvest_xlsx_workspace_admin_sees_only_its_jobs(tmp_path, monkeypatch):
    from app import accounts, harvest, workspaces
    from tests.test_harvest_routes import rig, run
    monkeypatch.setattr(harvest, "KICK", False)
    monkeypatch.setattr(harvest, "_last_call", {})
    c, owner, store, login_as, _ = rig(tmp_path)
    acct = accounts.create(store, "Client Co", "test")["id"]
    ws = workspaces.create(store, "Client One", "test", account_id=acct)["id"]
    ada = login_as("ada@client.example", "admin", [ws], acct)
    osm = {"keywords": "dentist", "locations": "Pune", "sources": ["osm"], "start": True}
    main_job = c.post("/api/harvest/jobs", json=osm, headers=owner).json()
    mine = c.post("/api/harvest/jobs", json=osm, headers={**ada, "X-Workspace": ws}).json()
    run(store)
    assert c.get(f"/api/harvest/jobs/{main_job['id']}/export.xlsx", headers=ada).status_code == 404
    assert len(table(book(c.get(f"/api/harvest/jobs/{mine['id']}/export.xlsx", headers=ada)))) == 1
