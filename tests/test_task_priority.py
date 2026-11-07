"""Task priority: low / normal / high (default normal) from the new-task form, validated, changeable, and the
task board can be ordered by it."""
from tests.test_activities import make


def test_new_task_priority_defaults_to_normal_and_is_validated(tmp_path):
    c = make(tmp_path)
    assert c.post("/api/tasks", json={"title": "Plain"}).json()["priority"] == "normal"
    assert c.post("/api/tasks", json={"title": "Loud", "priority": "High"}).json()["priority"] == "high"   # any case
    assert c.post("/api/tasks", json={"title": "Bad", "priority": "urgent"}).status_code == 400
    task = c.post("/api/tasks", json={"title": "Later", "priority": "low"}).json()
    assert c.put(f"/api/tasks/{task['id']}", json={"priority": "NORMAL"}).json()["priority"] == "normal"
    assert c.put(f"/api/tasks/{task['id']}", json={"priority": "top"}).status_code == 400


def test_board_orders_by_priority_on_request(tmp_path):
    c = make(tmp_path)
    for title, due, priority in (("A low early", "2026-12-01T10:00", "low"), ("B high late", "2026-12-03T10:00", "high"),
                                 ("C normal mid", "2026-12-02T10:00", "normal")):
        assert c.post("/api/tasks", json={"title": title, "due_at": due, "priority": priority}).status_code == 200
    by_due = [t["title"] for t in c.get("/api/tasks/board").json()["upcoming"]]
    by_priority = [t["title"] for t in c.get("/api/tasks/board?sort=priority").json()["upcoming"]]
    assert by_due == ["A low early", "C normal mid", "B high late"]
    assert by_priority == ["B high late", "C normal mid", "A low early"]
    assert c.get("/api/tasks/board?sort=size").status_code == 400
