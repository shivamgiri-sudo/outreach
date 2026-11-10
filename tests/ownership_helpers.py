"""Shared setup for the lead-ownership tests: one app, an owner, two members and a viewer (every workspace), a Main admin, a second workspace
with its own admin, three Main leads (owned by Ravi, Priya, nobody) and one lead in the second workspace."""
from fastapi.testclient import TestClient

from app import auth
from app.main import create_app

CSV = ("company,contact_name,title,email,phone,website\n"
       "Acme,Ravi Kumar,HR Head,ravi@acme.com,98765 43210,https://acme.com\n"
       "Beta Ltd,Sita Rao,HR,sita@beta.in,91234 56789,https://beta.in\n"
       "Cora Inc,Tej Nair,CTO,tej@cora.io,90000 11111,https://cora.io\n")
PW = "long-password-1"


class Rig:
    pass


def rig(tmp_path, mode_on=True):
    r = Rig()
    r.c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    r.store = r.c.app.state.store
    owner = r.c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    r.h = {"owner": {"Authorization": f"Bearer {owner}"}}
    r.w2 = r.c.post("/api/workspaces", json={"name": "Client Two"}, headers=r.h["owner"]).json()["id"]
    for email, role, wss in [("priya@x.in", "caller", []), ("ravi@x.in", "caller", []), ("adm@x.in", "admin", ["main"]),
                             ("adm2@x.in", "admin", [r.w2]), ("view@x.in", "viewer", [])]:
        assert r.c.post("/api/users", json={"email": email, "name": email.split("@")[0].capitalize(), "role": role, "password": PW,
                                            "workspaces": wss, "all_workspaces": not wss}, headers=r.h["owner"]).status_code == 200
        tok = auth.issue_token(r.store, auth.subject_principal(r.store, "admin-pass-123", "user:" + email))      # login is rate limited (5/min)
        r.h[email.split("@")[0]] = {"Authorization": f"Bearer {tok}"}
    r.c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=r.h["owner"])
    r.lead = {l["company"]: l for l in r.c.get("/api/leads", headers=r.h["owner"]).json()}
    for company, owner_name in (("Acme", "Ravi"), ("Beta Ltd", "Priya")):
        r.set(company, owner=owner_name, linkedin_url=f"https://www.linkedin.com/in/{company.split()[0].lower()}")
    r.set("Cora Inc", linkedin_url="https://www.linkedin.com/in/cora")
    other = dict(r.lead["Cora Inc"])
    other = {**{k: v for k, v in other.items() if k in ("company", "contact_name", "title")}, "company": "Other WS Co"}
    r.c.post("/api/leads/import", json={"csv": "company,contact_name,title,email\nOther WS Co,Zed,CEO,zed@other.com\n", "source": "apollo"},
             headers={**r.h["owner"], "X-Workspace": r.w2})
    r.other = next(l for l in r.c.get("/api/leads", headers={**r.h["owner"], "X-Workspace": r.w2}).json() if l["company"] == "Other WS Co")
    r.set("Other WS Co", owner="Priya", id=r.other["id"])
    if mode_on:
        r.mode(True)
    return r


def _set(self, company, id=None, **fields):
    lid = id or self.lead[company]["id"]
    self.store.update_lead(lid, lambda l: {**l, **fields})


def _mode(self, on, ws=("main", "w2")):
    wss = ["main", self.w2] if on else []
    self.store.update("settings", lambda s: {**s, "ownership_mode": {w: True for w in wss}})


Rig.set, Rig.mode = _set, _mode


def lead(r, company):
    return next(l for l in r.store.read("leads") if l["company"] == company)


def new_list(r, name, companies, assigned_to="", ws="main"):
    from app import lists
    return lists.create(r.store, name, [r.lead[c]["id"] for c in companies], "Admin", assigned_to=assigned_to, workspace=ws)
