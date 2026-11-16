"""Bulk email checker (app/bulk_email_check.py, app/routes_email_checker.py): statuses, flags, CSV safety, the 10,000
cap, background runs, roles and workspace isolation. No network: DNS is a fake."""
import pytest
from fastapi.testclient import TestClient

from app import bulk_email_check as bec, routes_email_checker
from app.main import create_app
from tests.test_workspace_isolation import admin_setup

DNS = {"acme.com": "ok", "gmail.com": "ok", "nomx.example": "no_mail", "ghost.example": "no_domain", "slow.example": "unknown",
       "catchy.com": "ok"}


def fake_dns(domain):
    return DNS.get(domain, "ok")


def statuses(rows):
    return {r["email"]: r["status"] for r in rows}


def test_each_status_and_flag():
    known = {**bec._empty_known(), "leads": {"ravi@acme.com"}, "suppressed": {"stop@acme.com"},
             "history": {"bounced": {"gone@acme.com"}, "delivered": set(), "domains": {}},
             "verdicts": {"anyone@catchy.com": "catch_all"}}
    emails = ["not-an-email", "ravi@acme.com", "RAVI@acme.com", "x@mailinator.com", "asha@gmial.com", "stop@acme.com",
              "gone@acme.com", "a@nomx.example", "b@ghost.example", "c@slow.example", "anyone@catchy.com", "info@acme.com",
              "priya@gmail.com", "neha@acme.com"]
    rows = bec.check_list(emails, known, fake_dns)
    got = statuses(rows)
    assert got == {"not-an-email": "invalid_syntax", "ravi@acme.com": "valid", "RAVI@acme.com": "duplicate",
                   "x@mailinator.com": "disposable", "asha@gmial.com": "typo", "stop@acme.com": "suppressed",
                   "gone@acme.com": "bounced", "a@nomx.example": "no_mail_server", "b@ghost.example": "no_mail_server",
                   "c@slow.example": "unknown", "anyone@catchy.com": "catch_all", "info@acme.com": "risky",
                   "priya@gmail.com": "valid", "neha@acme.com": "valid"}
    by = {r["email"]: r for r in rows}
    assert "already_lead" in by["ravi@acme.com"]["flags"] and "already_lead" in by["RAVI@acme.com"]["flags"]
    assert by["asha@gmial.com"]["suggest"] == "asha@gmail.com" and "typo" in by["asha@gmial.com"]["flags"]
    assert "role" in by["info@acme.com"]["flags"] and "free" in by["priya@gmail.com"]["flags"]
    assert "suppressed" in by["stop@acme.com"]["flags"] and "bounced" in by["gone@acme.com"]["flags"]
    s = bec.summary(rows)
    assert s["total"] == 14 and s["statuses"]["valid"] == 3 and s["statuses"]["catch_all"] == 1 and s["flags"]["role"] == 1
    assert bec.valid_addresses(rows) == ["priya@gmail.com", "neha@acme.com"]          # leads and suppressed left out


def test_catch_all_and_unknown_never_become_valid():
    known = {**bec._empty_known(), "verdicts": {"x@catchy.com": "catch_all"}}
    rows = bec.check_list(["x@catchy.com", "y@slow.example"], known, fake_dns)
    assert [r["status"] for r in rows] == ["catch_all", "unknown"]
    assert bec.valid_addresses(rows) == []


def test_reading_paste_and_csv():
    assert bec.read_addresses("a@x.com, b@y.com;c@z.com\n d@w.com") == ["a@x.com", "b@y.com", "c@z.com", "d@w.com"]
    assert bec.read_addresses("name,email,city\nRavi,ravi@acme.com,Pune\nSita,,Delhi\nAsha,asha@b.in,Goa\n") == ["ravi@acme.com", "asha@b.in"]
    assert bec.read_addresses("Ravi,ravi@acme.com\nAsha,asha@b.in\n") == ["ravi@acme.com", "asha@b.in"]
    assert len(bec.read_addresses("\n".join(f"p{i}@acme.com" for i in range(bec.MAX_ADDRESSES)))) == bec.MAX_ADDRESSES
    with pytest.raises(bec.TooMany):
        bec.read_addresses("\n".join(f"p{i}@acme.com" for i in range(bec.MAX_ADDRESSES + 1)))


def test_csv_neutralises_formulas():
    rows = [{"email": "=HYPERLINK(\"http://x\")@a.com", "status": "invalid_syntax", "flags": [], "suggest": "", "reason": "+1"},
            {"email": "@SUM(A1)", "status": "invalid_syntax", "flags": [], "suggest": "", "reason": "-x"}]
    text = bec.to_csv(rows)
    lines = text.strip().splitlines()
    assert lines[0] == "email,status,flags,suggestion,reason"
    assert lines[1].startswith("\"'=HYPERLINK") and lines[1].endswith(",'+1")
    assert lines[2].startswith("'@SUM") and lines[2].endswith(",'-x")


def test_background_runner_reports_progress_and_finishes():
    runner = bec.Runner()
    run = runner.start("me", ["a@acme.com", "b@acme.com"], bec._empty_known(), fake_dns)
    assert run["status"] == "running" and "rows" not in run
    import time
    for _ in range(100):
        if runner.get(run["id"], "me")["status"] == "done":
            break
        time.sleep(0.02)
    view = bec.Runner.view(runner.get(run["id"], "me"))
    assert view["status"] == "done" and view["summary"]["total"] == 2 and len(view["rows"]) == 2
    assert runner.get(run["id"], "someone-else") is None


def test_screen_shows_first_200_rows_only():
    runner = bec.Runner()
    run = runner.run_now("me", [f"p{i}@acme.com" for i in range(450)], bec._empty_known(), fake_dns)
    assert run["summary"]["total"] == 450 and len(run["rows"]) == 200 and run["shown"] == 200


def test_busy_when_too_many_runs():
    runner = bec.Runner()
    for _ in range(bec.MAX_RUNNING):
        runner._new("me", ["a@b.com"])
    with pytest.raises(bec.Busy):
        runner._new("me", ["a@b.com"])


@pytest.fixture
def sync_runs(monkeypatch):
    monkeypatch.setattr(routes_email_checker, "BACKGROUND", False)
    monkeypatch.setattr(routes_email_checker, "DNS", fake_dns)


def test_api_run_export_and_valid(tmp_path, sync_runs):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    ho = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]}
    c.post("/api/leads/import", json={"csv": "company,email\nAcme,ravi@acme.com\n", "source": "manual"}, headers=ho)
    r = c.post("/api/email-checker/runs", json={"text": "ravi@acme.com\nnew@acme.com\nnew@acme.com\n=cmd@x.com"}, headers=ho)
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "done" and run["summary"]["statuses"]["duplicate"] == 1 and run["valid_new"] == 1
    assert c.get(f"/api/email-checker/runs/{run['id']}", headers=ho).json()["summary"]["total"] == 4
    csv_text = c.get(f"/api/email-checker/runs/{run['id']}/export.csv", headers=ho).text
    assert "'=cmd@x.com" in csv_text and "already_lead" in csv_text
    assert c.get(f"/api/email-checker/runs/{run['id']}/valid", headers=ho).json() == {"emails": ["new@acme.com"]}
    big = "\n".join(f"p{i}@acme.com" for i in range(bec.MAX_ADDRESSES + 1))
    assert c.post("/api/email-checker/runs", json={"text": big}, headers=ho).status_code == 413
    assert c.post("/api/email-checker/runs", json={"text": "  "}, headers=ho).status_code == 400
    assert c.get("/api/email-checker/runs/nope", headers=ho).status_code == 404


def test_roles_and_workspace_isolation(tmp_path, sync_runs):
    c, ho, ha, w, w2 = admin_setup(tmp_path)            # main has ravi@acme.com; the admin only sees Client One
    owner_run = c.post("/api/email-checker/runs", json={"text": "ravi@acme.com"}, headers=ho).json()
    assert "already_lead" in owner_run["rows"][0]["flags"]
    admin = c.post("/api/email-checker/runs", json={"text": "ravi@acme.com"}, headers=ha)
    assert admin.status_code == 200 and admin.json()["rows"][0]["flags"] == []   # another workspace's lead is not revealed
    assert c.get(f"/api/email-checker/runs/{owner_run['id']}", headers=ha).status_code == 404     # only the login that ran it
    assert c.get(f"/api/email-checker/runs/{admin.json()['id']}/export.csv", headers=ha).status_code == 200
    hc = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "raj@example.com", "password": "long-password-1"}).json()["token"]}
    assert c.post("/api/email-checker/runs", json={"text": "a@b.com"}, headers=hc).status_code == 403
    assert c.post("/api/email-checker/runs", json={"text": "a@b.com"}).status_code == 401
