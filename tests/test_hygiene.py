"""Data hygiene: report, duplicate clusters, manual merge (re-pointing and strict status), standardise, conflicts.
All data is made up."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app

T_OLD = "2026-01-05T09:00:00+00:00"
T_MID = "2026-10-20T09:00:00+00:00"
T_NEW = "2026-11-01T09:00:00+00:00"


def lead(lead_id, **fields):
    return {"id": lead_id, "company": "", "contact_name": "", "title": "", "email": None, "phone": None, "website": "",
            "domain": None, "country": "", "source": "manual", "stage": "new", "consent": None,
            "engagement": {"opens": 0, "clicks": 0, "replies": 0}, "timeline": [{"at": T_NEW, "kind": "created", "text": "x"}],
            "created_at": T_NEW, "open_roles": 0, "company_size": 0, "max_job_age_days": 0, "repost_count": 0,
            "dedupe_key": f"id:{lead_id}", **fields}


@pytest.fixture()
def app(tmp_path):
    return create_app(data_file=tmp_path / "d.json")


@pytest.fixture()
def client(app):
    return TestClient(app)


def seed(app, *leads):
    app.state.store.update("leads", lambda rows: [*rows, *leads])


def by_id(client):
    return {l["id"]: l for l in client.get("/api/leads").json()}


def test_report_counts_gaps_bad_contacts_and_stale_leads(app, client):
    app.state.store.update("suppressed", lambda rows: [*rows, "gone@zeta.example"])
    seed(app,
         lead("a1", company="Acme", email="hr@acme.example", domain="acme.example", contact_name="Ravi Kumar"),
         lead("b1", company="Beta", email="sam@gmail.com", phone="+442079460958"),
         lead("c1", company="Gamma", email="bad@@gamma", phone="+919000030003", stage="contacted",
              timeline=[{"at": T_OLD, "kind": "created", "text": "x"}], created_at=T_OLD),
         lead("z1", company="Zeta", email="gone@zeta.example", stage="customer",
              timeline=[{"at": T_OLD, "kind": "created", "text": "x"}], created_at=T_OLD,
              merge_conflicts=[{"id": "k1", "field": "title", "kept": "Founder", "incoming": "CEO", "source": "lusha"}]))
    r = client.get("/api/hygiene/report").json()
    assert r["total"] == 4
    assert r["missing"] == {"email": 0, "phone": 2, "decision_maker": 3, "website": 3}
    assert r["emails"] == {"invalid": 1, "role": 1, "free_mail": 1, "suppressed": 1}
    assert r["foreign_phones"] == 1 and r["stale"] == 1          # the old customer is not "stale"
    assert r["conflicts"] == 1 and r["conflict_list"][0]["lead_id"] == "z1"


def test_duplicate_clusters_say_which_leads_and_why(app, client):
    seed(app,
         lead("e1", company="Acme", contact_name="Ravi Kumar", email="ravi@acme.example", domain="acme.example"),
         lead("e2", company="Acme Retail", contact_name="Ravi K", email="ravi@acme.example"),
         lead("p1", company="Beta", contact_name="Sam Shah", phone="+919000020002"),
         lead("p2", company="Beta Steels", contact_name="Sam Shah", phones=["+91 90000 20002"]),
         lead("g1", company="Gamma", contact_name="Meena", email="meena.iyer@gmail.com"),
         lead("g2", company="Gamma", contact_name="Meena Iyer", email="meenaiyer+x@gmail.com"),
         lead("d1", company="Delta Logistics", domain="delta.example"),
         lead("d2", company="Delta", contact_name="Arjun Das", email="arjun@delta.example"),
         lead("f1", company="Orion Softworks Pvt Ltd", city="Pune"),
         lead("f2", company="ORION SOFTWORK", city="Poona"),
         lead("n1", company="Nimbus", city="Pune"),
         lead("n2", company="Nimbus", city="Chennai"),
         lead("x1", company="Shared Phone Co", contact_name="Anita Rao", phone="+919000090009"),
         lead("x2", company="Shared Phone Co Two", contact_name="Dev Rao", phone="+919000090009"))
    r = client.get("/api/hygiene/report").json()
    clusters = {tuple(sorted(c["lead_ids"])): c["why"] for c in r["clusters"]}
    assert clusters[("e1", "e2")] == ["same email"]
    assert clusters[("p1", "p2")] == ["same phone"]
    assert clusters[("g1", "g2")] == ["same Gmail inbox (dots or +tag differ)"]
    assert clusters[("d1", "d2")] == ["same company domain (one has no contact person)"]
    assert clusters[("f1", "f2")][0].startswith("similar company name in the same city")
    assert ("n1", "n2") not in clusters and ("x1", "x2") not in clusters      # other city; different people
    assert r["duplicate_clusters"] == 5
    acme = next(c for c in r["clusters"] if "e1" in c["lead_ids"])
    assert acme["reasons"][0] == {"a": "e1", "b": "e2", "why": "same email"} and len(acme["leads"]) == 2


def test_manual_merge_repoints_rows_and_keeps_the_strictest_status(app, client):
    seed(app,
         lead("k1", company="Acme", contact_name="Ravi Kumar", email="ravi@acme.example", stage="engaged",
              consent={"text": "yes", "at": T_MID}, phones=["+919000010001"], phone="+919000010001",
              timeline=[{"at": T_NEW, "kind": "note", "text": "newest"}],
              engagement={"opens": 1, "clicks": 0, "replies": 1}),
         lead("m1", company="Acme Retail", contact_name="Anita Rao", email="anita@acme.example", stage="unsubscribed",
              consent_withdrawn_at=T_MID, phone="+911140001234", city="Bengaluru",
              timeline=[{"at": T_OLD, "kind": "created", "text": "oldest"}], engagement={"opens": 2, "clicks": 1, "replies": 0},
              other_contacts=[{"contact_name": "Dev Rao", "title": "HR"}]))
    store = app.state.store
    store.update("outbox", lambda rows: [*rows, {"lead_id": "m1", "to": "anita@acme.example"}, {"lead_id": "k1"}])
    store.update("tasks", lambda rows: [*rows, {"lead_id": "m1", "kind": "review"}])
    store.update("inbox", lambda rows: [*rows, {"id": "i1", "lead_id": "m1"}])
    store.update("whatsapp_outbox", lambda rows: [*rows, {"lead_id": "m1"}])
    r = client.post("/api/hygiene/merge", json={"keep_id": "k1", "merge_ids": ["m1"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["repointed"] == {"outbox": 1, "inbox": 1, "tasks": 1, "whatsapp_outbox": 1}
    kept = by_id(client)
    assert list(kept) == ["k1"]
    k1 = kept["k1"]
    assert k1["stage"] == "unsubscribed" and k1["consent"] is None and k1["consent_withdrawn_at"] == T_MID
    assert k1["merged_ids"] == ["m1"] and k1["city"] == "Bengaluru"
    assert k1["phones"] == ["+919000010001", "+911140001234"] and k1["contact_name"] == "Ravi Kumar"
    assert {c["contact_name"] for c in k1["other_contacts"]} == {"Anita Rao", "Dev Rao"}
    texts = [e["text"] for e in k1["timeline"]]
    assert texts[0] == "oldest" and texts[1] == "newest" and texts[-1].startswith("Merged 1 duplicate lead(s)")
    assert k1["engagement"] == {"opens": 3, "clicks": 1, "replies": 1}
    assert all(row["lead_id"] == "k1" for row in store.read("outbox") + store.read("tasks") + store.read("inbox"))


def test_manual_merge_keeps_the_more_advanced_stage_and_the_sequence(app, client):
    seed(app, lead("k2", company="Beta", stage="new"),
         lead("m2", company="Beta", stage="call_done", sequence={"enrolled_at": T_MID, "sent_days": [0]}),
         lead("m3", company="Beta", stage="disqualified"))
    client.post("/api/hygiene/merge", json={"keep_id": "k2", "merge_ids": ["m2"]})
    k2 = by_id(client)["k2"]
    assert k2["stage"] == "call_done" and k2["sequence"]["sent_days"] == [0]
    client.post("/api/hygiene/merge", json={"keep_id": "k2", "merge_ids": ["m3"]})
    assert by_id(client)["k2"]["stage"] == "disqualified"


def test_manual_merge_refuses_bad_requests(app, client):
    seed(app, lead("k3", company="Gamma"))
    assert client.post("/api/hygiene/merge", json={"keep_id": "k3", "merge_ids": ["k3"]}).status_code == 400
    assert client.post("/api/hygiene/merge", json={"keep_id": "k3", "merge_ids": ["nope"]}).status_code == 404
    assert client.post("/api/hygiene/merge", json={"keep_id": "k3", "merge_ids": []}).status_code == 422
    assert "k3" in by_id(client)


def test_standardise_existing_leads_previews_then_applies(app, client):
    seed(app, lead("s1", company="ACME RETAIL PVT. LTD.", contact_name="MR. RAVI KUMAR", title="head - human resources",
                   email="ravi@acme.example", phone="9000010001", city="Bangalore", website="https://careers.acme.example",
                   domain="careers.acme.example", company_size=120, stage="contacted"))
    preview = client.post("/api/hygiene/standardise", json={"dry_run": True}).json()
    assert preview["changed"] == 1
    fields = preview["changes"][0]["fields"]
    assert fields["company"] == {"from": "ACME RETAIL PVT. LTD.", "to": "Acme Retail"}
    assert fields["title"]["to"] == "HR Head" and fields["city"]["to"] == "Bengaluru" and fields["state"]["to"] == "Karnataka"
    assert fields["phone"]["to"] == "+919000010001" and fields["domain"]["to"] == "acme.example"
    assert by_id(client)["s1"]["company"] == "ACME RETAIL PVT. LTD."               # dry run saved nothing
    applied = client.post("/api/hygiene/standardise", json={"dry_run": False}).json()
    assert applied["changed"] == 1
    s1 = by_id(client)["s1"]
    assert s1["company"] == "Acme Retail" and s1["company_legal"] == "Acme Retail Pvt. Ltd."
    assert s1["contact_name"] == "Ravi Kumar" and s1["country"] == "IN" and s1["company_size_bucket"] == "51-200"
    assert s1["stage"] == "contacted" and s1["email"] == "ravi@acme.example"
    assert s1["timeline"][-1]["kind"] == "standardised"
    assert client.post("/api/hygiene/standardise", json={"dry_run": True}).json()["changed"] == 0


def test_standardise_keeps_a_size_bucket_the_import_already_set(app, client):
    seed(app, lead("b9", company="Beta Labs", company_size=1000, company_size_bucket="1000+"))
    assert client.post("/api/hygiene/standardise", json={"dry_run": True}).json()["changed"] == 0


def test_resolving_conflicts(app, client):
    app.state.store.update("suppressed", lambda rows: [*rows, "blocked@acme.example"])
    conflicts = [{"id": "c1", "field": "title", "kept": "Founder", "incoming": "HR Head", "source": "lusha"},
                 {"id": "c2", "field": "email", "kept": "ravi@acme.example", "incoming": "blocked@acme.example", "source": "x"},
                 {"id": "c3", "field": "city", "kept": "Pune", "incoming": "Mumbai", "source": "apollo"}]
    seed(app, lead("r1", company="Acme", title="Founder", email="ravi@acme.example", city="Pune",
                   dedupe_key="email:ravi@acme.example", merge_conflicts=conflicts))
    used = client.post("/api/hygiene/resolve-conflict", json={"lead_id": "r1", "conflict_id": "c1", "choice": "use_new"})
    assert used.status_code == 200 and used.json()["title"] == "HR Head"
    assert used.json()["field_sources"]["title"] == "lusha"
    refused = client.post("/api/hygiene/resolve-conflict", json={"lead_id": "r1", "conflict_id": "c2", "choice": "use_new"})
    assert refused.status_code == 400 and "do-not-contact" in refused.json()["detail"]
    kept = client.post("/api/hygiene/resolve-conflict", json={"lead_id": "r1", "conflict_id": "c3", "choice": "keep"}).json()
    assert kept["city"] == "Pune" and [c["id"] for c in kept["merge_conflicts"]] == ["c2"]
    assert kept["timeline"][-1]["text"].startswith("Kept the current city")
    again = client.post("/api/hygiene/resolve-conflict", json={"lead_id": "r1", "conflict_id": "c3", "choice": "keep"})
    assert again.status_code == 400
