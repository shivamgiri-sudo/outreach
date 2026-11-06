"""Industry scenarios 3-4: B2B SaaS companies (A/B split, Repeat with a wait, meeting task, one email per company per
day) and education / coaching institutes (a weekday schedule, if on replies and consent, WhatsApp only with consent)."""
from datetime import timedelta

from tests.flow_helpers import edge, node
from tests.scenario_helpers import assert_rendered, csv_of, ist, ist_text, run_path, world_factory  # noqa: F401

SIGN_OFF = "\n\n— {{sender}}\n\nNot relevant? Unsubscribe: {{unsub_link}}"

# ---------------------------------------------------------------- 3. B2B SaaS
SAAS = csv_of([
    ("CloudNest Technologies", "Aditya Rao", "VP Sales", "aditya@cloudnest.io", "", "https://cloudnest.io", "Bengaluru", "IN", 15, 220, "SDR"),
    ("Ledgerly", "Manish Jain", "Founder", "manish@ledgerly.in", "98330 44556", "https://ledgerly.in", "Mumbai", "IN", 6, 45, "Customer Success Manager"),
    ("ShipFast SaaS", "Ritu Arora", "Head of People", "ritu@shipfast.co", "", "https://shipfast.co", "Gurugram", "IN", 10, 90, "Account Executive"),
])
SAAS_SAME_COMPANY = csv_of([
    ("CloudNest Technologies", "Sneha Gupta", "Head of Talent", "sneha@cloudnest.io", "", "https://cloudnest.io", "Bengaluru", "IN", 15, 220, "SDR"),
])
VARIANT_A = "Cut screening time for {{role}} hires at {{company}}"
VARIANT_B = "{{first_name}}, your 5 best {{role}} applicants every Monday"


def saas_flow(w):
    nodes = [
        node("t", "trigger.manual"),
        node("ab", "split", percent_a=50),
        node("ea", "email", subject=VARIANT_A, body="{{greeting}}\n\nVariant A: an AI phone screen for every {{role}} applicant." + SIGN_OFF),
        node("eb", "email", subject=VARIANT_B, body="{{greeting}}\n\nVariant B: a ranked shortlist for {{company}} every Monday." + SIGN_OFF),
        node("w1", "wait", days=2, hours=0, business_hours=True),
        node("wr", "wait_for_reply", days=4),
        node("meet", "meeting_link", owner="Karan (AE)", note="Demo for {{company}} ({{role}} hiring)."),
        node("ae", "notify", to="Karan (AE)", message="{{first_name}} at {{company}} wants a demo."),
        node("lp", "loop", times=2, title="Nudge twice"),
        node("w2", "wait", days=3, hours=0),
        node("nudge", "email", subject="Quick nudge, {{first_name}}", body="{{greeting}}\n\nShall I send a sample shortlist for {{company}}?" + SIGN_OFF),
        node("tag", "add_tag", tag="saas-nurture"),
        node("park", "set_stage", stage="not_now"),
    ]
    edges = [edge("t", "ab"), edge("ab", "ea", "a"), edge("ab", "eb", "b"), edge("ea", "w1"), edge("eb", "w1"), edge("w1", "wr"),
             edge("wr", "meet", "interested"), edge("meet", "ae"), edge("wr", "lp", "timeout"), edge("lp", "w2", "again"),
             edge("w2", "nudge"), edge("nudge", "lp"), edge("lp", "tag", "done"), edge("tag", "park")]
    return w.flow("SaaS: A/B intro + two nudges", nodes, edges)


def test_b2b_saas_ab_split_repeat_and_one_email_per_company_a_day(world_factory):
    w = world_factory(ist(2, 10, 0), SAAS)                     # Mon 2 Nov 10:00 IST
    w.add_leads(SAAS_SAME_COMPANY)
    w.team("Karan (AE)")
    flow = saas_flow(w)
    leads = ["CloudNest Technologies", "Ledgerly", "ShipFast SaaS"]
    sneha = next(l["id"] for l in w.c.get("/api/leads").json() if l.get("email") == "sneha@cloudnest.io")
    aditya = next(l["id"] for l in w.c.get("/api/leads").json() if l.get("email") == "aditya@cloudnest.io")
    predicted = {"Ledgerly": w.simulate(flow, "Ledgerly", "interested"), "ShipFast SaaS": w.simulate(flow, "ShipFast SaaS")}
    ids = [aditya, sneha, w.id("Ledgerly"), w.id("ShipFast SaaS")]
    r = w.c.post(f"/api/flows/{flow['id']}/enroll", json={"lead_ids": ids + [aditya]}).json()
    assert r["enrolled"] == ids and r["refused"] == []          # the duplicate id in the same request is ignored
    again = w.c.post(f"/api/flows/{flow['id']}/enroll", json={"lead_ids": [aditya]}).json()
    assert again["enrolled"] == [] and "already in this flow" in again["refused"][0]["reason"]

    w.tick(ist(2, 10, 0))
    day1 = w.emails()
    assert sorted(m["to"] for m in day1) == ["aditya@cloudnest.io", "manish@ledgerly.in", "ritu@shipfast.co"]
    runs = {r["lead_id"]: r for r in w.c.get(f"/api/flows/{flow['id']}/runs").json()}
    for m in day1:
        variant = runs[m["lead_id"]]["variants"]["ab"]
        assert m["flow_variants"] == {"ab": variant} and m["flow_node"] == ("ea" if variant == "a" else "eb")
        assert m["subject"].startswith("Cut screening time") if variant == "a" else m["subject"].endswith("applicants every Monday")
        assert_rendered(m["subject"], m["body"])
    held = runs[sneha]
    assert held["status"] == "waiting" and held["deferred"].startswith("one email per company a day (cloudnest.io")
    stats = w.c.get(f"/api/flows/{flow['id']}").json()["stats"]["split"]["ab"]
    assert stats["a"] + stats["b"] == 4

    w.run_until(ist(3, 9, 30))
    sneha_mail = [m for m in w.emails() if m["to"] == "sneha@cloudnest.io"]
    assert [ist_text(m["sent_at"]) for m in sneha_mail] == ["Tue 03 Nov 09:00"]   # next weekday morning, not the same day

    w.run_until(ist(4, 15, 0), every=timedelta(hours=1))
    assert w.reply("Ledgerly", "Yes, interested. Let's talk next week.")["label"] == "interested"
    w.tick(ist(4, 15, 30))
    ledgerly = w.runs(flow)["Ledgerly"]
    assert ledgerly["status"] == "done"
    meeting = [t for t in w.tasks("Ledgerly") if t["kind"] == "meeting"]
    assert len(meeting) == 1 and meeting[0]["owner"] == "Karan (AE)"
    assert meeting[0]["text"] == "Book a meeting with Manish Jain (no calendar is connected). Demo for Ledgerly (customer success manager hiring)."
    assert [n["text"] for n in w.notifications("Ledgerly") if n["kind"] == "flow"] == ["Manish at Ledgerly wants a demo."]

    w.run_until(ist(17, 10, 0))
    ship = w.emails("ShipFast SaaS")
    assert [(m["subject"], ist_text(m["sent_at"])) for m in ship[1:]] == [
        ("Quick nudge, Ritu", "Wed 11 Nov 10:30"),              # reply wait began at the 10:30 tick Wed 4, ran out Sun 8; 3-day wait
        ("Quick nudge, Ritu", "Mon 16 Nov 09:00")]              # Sat 14 Nov 10:00 is the weekend: Monday morning
    final = w.runs(flow)
    ship_run = final["ShipFast SaaS"]
    assert [h["result"] for h in ship_run["history"] if h["node"] == "lp"] == ["round 1 of 2", "round 2 of 2", "repeated 2 time(s), moving on"]
    lead = w.full("ShipFast SaaS")
    assert lead["tags"] == ["saas-nurture"] and lead["stage"] == "not_now" and ship_run["status"] == "done"
    assert run_path(ship_run) == predicted["ShipFast SaaS"]      # the simulation now walks the Repeat rounds too
    assert run_path(ledgerly) == predicted["Ledgerly"]
    assert len(w.emails("Ledgerly")) == 1                       # no nudges after the yes


# ---------------------------------------------------------------- 4. education / coaching institutes
COACHING = csv_of([
    ("BrightPath Academy", "Pooja Menon", "Director", "pooja@brightpath.edu.in", "98860 12121", "https://brightpath.edu.in", "Kochi", "IN", 7, 80, "Admissions Counsellor"),
    ("Vidya Coaching Centre", "Harish Kumar", "Founder", "harish@vidyacoaching.in", "", "https://vidyacoaching.in", "Patna", "IN", 4, 35, "Physics Faculty"),
    ("Apex IIT Classes", "Sunita Reddy", "Centre Head", "sunita@apexiit.in", "97000 34343", "https://apexiit.in", "Hyderabad", "IN", 12, 150, "Academic Counsellor"),
    ("Scholars Hub", "Imran Khan", "Owner", "imran@scholarshub.in", "", "https://scholarshub.in", "Lucknow", "IN", 3, 20, "Tele-counsellor"),
])


def coaching_flow(w):
    nodes = [
        node("t", "trigger.schedule", when="weekdays", hour=10, tag="coaching", limit=50),
        node("sv", "set_vars", values=[{"name": "batch", "value": "the April batch"}]),
        node("m", "email", subject="{{company}}: hire counsellors for {{batch}} in a day",
             body="{{greeting}}\n\nAdmissions for {{batch}} start soon. We screen every {{role}} applicant by phone so you meet only the best." + SIGN_OFF),
        node("w", "wait", days=1, hours=0),
        node("rep", "if", field="has_replied", op="eq", value="true"),
        node("tell", "notify", message="{{company}} replied about {{batch}}: reply today."),
        node("ok", "if", field="has_consent", op="eq", value="true"),
        node("wa", "whatsapp", template="nudge_demo_not_opened"),
        node("call", "call_task", note="Call {{first_name}} at {{company}} about {{batch}} counsellors."),
        node("li", "linkedin_task", message="Hi {{first_name}}, I emailed {{company}} about counsellor hiring for {{batch}}."),
    ]
    edges = [edge("t", "sv"), edge("sv", "m"), edge("m", "w"), edge("w", "rep"), edge("rep", "tell", "yes"), edge("rep", "ok", "no"),
             edge("ok", "wa", "yes"), edge("wa", "call"), edge("wa", "li", "error"), edge("ok", "li", "no")]
    return w.flow("Coaching: weekday admissions push", nodes, edges, stop_on_reply=False)   # keeps going on replies


def test_education_schedule_flow_with_consent_branches_and_weekend_rules(world_factory):
    w = world_factory(ist(6, 9, 0), COACHING)                  # Fri 6 Nov 09:00 IST
    w.team("Pooja desk")
    for name in ("BrightPath Academy", "Vidya Coaching Centre", "Apex IIT Classes"):
        w.tag(name, "coaching")
    w.consent("BrightPath Academy")
    w.owner("BrightPath Academy", "Pooja desk")
    flow = coaching_flow(w)
    predicted = {"BrightPath Academy": w.simulate(flow, "BrightPath Academy"), "Vidya Coaching Centre": w.simulate(flow, "Vidya Coaching Centre")}

    w.tick(ist(6, 9, 59))
    assert w.runs(flow) == {}                                   # not before 10:00 IST
    w.tick(ist(6, 10, 0))
    assert set(w.runs(flow)) == {"BrightPath Academy", "Vidya Coaching Centre", "Apex IIT Classes"}   # Scholars Hub has no tag
    mails = w.emails()
    assert [(m["to"], ist_text(m["sent_at"])) for m in mails] == [
        ("harish@vidyacoaching.in", "Fri 06 Nov 10:00"), ("pooja@brightpath.edu.in", "Fri 06 Nov 10:00"), ("sunita@apexiit.in", "Fri 06 Nov 10:00")]
    assert mails[1]["subject"] == "BrightPath Academy: hire counsellors for the April batch in a day"
    assert "Admissions for the April batch start soon. We screen every admissions counsellor applicant" in mails[1]["body"]
    assert_rendered(*[m["subject"] + m["body"] for m in mails])

    w.tick(ist(6, 16, 0))
    assert w.reply("Apex IIT Classes", "Interesting. What does it cost for 10 counsellors?")["label"] == "question"
    # "assume a reply" only steers Wait-for-reply steps; an If on has_replied reads the lead as it is, so simulate now
    predicted["Apex IIT Classes"] = w.simulate(flow, "Apex IIT Classes")
    w.run_until(ist(9, 12, 0))                                  # through the weekend to Monday noon

    final = w.runs(flow)
    apex = [n for n in w.notifications("Apex IIT Classes") if n["kind"] == "flow"]
    assert [(n["to"], n["text"]) for n in apex] == [("Sales team", "Apex IIT Classes replied about the April batch: reply today.")]
    wa = w.whatsapps("BrightPath Academy")
    # the 1-day wait ended Sat 10:00, but WhatsApp waits for business hours: Monday 09:00 (fix), then the call task
    assert [(m["template"], ist_text(m["sent_at"])) for m in wa] == [("nudge_demo_not_opened", "Mon 09 Nov 09:00")]
    assert_rendered(wa[0]["body"])
    call = [t for t in w.tasks("BrightPath Academy") if t["kind"] == "call"]
    assert [(t["owner"], ist_text(t["due_at"])) for t in call] == [("Pooja desk", "Mon 09 Nov 09:00")]   # the lead's owner
    assert call[0]["text"] == "Flow Coaching: weekday admissions push: Call Pooja at BrightPath Academy about the April batch counsellors."
    vidya = [t for t in w.tasks("Vidya Coaching Centre") if t["kind"] == "linkedin"]
    assert len(vidya) == 1 and ist_text(vidya[0]["due_at"]) == "Sat 07 Nov 10:00" and w.whatsapps("Vidya Coaching Centre") == []
    assert w.whatsapps("Apex IIT Classes") == []
    for name, path in predicted.items():
        assert final[name]["status"] == "done" and run_path(final[name]) == path, name
    # Monday 10:00: the schedule runs again (weekdays) and only picks up the lead tagged since
    w.tag("Scholars Hub", "coaching")
    w.tick(ist(9, 12, 30))
    assert "Scholars Hub" not in w.runs(flow)                   # already ran today
    w.tick(ist(10, 10, 0))
    assert set(w.runs(flow)) == {"BrightPath Academy", "Vidya Coaching Centre", "Apex IIT Classes", "Scholars Hub"}
    assert len([m for m in w.emails() if m["to"] == "pooja@brightpath.edu.in"]) == 1   # never started twice
