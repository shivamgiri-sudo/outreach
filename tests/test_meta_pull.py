"""Meta Lead Ads pull mode, connector workspaces, owner + call task, No automatic messages and the junk flag.

The Graph API is faked: no test touches the network.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from app import capture_connectors, inbound_meta_pull, workspaces
from app.inbound_platforms import PlatformError

from .capture_helpers import add_connector, make_client, tasks_for
from .test_inbound_platforms import signed

TOKEN = "EAAPAGETOKENsecret0123456789abcdef"
FORM = "2545671539249632"
BASE = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


def meta_row(n: int, form=FORM, phone=None, name=None, email=None, when=None):
    when = when or BASE + timedelta(hours=n)
    return {"id": str(9000000000 + n), "created_time": when.strftime("%Y-%m-%dT%H:%M:%S+0000"), "ad_id": "ad1", "ad_name": "Employer video",
            "adset_id": "as1", "adset_name": "India 25-45", "campaign_id": "c1", "campaign_name": "BMI | B2B Employer Leads | India | Oct 2026",
            "form_id": form, "platform": "fb",
            "field_data": [{"name": "email", "values": [email or f"hr{n}@company{n}.in"]},
                           {"name": "full_name", "values": [name or f"Asha Rao {chr(65 + n % 26)}"]},
                           {"name": "phone_number", "values": [phone or f"+9198100{n:05d}"]},
                           {"name": "company_name", "values": [f"Company {n}"]},
                           {"name": "are_you_hiring_for_your_organisation_?", "values": ["Yes"]}]}


class FakeGraph:
    """Answers /{form}/leads (newest first, paged by `after`, honouring the time_created filter) and /{page}/leadgen_forms."""

    def __init__(self, leads=None, forms=None, page_size=2, error=None):
        self.leads = {FORM: list(leads or [])} if not isinstance(leads, dict) else leads
        self.forms, self.page_size, self.error, self.calls = forms or [], page_size, error, []

    def __call__(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        parts = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert q.get("access_token") == TOKEN
        node, edge = parts.path.strip("/").split("/")[1:3]
        if edge == "leadgen_forms":
            rows = list(self.forms)
        else:
            rows = sorted(self.leads.get(node, []), key=lambda r: r["created_time"], reverse=True)
            if q.get("filtering"):
                flt = json.loads(q["filtering"])[0]
                assert flt["field"] == "time_created" and flt["operator"] == "GREATER_THAN"
                rows = [r for r in rows if inbound_meta_pull.meta_time(r["created_time"]).timestamp() > flt["value"]]
        start = int(q.get("after") or 0)
        chunk = rows[start:start + self.page_size]
        more = start + self.page_size < len(rows)
        paging = {"cursors": {"before": str(start), "after": str(start + self.page_size)}}
        if more:
            paging["next"] = f"https://graph.facebook.com/v19.0/{node}/{edge}?access_token={TOKEN}&after={start + self.page_size}"
        return {"data": chunk, "paging": paging}

    def lead_calls(self):
        return [c for c in self.calls if "/leads?" in c]


def setup(tmp_path, graph, **settings):
    client, store = make_client(tmp_path, capture_graph_transport=graph)
    ws = workspaces.create(store, "BookMyInterview", "test")
    body = {"page_access_token": TOKEN, "form_ids": [FORM], "workspace": ws["id"], "default_owner": "Priya", **settings}
    conn = add_connector(client, "meta_ads", "BMI Meta", **body)
    return client, store, conn, ws


def pull_now(client, conn):
    r = client.post(f"/api/capture/connectors/{conn['id']}/pull")
    assert r.status_code == 200, r.text
    return r.json()


def test_pull_pages_through_results_and_backfills(tmp_path):
    graph = FakeGraph([meta_row(n) for n in range(7)], page_size=3)
    client, store, conn, ws = setup(tmp_path, graph)
    out = pull_now(client, conn)
    assert out["ok"] and out["fetched"] == 7 and out["new"] == 7
    assert len(graph.lead_calls()) == 3                                  # 3 + 3 + 1, following the `after` cursor
    first = parse_qs(urlsplit(graph.lead_calls()[0]).query)
    assert "filtering" not in first                                     # the first run backfills everything Meta keeps
    assert set(first["fields"][0].split(",")) >= {"id", "created_time", "ad_name", "campaign_name", "form_id", "platform", "field_data"}
    assert all("after=" in c for c in graph.lead_calls()[1:])
    leads = store.read("leads")
    assert len(leads) == 7 and len({l["id"] for l in leads}) == 7
    lead = next(l for l in leads if l["email"] == "hr3@company3.in")
    assert lead["attribution"]["campaign_name"] == "BMI | B2B Employer Leads | India | Oct 2026" and lead["attribution"]["form_id"] == FORM
    assert lead["custom_fields"]["meta_platform"] == "fb" and lead["source_id"] == "meta_ads"
    view = client.get(f"/api/capture/connectors/{conn['id']}").json()
    assert view["pull"]["last_count"] == 7 and view["pull"]["total_pulled"] == 7 and view["pull"]["last_pull_at"]
    assert view["counters"]["new"] == 7


def test_cursor_fetches_only_new_leads_on_the_second_run(tmp_path):
    graph = FakeGraph([meta_row(n) for n in range(4)], page_size=10)
    client, store, conn, _ = setup(tmp_path, graph)
    pull_now(client, conn)
    graph.leads[FORM].append(meta_row(10))
    graph.calls.clear()
    out = pull_now(client, conn)
    q = parse_qs(urlsplit(graph.lead_calls()[0]).query)
    since = json.loads(q["filtering"][0])[0]["value"]
    newest_before = (BASE + timedelta(hours=3)).timestamp()
    assert newest_before - inbound_meta_pull.OVERLAP.total_seconds() == since       # last created_time minus the overlap
    assert out["new"] == 1 and out["fetched"] == 2 and out["already_seen"] == 1 and len(store.read("leads")) == 5
    again = pull_now(client, conn)                                     # nothing new: the overlap row is skipped by its id
    assert again["new"] == 0 and len(store.read("leads")) == 5 and len(store.read("tasks")) == 5


def test_a_re_pull_never_creates_or_merges_twice(tmp_path):
    graph = FakeGraph([meta_row(n) for n in range(3)], page_size=10)
    client, store, conn, _ = setup(tmp_path, graph)
    pull_now(client, conn)
    capture_connectors.update_connector(store, conn["id"], lambda c: {**c, "pull_state": {}})     # cursor lost: full re-read
    out = pull_now(client, conn)
    assert out["fetched"] == 3 and out["new"] == out["merged"] == 0 and out["already_seen"] == 3
    capture_connectors.update_connector(store, conn["id"], lambda c: {**c, "pull_state": {}, "pull_seen": []})   # and the id list
    out = pull_now(client, conn)
    assert out["new"] == out["merged"] == 0 and out["replay"] == 3          # the capture engine's Meta-id memory still holds
    assert len(store.read("leads")) == 3 and len(store.read("tasks")) == 3


def test_dedupe_against_a_lead_the_webhook_captured(tmp_path):
    row = meta_row(1)
    graph = FakeGraph([row], page_size=10)
    client, store, conn, _ = setup(tmp_path, graph, app_secret="meta-app-secret-123")
    payload = {"object": "page", "entry": [{"id": "p", "time": 1, "changes": [{"field": "leadgen", "value": {
        "leadgen_id": row["id"], "form_id": FORM, "page_id": "p", "field_data": row["field_data"]}}]}]}
    assert signed(client, conn, payload).json()["results"] == ["new"]
    hooked = store.read("leads")[0]
    out = pull_now(client, conn)
    assert out["new"] == 0 and out["merged"] == 0 and out["replay"] == 1
    assert len(store.read("leads")) == 1 and store.read("leads")[0]["id"] == hooked["id"]
    assert len(tasks_for(store, hooked["id"])) == 1


def test_pulled_and_webhook_leads_map_the_same(tmp_path):
    row = meta_row(2)
    parsed = inbound_meta_pull.parsed_lead(row, {"page_id": ""})
    from app.inbound_platforms import meta_lead
    hooked = meta_lead({"leadgen_id": row["id"], "form_id": FORM, "ad_id": "ad1", "adgroup_id": "as1", "created_time": row["created_time"]},
                       TOKEN, "v19.0", lambda url: {k: v for k, v in row.items() if k != "platform"})
    assert parsed["fields"] == hooked["fields"] and parsed["platform_id"] == hooked["platform_id"] == row["id"]
    assert parsed["attribution"] == hooked["attribution"] and parsed["ids"] == hooked["ids"]


def test_leads_land_in_the_chosen_workspace_with_the_owner_and_a_call_task(tmp_path):
    graph = FakeGraph([meta_row(1)], page_size=10)
    client, store, conn, ws = setup(tmp_path, graph)
    assert conn["workspace"] == ws["id"] and conn["default_owner"] == "Priya"
    # The same person already in Main is NOT merged into: dedupe looks inside the connector's workspace only.
    store.update("leads", lambda rows: [*rows, {"id": "mainlead01", "company": "Company 1", "contact_name": "Asha Rao B", "email": "hr1@company1.in",
                                                "phone": "", "stage": "new", "timeline": [], "dedupe_key": "email:hr1@company1.in"}])
    pull_now(client, conn)
    lead = next(l for l in store.read("leads") if l["id"] != "mainlead01")
    assert lead["workspace"] == ws["id"] and lead["owner"] == "Priya" and lead["dedupe_key"] != "email:hr1@company1.in"
    [task] = tasks_for(store, lead["id"])
    assert task["kind"] == "call" and task["owner"] == "Priya" and task["priority"] == "high"
    assert next(l for l in store.read("leads") if l["id"] == "mainlead01").get("workspace") is None
    # A second lead for the same person inside the workspace merges there.
    graph.leads[FORM].append(meta_row(1, phone="+919876500001", when=BASE + timedelta(days=2)) | {"id": "9100000001"})
    out = pull_now(client, conn)
    assert out["merged"] == 1 and sum(1 for l in store.read("leads") if l.get("workspace") == ws["id"]) == 1


def test_pull_now_in_another_workspace_still_uses_the_connector_workspace(tmp_path):
    graph = FakeGraph([meta_row(1)], page_size=10)
    client, store, conn, ws = setup(tmp_path, graph)
    other = workspaces.create(store, "Other", "test")
    r = client.post(f"/api/capture/connectors/{conn['id']}/pull", headers={"X-Workspace": other["id"]})
    assert r.status_code == 200 and store.read("leads")[0]["workspace"] == ws["id"]


def test_existing_connectors_keep_main_and_the_workspace_is_checked(tmp_path):
    client, store = make_client(tmp_path)
    old = add_connector(client, "generic", "Old webhook")
    assert old["workspace"] == "main" and old["no_auto_messages"] is False
    capture_connectors.update_connector(store, old["id"], lambda c: {k: v for k, v in c.items() if k not in ("workspace", "no_auto_messages")})
    assert client.get(f"/api/capture/connectors/{old['id']}").json()["workspace"] == "main"     # saved before this feature
    r = client.put(f"/api/capture/connectors/{old['id']}", json={"workspace": "nope-1234"})
    assert r.status_code == 400 and "does not exist" in r.json()["detail"]
    ws = workspaces.create(store, "BookMyInterview", "test")
    with pytest.raises(capture_connectors.ConnectorError, match="cannot send leads"):
        capture_connectors.change(store, old["id"], {"workspace": ws["id"]}, user={"role": "caller", "email": "c@x.in", "workspaces": ["main"]})
    assert capture_connectors.change(store, old["id"], {"workspace": ws["id"]}, user={"role": "owner", "email": "o@x.in"})["workspace"] == ws["id"]
    cat = client.get("/api/capture/catalogue").json()
    assert {w["id"] for w in cat["workspaces"]} >= {"main", ws["id"]} and "owners" in cat


def test_webhook_and_generic_leads_go_into_the_connector_workspace(tmp_path):
    client, store = make_client(tmp_path)
    ws = workspaces.create(store, "BookMyInterview", "test")
    conn = add_connector(client, "generic", "Hook", workspace=ws["id"])
    r = client.post(f"/api/inbound/{conn['id']}", json={"name": "Ravi Kumar", "email": "ravi@acme.in"}, headers={"X-Capture-Token": conn["token"]})
    assert r.status_code == 200 and store.read("leads")[0]["workspace"] == ws["id"]


def test_no_automatic_messages_blocks_every_auto_enrol(tmp_path):
    graph = FakeGraph([meta_row(1)], page_size=10)
    client, store, conn, _ = setup(tmp_path, graph)
    assert conn["no_auto_messages"] is True                             # on by default for Meta Lead Ads
    r = client.put(f"/api/capture/connectors/{conn['id']}", json={"enroll_flow_id": "flow1"})
    assert r.status_code == 400 and "No automatic messages" in r.json()["detail"]
    pull_now(client, conn)
    lead = store.read("leads")[0]
    assert lead["no_auto_messages"] is True
    from app.flow_engine import FlowError, enroll
    from app.routes_outreach import EnrollRefused, enroll_lead
    flow = {"id": "f1", "status": "active", "version": 1, "nodes": [{"id": "t", "type": "trigger.lead_added", "config": {}}], "edges": []}
    with pytest.raises(FlowError, match="No automatic messages"):
        enroll(store, flow, lead["id"], datetime.now(timezone.utc), why="new lead")             # flow trigger / schedule
    with pytest.raises(EnrollRefused, match="No automatic messages"):
        enroll_lead(store, lead["id"], via="campaign auto-enrol")                               # campaign auto-enrol
    from app import workflows
    rule = {"id": "r1", "name": "Yes -> WhatsApp", "enabled": True, "trigger": {"type": "reply", "value": "interested"},
            "action": {"type": "whatsapp", "value": "first_after_yes"}}
    assert "not sent" in workflows._apply(store, rule, lead, datetime.now(timezone.utc))
    assert store.read("outbox") == []
    # A person can still start it by hand.
    assert enroll_lead(store, lead["id"], by="Priya", via="by hand")["sequence"]


def test_no_messages_off_still_lets_a_flow_start(tmp_path):
    client, store = make_client(tmp_path)
    conn = add_connector(client, "generic", "Hook", no_auto_messages=False)
    client.post(f"/api/inbound/{conn['id']}", json={"name": "Ravi Kumar", "email": "ravi@acme.in"}, headers={"X-Capture-Token": conn["token"]})
    assert not store.read("leads")[0].get("no_auto_messages")


@pytest.mark.parametrize("phone,name", [("+917627912779339---", "Neha Gupta"), ("+91821873,4*,0981", "Neha Gupta"),
                                        ("+919810012345", "asdfghjk 123"), ("+919999999999", "Neha Gupta")])
def test_junk_phone_or_name_is_tagged_and_still_captured(tmp_path, phone, name):
    graph = FakeGraph([meta_row(1, phone=phone, name=name)], page_size=10)
    client, store, conn, _ = setup(tmp_path, graph)
    out = pull_now(client, conn)
    assert out["new"] == 1
    [lead] = store.read("leads")
    assert "check-junk" in lead["tags"] and any("Check before calling" in e["text"] for e in lead["timeline"])
    [task] = tasks_for(store, lead["id"])
    assert task["priority"] == "low" and task["owner"] == "Priya"


def test_junk_lead_with_no_usable_contact_is_still_kept(tmp_path):
    row = meta_row(1, phone="+91821873,4*,0981", name="xxxx")
    row["field_data"] = [f for f in row["field_data"] if f["name"] != "email"]
    graph = FakeGraph([row], page_size=10)
    client, store, conn, _ = setup(tmp_path, graph)
    assert pull_now(client, conn)["new"] == 1
    [lead] = store.read("leads")
    assert "check-junk" in lead["tags"] and any("+91821873,4*,0981" in e["text"] for e in lead["timeline"])
    assert tasks_for(store, lead["id"])[0]["priority"] == "low"


def test_clean_lead_is_not_flagged(tmp_path):
    graph = FakeGraph([meta_row(1, name="Dr. Priya Sharma")], page_size=10)
    client, store, conn, _ = setup(tmp_path, graph)
    pull_now(client, conn)
    assert "check-junk" not in (store.read("leads")[0].get("tags") or [])


def test_the_token_is_never_in_views_logs_or_errors(tmp_path, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    graph = FakeGraph(error=PlatformError(f"boom {TOKEN} https://graph.facebook.com/x?access_token={TOKEN}"))
    client, store, conn, _ = setup(tmp_path, graph)
    assert TOKEN not in json.dumps(conn)
    out = pull_now(client, conn)
    assert not out["ok"] and TOKEN not in json.dumps(out) and "graph.facebook.com" not in out["error"]
    for path in (f"/api/capture/connectors/{conn['id']}", "/api/capture/connectors", "/api/inbound/log"):
        assert TOKEN not in client.get(path).text
    assert TOKEN not in json.dumps(store.read("capture_log"))
    assert TOKEN not in caplog.text and TOKEN not in "".join(capsys.readouterr())
    graph.error = RuntimeError(f"unexpected {TOKEN}")
    out = pull_now(client, conn)
    assert TOKEN not in json.dumps(out) and TOKEN not in json.dumps(store.read("capture_log"))


def test_form_discovery_when_no_form_ids(tmp_path):
    other = "777000111"
    graph = FakeGraph({FORM: [meta_row(1)], other: [meta_row(2, form=other)]}, page_size=10,
                      forms=[{"id": FORM, "name": "BMI | Employer Enquiry Form | India | Oct 2026", "status": "ACTIVE"},
                             {"id": other, "name": "Older form", "status": "ARCHIVED"}, {"id": "555", "name": "Gone", "status": "DELETED"}])
    client, store, conn, _ = setup(tmp_path, graph, form_ids=[], page_id="1122334455")
    assert conn["configured"] and conn["pull"]["configured"]
    out = pull_now(client, conn)
    assert out["new"] == 2 and any("/1122334455/leadgen_forms?" in c for c in graph.calls)
    assert not any("/555/leads" in c for c in graph.calls)
    view = client.get(f"/api/capture/connectors/{conn['id']}").json()
    assert {f["id"] for f in view["pull"]["forms"]} == {FORM, other}


def test_graph_error_backs_off_with_a_safe_message(tmp_path):
    graph = FakeGraph(error=PlatformError("Meta refused the page access token (expired, revoked or wrong). Paste a new one.", 400, 190))
    client, store, conn, _ = setup(tmp_path, graph)
    now = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    connector = capture_connectors.get(store, conn["id"])
    assert inbound_meta_pull.run_meta_pulls(store, graph, now) == 1
    state = capture_connectors.get(store, conn["id"])["pull_state"]
    assert state["fail_count"] == 1 and "page access token" in state["last_error"]
    first_wait = datetime.fromisoformat(state["next_pull_at"]) - now
    assert first_wait > inbound_meta_pull.PULL_EVERY
    assert inbound_meta_pull.run_meta_pulls(store, graph, now + timedelta(minutes=5)) == 0      # backing off: not due yet
    inbound_meta_pull.run_meta_pulls(store, graph, now + first_wait)
    state = capture_connectors.get(store, conn["id"])["pull_state"]
    assert state["fail_count"] == 2 and datetime.fromisoformat(state["next_pull_at"]) - (now + first_wait) > first_wait
    view = client.get(f"/api/capture/connectors/{conn['id']}").json()
    assert view["pull"]["last_error"].startswith("Meta refused") and view["pull"]["fail_count"] == 2
    assert any(r["outcome"] == "error" and "Meta pull" in r["error"] for r in store.read("capture_log"))
    graph.error, graph.leads = None, {FORM: [meta_row(1)]}
    out = pull_now(client, conn)                                       # Pull now ignores the back-off; success resets it
    state = capture_connectors.get(store, conn["id"])["pull_state"]
    assert out["ok"] and state["fail_count"] == 0 and state["last_error"] is None and connector


def test_graph_get_maps_meta_error_codes_without_echoing_text(monkeypatch):
    from app import inbound_platforms

    class Resp:
        status, body = 400, json.dumps({"error": {"message": f"Invalid OAuth access token {TOKEN}", "code": 190}}).encode()
    monkeypatch.setattr(inbound_platforms, "guarded_request", lambda *a, **k: Resp())
    with pytest.raises(PlatformError) as exc:
        inbound_platforms.graph_get(f"https://graph.facebook.com/v19.0/1/leads?access_token={TOKEN}")
    assert exc.value.code == 190 and TOKEN not in str(exc.value) and "page access token" in str(exc.value)


def test_missing_ads_permission_falls_back_to_basic_fields(tmp_path):
    class Picky(FakeGraph):
        def __call__(self, url):
            if "ad_name" in url and "/leads?" in url:
                self.calls.append(url)
                raise PlatformError("no ads_read", 400, 200)
            return super().__call__(url)
    graph = Picky([meta_row(1)], page_size=10)
    client, store, conn, _ = setup(tmp_path, graph)
    out = pull_now(client, conn)
    assert out["ok"] and out["new"] == 1
    assert capture_connectors.get(store, conn["id"])["pull_state"]["fields"] == "basic"


def test_capped_run_keeps_its_place(tmp_path, monkeypatch):
    monkeypatch.setattr(inbound_meta_pull, "MAX_NEW_PER_RUN", 3)
    graph = FakeGraph([meta_row(n) for n in range(5)], page_size=2)
    client, store, conn, _ = setup(tmp_path, graph)
    out = pull_now(client, conn)
    assert out["new"] == 3 and out["pending"] == 2
    out = pull_now(client, conn)
    assert out["new"] == 2 and out["pending"] == 0 and len(store.read("leads")) == 5


def test_switched_off_or_unconfigured_connectors_are_not_pulled(tmp_path):
    graph = FakeGraph([meta_row(1)])
    client, store, conn, _ = setup(tmp_path, graph)
    client.put(f"/api/capture/connectors/{conn['id']}", json={"enabled": False})
    assert inbound_meta_pull.run_meta_pulls(store, graph) == 0 and graph.calls == []
    assert client.post(f"/api/capture/connectors/{conn['id']}/pull").status_code == 409
    bare = add_connector(client, "meta_ads", "No token")
    assert client.post(f"/api/capture/connectors/{bare['id']}/pull").status_code == 400
    assert bare["pull"]["configured"] is False and bare["no_auto_messages"] is True
