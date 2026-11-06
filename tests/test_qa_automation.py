"""Regressions found by the browser QA sweep of flows, campaigns, lists, tasks and LinkedIn (area: automation)."""
from app.flow_actions import custom_facts, evaluate


def test_if_on_a_custom_field_explains_itself_without_the_internal_prefix():
    facts = custom_facts({"custom": {"industry_vertical": "Healthcare", "monthly_hiring_budget": 80000}})
    ok, why = evaluate({"field": "custom:industry_vertical", "op": "eq", "value": "Real estate"}, facts)
    assert (ok, why) == (False, "industry vertical is Healthcare")
    ok, why = evaluate({"field": "custom:monthly_hiring_budget", "op": "gt", "value": "100000"}, facts)
    assert (ok, why) == (False, "monthly hiring budget is 80000")
    assert evaluate({"field": "open_roles", "op": "gt", "value": "3"}, {"open_roles": 8})[1] == "open roles is 8"


def test_a_daily_flow_switched_on_after_its_hour_waits_for_the_next_day(tmp_path):
    # QA sweep: a "daily at 10:00 IST" flow switched on at 17:30 started leads at 17:30 straight away.
    from datetime import timedelta
    from tests.flow_helpers import T0, chain, make_client, make_flow, node, store_of, tick
    c = make_client(tmp_path)
    flow = make_flow(c, [node("t", "trigger.schedule", when="daily", hour=10, limit=5), node("tag", "add_tag", tag="s")], chain("t", "tag"))
    on_at = T0 + timedelta(hours=7)                                          # Tuesday 17:30 IST
    store_of(c).update("flows", lambda fs: [{**f, "activated_at": on_at.isoformat()} for f in fs])
    started = lambda: [r for r in store_of(c).read("flow_runs") if r["flow_id"] == flow["id"]]
    tick(c, on_at + timedelta(minutes=5))
    assert started() == []
    tick(c, on_at + timedelta(hours=17))                                     # Wednesday 10:30 IST
    assert len(started()) >= 1


def test_simple_rule_task_and_note_fill_in_the_lead_details(tmp_path):
    # QA sweep: a rule "Send pricing to {{company}}" created a task that literally said {{company}}.
    from tests.flow_helpers import lead, make_client, store_of
    c = make_client(tmp_path)
    for name, action in (("pricing", {"type": "task", "value": "Send pricing to {{company}} ({{first_name}})"}),
                         ("log", {"type": "note", "value": "{{company}} is engaged; {{unknown}} stays"})):
        assert c.post("/api/workflows", json={"name": name, "trigger": {"type": "stage_change", "value": "engaged"}, "action": action}).status_code == 200
    who = lead(c)
    assert c.post(f"/api/leads/{who['id']}/stage", json={"stage": "engaged"}).status_code == 200
    first = who["contact_name"].split()[0]
    tasks = [t for t in store_of(c).read("tasks") if t.get("lead_id") == who["id"]]
    assert any(t["text"] == f"Send pricing to {who['company']} ({first})" for t in tasks)
    assert any(e["text"] == f"{who['company']} is engaged; {{{{unknown}}}} stays" for e in lead(c)["timeline"])


def test_campaign_careers_page_follows_the_careers_site_like_read_job_pages():
    # QA sweep: a campaign with https://www.wipro.com/careers/ reported "no job listings found" while the Leads page
    # tool (discover_jobs) reads the jobs from the careers site's Careers link / public sitemap.
    from app import campaign_sources as src
    pages = {
        "https://www.acme-steel.in/careers/": '<html><title>Acme Steel</title><a href="https://careers.acme-steel.in/">Careers</a></html>',
        "https://careers.acme-steel.in/": "<html><script>app()</script></html>",
        "https://careers.acme-steel.in/robots.txt": "Sitemap: https://careers.acme-steel.in/sitemap.xml",
        "https://careers.acme-steel.in/sitemap.xml": ('<urlset><url><loc>https://careers.acme-steel.in/job/Pune-Machine-Operator/101/</loc>'
                                                    "<lastmod>2026-11-01</lastmod></url></urlset>"),
    }

    def fetch(url):
        if url not in pages:
            raise OSError("404")
        return pages[url]
    out = src.careers_postings(["https://www.acme-steel.in/careers/"], [], fetch)
    assert out["errors"] == [] and out["pages"] == 1
    assert [p["title"] for p, _ in out["postings"]] == ["Machine Operator"]


def test_a_held_sequence_email_names_the_missing_detail(tmp_path):
    # QA sweep: a campaign started the preset "Real estate" sequence; every lead showed "Waiting: a detail needed by the
    # email is missing" without saying which one ({{my_value_1}}, {{my_cta}}... from Brand settings).
    from datetime import datetime, timezone
    from app import sequence_runner as sr
    from tests.flow_helpers import lead, make_client, store_of
    c = make_client(tmp_path)
    store = store_of(c)
    step = {"name": "e1", "subject": "Hi {{company}}", "body": "{{greeting}} {{my_cta}} {{project_name}} {{unsub_link}}"}
    missing = sr.missing_details(lead(c), step, sr.get_settings(store), sr.get_offer(store), "http://x", datetime.now(timezone.utc))
    assert "project_name" in missing and "company" not in missing and "greeting" not in missing
    assert sr._blocked_text(["my_cta", "project_name"]) == "a detail needed by the email is missing: {{my_cta}}, {{project_name}}"


def test_a_linkedin_link_typed_without_https_is_tidied_and_can_be_started(tmp_path):
    # QA sweep: "linkedin.com/in/deepak-patel-navyug/" typed in Edit details was listed under "Suggested people" but
    # Start refused it with "Add the person's LinkedIn profile link first."
    from tests.flow_helpers import lead, make_client
    c = make_client(tmp_path)
    who = lead(c)
    r = c.patch(f"/api/leads/{who['id']}", json={"linkedin_url": "linkedin.com/in/deepak-patel-navyug/", "reason": ""})
    assert r.status_code == 200, r.text
    assert lead(c)["linkedin_url"] == "https://www.linkedin.com/in/deepak-patel-navyug"
    assert c.post(f"/api/leads/{who['id']}/linkedin/start").status_code == 200


def test_connection_note_lab_accepts_the_placeholders_the_steps_editor_accepts():
    # QA sweep: note B "Hi {first_name}, noticed {company} is hiring..." saved in Steps & personas was refused by the
    # A/B notes lab with "only {first_name} is filled in", although the sequence fills {company} in.
    from app.opener_lab import score_note
    ok = score_note("Hi {first_name}, noticed {company} is hiring. Would be good to connect.")
    assert ok["ok"] is not False and ok["score"] > 0
    bad = score_note("Hi {first_name}, about {favourite_food}.")
    assert bad["score"] == 0 and "favourite_food" in bad["reasons"][0]
    long = score_note("Hi {first_name}, " + "{company} " * 40)
    assert long["score"] == 0 and long["reasons"][0].startswith("Too long")
