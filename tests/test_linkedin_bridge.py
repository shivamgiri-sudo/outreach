"""The CSV hand-off to a third-party LinkedIn tool and the import of its results. No LinkedIn is ever contacted."""
import csv
import io

from fastapi.testclient import TestClient

from app import linkedin_bridge as bridge
from app import linkedin_seq as li
from app.main import create_app

BOM = "﻿"
CSV = ("company,contact_name,title,email,website\n"
       "Acme Retail,Dr. Asha Rao,HR Head,asha@acme-retail.in,https://acme-retail.in\n"
       "Beta Logistics,Ravi Kumar,Founder,ravi@beta.in,https://beta.in\n"
       "Gone Co,Meera Iyer,CEO,meera@gone.in,https://gone.in\n"
       "Out Co,Nisha Pal,CEO,nisha@out.in,https://out.in\n"
       "Evil Co,Zed,CEO,zed@evil.in,https://evil.in\n"
       "No Link Co,Lata Sen,CEO,lata@nolink.in,https://nolink.in\n"
       "Formula Co,=cmd|' /C calc'!A0,CEO,f@formula.in,https://formula.in\n")
LINKS = {'Acme Retail': 'https://www.linkedin.com/in/asha-rao/', 'Beta Logistics': 'https://in.linkedin.com/in/ravi-kumar?trk=x', 'Gone Co': 'https://www.linkedin.com/in/meera-iyer', 'Out Co': 'https://www.linkedin.com/in/nisha-pal', 'Evil Co': 'javascript:alert(1)', 'Formula Co': 'https://www.linkedin.com/in/formula'}


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "store.json"))
    assert c.post("/api/leads/import", json={"csv": CSV, "source": "manual"}).status_code == 200
    c.app.state.store.update("leads", lambda rows: [{**l, "linkedin_url": LINKS.get(l["company"], "")} for l in rows])
    return c


def lead(c, company):
    return next(l for l in c.get("/api/leads").json() if l["company"] == company)


def export(c, **params):
    r = c.get("/api/export/linkedin_tool.csv", params=params)
    assert r.status_code == 200, r.text
    text = r.content.decode("utf-8")
    assert text.startswith(BOM)
    return r, list(csv.DictReader(io.StringIO(text[len(BOM):])))


def test_export_has_the_tool_columns_and_only_open_leads_with_an_http_profile_link(tmp_path):
    c = make(tmp_path)
    c.post(f"/api/leads/{lead(c, 'Gone Co')['id']}/stage", json={"stage": "unsubscribed"})
    c.app.state.store.update("suppressed", lambda rows: [*rows, "nisha@out.in"])
    r, rows = export(c)
    assert next(csv.reader(io.StringIO(r.content.decode("utf-8")[1:]))) == ["firstName", "lastName", "companyName", "title", "profileUrl",
                                                                              "message1", "message2", "message3", "message4", "message5"]
    assert sorted(x["companyName"] for x in rows) == ["Acme Retail", "Beta Logistics", "Formula Co"]
    assert 'filename="outreach-linkedin-tool-' in r.headers["content-disposition"]
    acme = next(x for x in rows if x["companyName"] == "Acme Retail")
    assert (acme["firstName"], acme["lastName"], acme["title"]) == ("Asha", "Rao", "HR Head")
    assert acme["profileUrl"] == "https://www.linkedin.com/in/asha-rao/"


def test_messages_are_the_sequence_steps_note_limit_and_case_study_only_with_a_link(tmp_path):
    c = make(tmp_path)
    _, rows = export(c)
    acme = next(x for x in rows if x["companyName"] == "Acme Retail")
    settings = c.app.state.store.read("settings")
    steps = {s["n"]: s for s in li.STEPS}
    lead_row = lead(c, "Acme Retail")
    assert acme["message1"] == li.render(steps[1], lead_row, settings) and acme["message1"].startswith("Hi Asha,")
    assert len(acme["message1"]) <= 300
    assert acme["message2"] == li.render(steps[2], lead_row, settings) and "Thanks for connecting, Asha." in acme["message2"]
    assert acme["message3"] == li.render(steps[3], lead_row, settings)
    assert acme["message4"] == li.render(steps[5], lead_row, settings) and "bookmyinterview.ai" in acme["message4"]      # case study skipped
    assert acme["message5"] == ""
    assert c.put("/api/linkedin/settings", json={"case_study_url": "https://bookmyinterview.ai/case-study"}).status_code == 200
    _, rows = export(c)
    acme = next(x for x in rows if x["companyName"] == "Acme Retail")
    assert "case study" in acme["message4"] and "bookmyinterview.ai/" in acme["message5"] and acme["message5"] != ""


def test_long_note_is_cut_to_300_characters(tmp_path):
    c = make(tmp_path)
    lid = lead(c, "Acme Retail")["id"]
    c.app.state.store.update_lead(lid, lambda l: {**l, "contact_name": "Asha " + "x" * 400})
    _, rows = export(c)
    assert all(len(r["message1"]) <= 300 for r in rows)


def test_export_cells_cannot_run_as_formulas_and_filters_work(tmp_path):
    c = make(tmp_path)
    text = c.get("/api/export/linkedin_tool.csv").content.decode("utf-8")
    assert "'=cmd" in text and ",=cmd" not in text                                # csv_safe on the name column
    ids = [lead(c, "Acme Retail")["id"]]
    _, only = export(c, ids=",".join(ids))
    assert [r["companyName"] for r in only] == ["Acme Retail"]
    posted = c.post("/api/export/linkedin_tool.csv", json={"ids": ids})
    assert posted.status_code == 200 and "Beta Logistics" not in posted.content.decode("utf-8")
    listed = {d["dataset"]: d for d in c.get("/api/export").json()}
    assert listed["linkedin_tool"]["owner_only"] and listed["linkedin_tool"]["columns"] == bridge.TOOL_COLUMNS


def test_profile_key_matches_scheme_www_query_and_slash_differences():
    keys = {bridge.profile_key(u) for u in ("https://www.linkedin.com/in/Asha-Rao/", "http://linkedin.com/in/asha-rao?trk=1#x",
                                            "https://in.linkedin.com/in/asha-rao//")}
    assert keys == {"linkedin.com/in/asha-rao"}
    assert bridge.profile_key("javascript:alert(1)") == "" and bridge.profile_key("https://evil.com/in/asha-rao") == ""
    assert bridge.profile_key("https://www.linkedin.com/company/acme") == ""


def results(c, text):
    return c.post("/api/linkedin/results", json={"csv": text})


def test_results_mark_accepted_and_replied_by_profile_link_and_report_unmatched(tmp_path):
    c = make(tmp_path)
    for company in ("Acme Retail", "Beta Logistics", "Formula Co"):
        assert c.post(f"/api/leads/{lead(c, company)['id']}/linkedin/start").status_code == 200
        c.post(f"/api/leads/{lead(c, company)['id']}/linkedin/sent", json={"step": 1})
    text = ("profileUrl,status,fullName\n"
            "https://linkedin.com/in/ASHA-RAO,Connected,Asha\n"
            "https://www.linkedin.com/in/ravi-kumar/,replied,Ravi\n"
            "https://www.linkedin.com/in/formula,Invitation sent,F\n"
            "https://www.linkedin.com/in/someone-else,connected,S\n"
            "not a link,connected,X\n")
    r = results(c, text)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["rows"], out["matched"], out["accepted"], out["replied"], out["unmatched"]) == (5, 3, 1, 1, 2)
    assert out["unmatched_links"] == ["https://linkedin.com/in/someone-else"] and out["pending_or_unknown"] == 1
    assert lead(c, "Acme Retail")["linkedin_seq"]["accepted_at"] and lead(c, "Acme Retail")["linkedin_seq"]["status"] == "messaging"
    assert lead(c, "Beta Logistics")["linkedin_seq"]["status"] == "stopped" and lead(c, "Beta Logistics")["linkedin_seq"]["stopped"] == "replied"
    assert "accepted_at" not in lead(c, "Formula Co")["linkedin_seq"]
    again = results(c, text).json()                                              # the same file twice changes nothing
    assert (again["accepted"], again["replied"], again["already_up_to_date"]) == (0, 0, 2)


def test_results_for_a_lead_not_on_the_sequence_are_reported_not_started(tmp_path):
    c = make(tmp_path)
    out = results(c, "linkedin_url;connection status\nhttps://www.linkedin.com/in/asha-rao;Accepted\n").json()
    assert out["matched"] == 1 and out["accepted"] == 0 and out["not_on_sequence"] == 1 and out["problems"]
    assert "linkedin_seq" not in lead(c, "Acme Retail")


def test_results_file_problems_are_plain_english(tmp_path):
    c = make(tmp_path)
    assert "empty" in results(c, "").json()["detail"]
    assert "profile link column" in results(c, "name,status\nA,connected\n").json()["detail"]
    assert "status column" in results(c, "profileUrl,name\nhttps://www.linkedin.com/in/x,A\n").json()["detail"]
    assert results(c, "﻿profileUrl,Status\r\nhttps://www.linkedin.com/in/x,connected\r\n").json()["unmatched"] == 1     # BOM and CRLF are fine


def test_classify_status_wording():
    assert [bridge.classify(s) for s in ("Connected", "accepted", "Connection accepted", "Message sent", "Replied", "Has replied",
                                          "Invitation sent", "Pending", "no response", "weird")] == \
           ["accepted", "accepted", "accepted", "accepted", "replied", "replied", "pending", "pending", "unknown", "unknown"]
