"""Hiring-lead engine: agency flag, company email format, guesses are never emails, verification promotes only valid."""
from datetime import datetime, timezone

from app import hiring_engine as he
from app.sources import waterfall
from tests.capture_helpers import make_client

NOW = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)


def lead(store, **fields):
    base = {"id": fields.pop("id"), "dedupe_key": fields.get("domain", "x") + str(fields.get("contact_name")), "company": "Co",
            "stage": "new", "timeline": [], "is_test": False}
    store.add_leads([{**base, **fields}])


def test_agencies_are_flagged_not_treated_as_employers():
    assert he.classify_poster("Vision India Services", "visionindia.co.in")["kind"] == "agency"
    assert he.classify_poster("Sharma Manpower Solutions", "sharma.in")["kind"] == "agency"
    assert he.classify_poster("Alpha Recruitment Pvt Ltd", "alpha.in")["kind"] == "agency"
    assert he.classify_poster("Acme Retail", "acme-jobs.in")["kind"] == "agency"        # a jobs-site domain
    assert he.classify_poster("Acme Retail", "acme.in")["kind"] == "employer"
    assert he.classify_poster("", "")["kind"] == "unknown"


def test_company_format_is_learned_from_published_addresses_only():
    known = [{"contact_name": "Ravi Menon", "email": "ravi.menon@acme.in"}, {"contact_name": "Sita Rao", "email": "sita.rao@acme.in"},
             {"contact_name": "Bad Guess", "email": "bguess@acme.in", "email_source": "guess:flast"},
             {"contact_name": "Info", "email": "hr@acme.in"}]
    assert he.infer_pattern(known, "acme.in") == "first.last"
    assert he.infer_pattern(known, "other.in") is None
    assert [g["email"] for g in he.guess_emails("Anita", "Desai", "acme.in", "first.last")] == ["anita.desai@acme.in"]
    common = he.guess_emails("Anita", "Desai", "acme.in")
    assert [g["email"] for g in common] == ["anita.desai@acme.in", "anita@acme.in", "adesai@acme.in"]
    assert all(g["basis"] == "a common format" for g in common)


def test_trust_levels():
    assert he.trust({"email": "a@b.in", "email_check": {"status": "valid"}}) == "confirmed"
    assert he.trust({"email": "a@b.in"}) == "published"
    assert he.trust({"email_guess": "a@b.in", "email_guess_status": "unconfirmed"}) == "guess_mx"
    assert he.trust({"email_guess": "a@b.in", "email_guess_status": "risky"}) == "guess_risky"
    assert he.trust({"email_guess_status": "rejected"}) == "guess_rejected"
    assert he.trust({}) == "none"


class FakeRun:
    """Stands in for the verifier chain: one answer per address."""
    def __init__(self, answers):
        self.answers = answers
        self.asked = []
        self.errors = {}

        class Budget:
            spent = 0
        self.ctx = type("C", (), {"budget": Budget, "cache": type("K", (), {"flush": lambda s: None})()})()

    def check(self, email):
        self.asked.append(email)
        return {"status": self.answers.get(email, "unknown"), "provider": "fake", "reason": ""}


def run_with(monkeypatch, tmp_path, answers):
    client, store = make_client(tmp_path)
    fake = FakeRun(answers)
    monkeypatch.setattr(he, "_verifier_run", lambda *a, **k: fake)
    return client, store, fake


def test_guess_is_never_saved_as_email_unless_a_verifier_says_valid(monkeypatch, tmp_path):
    client, store, fake = run_with(monkeypatch, tmp_path, {"anita.desai@acme.in": "unknown"})
    lead(store, id="l1", company="Acme", domain="acme.in", contact_name="Anita Desai", title="HR Head", open_roles=11)
    report = he.run_engine(store, {}, NOW)
    saved = store.read("leads")[0]
    assert not saved.get("email") and saved["email_guess"] == "anita.desai@acme.in" and saved["email_guess_status"] == "unconfirmed"
    assert he.trust(saved) == "guess_mx" and report["kept_as_guess"] == 1 and report["confirmed"] == 0
    assert saved["poster_kind"] == "employer"


def test_valid_guess_is_promoted_with_its_source_labelled(monkeypatch, tmp_path):
    client, store, fake = run_with(monkeypatch, tmp_path, {"anita.desai@acme.in": "invalid", "anita@acme.in": "valid"})
    lead(store, id="l1", company="Acme", domain="acme.in", contact_name="Anita Desai", open_roles=3)
    report = he.run_engine(store, {}, NOW)
    saved = store.read("leads")[0]
    assert saved["email"] == "anita@acme.in" and saved["email_source"] == "guess:first" and saved["email_check"]["status"] == "valid"
    assert he.trust(saved) == "confirmed" and report["confirmed"] == 1 and fake.asked == ["anita.desai@acme.in", "anita@acme.in"]


def test_all_rejected_is_remembered_and_not_retried(monkeypatch, tmp_path):
    client, store, fake = run_with(monkeypatch, tmp_path, {e: "invalid" for e in ("anita.desai@acme.in", "anita@acme.in", "adesai@acme.in")})
    lead(store, id="l1", company="Acme", domain="acme.in", contact_name="Anita Desai")
    assert he.run_engine(store, {}, NOW)["rejected"] == 1
    assert he.trust(store.read("leads")[0]) == "guess_rejected"
    fake.asked.clear()
    he.run_engine(store, {}, NOW)
    assert fake.asked == []


def test_agencies_and_nameless_leads_get_no_guesses(monkeypatch, tmp_path):
    client, store, fake = run_with(monkeypatch, tmp_path, {})
    lead(store, id="a", company="Vision India Staffing", domain="visionindia.in", contact_name="Raj Kumar", open_roles=40)
    lead(store, id="b", company="Beta Ltd", domain="beta.in", contact_name="", open_roles=2)
    report = he.run_engine(store, {}, NOW)
    assert fake.asked == [] and report["agencies"] == 1 and report["skipped_no_name_or_domain"] == 1
    by_id = {l["id"]: l for l in store.read("leads")}
    assert by_id["a"]["poster_kind"] == "agency" and not by_id["a"].get("email_guess")


def test_dry_run_changes_nothing_and_sheet_and_api_work(monkeypatch, tmp_path):
    client, store, fake = run_with(monkeypatch, tmp_path, {})
    lead(store, id="l1", company="Acme", domain="acme.in", contact_name="Anita Desai", open_roles=11, max_job_age_days=20)
    lead(store, id="l2", company="Zed", domain="zed.in", contact_name="Zed Zed", open_roles=1)
    assert he.run_engine(store, {}, NOW, dry_run=True)["guessed"] >= 1
    assert "poster_kind" not in store.read("leads")[0] and fake.asked == []
    rows = he.sheet_rows(store)
    assert rows[0]["company"] == "Acme" and rows[0]["open_roles"] == 11 and rows[0]["email_trust"] == "none"
    r = client.get("/api/hiring-engine/summary")
    assert r.status_code == 200 and r.json()["total"] == 2
    csv = client.get("/api/export/hiring_sheet.csv")
    assert csv.status_code == 200 and "email_trust" in csv.text.splitlines()[0]
    assert client.post("/api/hiring-engine/run", json={"max_leads": 5, "dry_run": True}).status_code == 200


def test_checks_done_on_another_machine_are_applied_with_the_same_rules(tmp_path):
    client, store = make_client(tmp_path)
    lead(store, id="l1", company="Acme", domain="acme.in", contact_name="Anita Desai", open_roles=5)
    lead(store, id="l2", company="Beta", domain="beta.in", contact_name="Bina Shah", open_roles=2)
    lead(store, id="l3", company="Gamma", domain="gamma.in", contact_name="Gita Rao", open_roles=1)
    wanted = client.get("/api/hiring-engine/candidates").json()
    assert [w["lead_id"] for w in wanted] == ["l1", "l2", "l3"] and wanted[0]["guesses"][0]["email"] == "anita.desai@acme.in"
    body = {"results": [
        {"lead_id": "l1", "checks": [{"email": "anita.desai@acme.in", "status": "rejected"}, {"email": "anita@acme.in", "status": "confirmed"}]},
        {"lead_id": "l2", "checks": [{"email": "bina.shah@beta.in", "status": "unknown_blocked"}]},
        {"lead_id": "l3", "checks": [{"email": "gita.rao@gamma.in", "status": "catch_all"}, {"email": "evil@other.in", "status": "confirmed"}]},
        {"lead_id": "nope", "checks": [{"email": "x@y.in", "status": "confirmed"}]}]}
    report = client.post("/api/hiring-engine/apply", json=body).json()
    by_id = {l["id"]: l for l in store.read("leads")}
    assert by_id["l1"]["email"] == "anita@acme.in" and by_id["l1"]["email_source"] == "guess:first"
    assert not by_id["l2"].get("email") and by_id["l2"]["email_guess_status"] == "unconfirmed"
    assert not by_id["l3"].get("email") and by_id["l3"]["email_guess_status"] == "risky"         # catch-all proves nothing
    assert report["confirmed"] == 1 and report["ignored"] == 1
    assert client.get("/api/hiring-engine/candidates").json() == []


def test_recheck_reverifies_unconfirmed_guesses_only_when_asked(monkeypatch, tmp_path):
    client, store, fake = run_with(monkeypatch, tmp_path, {"anita.desai@acme.in": "valid"})
    lead(store, id="l1", company="Acme", domain="acme.in", contact_name="Anita Desai", open_roles=3, email_guess="anita@acme.in",
         email_guess_status="unconfirmed")
    lead(store, id="l2", company="Beta", domain="beta.in", contact_name="Bina Shah", open_roles=2, email_guess="bina@beta.in",
         email_guess_status="rejected")
    he.run_engine(store, {}, NOW)
    assert fake.asked == []                                          # default: an open guess is left alone
    report = he.run_engine(store, {}, NOW, recheck=True)
    saved = {l["id"]: l for l in store.read("leads")}
    assert fake.asked == ["anita.desai@acme.in"] and saved["l1"]["email"] == "anita.desai@acme.in" and report["confirmed"] == 1
    assert not saved["l2"].get("email")                              # a rejected set is never retried


def test_published_emails_that_look_wrong_are_labelled():
    ok = {"contact_name": "Ravi Menon", "email": "ravi.menon@acme.in", "domain": "acme.in"}
    assert he.email_notes(ok) == [] and he.trust(ok) == "published"
    other = {"contact_name": "Jayita Sahar", "email": "ravinder.bisht1@puresoftware.com", "domain": "happiestminds.com"}
    assert he.trust(other) == "published_mismatch" and "not the company's happiestminds.com" in he.email_notes(other)[0]
    shared = {"contact_name": "Aradhana Minawala", "email": "careers@thecaistore.com", "domain": "thecaistore.com"}
    assert he.trust(shared) == "published_doubtful" and he.email_notes(shared) == ["shared inbox, not the person"]
    odd = {"contact_name": "Poornima Naveen", "email": "sudharsang@thoucentric.com", "domain": "thoucentric.com"}
    assert he.trust(odd) == "published_doubtful" and "name does not appear" in he.email_notes(odd)[0]
    assert he.trust({**ok, "email_check": {"status": "invalid"}}) == "invalid"
    assert he.trust({**ok, "email_check": {"status": "valid"}}) == "confirmed"


def test_verify_published_checks_busiest_hirers_first_and_marks_invalid(monkeypatch, tmp_path):
    client, store, fake = run_with(monkeypatch, tmp_path, {"abe@acme.in": "valid", "bea@beta.in": "invalid"})
    lead(store, id="l1", company="Acme", domain="acme.in", contact_name="Abe Cole", email="abe@acme.in", open_roles=9)
    lead(store, id="l2", company="Beta", domain="beta.in", contact_name="Bea Cole", email="bea@beta.in", open_roles=4)
    lead(store, id="l3", company="Gamma", domain="gamma.in", contact_name="Gus Cole", email="gus@gamma.in", open_roles=1)
    report = he.run_engine(store, {}, NOW, check_published=2)
    saved = {l["id"]: l for l in store.read("leads")}
    assert fake.asked == ["abe@acme.in", "bea@beta.in"] and report["published_valid"] == 1 and report["published_invalid"] == 1
    assert he.trust(saved["l1"]) == "confirmed" and he.trust(saved["l2"]) == "invalid" and "email_check" not in saved["l3"]


def test_sequence_never_sends_to_an_address_a_check_called_invalid(tmp_path):
    client, store = make_client(tmp_path)
    r = client.post("/api/leads/import", json={"csv": "company,contact_name,email,role\nAcme,Abe Cole,abe@acme.in,Agent\nBeta,Bea Cole,bea@beta.in,Agent\n"})
    assert r.status_code == 200, r.text
    ids = {l["email"]: l["id"] for l in store.read("leads")}
    store.update_lead(ids["bea@beta.in"], lambda l: {**l, "email_check": {"status": "invalid", "email": l["email"]}})
    for lid in ids.values():
        assert client.post(f"/api/leads/{lid}/enroll").status_code == 200
    out = client.post("/api/sequences/run", json={"now": "2026-11-10T05:00:00+00:00"}).json()
    sent_to = [m["to"] for m in client.get("/api/outbox").json()]
    assert sent_to == ["abe@acme.in"] and out["blocked"] == 1
    assert "failed a mailbox check" in next(l for l in store.read("leads") if l["email"] == "bea@beta.in")["sequence"]["blocked"]


def test_campaign_verification_can_use_the_newer_verifiers(tmp_path):
    from app import campaign_pipeline
    client, store = make_client(tmp_path)
    store.write("integrations", {"email_verifier": {"mode": "api", "values": {"provider": "Kickbox", "api_key": "k"}}})
    calls = []

    def http(url, headers, body):
        calls.append(url)
        return {"result": "deliverable", "reason": "accepted_email", "success": True}
    spec_ctx = {"source_http": lambda method, url, headers, body, form: http(url, headers, body)}
    config = campaign_pipeline.src.verifier_config(store.read("integrations"))
    assert config["supported"] is False                                   # the old path still does not know Kickbox ...
    from app.sources import registry
    ctx = registry.make_ctx(store, spec_ctx, 10).bind(registry.get("kickbox"), {"api_key": "k"})
    assert registry.get("kickbox").verify(ctx, "a@acme.in")["status"] == "valid" and "api.kickbox.com" in calls[0]   # ... but the registry does
    assert hasattr(campaign_pipeline, "_verify_one")
