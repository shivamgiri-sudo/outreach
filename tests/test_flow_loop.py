"""The Repeat step: goes down 'again' N times, then 'done'; must contain a Wait; invalid counts are refused."""
from datetime import timedelta

from tests.flow_helpers import T0, edge, enroll, lead, make_client, make_flow, node, runs, tick


def test_repeat_goes_round_n_times_then_done(tmp_path):
    c = make_client(tmp_path)
    nodes = [node("t", "trigger.manual"), node("loop", "loop", times=2), node("w", "wait", days=0, hours=1),
             node("tag", "add_tag", tag="round"), node("end", "add_tag", tag="finished")]
    edges = [edge("t", "loop"), edge("loop", "w", "again"), edge("w", "tag"), edge("tag", "loop"), edge("loop", "end", "done")]
    flow = make_flow(c, nodes, edges)
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    for h in range(0, 8, 2):
        tick(c, T0 + timedelta(hours=h))
    run = runs(c, flow)[0]
    assert run["status"] == "done", run
    results = [h["result"] for h in run["history"] if h.get("node") == "loop"]
    assert results[:2] == ["round 1 of 2", "round 2 of 2"] and "moving on" in results[2]
    assert "finished" in c.get(f"/api/leads/{acme}").json()["tags"]


def test_repeat_without_wait_and_bad_count_are_refused(tmp_path):
    c = make_client(tmp_path)
    nodes = [node("t", "trigger.manual"), node("loop", "loop", times=2), node("tag", "add_tag", tag="x")]
    edges = [edge("t", "loop"), edge("loop", "tag", "again"), edge("tag", "loop")]
    r = c.post("/api/flows", json={"name": "bad", "nodes": nodes, "edges": edges})
    flow = r.json() if r.status_code == 200 else None
    refused = r.status_code != 200 or c.post(f"/api/flows/{flow['id']}/activate").status_code != 200
    assert refused
    nodes[1] = node("loop", "loop", times=50)
    r = c.post("/api/flows", json={"name": "bad2", "nodes": nodes, "edges": [edge("t", "loop")]})
    assert r.status_code != 200 or c.post(f"/api/flows/{r.json()['id']}/activate").status_code != 200
