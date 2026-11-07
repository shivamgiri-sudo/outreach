"""Campaigns per industry: a campaign picks imported leads (only chosen companies, business email rule), auto-enrols the
qualifying ones into that industry's own email sequence (with an A/B step), the sequence runs in practice mode over
simulated days, and a reply stops the sequence while a simple rule and a flow follow up (WhatsApp only with consent)."""
from datetime import timedelta

import pytest

from tests.flow_helpers import edge, node
from tests.scenario_helpers import assert_rendered, csv_of, ist, ist_text, world_factory  # noqa: F401

UNSUB = "\n\nNot relevant? Unsubscribe: {{unsub_link}}"

# industry -> (lead rows: first two are the chosen companies, the third is left out, the fourth uses a free-mail address)
INDUSTRIES = {
    "recruitment": [
        ("TalentBridge Staffing", "Kavya Iyer", "Head of Delivery", "kavya@talentbridge.in", "98450 11223", "https://talentbridge.in", "Bengaluru", "IN", 25, 180, "Recruiter"),
        ("StaffKart", "Vikram Joshi", "VP Operations", "vikram@staffkart.in", "", "https://staffkart.in", "Pune", "IN", 12, 300, "Payroll Executive"),
        ("HireSpring Consulting", "Rohit Sinha", "Director", "rohit@hirespring.co.in", "", "https://hirespring.co.in", "Hyderabad", "IN", 9, 60, "Sourcing Specialist"),
        ("Quick Placements", "Raju Das", "Owner", "quickplacements@gmail.com", "", "", "Kolkata", "IN", 4, 8, "Recruiter")],
    "real_estate": [
        ("Skyline Realty", "Neha Kapoor", "Sales Director", "neha@skylinerealty.in", "98111 22334", "https://skylinerealty.in", "Pune", "IN", 8, 120, "Sales Executive"),
        ("Bayview Homes", "Farah Shaikh", "HR Manager", "farah@bayviewhomes.in", "", "https://bayviewhomes.in", "Mumbai", "IN", 6, 90, "Site Supervisor"),
        ("Gharonda Homes", "Arjun Mehta", "Founder", "arjun@gharonda.co.in", "", "https://gharonda.co.in", "Mumbai", "IN", 2, 15, "Telecaller"),
        ("Plot Deals", "Suresh Patil", "Broker", "plotdeals.pune@yahoo.com", "", "", "Pune", "IN", 1, 3, "Telecaller")],
    "saas": [
        ("Ledgerly", "Manish Jain", "Founder", "manish@ledgerly.in", "98330 44556", "https://ledgerly.in", "Mumbai", "IN", 6, 45, "Customer Success Manager"),
        ("ShipFast SaaS", "Ritu Arora", "Head of People", "ritu@shipfast.co", "", "https://shipfast.co", "Gurugram", "IN", 10, 90, "Account Executive"),
        ("CloudNest Technologies", "Aditya Rao", "VP Sales", "aditya@cloudnest.io", "", "https://cloudnest.io", "Bengaluru", "IN", 15, 220, "SDR"),
        ("DevTiny", "Kiran B", "Founder", "devtiny.app@gmail.com", "", "", "Pune", "IN", 2, 4, "Developer")],
    "education": [
        ("BrightPath Academy", "Pooja Menon", "Director", "pooja@brightpath.edu.in", "98860 12121", "https://brightpath.edu.in", "Kochi", "IN", 7, 80, "Admissions Counsellor"),
        ("Apex IIT Classes", "Sunita Reddy", "Centre Head", "sunita@apexiit.in", "", "https://apexiit.in", "Hyderabad", "IN", 12, 150, "Academic Counsellor"),
        ("Vidya Coaching Centre", "Harish Kumar", "Founder", "harish@vidyacoaching.in", "", "https://vidyacoaching.in", "Patna", "IN", 4, 35, "Physics Faculty"),
        ("Tuition Point", "Meena", "Owner", "tuitionpoint@gmail.com", "", "", "Ranchi", "IN", 1, 5, "Tutor")],
    "insurance": [
        ("SecureLife Advisors", "Deepak Malhotra", "Branch Head", "deepak@securelife.in", "98101 77889", "https://securelife.in", "Delhi", "IN", 20, 140, "Insurance Advisor"),
        ("Kavach Insurance Marketing", "Ramesh Iyer", "Director", "ramesh@kavachinsure.in", "", "https://kavachinsure.in", "Chennai", "IN", 9, 55, "Field Sales Officer"),
        ("ShieldCover Brokers", "Nisha Batra", "Principal Officer", "nisha@shieldcover.co.in", "", "https://shieldcover.co.in", "Chandigarh", "IN", 6, 30, "Relationship Manager"),
        ("Bima Mitra", "Gopal", "Agent", "bimamitra@rediffmail.com", "", "", "Jaipur", "IN", 1, 2, "Agent")],
    "clinic": [
        ("Sunrise Multispeciality Clinic", "Dr. Ashok Verma", "Medical Director", "ashok@sunriseclinic.in", "98270 12345", "https://sunriseclinic.in", "Indore", "IN", 5, 60, "Staff Nurse"),
        ("SmileCare Dental", "Dr. Leena Thomas", "Founder", "leena@smilecaredental.in", "", "https://smilecaredental.in", "Bengaluru", "IN", 3, 18, "Dental Assistant"),
        ("CarePlus Diagnostics", "Vivek Nair", "Operations Head", "vivek@careplusdiag.in", "", "https://careplusdiag.in", "Kochi", "IN", 8, 75, "Lab Technician"),
        ("City Clinic", "Dr. Sharma", "Doctor", "cityclinic.doc@gmail.com", "", "", "Agra", "IN", 1, 4, "Receptionist")],
    "retail_d2c": [
        ("UrbanKart Fashion", "Tanvi Sharma", "HR Manager", "tanvi@urbankart.in", "98990 66778", "https://urbankart.in", "Delhi", "IN", 30, 400, "Store Associate"),
        ("Masala Box Foods", "Kunal Agarwal", "Co-founder", "kunal@masalabox.in", "", "https://masalabox.in", "Indore", "IN", 8, 50, "Warehouse Associate"),
        ("GlowUp Beauty", "Ishita Sen", "Head of Retail", "ishita@glowupbeauty.in", "", "https://glowupbeauty.in", "Mumbai", "IN", 14, 120, "Beauty Advisor"),
        ("Handmade Hues", "Rekha", "Owner", "handmadehues@gmail.com", "", "", "Jaipur", "IN", 1, 3, "Packer")],
}


def industry_sequence(w, industry: str) -> dict:
    label = industry.replace("_", " ")
    steps = [
        {"day": 0, "name": "Intro", "subject": "{{company}}: faster {{role}} hiring",
         "body": "{{greeting}}\n\nTeams in " + label + " use us to phone-screen every {{role}} applicant." + UNSUB,
         "variants": [{"name": "B", "subject": "{{first_name}}, 5 screened {{role}} applicants this week?"}]},
        {"day": 3, "name": "Proof", "subject": "How {{company}} could save 10 hours a week",
         "body": "{{greeting}}\n\nA short example of a ranked shortlist for a {{role}} opening." + UNSUB},
        {"day": 7, "name": "Close", "subject": "Should I close your file, {{first_name}}?",
         "body": "{{greeting}}\n\nIf hiring {{role}} staff is not a priority at {{company}}, I'll stop here." + UNSUB},
    ]
    r = w.c.post("/api/sequences", json={"name": f"{label.title()} outreach", "steps": steps})
    assert r.status_code == 200, r.text
    return r.json()


def run_sequence(w, at):
    w.now = at
    r = w.c.post("/api/sequences/run", json={"now": at.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("industry", list(INDUSTRIES))
def test_campaign_auto_enrols_into_the_industry_sequence_and_replies_hand_over(world_factory, industry):
    rows = INDUSTRIES[industry]
    w = world_factory(ist(3, 9, 0), csv_of(rows))              # Tue 3 Nov
    chosen, other, free = [r[0] for r in rows[:2]], rows[2][0], rows[3][0]
    seq = industry_sequence(w, industry)
    # simple rule: a yes by email gets the sample-screen WhatsApp (only with consent and a phone)
    rule = w.c.post("/api/workflows", json={"name": "Yes → WhatsApp", "trigger": {"type": "reply", "value": "interested"},
                                             "action": {"type": "whatsapp", "value": "first_after_yes"}})
    assert rule.status_code == 200, rule.text
    # and a flow: any reply -> a call task for the owner
    w.team("Asha (calls)")
    flow = w.flow(f"{industry}: reply → call", [node("t", "trigger.reply", label="any"),
                                                 node("call", "call_task", owner="Asha (calls)", note="{{first_name}} at {{company}} replied to the sequence.")],
                  [edge("t", "call")])
    bad = w.c.post("/api/campaigns", json={"name": "x", "rules": {"auto_enrol": True, "sequence_id": "nope"}})
    assert bad.status_code == 400 and "does not exist" in bad.json()["detail"]
    camp = w.c.post("/api/campaigns", json={
        "name": f"{industry} Q4 push", "sources": {"csv_import_ids": [w.id(r[0]) for r in rows]},
        "enrich": {"crawler": False},
        "rules": {"min_score": 0, "require_business_email": True, "auto_enrol": True, "sequence_id": seq["id"],
                  "only_companies": [*chosen, free]}})
    assert camp.status_code == 200, camp.text
    report = w.c.post(f"/api/campaigns/{camp.json()['id']}/run", json={"now": ist(3, 9, 30).isoformat()}).json()
    assert report["attached"] == 4 and report["qualified"] == 3 and report["enrolled"] == 2, report
    assert any("not in this campaign's chosen companies" in e["message"] for e in report["errors"])
    for name in chosen:
        lead = w.full(name)
        assert lead["sequence"]["sequence_id"] == seq["id"] and lead["campaign_id"] == camp.json()["id"]
        assert any(f"({seq['name']})" in t for t in w.timeline(name))
    for name in (other, free):
        assert w.full(name).get("sequence") is None, name
    assert w.full(free)["campaign_check"]["reasons"] == ["free-mail address, not a business email"]

    first, second = chosen
    run_sequence(w, ist(3, 10, 0))
    out = {m["to"]: m for m in w.c.get("/api/outbox").json()}
    assert len(out) == 2
    for name in chosen:
        m = out[w.full(name)["email"]]
        assert m["status"] == "dry_run" and m["sequence_id"] == seq["id"] and m["step_day"] == 0 and ist_text(m["sent_at"]) == "Tue 03 Nov 10:00"
        first_name = w.full(name)["contact_name"].replace("Dr. ", "").split()[0]
        if m["variant"] == "A":
            assert m["subject"].startswith(f"{name}: faster ")
        else:
            assert m["variant"] == "B" and m["subject"].startswith(f"{first_name}, 5 screened ")
        assert f"Teams in {industry.replace('_', ' ')} use us" in m["body"] and "/u/" in m["body"]
        assert_rendered(m["subject"], m["body"])

    # the first company replies yes on Wednesday: its sequence stops, the rule WhatsApps (consent came with the yes)
    w.now = ist(4, 11, 0)
    assert w.reply(first, "Yes, interested. Please WhatsApp me or call me.")["label"] == "interested"   # naming WhatsApp in the reply is WhatsApp consent
    wa = w.whatsapps(first)
    if w.full(first).get("phone"):
        assert [m["template"] for m in wa] == ["first_after_yes"] and wa[0]["status"] == "dry_run"
        assert_rendered(wa[0]["body"])
    else:
        assert wa == []
    assert w.whatsapps(second) == []                            # no reply, no consent: never WhatsApp
    w.tick(ist(4, 11, 1))
    call = [t for t in w.tasks(first) if t["kind"] == "call"]
    assert len(call) == 1 and call[0]["owner"] == "Asha (calls)" and "replied to the sequence." in call[0]["text"]

    for day in range(4, 12):                                    # every day at 10:00 IST for a week
        run_sequence(w, ist(day, 10, 0))
    second_mails = sorted((m for m in w.c.get("/api/outbox").json() if m["to"] == w.full(second)["email"]), key=lambda m: m["sent_at"])
    assert [(m["step_day"], ist_text(m["sent_at"])) for m in second_mails] == [
        (0, "Tue 03 Nov 10:00"), (3, "Fri 06 Nov 10:00"), (7, "Tue 10 Nov 10:00")]
    assert second_mails[2]["subject"].startswith("Should I close your file, ")
    assert_rendered(*[m["subject"] + m["body"] for m in second_mails])
    first_mails = [m for m in w.c.get("/api/outbox").json() if m["to"] == w.full(first)["email"]]
    assert len(first_mails) == 1                                # the yes stopped the sequence
    assert w.full(first)["sequence"]["stopped"] == "replied" and w.runs(flow)[first]["status"] == "done"
