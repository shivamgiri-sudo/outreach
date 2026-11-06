"""Send email now: one lead's next due email goes at once, logged with who did it; refused when not allowed."""
from tests.flow_helpers import lead, make_client, patch_lead, store_of


def test_email_now_sends_the_due_step_once_and_refuses_otherwise(tmp_path):
    c = make_client(tmp_path)
    a = lead(c, "Acme")["id"]
    assert c.post(f"/api/leads/{a}/email-now").status_code == 400                  # not enrolled yet
    assert c.post(f"/api/leads/{a}/enroll").status_code == 200
    r = c.post(f"/api/leads/{a}/email-now")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "practice" and body["status"] == "dry_run" and body["to"]
    out = [m for m in store_of(c).read("outbox") if m.get("manual")]
    assert len(out) == 1 and out[0]["to"] == body["to"]
    ev = body["lead"]["timeline"][-1]
    assert ev["kind"] == "practice" and "by" in ev
    again = c.post(f"/api/leads/{a}/email-now")
    assert again.status_code == 400 and "No email is due" in again.json()["detail"]
    patch_lead(c, a, stage="unsubscribed")
    assert c.post(f"/api/leads/{a}/email-now").status_code == 400
