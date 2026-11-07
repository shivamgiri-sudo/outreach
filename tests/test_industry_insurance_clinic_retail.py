"""Industry scenarios 5-7: insurance agencies (no-reply-for-N-days and stage-changed triggers, unsubscribe mid-flow),
healthcare clinics (reply-triggered warm flow, WhatsApp with and without consent, meeting check) and retail / D2C
brands (fields used in later messages, If on city, webhook to a CRM, Repeat, daily cap, a lead deleted mid-run)."""
from datetime import timedelta

from tests.flow_helpers import FakeTransport, edge, node
from tests.scenario_helpers import assert_rendered, csv_of, ist, ist_text, run_path, world_factory  # noqa: F401

SIGN_OFF = "\n\n— {{sender}}\n\nNot relevant? Unsubscribe: {{unsub_link}}"

# ---------------------------------------------------------------- 5. insurance agencies
INSURANCE = csv_of([
    ("SecureLife Advisors", "Deepak Malhotra", "Branch Head", "deepak@securelife.in", "98101 77889", "https://securelife.in", "Delhi", "IN", 20, 140, "Insurance Advisor"),
    ("ShieldCover Brokers", "Nisha Batra", "Principal Officer", "nisha@shieldcover.co.in", "", "https://shieldcover.co.in", "Chandigarh", "IN", 6, 30, "Relationship Manager"),
    ("Kavach Insurance Marketing", "Ramesh Iyer", "Director", "ramesh@kavachinsure.in", "", "https://kavachinsure.in", "Chennai", "IN", 9, 55, "Field Sales Officer"),
])


def insurance_flows(w):
    intro = w.flow("Insurance: intro email", [
        node("t", "trigger.manual"),
        node("m", "email", subject="{{company}}: 40 {{role}} applicants screened by Friday",
             body="{{greeting}}\n\nAgency hiring is a numbers game. We phone-screen every {{role}} applicant and send you the shortlist." + SIGN_OFF),
    ], [edge("t", "m")])
    quiet = w.flow("Insurance: no reply in 4 days", [
        node("t", "trigger.no_reply_days", days=4),
        node("ph", "if", field="has_phone", op="eq", value="true"),
        node("call", "call_task", owner="Anil (agency desk)", note="{{first_name}} at {{company}} did not reply in 4 days. One call, then close."),
        node("li", "linkedin_task", message="Hi {{first_name}}, I wrote about {{role}} hiring at {{company}}. Worth a chat?"),
        node("w", "wait", days=2, hours=0, business_hours=True),
        node("last", "email", subject="Last note from {{sender}}",
             body="{{greeting}}\n\nI'll stop here. If {{company}} needs {{role}} hires later, just reply." + SIGN_OFF),
        node("park", "set_stage", stage="not_now"),
    ], [edge("t", "ph"), edge("ph", "call", "yes"), edge("ph", "li", "no"), edge("call", "w"), edge("li", "w"), edge("w", "last"),
        edge("last", "park")])
    engaged = w.flow("Insurance: engaged leads", [
        node("t", "trigger.stage_changed", stage="engaged"),
        node("n", "notify", to="Anil (agency desk)", message="{{company}} is engaged: reply to {{first_name}} today."),
        node("tag", "add_tag", tag="hot-insurance"),
    ], [edge("t", "n"), edge("n", "tag")], stop_on_reply=False)
    return intro, quiet, engaged


def test_insurance_no_reply_and_stage_changed_triggers(world_factory):
    w = world_factory(ist(2, 11, 0), INSURANCE)                  # Mon 2 Nov 11:00 IST
    w.team("Anil (agency desk)")
    intro, quiet, engaged = insurance_flows(w)
    names = ["SecureLife Advisors", "ShieldCover Brokers", "Kavach Insurance Marketing"]
    w.enroll(intro, *names)
    w.tick(ist(2, 11, 0))
    assert sorted(m["to"] for m in w.emails()) == ["deepak@securelife.in", "nisha@shieldcover.co.in", "ramesh@kavachinsure.in"]
    predicted = w.simulate(quiet, "SecureLife Advisors")

    w.run_until(ist(4, 12, 0), every=timedelta(hours=1))
    assert w.reply("Kavach Insurance Marketing", "Sounds useful. Which cities do you cover?")["label"] == "question"
    assert w.full("Kavach Insurance Marketing")["stage"] == "engaged"
    # the reply moved them to Engaged: the "stage changes" flow starts (fix: replies used to change the stage silently)
    assert "Kavach Insurance Marketing" in w.runs(engaged)
    w.tick(ist(4, 12, 30))
    assert [n["text"] for n in w.notifications("Kavach Insurance Marketing") if n["kind"] == "flow"] == [
        "Kavach Insurance Marketing is engaged: reply to Ramesh today."]
    assert w.full("Kavach Insurance Marketing")["tags"] == ["hot-insurance"]

    w.run_until(ist(6, 10, 0), every=timedelta(hours=1))
    assert w.runs(quiet) == {}                                   # 4 full days not over yet (sent Mon 11:00)
    w.tick(ist(6, 11, 0))
    runs = w.runs(quiet)
    assert set(runs) == {"SecureLife Advisors", "ShieldCover Brokers"}   # Kavach replied: never "no reply"
    call = [t for t in w.tasks("SecureLife Advisors") if t["kind"] == "call"]
    assert [(t["owner"], ist_text(t["due_at"])) for t in call] == [("Anil (agency desk)", "Fri 06 Nov 11:00")]
    assert call[0]["text"].endswith("Deepak at SecureLife Advisors did not reply in 4 days. One call, then close.")
    nisha = [t for t in w.tasks("ShieldCover Brokers") if t["kind"] == "linkedin"]
    assert [t["text"] for t in nisha] == ["Hi Nisha, I wrote about relationship manager hiring at ShieldCover Brokers. Worth a chat?"]

    w.tick(ist(7, 12, 0))
    w.unsubscribe("ShieldCover Brokers")                         # Saturday: she clicks the unsubscribe link
    w.run_until(ist(9, 12, 0))
    final = w.runs(quiet)
    deepak = w.emails("SecureLife Advisors")
    assert [(m["subject"], ist_text(m["sent_at"])) for m in deepak] == [
        ("SecureLife Advisors: 40 insurance advisor applicants screened by Friday", "Mon 02 Nov 11:00"),
        ("Last note from BookMyInterview team", "Mon 09 Nov 09:00")]
    assert_rendered(*[m["subject"] + m["body"] for m in deepak])
    assert w.full("SecureLife Advisors")["stage"] == "not_now" and final["SecureLife Advisors"]["status"] == "done"
    assert run_path(final["SecureLife Advisors"]) == predicted
    shield = final["ShieldCover Brokers"]
    assert shield["status"] == "stopped" and shield["stop_reason"].startswith("lead is unsubscribed")
    assert len(w.emails("ShieldCover Brokers")) == 1             # nothing after the unsubscribe
    assert w.runs(intro)["Kavach Insurance Marketing"]["status"] == "done"


# ---------------------------------------------------------------- 6. healthcare clinics
CLINICS = csv_of([
    ("Sunrise Multispeciality Clinic", "Dr. Ashok Verma", "Medical Director", "ashok@sunriseclinic.in", "98270 12345", "https://sunriseclinic.in", "Indore", "IN", 5, 60, "Staff Nurse"),
    ("SmileCare Dental", "Dr. Leena Thomas", "Founder", "leena@smilecaredental.in", "99000 54321", "https://smilecaredental.in", "Bengaluru", "IN", 3, 18, "Dental Assistant"),
    ("CarePlus Diagnostics", "Vivek Nair", "Operations Head", "vivek@careplusdiag.in", "", "https://careplusdiag.in", "Kochi", "IN", 8, 75, "Lab Technician"),
])


def clinic_flows(w):
    cold = w.flow("Clinics: intro", [
        node("t", "trigger.manual"),
        node("m", "email", subject="Front desk and {{role}} hiring at {{company}}",
             body="{{greeting}}\n\nClinics lose good {{role}} applicants to slow callbacks. We call each one within the hour." + SIGN_OFF),
        node("wr", "wait_for_reply", days=5),
        node("q", "notify", to="Ritika (clinic desk)", message="{{company}} asked a question: answer {{first_name}} today."),
        node("m2", "email", subject="Re: {{role}} hiring at {{company}}", body="{{greeting}}\n\nShould I send two sample candidate reports?" + SIGN_OFF),
    ], [edge("t", "m"), edge("m", "wr"), edge("wr", "q", "question"), edge("wr", "m2", "timeout")])
    warm = w.flow("Clinics: any reply → WhatsApp + call", [       # default setting: stop on reply (it must still run)
        node("t", "trigger.reply", label="any"),
        node("ok", "if", field="has_consent", op="eq", value="true"),
        node("wa", "whatsapp", template="first_after_yes"),
        node("call", "call_task", owner="Ritika (clinic desk)", note="{{first_name}} at {{company}} replied. Call before 18:55."),
        node("w", "wait", days=2, hours=0, business_hours=True),
        node("meet", "if", field="has_meeting", op="eq", value="false"),
        node("nudge", "notify", to="Ritika (clinic desk)", message="No meeting yet with {{company}}. Follow up with {{first_name}}."),
    ], [edge("t", "ok"), edge("ok", "wa", "yes"), edge("ok", "call", "no"), edge("wa", "call"), edge("wa", "call", "error"),
        edge("call", "w"), edge("w", "meet"), edge("meet", "nudge", "yes")])
    return cold, warm


def test_healthcare_clinic_reply_flow_whatsapp_with_and_without_consent(world_factory):
    w = world_factory(ist(3, 10, 0), CLINICS)                    # Tue 3 Nov 10:00 IST
    w.team("Ritika (clinic desk)")
    cold, warm = clinic_flows(w)
    w.enroll(cold, "Sunrise Multispeciality Clinic", "SmileCare Dental", "CarePlus Diagnostics")
    w.tick(ist(3, 10, 0))
    first = w.emails()
    assert len(first) == 3 and all(m["body"].startswith("Hi ") for m in first)
    by_to = {m["to"]: m for m in first}
    assert by_to["ashok@sunriseclinic.in"]["body"].startswith("Hi Ashok,\n\nClinics lose good staff nurse applicants")

    w.tick(ist(3, 15, 0))
    w.unsubscribe("CarePlus Diagnostics")
    w.tick(ist(4, 18, 30))                                       # Wed 18:30: after hours
    w.consent("Sunrise Multispeciality Clinic")                  # consent is per channel: an email yes alone is not a WhatsApp yes
    assert w.reply("Sunrise Multispeciality Clinic", "Yes please, interested. We need 3 nurses.")["label"] == "interested"
    assert w.reply("SmileCare Dental", "Do you have candidates in Bengaluru? What is the price?")["label"] == "question"
    assert set(w.runs(warm)) == {"Sunrise Multispeciality Clinic", "SmileCare Dental"}
    predicted = {name: w.simulate(warm, name) for name in ("Sunrise Multispeciality Clinic", "SmileCare Dental")}
    w.tick(ist(4, 18, 31))
    assert w.whatsapps() == []                                   # 18:31 is outside business hours: WhatsApp waits
    sunrise_run = w.runs(warm)["Sunrise Multispeciality Clinic"]
    assert sunrise_run["status"] == "waiting" and sunrise_run["deferred"].startswith("waiting for business hours")
    w.tick(ist(5, 9, 0))
    wa = w.whatsapps()
    assert [(m["to"], m["template"], ist_text(m["sent_at"])) for m in wa] == [("+919827012345", "first_after_yes", "Thu 05 Nov 09:00")]
    assert wa[0]["body"].startswith("Hi Ashok, this is BookMyInterview team from BookMyInterview.ai. Thanks for replying yes")
    assert_rendered(wa[0]["body"])
    assert w.full("Sunrise Multispeciality Clinic")["stage"] == "whatsapp_active"
    for name in ("Sunrise Multispeciality Clinic", "SmileCare Dental"):
        call = [t for t in w.tasks(name) if t["kind"] == "call"]
        assert len(call) == 1 and call[0]["owner"] == "Ritika (clinic desk)", name
    smile_call = [t for t in w.tasks("SmileCare Dental") if t["kind"] == "call"][0]
    assert smile_call["text"].endswith("Leena at SmileCare Dental replied. Call before 18:55.")
    assert w.whatsapps("SmileCare Dental") == []                 # a question is not consent: no WhatsApp
    q = [n for n in w.notifications("SmileCare Dental") if n["kind"] == "flow" and "asked a question" in n["text"]]
    assert [n["to"] for n in q] == ["Ritika (clinic desk)"]

    # Sunrise books a meeting on Friday; SmileCare does not
    w.store.update_lead(w.id("Sunrise Multispeciality Clinic"), lambda l: {**l, "meetings": [
        {"id": "mt1", "status": "scheduled", "start": "2026-11-10T11:00:00+05:30", "created_at": "2026-11-06T05:00:00+00:00"}]})
    predicted["Sunrise Multispeciality Clinic"] = w.simulate(warm, "Sunrise Multispeciality Clinic")
    w.run_until(ist(9, 12, 0))
    final = w.runs(warm)
    nudges = [n["text"] for n in w.notifications() if n["kind"] == "flow" and n["text"].startswith("No meeting yet")]
    assert nudges == ["No meeting yet with SmileCare Dental. Follow up with Leena."]
    for name, path in predicted.items():
        assert final[name]["status"] == "done" and run_path(final[name]) == path, name
    cold_runs = w.runs(cold)
    assert cold_runs["CarePlus Diagnostics"]["status"] == "stopped" and "unsubscribed" in cold_runs["CarePlus Diagnostics"]["stop_reason"]
    assert len(w.emails("CarePlus Diagnostics")) == 1 and w.full("CarePlus Diagnostics")["stage"] == "unsubscribed"
    assert "CarePlus Diagnostics" not in final                   # an unsubscribe is not a reply that starts a flow
    assert len(w.emails()) == 3                                  # nobody got the follow-up: two replied, one unsubscribed


# ---------------------------------------------------------------- 7. retail / D2C brands
RETAIL = csv_of([
    ("UrbanKart Fashion", "Tanvi Sharma", "HR Manager", "tanvi@urbankart.in", "", "https://urbankart.in", "Delhi", "IN", 30, 400, "Store Associate"),
    ("Masala Box Foods", "Kunal Agarwal", "Co-founder", "kunal@masalabox.in", "", "https://masalabox.in", "Indore", "IN", 8, 50, "Warehouse Associate"),
    ("GlowUp Beauty", "Ishita Sen", "Head of Retail", "ishita@glowupbeauty.in", "", "https://glowupbeauty.in", "Mumbai", "IN", 14, 120, "Beauty Advisor"),
])


def retail_flow(w):
    nodes = [
        node("t", "trigger.manual"),
        node("sv", "set_vars", values=[{"name": "season", "value": "Diwali"},
                                       {"name": "offer", "value": "screen 50 {{role}} applicants free before {{season}}"}]),
        node("metro", "if", field="city", op="in", value="Delhi, Mumbai, Bengaluru"),
        node("em", "email", subject="{{season}} hiring at {{company}}", body="{{greeting}}\n\nMetro stores are hiring fast for {{season}}. We can {{offer}}." + SIGN_OFF),
        node("et", "email", subject="{{season}} staff for {{company}}", body="{{greeting}}\n\nFor your warehouse team: we can {{offer}}." + SIGN_OFF),
        node("crm", "webhook", url="https://crm.example.in/hooks/outreach"),
        node("tag", "add_tag", tag="diwali-2026"),
        node("wr", "wait_for_reply", days=2),
        node("hot", "notify", to="Rina (retail)", message="{{company}} replied to the {{season}} email."),
        node("lp", "loop", times=2),
        node("w", "wait", days=1, hours=0),
        node("rem", "email", subject="{{season}} is close, {{first_name}}", body="{{greeting}}\n\nStill happy to {{offer}}." + SIGN_OFF),
        node("cold", "notify", to="Rina (retail)", message="{{company}} went quiet after the {{season}} emails."),
    ]
    edges = [edge("t", "sv"), edge("sv", "metro"), edge("metro", "em", "yes"), edge("metro", "et", "no"), edge("em", "crm"),
             edge("et", "crm"), edge("crm", "tag"), edge("crm", "tag", "error"), edge("tag", "wr"), edge("wr", "hot", "any"),
             edge("wr", "lp", "timeout"), edge("lp", "w", "again"), edge("w", "rem"), edge("rem", "lp"), edge("lp", "cold", "done")]
    return w.flow("D2C: Diwali store hiring", nodes, edges)


def test_retail_d2c_fields_city_branch_webhook_repeat_cap_and_deleted_lead(world_factory):
    crm = FakeTransport(reply={"ok": True})
    w = world_factory(ist(2, 10, 0), RETAIL, channel_transport=crm)   # Mon 2 Nov 10:00 IST
    w.team("Rina (retail)")
    assert w.c.put("/api/settings", json={"daily_cap": 2}).status_code == 200
    flow = retail_flow(w)
    predicted = {"UrbanKart Fashion": w.simulate(flow, "UrbanKart Fashion"),
                 "Masala Box Foods": w.simulate(flow, "Masala Box Foods", "question")}
    w.enroll(flow, "UrbanKart Fashion", "Masala Box Foods", "GlowUp Beauty")
    w.tick(ist(2, 10, 0))
    day1 = w.emails()
    assert len(day1) == 2                                        # daily cap of 2: the third waits for tomorrow
    subjects = {m["to"]: (m["subject"], m["body"]) for m in day1}
    assert subjects["tanvi@urbankart.in"][0] == "Diwali hiring at UrbanKart Fashion"
    assert "Metro stores are hiring fast for Diwali. We can screen 50 store associate applicants free before Diwali." in subjects["tanvi@urbankart.in"][1]
    assert subjects["kunal@masalabox.in"] == ("Diwali staff for Masala Box Foods", subjects["kunal@masalabox.in"][1])
    assert "For your warehouse team: we can screen 50 warehouse associate applicants free before Diwali." in subjects["kunal@masalabox.in"][1]
    assert_rendered(*[m["subject"] + m["body"] for m in day1])
    held = w.runs(flow)["GlowUp Beauty"]
    assert held["status"] == "waiting" and held["deferred"].startswith("today's email limit (2)")
    hooks = [c for c in crm.calls if "crm.example.in" in c["url"]]
    assert len(hooks) == 2 and {h["body"]["lead"]["company"] for h in hooks} == {"UrbanKart Fashion", "Masala Box Foods"}
    assert all("email" not in h["body"]["lead"] for h in hooks)  # least data: no contact details unless asked

    w.tick(ist(2, 16, 0))
    assert w.reply("Masala Box Foods", "Can you share the pricing for 8 warehouse hires?")["label"] == "question"
    w.tick(ist(2, 16, 30))
    assert [n["text"] for n in w.notifications("Masala Box Foods") if n["kind"] == "flow"] == ["Masala Box Foods replied to the Diwali email."]

    w.tick(ist(3, 9, 0))
    glow = w.emails("GlowUp Beauty")
    assert [(m["subject"], ist_text(m["sent_at"])) for m in glow] == [("Diwali hiring at GlowUp Beauty", "Tue 03 Nov 09:00")]
    w.tick(ist(3, 12, 0))
    w.delete("GlowUp Beauty")                                    # removed by the owner mid-run
    w.run_until(ist(9, 12, 0))
    final = w.runs(flow)
    glow_run = next(r for r in w.c.get(f"/api/flows/{flow['id']}/runs").json() if r["company"] is None)
    assert glow_run["status"] == "stopped" and glow_run["stop_reason"] == "the lead no longer exists (merged or deleted)"
    urban = w.emails("UrbanKart Fashion")
    assert [(m["subject"], ist_text(m["sent_at"])) for m in urban] == [
        ("Diwali hiring at UrbanKart Fashion", "Mon 02 Nov 10:00"),
        ("Diwali is close, Tanvi", "Thu 05 Nov 10:00"),          # reply wait to Wed 10:00, then a 1-day wait
        ("Diwali is close, Tanvi", "Fri 06 Nov 10:00")]
    assert [n["text"] for n in w.notifications("UrbanKart Fashion") if n["kind"] == "flow"] == ["UrbanKart Fashion went quiet after the Diwali emails."]
    assert w.full("UrbanKart Fashion")["tags"] == ["diwali-2026"]
    for name, path in predicted.items():
        assert final[name]["status"] == "done" and run_path(final[name]) == path, name
