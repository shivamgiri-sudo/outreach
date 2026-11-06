"""Chrome assistant API: pairing tokens, the narrow extension principal, matching, and the human-confirmed flow."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import extension_api as ext, linkedin_seq as li
from app.activities import now_utc
from app.main import create_app

ADMIN = "admin-pass-123"
CSV = ("company,contact_name,title,email,phone,website,open_roles,company_size,country\n"
       "Acme Pvt Ltd,Ravi Kumar,Head of Talent Acquisition,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n"
       "Globex,Meera Rao,Founder,meera@globex.com,98765 43211,https://globex.com,3,50,IN\n")
URL = "https://www.linkedin.com/in/ravi-kumar-example"


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


def make(tmp_path, password=ADMIN):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=password), client=("203.0.113.9", 50000))
    owner = c.post("/api/login", json={"email": "", "password": password}).json()["token"]
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=bearer(owner))
    leads = {l["company"]: l["id"] for l in c.get("/api/leads", headers=bearer(owner)).json()}
    store = c.app.state.store
    store.update_lead(leads["Acme Pvt Ltd"], lambda l: {**l, "linkedin_url": URL})
    return c, owner, leads["Acme Pvt Ltd"], leads["Globex"]


def pair(c, owner, label="laptop"):
    r = c.post("/api/extension/pair", json={"label": label}, headers=bearer(owner))
    assert r.status_code == 200, r.text
    return r.json()


def match(c, token, **params):
    return c.get("/api/extension/match", params=params, headers=bearer(token))


# ---------- pairing ----------
def test_pairing_stores_only_a_hash_and_shows_the_token_once(tmp_path):
    c, owner, _, _ = make(tmp_path)
    p = pair(c, owner)
    assert p["token"].startswith("mcnx_") and len(p["token"]) > 40
    stored = c.app.state.store.read("settings")["extension_tokens"]
    assert len(stored) == 1 and p["token"] not in str(stored) and stored[0]["hash"] == ext._hash(p["token"])
    listed = c.get("/api/extension/tokens", headers=bearer(owner)).json()
    assert [t["id"] for t in listed] == [p["id"]] and "token" not in listed[0] and "hash" not in listed[0]
    assert "extension_tokens" not in c.get("/api/settings", headers=bearer(owner)).text


def test_revoked_token_stops_working(tmp_path):
    c, owner, _, _ = make(tmp_path)
    p = pair(c, owner)
    assert match(c, p["token"], profile_url=URL).status_code == 200
    assert c.delete(f"/api/extension/tokens/{p['id']}", headers=bearer(owner)).status_code == 200
    assert match(c, p["token"], profile_url=URL).status_code == 401
    assert c.delete(f"/api/extension/tokens/{p['id']}", headers=bearer(owner)).status_code == 404


def test_expired_and_unknown_tokens_are_refused(tmp_path):
    c, owner, _, _ = make(tmp_path)
    p = pair(c, owner)
    c.app.state.store.update("settings", lambda s: {**s, "extension_tokens": [{**t, "expires_at": "2020-01-01T00:00:00+00:00"} for t in s["extension_tokens"]]})
    assert match(c, p["token"], profile_url=URL).status_code == 401
    assert match(c, "mcnx_" + "a" * 43, profile_url=URL).status_code == 401


def test_viewer_cannot_create_list_or_revoke_tokens(tmp_path):
    c, owner, _, _ = make(tmp_path)
    c.post("/api/users", json={"email": "v@x.com", "role": "viewer", "password": "long-password-1"}, headers=bearer(owner))
    viewer = c.post("/api/login", json={"email": "v@x.com", "password": "long-password-1"}).json()["token"]
    assert c.post("/api/extension/pair", json={}, headers=bearer(viewer)).status_code == 403
    assert c.get("/api/extension/tokens", headers=bearer(viewer)).status_code == 403
    p = pair(c, owner)
    assert c.delete(f"/api/extension/tokens/{p['id']}", headers=bearer(viewer)).status_code == 403


def test_caller_sees_and_revokes_only_their_own_tokens(tmp_path):
    c, owner, _, _ = make(tmp_path)
    c.post("/api/users", json={"email": "c@x.com", "role": "caller", "password": "long-password-1"}, headers=bearer(owner))
    caller = c.post("/api/login", json={"email": "c@x.com", "password": "long-password-1"}).json()["token"]
    mine = pair(c, caller, "mine")
    theirs = pair(c, owner, "owners")
    assert [t["id"] for t in c.get("/api/extension/tokens", headers=bearer(caller)).json()] == [mine["id"]]
    assert {t["id"] for t in c.get("/api/extension/tokens", headers=bearer(owner)).json()} == {mine["id"], theirs["id"]}
    assert c.delete(f"/api/extension/tokens/{theirs['id']}", headers=bearer(caller)).status_code == 404


def test_token_limit_per_person(tmp_path):
    c, owner, _, _ = make(tmp_path)
    for _ in range(ext.MAX_TOKENS_PER_USER):
        pair(c, owner)
    assert c.post("/api/extension/pair", json={}, headers=bearer(owner)).status_code == 400


# ---------- the extension principal is narrow ----------
@pytest.mark.parametrize("method,path", [
    ("GET", "/api/leads"), ("GET", "/api/dashboard"), ("GET", "/api/users"), ("GET", "/api/settings"), ("GET", "/api/me"),
    ("POST", "/api/leads/import"), ("PUT", "/api/settings"), ("GET", "/api/backup"), ("GET", "/api/linkedin/queue"),
    ("POST", "/api/extension/pair"), ("GET", "/api/extension/tokens"), ("DELETE", "/api/extension/tokens/abc"),
    ("POST", "/api/logout"), ("GET", "/api/extension/leads/x/sent"), ("POST", "/api/extension/match"),
])
def test_extension_token_cannot_call_anything_else(tmp_path, method, path):
    c, owner, lid, _ = make(tmp_path)
    p = pair(c, owner)
    r = c.request(method, path, headers=bearer(p["token"]), json={} if method != "GET" else None)
    assert r.status_code == 403, (path, r.status_code, r.text)


def test_extension_token_works_on_its_own_routes_only(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    p = pair(c, owner)
    assert c.get("/api/extension/queue", headers=bearer(p["token"])).status_code == 200
    assert match(c, p["token"], profile_url=URL).status_code == 200


def test_token_of_a_viewer_is_read_only(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    c.post("/api/users", json={"email": "c@x.com", "role": "caller", "password": "long-password-1"}, headers=bearer(owner))
    caller = c.post("/api/login", json={"email": "c@x.com", "password": "long-password-1"}).json()["token"]
    p = pair(c, caller)
    li.start(c.app.state.store, lid)
    c.put("/api/users/c@x.com", json={"role": "viewer"}, headers=bearer(owner))
    assert match(c, p["token"], profile_url=URL).status_code == 200
    assert c.post(f"/api/extension/leads/{lid}/sent", json={"step": 1}, headers=bearer(p["token"])).status_code == 403
    c.delete("/api/users/c@x.com", headers=bearer(owner))
    assert match(c, p["token"], profile_url=URL).status_code == 401           # the person is gone, so is the token


def test_login_rate_limit_unaffected(tmp_path):
    c, owner, _, _ = make(tmp_path)
    codes = [c.post("/api/login", json={"email": "", "password": "wrong"}).status_code for _ in range(7)]     # make() used 1 of 5/min
    assert codes[:4] == [401] * 4 and codes[-1] == 429
    p = pair(c, owner)                                                    # an already-issued token still works
    assert match(c, p["token"], profile_url=URL).status_code == 200


def test_no_password_mode_is_local_only(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=""))     # testclient = local
    p = c.post("/api/extension/pair", json={}).json()
    assert c.get("/api/leads", headers=bearer(p["token"])).status_code == 403        # even locally, nothing but /api/extension
    remote = TestClient(c.app, client=("203.0.113.9", 1))
    assert remote.get("/api/extension/queue", headers=bearer(p["token"])).status_code == 403


# ---------- matching ----------
@pytest.mark.parametrize("url", [
    URL, URL + "/", URL + "?utm_source=share&trk=x", URL + "/?locale=en", "https://in.linkedin.com/in/ravi-kumar-example/",
    "https://UK.LinkedIn.com/in/Ravi-Kumar-Example", "linkedin.com/in/ravi-kumar-example", "https://www.linkedin.com/in/ravi-kumar-example/details/experience/",
])
def test_match_by_url_variants(tmp_path, url):
    c, owner, lid, _ = make(tmp_path)
    p = pair(c, owner)
    body = match(c, p["token"], profile_url=url).json()
    assert body["matched"] and body["lead_id"] == lid and body["name"] == "Ravi Kumar"


def test_stored_url_variants_match_too(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    c.app.state.store.update_lead(lid, lambda l: {**l, "linkedin_url": "https://in.linkedin.com/in/Ravi-Kumar-Example/?x=1"})
    p = pair(c, owner)
    assert match(c, p["token"], profile_url=URL).json()["lead_id"] == lid


def test_no_match_for_other_sites_or_unknown_people(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    p = pair(c, owner)
    for url in ("https://evil.example/in/ravi-kumar-example", "https://notlinkedin.com/in/ravi-kumar-example",
                "https://www.linkedin.com/in/someone-else", "https://www.linkedin.com/company/acme", ""):
        body = match(c, p["token"], profile_url=url).json()
        assert body["matched"] is False and body["can_send"] is False and body["message"] is None


def test_match_by_name_and_company_fallback(tmp_path):
    c, owner, lid, other = make(tmp_path)
    p = pair(c, owner)
    assert match(c, p["token"], profile_url="https://www.linkedin.com/in/unknown-slug", name="ravi  KUMAR", company="Acme").json()["lead_id"] == lid
    assert match(c, p["token"], name="Meera Rao", company="Globex Limited").json()["lead_id"] == other
    assert match(c, p["token"], name="Ravi Kumar").json()["matched"] is False              # name alone never matches
    assert match(c, p["token"], name="Ravi Kumar", company="Other Co").json()["matched"] is False


def test_ambiguous_name_and_company_does_not_guess(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    c.post("/api/leads/import", json={"csv": CSV.replace("ravi@acme.com", "ravi2@acme.com").replace("98765 43210", "98765 43299"), "source": "x"}, headers=bearer(owner))
    p = pair(c, owner)
    rows = [l for l in c.app.state.store.read("leads") if l["contact_name"] == "Ravi Kumar"]
    assert len(rows) == 2
    assert match(c, p["token"], name="Ravi Kumar", company="Acme").json()["matched"] is False


# ---------- can_send rules ----------
def test_lead_not_on_sequence_gets_no_message(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    b = match(c, pair(c, owner)["token"], profile_url=URL).json()
    assert b["matched"] and not b["can_send"] and b["message"] is None and "Start it" in b["reason"]


def test_connect_step_is_offered_with_the_rendered_note(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    li.start(c.app.state.store, lid)
    b = match(c, pair(c, owner)["token"], profile_url=URL).json()
    assert b["can_send"] and b["message"]["step"] == 1 and b["message"]["kind"] == "connect"
    assert b["message"]["text"].startswith("Hi Ravi,") and len(b["message"]["text"]) <= li.NOTE_LIMIT
    assert b["linkedin_seq"]["status"] == "waiting_to_connect" and b["linkedin_seq"]["next_step"] == 1 and b["linkedin_seq"]["due"]
    assert set(b) >= {"lead_id", "name", "company", "title", "linkedin_seq", "message", "can_send", "reason"}


@pytest.mark.parametrize("stage", ["unsubscribed", "disqualified"])
def test_unsubscribed_or_disqualified_never_offers_a_message(tmp_path, stage):
    c, owner, lid, _ = make(tmp_path)
    li.start(c.app.state.store, lid)
    c.app.state.store.update_lead(lid, lambda l: {**l, "stage": stage})
    t = pair(c, owner)["token"]
    b = match(c, t, profile_url=URL).json()
    assert b["matched"] and b["can_send"] is False and b["message"] is None and stage in b["reason"]
    assert c.post(f"/api/extension/leads/{lid}/sent", json={"step": 1}, headers=bearer(t)).status_code == 409
    assert c.get("/api/extension/queue", headers=bearer(t)).json()["due"] == []


def test_waiting_for_acceptance_has_no_message(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    store = c.app.state.store
    li.start(store, lid)
    li.mark_sent(store, lid, 1)
    b = match(c, pair(c, owner)["token"], profile_url=URL).json()
    assert b["can_send"] is False and b["message"] is None and "accept" in b["reason"] and "accepted" in b["actions"]
    assert b["linkedin_seq"]["status"] == "waiting_for_acceptance"


def test_replied_sequence_is_stopped(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    store = c.app.state.store
    li.start(store, lid)
    li.mark_replied(store, lid, "")
    b = match(c, pair(c, owner)["token"], profile_url=URL).json()
    assert b["can_send"] is False and b["message"] is None and "stopped" in b["reason"] and b["linkedin_seq"]["status"] == "stopped"


def test_next_message_is_not_offered_before_it_is_due(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    store = c.app.state.store
    t0 = now_utc() - timedelta(days=5)
    li.start(store, lid, t0)
    li.mark_sent(store, lid, 1, t0)
    li.mark_accepted(store, lid, t0 + timedelta(days=1))        # step 2 due at t0+3d: already due
    token = pair(c, owner)["token"]
    b = match(c, token, profile_url=URL).json()
    assert b["can_send"] and b["message"]["step"] == 2 and b["message"]["kind"] == "message"
    li.mark_sent(store, lid, 2, t0 - timedelta(days=3))         # sent long ago; step 3 due 4 days after that
    store.update("tasks", lambda rows: [{**t, "due_at": (now_utc() + timedelta(days=3)).isoformat(timespec="seconds")} if t.get("status", "open") == "open" else t for t in rows])
    b = match(c, token, profile_url=URL).json()
    assert b["can_send"] is False and b["message"] is None and "not due" in b["reason"]


def test_already_sent_today(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    store = c.app.state.store
    t0 = now_utc() - timedelta(days=5)
    li.start(store, lid, t0)
    li.mark_sent(store, lid, 1, t0)
    li.mark_accepted(store, lid, t0)
    li.mark_sent(store, lid, 2, now_utc())
    store.update("tasks", lambda rows: [{**t, "due_at": now_utc().isoformat(timespec="seconds")} if t.get("status", "open") == "open" else t for t in rows])
    b = match(c, pair(c, owner)["token"], profile_url=URL).json()
    assert b["can_send"] is False and "Already sent today" in b["reason"]


# ---------- the confirmed flow ----------
def test_sent_accepted_replied_flow_advances_the_sequence(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    store = c.app.state.store
    li.start(store, lid)
    t = pair(c, owner)["token"]
    h = bearer(t)
    # wrong step is refused
    assert c.post(f"/api/extension/leads/{lid}/sent", json={"step": 2}, headers=h).status_code == 409
    r = c.post(f"/api/extension/leads/{lid}/sent", json={"step": 1}, headers=h)
    assert r.status_code == 200 and r.json()["match"]["linkedin_seq"]["status"] == "waiting_for_acceptance"
    assert c.post(f"/api/extension/leads/{lid}/sent", json={"step": 1}, headers=h).status_code == 409      # no double record
    r = c.post(f"/api/extension/leads/{lid}/accepted", headers=h)
    assert r.status_code == 200 and r.json()["match"]["linkedin_seq"]["status"] == "messaging" and r.json()["match"]["linkedin_seq"]["next_step"] == 2
    assert c.post(f"/api/extension/leads/{lid}/accepted", headers=h).status_code == 409
    r = c.post(f"/api/extension/leads/{lid}/replied", json={"text": "Interesting, tell me more"}, headers=h)
    assert r.status_code == 200 and r.json()["match"]["linkedin_seq"]["status"] == "stopped"
    lead = next(l for l in store.read("leads") if l["id"] == lid)
    assert lead["linkedin_seq"]["stopped"] == "replied" and lead["linkedin_seq"]["done"] == [1]
    assert any("LinkedIn step 1 sent" in e["text"] for e in lead["timeline"])


def test_replied_without_text_adds_a_follow_up_task(tmp_path):
    c, owner, lid, _ = make(tmp_path)
    store = c.app.state.store
    li.start(store, lid)
    t = pair(c, owner)["token"]
    assert c.post(f"/api/extension/leads/{lid}/replied", json={}, headers=bearer(t)).status_code == 200
    assert any(x.get("kind") == "follow_up" and x["lead_id"] == lid for x in store.read("tasks"))


def test_flow_errors(tmp_path):
    c, owner, lid, other = make(tmp_path)
    h = bearer(pair(c, owner)["token"])
    assert c.post("/api/extension/leads/nope/sent", json={"step": 1}, headers=h).status_code == 404
    assert c.post(f"/api/extension/leads/{other}/sent", json={"step": 1}, headers=h).status_code == 409      # not on the sequence
    assert c.post(f"/api/extension/leads/{other}/replied", json={}, headers=h).status_code == 409
    assert c.post(f"/api/extension/leads/{lid}/sent", json={"step": 99}, headers=h).status_code == 422
    assert c.post(f"/api/extension/leads/{lid}/replied", json={"text": "x" * 3000}, headers=h).status_code == 422


# ---------- queue ----------
def test_queue_lists_due_steps_with_verified_links(tmp_path):
    c, owner, lid, other = make(tmp_path)
    store = c.app.state.store
    li.start(store, lid)
    store.update_lead(other, lambda l: {**l, "linkedin_url": "https://www.linkedin.com/in/meera"})
    li.start(store, other)
    store.update_lead(other, lambda l: {**l, "linkedin_url": "javascript:alert(1)"})
    q = c.get("/api/extension/queue", headers=bearer(pair(c, owner)["token"])).json()
    by = {d["lead_id"]: d for d in q["due"]}
    assert by[lid]["profile_url"] == URL and by[lid]["step"] == 1 and by[lid]["kind"] == "connect"
    assert by[other]["profile_url"] == ""                    # a non-http(s) link is never offered
    assert "message" not in by[lid]


def test_agent_reports_open_profile_once_and_it_shows_everywhere(tmp_path):
    c, owner, acme, _ = make(tmp_path)
    p = pair(c, owner)
    r = c.post(f"/api/extension/leads/{acme}/profile", json={"open_profile": True, "degree": "2nd"}, headers=bearer(p["token"]))
    assert r.status_code == 200, r.text
    c.post(f"/api/extension/leads/{acme}/profile", json={"open_profile": True, "degree": "2nd"}, headers=bearer(p["token"]))
    lead = c.get(f"/api/leads/{acme}", headers=bearer(owner)).json()
    assert lead["linkedin_profile"]["open_profile"] is True and lead["linkedin_profile"]["degree"] == "2nd"
    assert sum("Open Profile" in e["text"] for e in lead["timeline"]) == 1
    assert match(c, p["token"], profile_url=URL).json()["open_profile"] is True
    assert c.get("/api/intelligence", headers=bearer(owner)).json()["channels"]["linkedin"]["open_profiles"] == 1
    bad = c.post(f"/api/extension/leads/{acme}/profile", json={"open_profile": False, "degree": "9th"}, headers=bearer(p["token"]))
    assert bad.status_code == 422 or c.get(f"/api/leads/{acme}", headers=bearer(owner)).json()["linkedin_profile"]["degree"] == ""
