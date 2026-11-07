"""A Set stage step is checked when the flow is saved: no stage, or a stage that is not configured, is refused with a
clear message; a stage written as the owner's renamed label is stored as its id."""
from tests.flow_helpers import make_client

TRIGGER = {"id": "t", "type": "trigger.manual", "config": {}, "x": 0, "y": 0}
EDGE = [{"id": "e1", "from": "t", "to": "s", "label": "next"}]


def _set_stage(**config):
    return {"id": "s", "type": "set_stage", "config": config, "x": 0, "y": 120}


def test_create_refuses_a_set_stage_step_with_no_stage(tmp_path):
    c = make_client(tmp_path, csv=None)
    r = c.post("/api/flows", json={"name": "Park", "nodes": [TRIGGER, _set_stage()], "edges": EDGE})
    assert r.status_code == 400 and "has no stage" in r.json()["detail"]


def test_edit_refuses_an_unknown_stage_and_names_the_real_ones(tmp_path):
    c = make_client(tmp_path, csv=None)
    flow = c.post("/api/flows", json={"name": "Park", "nodes": [TRIGGER, _set_stage(stage="not_now")], "edges": EDGE}).json()
    r = c.put(f"/api/flows/{flow['id']}", json={"nodes": [TRIGGER, _set_stage(stage="hot lead")], "edges": EDGE})
    assert r.status_code == 400
    assert "'hot lead', which is not one of your stages" in r.json()["detail"] and "Not now" in r.json()["detail"]
    assert c.get(f"/api/flows/{flow['id']}").json()["nodes"][1]["config"]["stage"] == "not_now"     # nothing saved


def test_import_refuses_an_unknown_stage(tmp_path):
    c = make_client(tmp_path, csv=None)
    r = c.post("/api/flows/import", json={"name": "X", "nodes": [TRIGGER, _set_stage(stage="Warm")], "edges": EDGE})
    assert r.status_code == 400 and "not one of your stages" in r.json()["detail"]


def test_configured_stage_label_is_accepted_and_stored_as_the_id(tmp_path):
    c = make_client(tmp_path, csv=None)
    c.app.state.store.update("settings", lambda s: {**s, "stage_labels": {"not_now": "Parked"}})
    r = c.post("/api/flows", json={"name": "Park", "nodes": [TRIGGER, _set_stage(stage="parked")], "edges": EDGE})
    assert r.status_code == 200, r.text
    assert r.json()["nodes"][1]["config"]["stage"] == "not_now"
    assert "Parked" in c.put(f"/api/flows/{r.json()['id']}", json={"nodes": [TRIGGER, _set_stage(stage="Nope")], "edges": EDGE}).json()["detail"]
