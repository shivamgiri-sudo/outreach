"""Industry scenarios 1-2: a recruitment agency list and a real-estate developer list, built through the API as a user
would, run forward with the tick, and checked lead by lead (messages, times, tasks, stages, tags, run path)."""
from datetime import timedelta

from tests.flow_helpers import edge, node
from tests.scenario_helpers import assert_rendered, csv_of, ist, ist_text, run_path, world_factory  # noqa: F401

SIGN_OFF = "\n\n— {{sender}}\n\nNot relevant? Unsubscribe: {{unsub_link}}"

# ---------------------------------------------------------------- 1. recruitment / staffing agencies
RECRUITERS = csv_of([
    ("TalentBridge Staffing", "Kavya Iyer", "Head of Delivery", "kavya@talentbridge.in", "98450 11223", "https://talentbridge.in", "Bengaluru", "IN", 25, 180, "Recruiter"),
    ("HireSpring Consulting", "Rohit Sinha", "Director", "rohit@hirespring.co.in", "", "https://hirespring.co.in", "Hyderabad", "IN", 9, 60, "Sourcing Specialist"),
    ("PeopleFirst HR", "Anjali Rao", "Founder", "anjali@peoplefirsthr.com", "", "https://peoplefirsthr.com", "Chennai", "IN", 6, 40, "Talent Acquisition Executive"),
    ("StaffKart", "Vikram Joshi", "VP Operations", "vikram@staffkart.in", "99200 33445", "https://staffkart.in", "Pune", "IN", 12, 300, "Payroll Executive"),
    ("NicheSearch Partners", "Meera Pillai", "Partner", "meera@nichesearch.in", "", "https://nichesearch.in", "Delhi", "IN", 3, 12, ""),
])
OTHER_SOURCE = csv_of([("GlobalHire", "Sam Thomas", "CEO", "sam@globalhire.in", "", "https://globalhire.in", "Mumbai", "IN", 40, 500, "Recruiter")])


def recruitment_flow(w):
    nodes = [
        node("t", "trigger.lead_added", source="naukri", title="New lead from the job-board export"),
        node("big", "if", field="open_roles", op="gt", value=5, title="More than 5 openings?"),
        node("sv", "set_vars", values=[{"name": "hook", "value": "your {{role}} openings at {{company}}"}]),
        node("m1", "email", subject="{{company}}: shortlists for {{role}} in 48 hours",
             body="{{greeting}}\n\nI saw {{hook}}. We screen every applicant with a short AI interview, so your recruiters only "
                  "meet the best five." + SIGN_OFF),
        node("wr", "wait_for_reply", days=3),
        node("ct", "call_task", owner="Priya Nair", note="{{first_name}} at {{company}} said yes about {{role}} hiring. Call today."),
        node("nt", "notify", to="Rahul Verma", message="{{company}} asked a question about the screen; answer {{first_name}} today."),
        node("later", "add_tag", tag="recruit-q1"),
        node("m2", "email", subject="Re: {{company}} hiring",
             body="{{greeting}}\n\nFollowing up on {{hook}}. Happy to run your next 20 applicants through the screen free." + SIGN_OFF),
        node("li", "linkedin_task", message="Hi {{first_name}}, I emailed about {{role}} hiring at {{company}}. Happy to share a sample shortlist."),
        node("small", "add_tag", tag="small-hirer"),
    ]
    edges = [edge("t", "big"), edge("big", "sv", "yes"), edge("sv", "m1"), edge("m1", "wr"), edge("wr", "ct", "interested"),
             edge("wr", "nt", "question"), edge("wr", "later", "not_now"), edge("wr", "m2", "timeout"), edge("m2", "li"),
             edge("big", "small", "no")]
    return w.flow("Staffing agencies: job-board leads", nodes, edges)


def test_recruitment_agency_flow_end_to_end(world_factory):
    w = world_factory(ist(3, 10, 0))
    w.team("Priya Nair", "Rahul Verma")
    flow = recruitment_flow(w)
    w.add_leads(RECRUITERS, source="naukri")
    w.add_leads(OTHER_SOURCE, source="apollo")
    expected = {"TalentBridge Staffing": "interested", "HireSpring Consulting": "question", "PeopleFirst HR": "not_now",
                "StaffKart": "timeout", "NicheSearch Partners": "timeout"}
    predicted = {name: w.simulate(flow, name, assume) for name, assume in expected.items()}

    w.tick(ist(3, 10, 30))                                      # Tue 10:30 IST: the new naukri leads start
    assert set(w.runs(flow)) == set(expected)                    # GlobalHire came from Apollo: not started
    first = w.emails()
    assert [(m["to"], ist_text(m["sent_at"])) for m in first] == [
        ("anjali@peoplefirsthr.com", "Tue 03 Nov 10:30"), ("kavya@talentbridge.in", "Tue 03 Nov 10:30"),
        ("rohit@hirespring.co.in", "Tue 03 Nov 10:30"), ("vikram@staffkart.in", "Tue 03 Nov 10:30")]
    kavya = first[1]
    assert kavya["subject"] == "TalentBridge Staffing: shortlists for recruiter in 48 hours"
    assert kavya["body"].startswith("Hi Kavya,\n\nI saw your recruiter openings at TalentBridge Staffing. We screen")
    assert "/u/" in kavya["body"] and kavya["status"] == "dry_run"
    assert_rendered(*[m["subject"] + m["body"] for m in first])

    w.tick(ist(4, 11, 0))
    assert w.reply("TalentBridge Staffing", "Yes, interested. Can you call me tomorrow?")["label"] == "interested"
    assert w.reply("HireSpring Consulting", "How much does it cost per hire?")["label"] == "question"
    assert w.reply("PeopleFirst HR", "Not right now, maybe next quarter.")["label"] == "not_now"
    w.tick(ist(4, 11, 30))

    runs = w.runs(flow)
    # TalentBridge: the yes reply made them warm, but the flow waited for that reply, so its branch runs (bug fix).
    assert runs["TalentBridge Staffing"]["status"] == "done"
    call = [t for t in w.tasks("TalentBridge Staffing") if t["kind"] == "call"]
    assert len(call) == 1 and call[0]["owner"] == "Priya Nair" and call[0]["status"] == "open"
    assert "Kavya at TalentBridge Staffing said yes about recruiter hiring. Call today." in call[0]["text"]
    assert_rendered(call[0]["text"])
    assert w.full("TalentBridge Staffing")["stage"] == "interested"

    rahul = [n for n in w.notifications("HireSpring Consulting") if n["kind"] == "flow"]
    assert [(n["to"], n["text"]) for n in rahul] == [("Rahul Verma", "HireSpring Consulting asked a question about the screen; answer Rohit today.")]
    assert w.full("HireSpring Consulting")["stage"] == "engaged" and runs["HireSpring Consulting"]["status"] == "done"

    people = w.full("PeopleFirst HR")
    assert people["stage"] == "not_now" and people["tags"] == ["recruit-q1"] and runs["PeopleFirst HR"]["status"] == "done"
    assert w.full("NicheSearch Partners")["tags"] == ["small-hirer"] and w.emails("NicheSearch Partners") == []

    w.run_until(ist(6, 12, 0), every=timedelta(hours=1))       # Fri: StaffKart's 3-day wait runs out
    staff = w.emails("StaffKart")
    assert [(m["subject"], ist_text(m["sent_at"])) for m in staff] == [
        ("StaffKart: shortlists for payroll executive in 48 hours", "Tue 03 Nov 10:30"), ("Re: StaffKart hiring", "Fri 06 Nov 10:30")]
    assert "Following up on your payroll executive openings at StaffKart." in staff[1]["body"]
    linkedin = [t for t in w.tasks("StaffKart") if t["kind"] == "linkedin"]
    assert len(linkedin) == 1 and linkedin[0]["owner"] == "Sales team"
    assert linkedin[0]["text"] == "Hi Vikram, I emailed about payroll executive hiring at StaffKart. Happy to share a sample shortlist."
    assert w.runs(flow)["StaffKart"]["status"] == "done"
    # nobody else got a second email, and no message went to the replies
    assert len(w.emails()) == 5
    # the dry run predicted the same path as the real run for every lead
    final = w.runs(flow)
    for name, path in predicted.items():
        assert run_path(final[name]) == path, name
    assert any("Flow Staffing agencies: job-board leads: email" in t for t in w.timeline("StaffKart"))


# ---------------------------------------------------------------- 2. real estate developers (webhook from the site form)
def realestate_flow(w):
    """A developer's sales team fills a web form; the flow routes by city to the right sales head and only WhatsApps a
    lead who has said yes to WhatsApp."""
    nodes = [
        node("t", "trigger.webhook", title="Enquiry form on our site"),
        node("phone", "if", field="has_phone", op="eq", value="true"),
        node("ok", "if", field="has_consent", op="eq", value="true", title="Said yes to WhatsApp?"),
        node("wa", "whatsapp", template="pilot_offer"),
        node("mail", "email", subject="{{company}}: hiring {{role}} for the new launch?",
             body="{{greeting}}\n\nYou asked about screening {{role}} applicants for {{company}}. Here is how it works in 3 steps." + SIGN_OFF),
        node("biz", "wait", days=0, hours=2, business_hours=True),
        node("city", "switch", field="city", cases=["Pune", "Mumbai"]),
        node("pune", "notify", to="Neha (Pune)", message="New enquiry from {{company}} ({{first_name}}), hiring {{role}}."),
        node("mum", "notify", to="Arjun (Mumbai)", message="New enquiry from {{company}} ({{first_name}}), hiring {{role}}."),
        node("rest", "call_task", note="Out-of-city enquiry from {{company}}: qualify on a call."),
        node("stage", "set_stage", stage="engaged"),           # they wrote to us: engaged (new -> contacted would skip steps and is refused)
        node("tag", "add_tag", tag="site-enquiry"),
    ]
    edges = [edge("t", "phone"), edge("phone", "ok", "yes"), edge("phone", "mail", "no"), edge("ok", "wa", "yes"), edge("ok", "mail", "no"),
             edge("wa", "biz"), edge("wa", "mail", "error"), edge("mail", "biz"), edge("biz", "city"), edge("city", "pune", "c1"),
             edge("city", "mum", "c2"), edge("city", "rest", "other"), edge("pune", "stage"), edge("mum", "stage"),
             edge("rest", "stage"), edge("stage", "tag")]
    return w.flow("Real estate: site enquiries", nodes, edges, stop_on_reply=True)


def post_enquiry(w, flow, **fields):
    hook = w.c.get(f"/api/flows/{flow['id']}/webhook").json()
    r = w.c.post(f"/api/inbound/{hook['connector_id']}", headers={"Authorization": f"Bearer {hook['token']}"}, json=fields)
    assert r.status_code == 200, r.text
    return r.json()["lead_id"]


def test_real_estate_webhook_flow_whatsapp_only_with_consent(world_factory):
    w = world_factory(ist(5, 17, 0))                             # Thu 17:00 IST
    w.team("Neha (Pune)", "Arjun (Mumbai)")
    flow = realestate_flow(w)
    post_enquiry(w, flow, name="Sanjay Kulkarni", email="sanjay@skylinerealty.in", company="Skyline Realty", phone="98111 22334",
                 city="Pune", title="Director, Sales", whatsapp_consent="yes")
    post_enquiry(w, flow, name="Farah Shaikh", email="farah@bayviewhomes.in", company="Bayview Homes", phone="98200 55667",
                 city="Mumbai", title="HR Manager")
    post_enquiry(w, flow, name="Gaurav Bansal", email="gaurav@greenacres.co.in", company="Green Acres Developers",
                 city="Jaipur", title="Owner")
    runs = w.runs(flow)
    assert set(runs) == {"Skyline Realty", "Bayview Homes", "Green Acres Developers"}
    skyline, bayview = w.full("Skyline Realty"), w.full("Bayview Homes")
    assert bool(skyline.get("consent")) and not bayview.get("consent") and bayview["phone"]
    predicted = {name: w.simulate(flow, name) for name in runs}

    w.tick(ist(5, 17, 1))
    wa = w.whatsapps()
    assert [(m["to"], m["template"], ist_text(m["sent_at"])) for m in wa] == [("+919811122334", "pilot_offer", "Thu 05 Nov 17:01")]
    assert wa[0]["body"].startswith("Hi Sanjay, ") and wa[0]["body"].endswith("Reply STOP to opt out.") and wa[0]["status"] == "dry_run"
    assert_rendered(wa[0]["body"])
    mails = w.emails()
    assert [(m["to"], ist_text(m["sent_at"])) for m in mails] == [("farah@bayviewhomes.in", "Thu 05 Nov 17:01"),
                                                                    ("gaurav@greenacres.co.in", "Thu 05 Nov 17:01")]
    assert mails[0]["subject"] == "Bayview Homes: hiring open roles for the new launch?"   # a form sends no opening: the fallback reads naturally
    assert w.whatsapps("Bayview Homes") == []                   # phone but no consent: email instead, never WhatsApp

    w.run_until(ist(6, 10, 0))                                   # the 2-hour wait ends after hours, so Fri 09:00
    notes = {n["lead_id"]: (n["to"], n["text"], ist_text(n["at"])) for n in w.notifications() if n["kind"] == "flow"}
    assert notes[w.id("Skyline Realty")][:2] == ("Neha (Pune)", "New enquiry from Skyline Realty (Sanjay), hiring open roles.")
    assert notes[w.id("Bayview Homes")][:2] == ("Arjun (Mumbai)", "New enquiry from Bayview Homes (Farah), hiring open roles.")
    call = [t for t in w.tasks("Green Acres Developers") if t["kind"] == "call"]     # (capture also adds its own follow-up task)
    assert [t["owner"] for t in call] == ["Sales team"]
    assert "Out-of-city enquiry from Green Acres Developers: qualify on a call." in call[0]["text"]
    for name in runs:
        lead = w.full(name)
        assert lead["stage"] == "engaged" and "site-enquiry" in (lead.get("tags") or []), name
    final = w.runs(flow)
    waited = [h for h in final["Skyline Realty"]["history"] if h["node"] == "biz"]
    assert waited[0]["result"] == "waiting until Fri 6 Nov, 9:00 AM IST"       # 17:01 + 2 h is after hours: next morning
    assert ist_text(waited[-1]["at"]) == "Fri 06 Nov 09:01"                         # the first tick after 09:00
    for name, path in predicted.items():
        assert final[name]["status"] == "done" and run_path(final[name]) == path, name
