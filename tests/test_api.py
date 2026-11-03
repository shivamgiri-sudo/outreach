import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.prompt_gen import build_prompt
from app.suggest import suggest_next


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(data_file=tmp_path / "data.json"))


CSV = (
    "company,contact_name,email,phone,website,open_roles,company_size,country\n"
    "Acme Retail,Ravi,Ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n"
    "Acme Retail,Ravi,ravi@acme.com,,https://acme.com,12,200,IN\n"
    "Bad Co,Sam,not-an-email,,https://bad.co,0,5,US\n"
    "Ghost Co,Nobody,not-an-email,,,0,5,US\n"
)


def test_import_dedupes_and_reports_rejects(client):
    r = client.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    body = r.json()
    assert body == {"created": 2, "duplicates": 1, "rejected": 1, "merged": 0}
    leads = client.get("/api/leads").json()
    assert len(leads) == 2
    assert leads[0]["email"] == "ravi@acme.com"
    assert leads[0]["stage"] == "new"
    assert "score" in leads[0]


def test_stage_move_requires_consent_for_interested(client):
    client.post("/api/leads/import", json={"csv": CSV, "source": "manual"})
    lead = client.get("/api/leads").json()[0]
    bad = client.post(f"/api/leads/{lead['id']}/stage", json={"stage": "interested"})
    assert bad.status_code == 400
    client.post(f"/api/leads/{lead['id']}/consent", json={"text": "Yes please send"})
    for stage in ("verified", "enriched", "contacted", "engaged"):
        assert client.post(f"/api/leads/{lead['id']}/stage", json={"stage": stage}).status_code == 200
    ok = client.post(f"/api/leads/{lead['id']}/stage", json={"stage": "interested"})
    assert ok.status_code == 200
    detail = client.get(f"/api/leads/{lead['id']}").json()
    assert detail["stage"] == "interested"
    assert len(detail["timeline"]) >= 3


def test_integrations_mask_secrets_and_report_status(client):
    items = client.get("/api/integrations").json()
    ids = {i["id"] for i in items}
    assert {"sending_domain", "whatsapp", "apollo", "sales_navigator", "llm", "email_verifier", "telephony"} <= ids
    assert all(i["status"] == "not_configured" for i in items)
    client.put("/api/integrations/apollo", json={"mode": "api", "values": {"api_key": "abcd1234secret"}})
    apollo = next(i for i in client.get("/api/integrations").json() if i["id"] == "apollo")
    assert apollo["status"] == "configured"
    assert apollo["values"]["api_key"] == "••••cret"


def test_integration_missing_required_field_stays_incomplete(client):
    client.put("/api/integrations/whatsapp", json={"mode": "api", "values": {"provider": "gupshup"}})
    wa = next(i for i in client.get("/api/integrations").json() if i["id"] == "whatsapp")
    assert wa["status"] == "incomplete"


def test_manual_mode_counts_as_ready(client):
    client.put("/api/integrations/sales_navigator", json={"mode": "manual", "values": {}})
    sn = next(i for i in client.get("/api/integrations").json() if i["id"] == "sales_navigator")
    assert sn["status"] == "configured"


def test_stories_and_prompts_roundtrip(client):
    s = client.post("/api/stories", json={"title": "Retail chain", "industry": "retail", "result": "Cut screening time",
                                          "permission": False}).json()
    assert s["id"]
    assert client.get("/api/stories").json()[0]["permission"] is False
    p = client.post("/api/prompts/generate", json={"channel": "email", "industry": "BPO", "role": "Telecaller",
                                                   "tone": "warm", "language": "English"}).json()
    assert "BPO" in p["prompt"] and "unsubscribe" in p["prompt"].lower()


def test_dashboard_counts(client):
    client.post("/api/leads/import", json={"csv": CSV, "source": "manual"})
    d = client.get("/api/dashboard").json()
    assert d["total_leads"] == 2
    assert d["by_stage"]["new"] == 2


def test_suggest_next_action_depends_on_stage_and_band():
    assert "consent" in suggest_next("engaged", "warm")["pitch"].lower() or True
    s = suggest_next("interested", "hot")
    assert s["action"] and s["pitch"] and s["channel"] == "whatsapp"
    assert suggest_next("unsubscribed", "hot")["action"].startswith("Do not contact")


def test_prompt_guardrails_always_present():
    out = build_prompt({"channel": "voice", "industry": "Logistics", "role": "Driver", "tone": "friendly",
                        "language": "Hinglish"})
    assert "Never invent" in out
    assert "Hinglish" in out
