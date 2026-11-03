"""The generic capture engine: new, merged, refused, replays, field maps, form posts, consent, speed-to-lead."""
import hashlib

from app.privacy import contact_hash

from .capture_helpers import add_connector, lead_by_email, leads, make_client, post_lead, tasks_for

PAYLOAD = {"full_name": "Asha Rao", "email": "Asha@Acme-Retail.in", "mobile": "98100 12345", "organization": "ACME RETAIL PVT LTD",
           "designation": "HR Head", "city": "Bangalore", "message": "Need 20 telecallers", "utm_source": "facebook",
           "utm_campaign": "diwali", "fbclid": "abc123"}


def setup(tmp_path, **extra):
    client, store = make_client(tmp_path)
    return client, store, add_connector(client, **extra)


def test_new_lead_is_normalised_attributed_and_sourced(tmp_path):
    client, store, conn = setup(tmp_path)
    r = post_lead(client, conn, PAYLOAD)
    assert r.status_code == 200 and r.json()["status"] == "new" and r.json()["new"] is True
    lead = lead_by_email(store, "asha@acme-retail.in")
    assert lead["id"] == r.json()["lead_id"]
    assert (lead["company"], lead["contact_name"], lead["phone"], lead["city"], lead["title"]) == (
        "Acme Retail", "Asha Rao", "+919810012345", "Bengaluru", "HR Head")
    assert lead["stage"] == "new" and lead["source"] == "zapier_webhook" and lead["source_id"] == "zapier_webhook"
    assert lead["inbound"] is True and lead["first_inbound_at"] and lead["id"]
    att = lead["attribution"]
    assert att["utm_source"] == "facebook" and att["utm_campaign"] == "diwali" and att["click_id"] == "abc123" and att["captured_at"]
    assert lead["sources"][0]["id"] == "zapier_webhook" and lead["sources"][0]["attribution"]["utm_campaign"] == "diwali"
    kinds = [e["kind"] for e in lead["timeline"]]
    assert "created" in kinds and any("Need 20 telecallers" in e["text"] for e in lead["timeline"])
    assert lead["timeline"][0]["text"].startswith("Inbound from Webhook (Zapier / Make / n8n)")


def test_existing_lead_is_merged_never_dropped(tmp_path):
    client, store, conn = setup(tmp_path)
    client.post("/api/leads/import", json={"csv": "company,email,title\nAcme Retail,asha@acme-retail.in,\n", "source": "apollo"})
    before = leads(store)[0]
    r = post_lead(client, conn, {**PAYLOAD, "message": ""})
    assert r.json()["status"] == "merged" and r.json()["lead_id"] == before["id"] and len(leads(store)) == 1
    lead = leads(store)[0]
    assert lead["source"] == "apollo" and lead["phone"] == "+919810012345" and lead["title"] == "HR Head"
    assert [t["id"] for t in lead["sources"]] == ["apollo", "zapier_webhook"] and lead["inbound"] is True
    assert any(e["text"].startswith("Inbound from Webhook") and "same email" in e["text"] for e in lead["timeline"])


def test_phone_only_lead_matches_an_existing_phone(tmp_path):
    client, store, conn = setup(tmp_path)
    client.post("/api/leads/import", json={"csv": "company,phone\nBeta Logistics,98100 12345\n", "source": "manual"})
    r = post_lead(client, conn, {"name": "Sam Rao", "phone": "+91 98100 12345"})
    assert r.json()["status"] == "merged" and len(leads(store)) == 1


def test_bad_shapes_and_tokens_are_refused_with_4xx(tmp_path):
    client, store, conn = setup(tmp_path)
    assert post_lead(client, conn, {"name": "No contact"}).status_code == 422
    assert post_lead(client, conn, {"name": "Typo", "email": "not-an-email"}).status_code == 422
    assert client.post(f"/api/inbound/{conn['id']}", json=PAYLOAD).status_code == 401
    assert post_lead(client, conn, PAYLOAD, token="wrong").status_code == 401
    assert client.post("/api/inbound/capdeadbeef", json=PAYLOAD, headers={"X-Capture-Token": "x"}).status_code == 401
    assert client.post(f"/api/inbound/{conn['id']}", content=b"{not json", headers={"X-Capture-Token": conn["token"],
                                                                                   "Content-Type": "application/json"}).status_code == 400
    assert leads(store) == []
    assert client.get("/api/inbound/log").json()[0]["outcome"] == "error"


def test_token_in_query_and_form_encoded_bodies_work(tmp_path):
    client, store, conn = setup(tmp_path)
    r = client.post(f"/api/inbound/{conn['id']}?token={conn['token']}", data={"name": "Form Person", "email": "f@form.in", "phone": "9810012345"})
    assert r.status_code == 200 and r.json()["new"]
    r = client.post(f"/api/inbound/{conn['id']}", data={"email": "g@gform.in"}, headers={"Authorization": f"Bearer {conn['token']}"})
    assert r.status_code == 200 and len(leads(store)) == 2


def test_field_map_with_dotted_paths_and_custom_fields(tmp_path):
    client, store, conn = setup(tmp_path, field_map={"email": "contact.mail_address", "name": "contact.who", "phone": "contact.numbers.0",
                                                    "company": "biz.title", "custom.budget": "answers.budget"})
    payload = {"contact": {"mail_address": "x@mapco.in", "who": "Dev Patel", "numbers": ["9810055555", "9810066666"]},
               "biz": {"title": "MapCo"}, "answers": {"budget": "5 lakh"}, "fav_colour": "blue", "email": "decoy@other.in"}
    r = post_lead(client, conn, payload)
    assert r.status_code == 200
    lead = leads(store)[0]
    assert (lead["email"], lead["contact_name"], lead["phone"], lead["company"]) == ("x@mapco.in", "Dev Patel", "+919810055555", "MapCo")
    assert lead["custom_fields"]["budget"] == "5 lakh" and lead["custom_fields"]["fav_colour"] == "blue"


def test_attribution_is_whitelisted_stringified_and_capped(tmp_path):
    client, store, conn = setup(tmp_path)
    payload = {**PAYLOAD, "utm_term": "x" * 5000, "utm_medium": {"nested": "no"}, "evil": "<script>", "campaign_name": ["a", "b"],
               "landing_url": "https://acme.in/lp?x=1", "referrer": "https://google.com", "adset_id": 123}
    post_lead(client, conn, payload)
    att = leads(store)[0]["attribution"]
    assert len(att["utm_term"]) == 300 and "utm_medium" not in att and "evil" not in att
    assert att["campaign_name"] == "a, b" and att["adset_id"] == "123" and att["landing_url"] == "https://acme.in/lp?x=1"
    assert set(att) <= {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "campaign_id", "campaign_name", "adset_id",
                        "adset_name", "ad_id", "ad_name", "form_id", "form_name", "platform", "click_id", "landing_url", "referrer",
                        "captured_at"}


def test_suppressed_and_erased_people_are_refused_with_200_and_logged(tmp_path):
    client, store, conn = setup(tmp_path)
    store.write("suppressed", ["blocked@acme.in"])
    store.write("suppressed_hashes", [contact_hash("+919810099999")])
    r1 = post_lead(client, conn, {"name": "B", "email": "Blocked@acme.in"})
    r2 = post_lead(client, conn, {"name": "E", "email": "fresh@acme.in", "phone": "98100 99999"})
    assert r1.status_code == r2.status_code == 200 and r1.json()["status"] == r2.json()["status"] == "refused"
    assert "lead_id" not in r1.json() and leads(store) == []
    log = client.get("/api/inbound/log").json()
    assert [r["outcome"] for r in log] == ["refused", "refused"] and log[0]["error"] == "refused: suppressed/erased"
    assert "blocked@acme.in" not in str(log)
    assert client.get("/api/capture/connectors").json()[0]["counters"]["refused"] == 2


def test_replays_do_not_duplicate_events_or_tasks(tmp_path):
    client, store, conn = setup(tmp_path)
    first = post_lead(client, conn, {**PAYLOAD, "lead_id": "L-1"}).json()
    again = post_lead(client, conn, {**PAYLOAD, "lead_id": "L-1", "message": "changed"}).json()
    same_hash = post_lead(client, conn, {k: v for k, v in PAYLOAD.items()}).json()
    keyed = post_lead(client, conn, {"name": "K", "email": "k@k.in"}, headers={"Idempotency-Key": "abc"}).json()
    keyed2 = post_lead(client, conn, {"name": "K", "email": "k@k.in", "message": "x"}, headers={"Idempotency-Key": "abc"}).json()
    assert first["status"] == "new" and again["status"] == "replay" and again["lead_id"] == first["lead_id"]
    assert same_hash["status"] == "replay" and keyed["status"] == "new" and keyed2["status"] == "replay"
    lead = lead_by_email(store, "asha@acme-retail.in")
    assert len(lead["sources"]) == 1 and len([e for e in lead["timeline"] if e["kind"] == "inbound"]) == 0
    assert len(tasks_for(store, lead["id"])) == 1
    assert [r["outcome"] for r in client.get("/api/inbound/log").json()].count("replay") == 3


def test_a_genuine_second_submission_later_is_merged_with_its_own_touch(tmp_path):
    client, store, conn = setup(tmp_path)
    post_lead(client, conn, {"name": "Asha", "email": "a@a.in", "utm_campaign": "one"})
    r = post_lead(client, conn, {"name": "Asha", "email": "a@a.in", "utm_campaign": "two", "message": "second time"})
    assert r.json()["status"] == "merged"
    lead = leads(store)[0]
    assert [t["attribution"]["utm_campaign"] for t in lead["sources"]] == ["one", "two"]
    assert lead["attribution"]["utm_campaign"] == "one"                      # first-touch stays on the lead
    assert len(tasks_for(store, lead["id"])) == 1                            # one open call task, not two


def test_consent_is_email_and_call_only_unless_whatsapp_opt_in(tmp_path):
    client, store, conn = setup(tmp_path)
    post_lead(client, conn, {"name": "A", "email": "a@a.in", "phone": "9810012345"})
    post_lead(client, conn, {"name": "B", "email": "b@b.in", "phone": "9810012346", "whatsapp_optin": "yes"})
    post_lead(client, conn, {"name": "C", "email": "c@c.in", "phone": "9810012347", "whatsapp_optin": "no"})
    post_lead(client, conn, {"name": "D", "email": "d@d.in", "phone": "9810012348", "consent": "no"})
    post_lead(client, conn, {"name": "E", "email": "e@e.in", "phone": "9810012349", "email_optin": "false", "whatsapp_optin": True})
    ledger = store.read("consent_ledger")
    chans = lambda email: sorted(r["channel"] for r in ledger if r["lead_id"] == lead_by_email(store, email)["id"])
    assert chans("a@a.in") == ["call", "email"] and chans("b@b.in") == ["call", "email", "whatsapp"]
    assert chans("c@c.in") == ["call", "email"] and chans("d@d.in") == [] and chans("e@e.in") == ["call", "whatsapp"]
    assert all(r["event"] == "given" and r["text"].startswith("Submitted") and r["source"] == "inbound:zapier_webhook" for r in ledger)
    assert not lead_by_email(store, "a@a.in").get("consent")                  # lead.consent opens WhatsApp sends: only an opt-in sets it
    assert lead_by_email(store, "b@b.in")["consent"]["channel"] == "whatsapp"
    assert client.post(f"/api/leads/{lead_by_email(store, 'a@a.in')['id']}/whatsapp", json={"template": "first_after_yes"}).status_code == 400


def test_speed_to_lead_task_notification_and_no_automatic_sending(tmp_path):
    client, store, conn = setup(tmp_path, default_owner="Priya")
    r = post_lead(client, conn, {"name": "Asha", "email": "a@a.in", "phone": "9810012345", "campaign_name": "Diwali"})
    lead_id = r.json()["lead_id"]
    task = tasks_for(store, lead_id)[0]
    assert task["priority"] == "high" and task["owner"] == "Priya" and task["kind"] == "call" and task["due_at"]
    assert task["title"] == "Call now: inbound lead from Webhook (Zapier / Make / n8n)"
    note = store.read("notifications")[0]
    assert note["lead_id"] == lead_id and note["to"] == "Priya" and note["task_id"] == task["id"]
    assert store.read("outbox") == [] and store.read("whatsapp_outbox") == [] and "sequence" not in leads(store)[0]
    post_lead(client, conn, {"name": "NoPhone", "email": "n@n.in"})
    assert tasks_for(store, lead_by_email(store, "n@n.in")["id"])[0]["title"].startswith("Reply now")
    off = add_connector(client, name="No tasks", call_task=False)
    post_lead(client, off, {"name": "Q", "email": "q@q.in"})
    assert tasks_for(store, lead_by_email(store, "q@q.in")["id"]) == []


def test_unsubscribed_lead_is_merged_but_flagged_for_a_human_not_re_consented(tmp_path):
    client, store, conn = setup(tmp_path)
    client.post("/api/leads/import", json={"csv": "company,email\nOld Co,old@old.in\n", "source": "apollo"})
    lead_id = leads(store)[0]["id"]
    store.update_lead(lead_id, lambda l: {**l, "stage": "unsubscribed", "consent_withdrawn_at": "2026-01-01T00:00:00+00:00"})
    r = post_lead(client, conn, {"name": "Old", "email": "old@old.in", "phone": "9810012345"})
    assert r.json()["status"] == "merged"
    lead = leads(store)[0]
    assert lead["stage"] == "unsubscribed" and not lead.get("inbound_consent") and store.read("consent_ledger") == []
    assert tasks_for(store, lead_id)[0]["title"].startswith("Check before calling")


def test_enroll_flow_only_when_it_exists_and_is_active(tmp_path):
    client, store, conn = setup(tmp_path, enroll_flow_id="missing")
    assert post_lead(client, conn, {"name": "A", "email": "a@a.in"}).status_code == 200
    assert store.read("flow_runs") == [] if "flow_runs" in store.snapshot() else True
    graph = {"nodes": [{"id": "t", "type": "trigger.manual", "config": {}, "x": 0, "y": 0},
                       {"id": "n", "type": "add_tag", "config": {"tag": "from-ad"}, "x": 0, "y": 0}],
             "edges": [{"id": "t-next-n", "from": "t", "to": "n", "label": "next"}]}
    flow = client.post("/api/flows", json={"name": "Inbound welcome", **graph}).json()
    client.put(f"/api/capture/connectors/{conn['id']}", json={"enroll_flow_id": flow["id"]})
    post_lead(client, conn, {"name": "B", "email": "b@b.in"})
    assert store.read("flow_runs") == []                                     # a draft flow takes nobody
    assert client.post(f"/api/flows/{flow['id']}/activate").status_code == 200
    r = post_lead(client, conn, {"name": "C", "email": "c@c.in"})
    runs = store.read("flow_runs")
    assert len(runs) == 1 and runs[0]["lead_id"] == r.json()["lead_id"] and runs[0]["flow_id"] == flow["id"]
    assert post_lead(client, conn, {"name": "C", "email": "c@c.in", "message": "again"}).json()["status"] == "merged"
    assert len(store.read("flow_runs")) == 1                                 # merged leads are not enrolled again


def test_test_button_creates_a_labelled_excluded_removable_lead(tmp_path):
    client, store, conn = setup(tmp_path)
    r = client.post(f"/api/inbound/{conn['id']}/test").json()
    assert r["ok"] and r["outcome"] == "new" and r["is_test"] and r["lead"]["company"] == "ZZ Test capture"
    lead = leads(store)[0]
    assert lead["is_test"] and "test" in lead["tags"] and lead["source_id"] == "zapier_webhook"
    assert store.read("tasks") == [] and store.read("consent_ledger") == []
    d = client.get("/api/dashboard").json()
    assert d["total_leads"] == 0 and d["by_source"] == [] and d["inbound_7d"] == 0
    assert client.get("/api/insights").json()["totals"]["leads"] == 0
    assert client.get("/api/inbound/log").json()[0]["is_test"] is True
    again = client.post(f"/api/inbound/{conn['id']}/test").json()
    assert again["outcome"] == "merged" and len(leads(store)) == 1
    assert client.delete(f"/api/capture/connectors/{conn['id']}/test-leads").json() == {"removed": 1}
    assert leads(store) == []


def test_test_lead_never_merges_into_a_real_person(tmp_path):
    client, store, conn = setup(tmp_path)
    client.post("/api/leads/import", json={"csv": "company,email\nReal Co,capture-test+x@example.com\n", "source": "apollo"})
    store.update("leads", lambda ls: [{**l, "email": f"capture-test+{conn['id']}@example.com"} for l in ls])
    client.post(f"/api/inbound/{conn['id']}/test")
    assert len(leads(store)) == 2 and sum(1 for l in leads(store) if l.get("is_test")) == 1


def test_capture_log_keeps_the_last_500_and_never_the_person(tmp_path):
    client, store, conn = setup(tmp_path)
    store.write("capture_log", [{"id": str(i), "at": "x", "connector_id": conn["id"], "outcome": "new"} for i in range(499)])
    for i in range(3):
        post_lead(client, conn, {"name": f"P{i}", "email": f"p{i}@log.in"})
    rows = store.read("capture_log")
    assert len(rows) == 500 and rows[0]["id"] == "2"
    assert "p0@log.in" not in str(rows) and "P0" not in str(rows)
    assert len(client.get("/api/inbound/log?limit=5").json()) == 5


def test_disabled_connector_refuses_and_body_limit(tmp_path):
    client, store, conn = setup(tmp_path)
    client.put(f"/api/capture/connectors/{conn['id']}", json={"enabled": False})
    assert post_lead(client, conn, PAYLOAD).status_code == 403
    client.put(f"/api/capture/connectors/{conn['id']}", json={"enabled": True})
    assert post_lead(client, conn, {"name": "Big", "email": "b@b.in", "message": "x" * 300_000}).status_code == 413
