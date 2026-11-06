"""Intelligence numbers, campaign company filter and the signed-in name on WhatsApp."""
from app.analytics import build, reply_timing, stale
from tests.flow_helpers import lead, make_client, patch_lead, store_of


def test_intelligence_endpoint_shape_and_correction_counts(tmp_path):
    c = make_client(tmp_path)
    a = lead(c, "Acme")
    c.patch(f"/api/leads/{a['id']}", json={"title": "Head of Talent"})
    d = c.get("/api/intelligence").json()
    assert set(d) == {"channels", "timing", "personas", "companies", "quality", "team", "stale", "lists"}
    assert d["quality"]["leads"] == 2 and d["quality"]["manual_corrections"] == 1 and d["quality"]["corrections_by_field"][0]["field"] == "Title"
    assert d["quality"]["no_linkedin"] == 2 and d["channels"]["email"]["enrolled"] == 0
    assert d["team"][0]["by_kind"]["edit"] == 1


def test_funnel_and_timing_from_facts(tmp_path):
    c = make_client(tmp_path)
    a = lead(c, "Acme")
    patch_lead(c, a["id"], sequence={"sent_days": [0], "stopped": None, "enrolled_at": "2026-11-02T04:00:00+00:00"},
               engagement={"opens": 1, "clicks": 0, "replies": 1},
               timeline=[{"at": "2026-11-02T04:00:00+00:00", "kind": "email", "text": "sent"},
                         {"at": "2026-11-02T10:00:00+00:00", "kind": "reply", "text": "[interested] (email, x) yes"}])
    d = build(store_of(c))
    assert d["channels"]["email"] == {"enrolled": 1, "sent": 1, "opened": 1, "clicked": 0, "replied": 1, "reply_rate": 100.0}
    assert d["timing"]["median_hours_to_reply"] == 6.0 and d["timing"]["best_day"] == "Mon"
    assert d["companies"][0]["company"] == "Acme" and d["companies"][0]["replied"] == 1


def test_stale_lists_warm_leads_without_recent_touch():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    leads = [{"id": "1", "company": "X", "stage": "interested", "timeline": [{"at": "2026-11-01T00:00:00+00:00", "kind": "note", "text": "t"}]},
             {"id": "2", "company": "Y", "stage": "interested", "timeline": [{"at": "2026-11-19T00:00:00+00:00", "kind": "note", "text": "t"}]},
             {"id": "3", "company": "Z", "stage": "new", "timeline": []}]
    assert [x["id"] for x in stale(leads, now)["leads"]] == ["1"]


def test_whatsapp_uses_signed_in_name_and_logs_it(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.flow_helpers import CSV
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    h = lambda t: {"Authorization": f"Bearer {t}"}
    owner = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    c.post("/api/users", json={"email": "meena@example.com", "name": "Meena", "role": "caller", "password": "long-password-1"}, headers=h(owner))
    meena = c.post("/api/login", json={"email": "meena@example.com", "password": "long-password-1"}).json()["token"]
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=h(owner))
    a = next(l for l in c.get("/api/leads", headers=h(owner)).json() if l["company"] == "Acme")
    c.post(f"/api/leads/{a['id']}/consent", json={"text": "yes on call", "channel": "whatsapp"}, headers=h(meena))
    r = c.post(f"/api/leads/{a['id']}/whatsapp", json={"template": "first_after_yes"}, headers=h(meena))
    assert r.status_code == 200, r.text
    wa = c.app.state.store.read("whatsapp_outbox")[-1]
    assert wa["by"] == "Meena" and "this is Meena from BookMyInterview" in wa["body"]
    tl = r.json()["timeline"]
    assert any(e["kind"] == "whatsapp" and e.get("by") == "Meena" for e in tl) and any(e["kind"] == "consent" and e.get("by") == "Meena" for e in tl)


def test_campaign_rules_accept_only_companies(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/api/campaigns", json={"name": "Pilot", "rules": {"auto_enrol": True, "only_companies": ["Acme"]}})
    assert r.status_code == 200 and r.json()["rules"]["only_companies"] == ["Acme"]


def test_change_log_csv_and_formula_guard(tmp_path):
    from tests.flow_helpers import make_client, lead, store_of
    client = make_client(tmp_path)
    store = store_of(client)
    store.update("audit", lambda rows: [*rows, {"id": "1", "lead_id": "x", "field": "company", "old": "=SUM(1)", "new": "Acme", "by": "Meena", "at": "2026-11-06T00:00:00+00:00"}])
    res = client.get("/api/change-log.csv")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    assert "'=SUM(1)" in res.text and "Meena" in res.text


def test_remind_stale_makes_one_task_per_cold_lead_and_is_repeatable(tmp_path):
    from tests.flow_helpers import make_client, lead, patch_lead, store_of
    client = make_client(tmp_path)
    lid = lead(client, "Acme")["id"]
    patch_lead(client, lid, stage="interested", timeline=[{"at": "2020-01-01T00:00:00+00:00", "kind": "note", "text": "old"}])
    first = client.post("/api/intelligence/remind-stale").json()
    second = client.post("/api/intelligence/remind-stale").json()
    assert first["created"] == 1 and second["created"] == 0
    assert sum(1 for t in store_of(client).read("tasks") if t["lead_id"] == lid and t["kind"] == "follow_up") == 1


def test_linkedin_connection_note_variants_split_and_are_counted():
    from app import linkedin_seq as li, analytics
    ids = [f"lead{i}" for i in range(40)]
    variants = {i: li.pick_variant(i) for i in ids}
    assert {"A", "B"} == set(variants.values()) and 10 < list(variants.values()).count("A") < 30
    for v in ("A", "B"):
        text = li.render(li.STEPS[0], {"contact_name": "Asha Rao", "title": "CHRO", "linkedin_seq": {"variant": v}}, {})
        assert text.startswith("Hi Asha") and len(text) <= li.NOTE_LIMIT
    leads = [{"linkedin_seq": {"done": [1], "variant": "A", "accepted_at": "x"}}, {"linkedin_seq": {"done": [1], "variant": "B"}}]
    out = analytics.channel_funnels(leads)["linkedin"]["variants"]
    assert out["A"]["accept_rate"] == 100 and out["B"]["accepted"] == 0
