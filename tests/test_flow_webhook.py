"""A flow that starts from a webhook: the address is made on turning the flow on, and a post to it starts the flow."""
from app import capture_connectors
from tests.flow_helpers import edge, make_client, node


def _flow(c, trigger="trigger.webhook"):
    nodes = [node("t", trigger), node("n", "add_tag", tag="from-hook")]
    r = c.post("/api/flows", json={"name": "Hook flow", "nodes": nodes, "edges": [edge("t", "n")]})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_webhook_address_only_for_webhook_flows(tmp_path):
    c = make_client(tmp_path, csv=None)
    other = _flow(c, "trigger.manual")
    assert c.get(f"/api/flows/{other}/webhook").status_code == 400


def test_post_to_the_address_starts_the_flow(tmp_path):
    c = make_client(tmp_path, csv=None)
    fid = _flow(c)
    assert c.post(f"/api/flows/{fid}/activate").status_code == 200
    hook = c.get(f"/api/flows/{fid}/webhook").json()
    assert hook["active"] and hook["token"] and hook["urls"]["capture"].endswith(hook["connector_id"])
    r = c.post(f"/api/inbound/{hook['connector_id']}", headers={"Authorization": f"Bearer {hook['token']}"},
               json={"name": "Asha Rao", "email": "asha@hooked.example", "company": "Hooked Co", "phone": "98100 11111"})
    assert r.status_code == 200, r.text
    runs = c.get(f"/api/flows/{fid}/runs").json()
    assert len(runs) == 1 and runs[0]["lead_id"] == r.json()["lead_id"]


def test_address_is_stable_and_one_per_flow(tmp_path):
    c = make_client(tmp_path, csv=None)
    fid = _flow(c)
    first = c.get(f"/api/flows/{fid}/webhook").json()
    c.post(f"/api/flows/{fid}/activate")
    again = c.get(f"/api/flows/{fid}/webhook").json()
    assert first["connector_id"] == again["connector_id"] and first["token"] == again["token"]
    assert sum(1 for x in capture_connectors.all_connectors(c.app.state.store) if x.get("enroll_flow_id") == fid) == 1


def test_wrong_token_is_refused(tmp_path):
    c = make_client(tmp_path, csv=None)
    fid = _flow(c)
    c.post(f"/api/flows/{fid}/activate")
    hook = c.get(f"/api/flows/{fid}/webhook").json()
    r = c.post(f"/api/inbound/{hook['connector_id']}", headers={"Authorization": "Bearer nope"}, json={"email": "x@y.example"})
    assert r.status_code == 401
