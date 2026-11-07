"""Owner-only un-suppress for the prospect database: audited, and never lifts anyone on a global list."""
from app import prospect_db as P
from app.privacy import hashes_for
from tests.test_prospect_api import app_client, no_background_sync, seed  # noqa: F401 - fixture is autouse
from tests.test_security_auth import ADMIN, add_user, bearer, login


def _audit(store, action):
    return [r for r in store.read("audit") if r["action"] == action]


def test_unsuppress_email_restores_person_and_is_audited(tmp_path):
    c = app_client(tmp_path)
    store = c.app.state.store
    ids = seed(store)
    assert c.post("/api/prospects/suppress", json={"value": "bala@acme.example"}).json()["people"] == 1
    rows = c.get("/api/prospects/suppressions").json()["rows"]
    assert [(r["value"], r["kind"], r["blocked"]) for r in rows] == [("bala@acme.example", "email", "")]
    r = c.post("/api/prospects/unsuppress", json={"value": "BALA@acme.example"})
    assert r.status_code == 200 and r.json() == {"unsuppressed": "bala@acme.example", "kind": "email", "people": 1, "kept": 0}
    assert not P.get_person(store, ids["Bala Iyer"])["suppressed"]
    assert c.get("/api/prospects/suppressions").json()["rows"] == []
    assert _audit(store, "prospects.unsuppress")[0]["target"] == "bala@acme.example"
    assert c.post("/api/prospects/unsuppress", json={"value": "bala@acme.example"}).status_code == 404


def test_unsuppress_refused_for_global_lists(tmp_path):
    c = app_client(tmp_path)
    store = c.app.state.store
    ids = seed(store)
    for email in ("asha@acme.example", "bala@acme.example", "chitra@acme.example"):
        c.post("/api/prospects/suppress", json={"value": email})
    store.update("suppressed", lambda rows: [*rows, "asha@acme.example"])                      # unsubscribe / do-not-contact
    store.update("suppressed_hashes", lambda rows: [*rows, *hashes_for("bala@acme.example")])    # erased on request
    store.update("leads", lambda rows: [*rows, {"id": "L1", "email": "chitra@acme.example", "stage": "unsubscribed"}])
    blocked = {r["value"]: r["blocked"] for r in c.get("/api/prospects/suppressions").json()["rows"]}
    assert all(blocked.values()) and "unsubscribe" in blocked["asha@acme.example"] and "erased" in blocked["bala@acme.example"]
    for email, why in (("asha@acme.example", "do-not-contact"), ("bala@acme.example", "erased"), ("chitra@acme.example", "unsubscribed")):
        r = c.post("/api/prospects/unsuppress", json={"value": email})
        assert r.status_code == 409 and why in r.json()["detail"], r.text
    assert all(P.get_person(store, ids[n])["suppressed"] for n in ("Asha Rao", "Bala Iyer", "Chitra Das"))
    assert len(c.get("/api/prospects/suppressions").json()["rows"]) == 3
    assert len(_audit(store, "prospects.unsuppress_refused")) == 3


def test_unsuppress_domain_keeps_globally_blocked_people(tmp_path):
    c = app_client(tmp_path)
    store = c.app.state.store
    ids = seed(store)
    c.post("/api/prospects/suppress", json={"value": "acme.example"})
    c.post("/api/prospects/suppress", json={"value": "bala@acme.example"})                      # own opt-out stays
    store.update("suppressed", lambda rows: [*rows, "asha@acme.example"])
    assert all(P.get_person(store, i)["suppressed"] for i in ids.values())
    r = c.post("/api/prospects/unsuppress", json={"value": "acme.example"}).json()
    assert r["kind"] == "domain" and r["people"] == 2 and r["kept"] == 2
    state = {n: P.get_person(store, i)["suppressed"] for n, i in ids.items()}
    assert state == {"Asha Rao": True, "Bala Iyer": True, "Chitra Das": False, "Dev Nair": False}
    assert not P.get_company(store, "acme.example")["suppressed"]


def test_unsuppress_is_owner_only(tmp_path):
    c = app_client(tmp_path, password=ADMIN)
    owner = login(c)
    add_user(c, owner, "cal@x.example", "caller")
    caller = login(c, "long-password-1", "cal@x.example")
    seed(c.app.state.store)
    c.post("/api/prospects/suppress", json={"value": "bala@acme.example"}, headers=bearer(owner))
    assert c.get("/api/prospects/suppressions", headers=bearer(caller)).status_code == 403
    assert c.post("/api/prospects/unsuppress", json={"value": "bala@acme.example"}, headers=bearer(caller)).status_code == 403
    assert c.post("/api/prospects/unsuppress", json={"value": "bala@acme.example"}, headers=bearer(owner)).status_code == 200
