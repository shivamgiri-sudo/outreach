"""QA regressions for bugs found while testing the Integrations hub (each fixed in its own commit)."""
from app.channels.custom_http import RESPONSE_PREVIEW_BYTES, response_preview
from tests.qa.integrations_hub_support import make


def test_reply_preview_is_cut_at_2kb_even_for_hindi_text():
    """Regression: the cut counted characters, so a Devanagari reply showed ~6 KB instead of the documented 2 KB."""
    text = response_preview({"message": "संदेश भेजा गया " * 400})
    shown = text.removesuffix("… (cut)")
    assert text.endswith("… (cut)")
    assert len(shown.encode("utf-8")) <= RESPONSE_PREVIEW_BYTES
    assert len(shown.encode("utf-8")) > RESPONSE_PREVIEW_BYTES - 4                # cut on a character boundary
    assert "�" not in shown


# ---------- go-live checklist: public address must be https ----------
def item(c, item_id):
    return next(i for i in c.get("/api/readiness").json()["items"] if i["id"] == item_id)


def blockers(c):
    return c.get("/api/sending/status").json()["live_blockers"]


def test_readiness_never_counts_a_plain_http_address_as_public(tmp_path):
    """Regression: the checklist ticked 'public web address' for http://, so it said 'ready' while the switch refused."""
    c = make(tmp_path)
    c.put("/api/settings", json={"public_base_url": "http://outreach.hirewise-outreach.example"})
    assert item(c, "public_url")["status"] == "todo"
    assert any("https" in b for b in blockers(c))
    c.put("/api/settings", json={"public_base_url": "https://outreach.hirewise-outreach.example"})
    assert item(c, "public_url")["status"] == "done"


# ---------- legacy /api/integrations: only offered modes ----------
def legacy(c, integration_id):
    return next(i for i in c.get("/api/integrations").json() if i["id"] == integration_id)


def test_legacy_save_refuses_a_mode_the_integration_does_not_offer(tmp_path):
    """Regression: PUT /api/integrations/sending_domain {mode: manual} ticked the checklist's 'sending domain' row
    although no sender was connected (sending_domain only offers 'api')."""
    c = make(tmp_path)
    r = c.put("/api/integrations/sending_domain", json={"mode": "manual", "values": {}})
    assert r.status_code == 400
    assert item(c, "sending_domain")["status"] == "todo" and legacy(c, "sending_domain")["status"] == "not_configured"
    assert c.put("/api/integrations/sales_navigator", json={"mode": "manual", "values": {}}).status_code == 200
    assert c.put("/api/integrations/sending_domain", json={"mode": "bogus", "values": {}}).status_code == 400


