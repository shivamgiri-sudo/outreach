"""Meta Lead Ads, Google Ads, website forms, presets, sheet sync and WhatsApp click-to-chat."""
import hashlib
import hmac
import json

from .capture_helpers import add_connector, lead_by_email, leads, make_client, tasks_for

APP_SECRET = "meta-app-secret-123"
GRAPH_LEAD = {"id": "9001", "created_time": "2026-11-03T10:00:00+0000", "ad_id": "ad1", "ad_name": "Diwali video", "adset_id": "as1",
              "adset_name": "Pune 25-40", "campaign_id": "c1", "campaign_name": "Diwali Hiring", "form_id": "f1",
              "field_data": [{"name": "full_name", "values": ["Meera Shah"]}, {"name": "email", "values": ["Meera@Shah.in"]},
                             {"name": "phone_number", "values": ["+919810012345"]}, {"name": "company_name", "values": ["Shah Traders"]},
                             {"name": "city", "values": ["Pune"]}]}


class Graph:
    def __init__(self, answers=None, error=None):
        self.calls, self.answers, self.error = [], answers or {}, error

    def __call__(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.answers.get(url.split("?")[0].rsplit("/", 1)[-1], GRAPH_LEAD)


def meta_setup(tmp_path, graph=None):
    graph = graph or Graph()
    client, store = make_client(tmp_path, capture_graph_transport=graph)
    conn = add_connector(client, "meta_ads", "Meta lead ads", app_secret=APP_SECRET, page_access_token="PAGE-TOKEN", graph_version="v19.0")
    return client, store, conn, graph


def signed(client, conn, payload, secret=APP_SECRET, headers=None):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(f"/api/inbound/meta/{conn['id']}", content=body,
                       headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig, **(headers or {})})


def notification(leadgen_id="9001"):
    return {"object": "page", "entry": [{"id": "page1", "time": 1, "changes": [{"field": "leadgen", "value": {
        "leadgen_id": leadgen_id, "form_id": "f1", "page_id": "page1", "ad_id": "ad1", "adgroup_id": "as1", "created_time": 1730000000}}]}]}


def test_meta_handshake_accepts_the_verify_token_only(tmp_path):
    client, store, conn, _ = meta_setup(tmp_path)
    assert conn["verify_token"] and conn["urls"]["callback"].endswith(f"/api/inbound/meta/{conn['id']}")
    good = client.get(f"/api/inbound/meta/{conn['id']}", params={"hub.mode": "subscribe", "hub.verify_token": conn["verify_token"], "hub.challenge": "777"})
    assert good.status_code == 200 and good.text == "777"
    for params in ({"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
                   {"hub.mode": "subscribe", "hub.challenge": "1"}, {"hub.mode": "unsubscribe", "hub.verify_token": conn["verify_token"]}):
        assert client.get(f"/api/inbound/meta/{conn['id']}", params=params).status_code == 401


def test_meta_signature_good_and_bad(tmp_path):
    client, store, conn, graph = meta_setup(tmp_path)
    assert signed(client, conn, notification(), secret="wrong-secret").status_code == 401
    assert client.post(f"/api/inbound/meta/{conn['id']}", json=notification()).status_code == 401
    assert graph.calls == [] and leads(store) == []
    assert signed(client, conn, notification()).status_code == 200


def test_meta_fetches_the_lead_through_the_injected_transport(tmp_path):
    client, store, conn, graph = meta_setup(tmp_path)
    r = signed(client, conn, notification())
    assert r.status_code == 200 and r.json()["results"] == ["new"]
    assert len(graph.calls) == 1 and graph.calls[0].startswith("https://graph.facebook.com/v19.0/9001?access_token=PAGE-TOKEN")
    lead = lead_by_email(store, "meera@shah.in")
    assert (lead["company"], lead["contact_name"], lead["phone"], lead["city"]) == ("Shah Traders", "Meera Shah", "+919810012345", "Pune")
    assert lead["source"] == "meta_ads" and lead["inbound"]
    att = lead["attribution"]
    assert (att["campaign_name"], att["adset_name"], att["ad_name"], att["form_id"], att["ad_id"], att["platform"]) == (
        "Diwali Hiring", "Pune 25-40", "Diwali video", "f1", "ad1", "meta")
    assert "PAGE-TOKEN" not in json.dumps(store.read("capture_log")) and store.read("capture_log")[0]["platform_ids"]["leadgen_id"] == "9001"
    assert tasks_for(store, lead["id"])[0]["title"] == "Call now: inbound lead from Facebook / Instagram ads"
    replay = signed(client, conn, notification())                                # Meta retries the same notification
    assert replay.json()["results"] == ["replay"] and len(leads(store)) == 1 and len(tasks_for(store, lead["id"])) == 1


def test_meta_fetch_failure_is_a_5xx_so_meta_retries_and_is_logged_without_the_token(tmp_path):
    from app.inbound_platforms import PlatformError
    client, store, conn, graph = meta_setup(tmp_path, Graph(error=PlatformError("Meta could not be reached.")))
    r = signed(client, conn, notification())
    assert r.status_code == 502 and leads(store) == []
    row = client.get("/api/inbound/log").json()[0]
    assert row["outcome"] == "error" and "Meta could not be reached" in row["error"] and "PAGE-TOKEN" not in json.dumps(row)
    graph.error = None
    assert signed(client, conn, notification()).status_code == 200 and len(leads(store)) == 1


def test_meta_payload_with_field_data_needs_no_fetch_and_accepts_the_capture_token(tmp_path):
    client, store, conn, graph = meta_setup(tmp_path)
    direct = {"leadgen_id": "55", "form_id": "f9", "field_data": GRAPH_LEAD["field_data"]}
    r = client.post(f"/api/inbound/meta/{conn['id']}", json=direct, headers={"X-Capture-Token": conn["token"]})
    assert r.status_code == 200 and r.json()["results"] == ["new"] and graph.calls == []
    entry = {"entry": [{"changes": [{"field": "leadgen", "value": {"leadgen_id": "56", "form_id": "f9", "field_data": [
        {"name": "email", "values": ["z@zeta.in"]}, {"name": "full_name", "values": ["Zed"]}]}}]}]}
    assert signed(client, conn, entry).json()["results"] == ["new"] and graph.calls == []


def test_google_ads_payload_key_and_test_flag(tmp_path):
    client, store = make_client(tmp_path)
    conn = add_connector(client, "google_ads", "Google lead form")
    body = {"lead_id": "g-1", "api_version": "1.0", "form_id": "form7", "campaign_id": "camp9", "adgroup_id": "ag3", "creative_id": "cr2",
            "gcl_id": "GCL1", "google_key": conn["token"], "is_test": False,
            "user_column_data": [{"column_id": "FULL_NAME", "string_value": "Kiran Rao"}, {"column_id": "EMAIL", "string_value": "kiran@kr.in"},
                                 {"column_id": "PHONE_NUMBER", "string_value": "+91 98100 12345"}, {"column_id": "COMPANY_NAME", "string_value": "KR Foods"},
                                 {"column_id": "JOB_TITLE", "string_value": "Owner"}, {"column_id": "CITY", "string_value": "Noida"}]}
    url = conn["urls"]["webhook"]
    assert url.endswith(f"/api/inbound/google/{conn['id']}")
    assert client.post(f"/api/inbound/google/{conn['id']}", json={**body, "google_key": "wrong"}).status_code == 401
    assert client.post(f"/api/inbound/google/{conn['id']}", json={k: v for k, v in body.items() if k != "google_key"}).status_code == 401
    r = client.post(f"/api/inbound/google/{conn['id']}", json=body)
    assert r.status_code == 200 and r.json()["new"]
    lead = lead_by_email(store, "kiran@kr.in")
    assert (lead["title"], lead["city"], lead["source"], lead["contact_name"]) == ("Owner", "Noida", "google_ads", "Kiran Rao")
    assert lead["company"].lower() == "kr foods" and lead["phone"] == "+919810012345"
    att = lead["attribution"]
    assert (att["campaign_id"], att["adset_id"], att["ad_id"], att["form_id"], att["click_id"], att["platform"]) == (
        "camp9", "ag3", "cr2", "form7", "GCL1", "google")
    assert client.post(f"/api/inbound/google/{conn['id']}", json=body).json()["status"] == "replay"
    test = client.post(f"/api/inbound/google/{conn['id']}", json={**body, "lead_id": "t-1", "is_test": True,
                                                                  "user_column_data": [{"column_id": "EMAIL", "string_value": "t@test.in"}]})
    assert test.status_code == 200
    t = lead_by_email(store, "t@test.in")
    assert t["is_test"] and tasks_for(store, t["id"]) == [] and client.get("/api/dashboard").json()["total_leads"] == 1


def form_setup(tmp_path, **extra):
    client, store = make_client(tmp_path)
    conn = add_connector(client, "website_form", "Landing page", allowed_origins=["https://www.brand.in"], min_fill_seconds=3, **extra)
    return client, store, conn


def form_post(client, conn, fields, origin="https://www.brand.in", started_ms=None):
    import time
    data = {"_form_key": conn["form_key"], "_t": str(started_ms if started_ms is not None else int((time.time() - 30) * 1000)), **fields}
    return client.post(f"/api/inbound/{conn['id']}", data=data, headers={"Origin": origin} if origin else {})


def test_website_form_embed_script_has_no_secret(tmp_path):
    client, store, conn = form_setup(tmp_path)
    r = client.get(f"/api/inbound/{conn['id']}/embed.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"]
    assert conn["form_key"] in r.text and conn["token"] not in r.text and f"/api/inbound/{conn['id']}" in r.text
    assert conn["snippet"].startswith('<div id="mcn-lead-form"></div>') and conn["urls"]["embed"] in conn["snippet"]
    other = add_connector(client, "generic", "not a form")
    assert client.get(f"/api/inbound/{other['id']}/embed.js").status_code == 401


def test_website_form_accepts_the_public_key_and_applies_cors_for_allowed_origins_only(tmp_path):
    client, store, conn = form_setup(tmp_path)
    r = form_post(client, conn, {"name": "Visitor", "phone": "9810012345", "email": "v@visit.in", "consent": "yes", "utm_source": "google",
                                 "landing_url": "https://www.brand.in/lp", "whatsapp_optin": "yes"})
    assert r.status_code == 200 and r.json()["new"] and r.headers["access-control-allow-origin"] == "https://www.brand.in"
    lead = lead_by_email(store, "v@visit.in")
    assert lead["source"] == "website_form" and lead["attribution"]["utm_source"] == "google" and lead["attribution"]["platform"] == "website"
    assert sorted(x["channel"] for x in store.read("consent_ledger")) == ["call", "email", "whatsapp"]
    bad = form_post(client, conn, {"name": "Evil", "email": "e@evil.in"}, origin="https://evil.example")
    assert bad.status_code == 403 and "access-control-allow-origin" not in bad.headers
    assert form_post(client, conn, {"name": "No origin", "email": "n@evil.in"}, origin=None).status_code == 403
    assert len(leads(store)) == 1
    assert client.post(f"/api/inbound/{conn['id']}", data={"_form_key": "pk_wrong", "email": "x@x.in"}).status_code == 401
    # the secret token works from servers whatever the origin
    assert client.post(f"/api/inbound/{conn['id']}", json={"name": "Server", "email": "s@srv.in"}, headers={"X-Capture-Token": conn["token"]}).status_code == 200


def test_website_form_honeypot_and_minimum_fill_time(tmp_path):
    client, store, conn = form_setup(tmp_path)
    import time
    trap = form_post(client, conn, {"name": "Bot", "email": "bot@bot.in", "hp": "http://spam"})
    fast = form_post(client, conn, {"name": "Fast", "email": "fast@bot.in"}, started_ms=int(time.time() * 1000))
    no_clock = client.post(f"/api/inbound/{conn['id']}", data={"_form_key": conn["form_key"], "name": "Raw", "email": "raw@bot.in"},
                           headers={"Origin": "https://www.brand.in"})
    assert trap.status_code == fast.status_code == no_clock.status_code == 200      # bots are told nothing
    assert leads(store) == []
    errors = [r["error"] for r in client.get("/api/inbound/log").json()]
    assert "refused: spam trap filled" in errors and "refused: form sent too fast" in errors
    assert form_post(client, conn, {"name": "Real", "email": "real@human.in"}).json()["new"]


def test_website_form_with_no_origin_list_accepts_any_site(tmp_path):
    client, store = make_client(tmp_path)
    conn = add_connector(client, "website_form", "Open form")
    r = client.post(f"/api/inbound/{conn['id']}", data={"_form_key": conn["form_key"], "name": "Any", "email": "a@any.in"}, headers={"Origin": "https://x.example"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "https://x.example"


def test_presets_use_the_generic_engine(tmp_path):
    client, store = make_client(tmp_path)
    cases = {
        "indiamart": {"CODE": 200, "STATUS": "SUCCESS", "RESPONSE": {"UNIQUE_QUERY_ID": "IM-77", "SENDER_NAME": "Ravi Jain", "SENDER_MOBILE": "+91-9810012345",
                      "SENDER_EMAIL": "ravi@jain.in", "SENDER_COMPANY": "Jain Steel", "SENDER_CITY": "Jaipur", "QUERY_MESSAGE": "Need quote", "QUERY_PRODUCT_NAME": "TMT bars"}},
        "justdial": {"leadid": "JD-1", "name": "Seema", "mobile": "9810012346", "email": "seema@jd.in", "city": "Delhi", "category": "Staffing"},
        "tradeindia": {"inquiry_id": "TI-1", "sender_name": "Omar", "sender_mobile": "9810012347", "sender_email": "omar@ti.in", "sender_co": "Omar Exports"},
        "tiktok_ads": {"lead_id": "TT-1", "full_name": "Tina", "phone_number": "9810012348", "email": "tina@tt.in", "campaign_name": "Spring", "ad_name": "Ad A"},
        "linkedin_leadgen": {"Lead ID": "LI-1", "First Name": "Lara", "Last Name": "Pinto", "Email": "lara@li.in", "Phone Number": "9810012349",
                             "Company Name": "Pinto & Co", "Job Title": "CHRO", "Campaign Name": "Q4 hiring"},
    }
    for kind, payload in cases.items():
        conn = add_connector(client, kind)
        assert conn["field_map"] and conn["source_id"] == kind
        r = client.post(f"/api/inbound/{conn['id']}?token={conn['token']}", json=payload)
        assert r.status_code == 200 and r.json()["new"], (kind, r.text)
        assert client.post(f"/api/inbound/{conn['id']}?token={conn['token']}", json=payload).json()["status"] == "replay", kind
    by_source = {l["source"]: l for l in leads(store)}
    assert set(by_source) == set(cases)
    assert by_source["indiamart"]["email"] == "ravi@jain.in" and by_source["indiamart"]["phone"] == "+919810012345"
    assert by_source["linkedin_leadgen"]["contact_name"] == "Lara Pinto" and by_source["linkedin_leadgen"]["attribution"]["campaign_name"] == "Q4 hiring"
    assert by_source["justdial"]["city"] == "Delhi" and by_source["tradeindia"]["company"] == "Omar Exports"
    assert by_source["tiktok_ads"]["attribution"]["ad_name"] == "Ad A"
    for kind in cases:
        c = next(x for x in client.get("/api/capture/connectors").json() if x["type"] == kind)
        assert client.post(f"/api/inbound/{c['id']}/test").json()["ok"], kind          # the Test button builds a payload from the preset


def sheet_connector(client, url="https://docs.google.com/spreadsheets/d/e/2PACX-abc/pub?output=csv", **extra):
    return add_connector(client, "sheet_sync", "Meta leads sheet", sheet_url=url, **extra)


CSV1 = "Name,Email,Phone,Company\nAsha Rao,asha@acme.in,98100 12345,Acme\nBen Roy,ben@beta.in,,Beta\n"


def test_sheet_sync_imports_new_rows_only_and_is_idempotent(tmp_path):
    seen = []
    state = {"csv": CSV1}
    client, store = make_client(tmp_path, capture_sheet_fetcher=lambda url: seen.append(url) or state["csv"])
    conn = sheet_connector(client)
    first = client.post(f"/api/inbound/{conn['id']}/sync").json()
    assert first["new"] == 2 and first["already_seen"] == 0 and len(leads(store)) == 2
    assert seen == ["https://docs.google.com/spreadsheets/d/e/2PACX-abc/pub?output=csv"]
    again = client.post(f"/api/inbound/{conn['id']}/sync").json()
    assert again["new"] == 0 and again["already_seen"] == 2 and len(leads(store)) == 2
    state["csv"] = CSV1 + "Cy Das,cy@gamma.in,,Gamma\n"
    third = client.post(f"/api/inbound/{conn['id']}/sync").json()
    assert third["new"] == 1 and third["already_seen"] == 2 and len(leads(store)) == 3
    lead = lead_by_email(store, "asha@acme.in")
    assert lead["source"] == "sheet_sync" and lead["inbound"] and store.read("tasks") == []        # no 'Call now' flood for sheet rows
    assert store.read("consent_ledger") == []                                                       # rows are not assumed to have agreed
    assert next(c for c in client.get("/api/capture/connectors").json() if c["id"] == conn["id"])["counters"]["new"] == 3


def test_sheet_sync_background_run_and_google_edit_link_conversion(tmp_path):
    from app.inbound_sheet import csv_url
    from app.routes_inbound import run_sheet_syncs
    assert csv_url("https://docs.google.com/spreadsheets/d/AbC_123/edit#gid=77") == "https://docs.google.com/spreadsheets/d/AbC_123/export?format=csv&gid=77"
    client, store = make_client(tmp_path, capture_sheet_fetcher=lambda url: CSV1)
    conn = sheet_connector(client, url="https://docs.google.com/spreadsheets/d/AbC_123/edit", assume_consent=True)
    assert run_sheet_syncs(store, lambda url: CSV1) == 1 and len(leads(store)) == 2
    assert run_sheet_syncs(store, lambda url: CSV1) == 1 and len(leads(store)) == 2
    assert {r["channel"] for r in store.read("consent_ledger")} == {"email", "call"}
    off = client.put(f"/api/capture/connectors/{conn['id']}", json={"enabled": False})
    assert run_sheet_syncs(store, lambda url: CSV1) == 0 and off.json()["enabled"] is False


def test_sheet_sync_refuses_private_addresses_with_the_real_fetcher(tmp_path):
    client, store = make_client(tmp_path)
    for url in ("https://169.254.169.254/latest/meta-data", "https://localhost/sheet.csv", "https://10.0.0.5/x.csv", "https://printer.internal/x.csv"):
        conn = sheet_connector(client, url=url)
        r = client.post(f"/api/inbound/{conn['id']}/sync")
        assert r.status_code == 400 and "private" in r.json()["detail"], url
    assert client.put(f"/api/capture/connectors/{conn['id']}", json={"sheet_url": "http://example.com/x.csv"}).status_code == 400
    assert leads(store) == []
    assert any(r["outcome"] == "error" for r in client.get("/api/inbound/log").json())


def test_whatsapp_click_creates_a_lead_from_an_unknown_first_message(tmp_path):
    from app.inbound import process_inbound_message
    client, store = make_client(tmp_path)
    msg = lambda text, mid, phone="+919810012345": {"channel": "whatsapp", "provider": "meta_cloud", "from_phone": phone, "text": text, "message_id": mid,
                                                    "received_at": "2026-11-03T05:00:00+00:00"}
    process_inbound_message(store, msg("hello", "m0", "+919810099990"))
    assert leads(store) == [] and any(t["kind"] == "unmatched_reply" for t in store.read("tasks"))      # off by default
    add_connector(client, "whatsapp_click", "WhatsApp ads")
    out = process_inbound_message(store, msg("Hi, I saw your ad", "m1"))
    assert out["matched"] and out["matched_by"] == "whatsapp_click"
    lead = leads(store)[0]
    assert lead["source"] == "whatsapp_click" and lead["phone"] == "+919810012345" and lead["inbound"]
    assert lead["consent"]["channel"] == "whatsapp" and lead["whatsapp_last_inbound_at"]
    ledger = store.read("consent_ledger")
    assert [(r["channel"], r["event"]) for r in ledger] == [("whatsapp", "given")] and "Messaged us first" in ledger[0]["text"]
    assert any(t["title"].startswith("Call now") for t in tasks_for(store, lead["id"]))
    again = process_inbound_message(store, msg("Can you call me?", "m2"))
    assert again["lead_id"] == lead["id"] and len(leads(store)) == 1
    stop = process_inbound_message(store, msg("STOP", "m3", "+919810077777"))
    assert not stop["matched"] and len(leads(store)) == 1 and store.read("suppressed_hashes") == []
    erase = process_inbound_message(store, msg("please delete my data", "m4", "+919810066666"))
    assert not erase["matched"] and len(leads(store)) == 1
