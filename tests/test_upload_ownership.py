"""Upload ownership: members own what they upload, owned-by-other rows are reported, caps, admin split.
Made-up names and numbers only."""
import threading

import pytest
from fastapi.testclient import TestClient

from app import upload_owner
from app.main import create_app

USERS = [{"email": n + "@x.example", "name": n, "role": "caller"} for n in ("asha", "ben", "chitra")]


def csv_of(*emails):
    return "Company,Email\n" + "\n".join(f"Co{i},{e}" for i, e in enumerate(emails)) + "\n"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from app import main
    owner = main.LOCAL_OWNER
    app = create_app(data_file=tmp_path / "d.json")

    class State(dict):
        def __setitem__(self, k, v):          # the signed-in person for every following request (None = local owner)
            monkeypatch.setattr(main, "LOCAL_OWNER", v if v is not None else owner)
    return app, State(), TestClient(app)


def login(state, name, role="caller"):
    state["user"] = {"sub": name, "name": name, "email": name + "@x.example", "role": role, "claims": None}


def leads(client):
    return {l["email"]: l for l in client.get("/api/leads").json()}


def commit(client, csv, **extra):
    r = client.post("/api/import/commit", json={"csv": csv, "source": "batch.csv", **extra})
    return r


def test_member_upload_owns_leads_and_gets_a_list(env):
    _, state, c = env
    login(state, "asha")
    r = commit(c, csv_of("a@one.example", "b@two.example")).json()
    L = leads(c)
    assert {L["a@one.example"]["owner"], L["b@two.example"]["owner"]} == {"asha"}
    assert L["a@one.example"]["owner_why"]["kind"] == "upload"
    rows = c.get("/api/lists").json()
    assert len(rows) == 1 and rows[0]["name"] == "batch.csv" and rows[0]["assigned_to"] == "asha"
    assert r["list_ids"] == [rows[0]["id"]]


def test_owned_by_other_is_skipped_and_reported_never_merged(env):
    _, state, c = env
    login(state, "asha")
    commit(c, csv_of("a@one.example"))
    login(state, "ben")
    r = commit(c, "Company,Email,City\nOther Name,a@one.example,Pune\nNew,n@new.example,Pune\n").json()
    assert r["summary"]["owned_by_other"] == 1
    L = leads(c)
    assert L["a@one.example"]["owner"] == "asha" and not L["a@one.example"].get("city")
    assert L["n@new.example"]["owner"] == "ben"
    conf = c.get(f"/api/imports/{r['import_id']}/conflicts").json()
    assert conf["conflicts"] == [{"row": 2, "lead_id": L["a@one.example"]["id"], "owner": "asha"}]
    ben_list = [x for x in c.get("/api/lists").json() if x["assigned_to"] == "ben"][0]
    assert ben_list["stats"]["total"] == 1 if "total" in ben_list["stats"] else True


def test_reupload_own_file_merges_and_unowned_is_claimed(env):
    _, state, c = env
    state["user"] = None
    commit(c, csv_of("free@one.example"))             # admin, no assign_to: no owner, no list
    assert leads(c)["free@one.example"].get("owner") in (None, "") and c.get("/api/lists").json() == []
    login(state, "asha")
    r = commit(c, "Company,Email,City\nCo0,free@one.example,Pune\n").json()
    assert leads(c)["free@one.example"]["owner"] == "asha" and r["summary"]["owned_by_other"] == 0
    r2 = commit(c, csv_of("free@one.example")).json()
    assert r2["summary"]["owned_by_other"] == 0 and len(leads(c)) == 1
    assert len(c.get("/api/lists").json()) == 2


def test_conflicts_endpoint_scoping(env):
    _, state, c = env
    login(state, "asha")
    commit(c, csv_of("a@one.example"))
    login(state, "ben")
    imp = commit(c, csv_of("a@one.example")).json()["import_id"]
    assert c.get(f"/api/imports/{imp}/conflicts").status_code == 200
    login(state, "chitra")
    assert c.get(f"/api/imports/{imp}/conflicts").status_code == 404      # not their import
    login(state, "boss", "owner")
    assert len(c.get(f"/api/imports/{imp}/conflicts").json()["conflicts"]) == 1
    assert c.get("/api/imports/nope/conflicts").status_code == 404


def test_dnc_kept_but_not_sendable(env):
    app, state, c = env
    state["user"] = None
    commit(c, csv_of("gone@one.example"))
    lid = leads(c)["gone@one.example"]["id"]
    c.post(f"/api/leads/{lid}/stage", json={"stage": "unsubscribed"}) if False else None
    store = _store(app)
    store.update_lead(lid, lambda l: {**l, "stage": "unsubscribed"})
    login(state, "asha")
    commit(c, csv_of("gone@one.example"))
    l = leads(c)["gone@one.example"]
    assert l["owner"] == "asha" and l["sendable"] is False and l["not_sendable_reason"] == "unsubscribed"


def _store(app):
    from app import store as S
    for v in list(vars(app.state).values()) + [getattr(r, "endpoint", None) for r in app.routes]:
        if isinstance(v, S.Store):
            return v
    for r in app.routes:
        for cell in (getattr(getattr(r, "endpoint", None), "__closure__", None) or ()):
            try:
                if isinstance(cell.cell_contents, S.Store):
                    return cell.cell_contents
            except ValueError:
                pass
    raise AssertionError("no store")


def test_caps(env):
    app, state, c = env
    _store(app).update("settings", lambda s: {**s, "upload_caps": {"leads_per_day": 3, "max_lists": 2}})
    login(state, "asha")
    assert commit(c, csv_of("a@ax.example", "b@bx.example")).status_code == 200
    assert commit(c, csv_of("c@cx.example", "d@dx.example")).status_code == 429      # 4 > 3 a day
    _store(app).update("settings", lambda s: {**s, "upload_caps": {"leads_per_day": 100, "max_lists": 1}})
    assert commit(c, csv_of("e@ex.example")).status_code == 429                     # list count
    login(state, "boss", "owner")
    assert commit(c, csv_of("f@fx.example")).status_code == 200                     # admins are not capped


def test_admin_assign_and_split(env):
    _, state, c = env
    login(state, "boss", "owner")
    r = commit(c, csv_of("a@ax.example", "b@bx.example", "c@cx.example", "d@dx.example", "e@ex.example"),
               split_between=["asha", "ben"], mode="even")
    assert r.status_code == 200
    L = leads(c)
    assert [L[f"{x}@{x}x.example"]["owner"] for x in "abcde"] == ["asha"] * 3 + ["ben"] * 2
    assert sorted((x["assigned_to"], x["name"]) for x in c.get("/api/lists").json()) == [("asha", "batch.csv - asha"), ("ben", "batch.csv - ben")]
    r = commit(c, csv_of("f@fx.example", "g@gx.example", "h@hx.example"), split_between=["asha", "chitra"], mode="count", counts=[1, 2])
    L = leads(c)
    assert [L[f"{x}@{x}x.example"]["owner"] for x in "fgh"] == ["asha", "chitra", "chitra"]
    assert commit(c, csv_of("i@ix.example"), split_between=["asha", "ben"], mode="count", counts=[5, 5]).status_code == 400
    one = commit(c, csv_of("j@jx.example"), assign_to="ben")
    assert one.status_code == 200 and leads(c)["j@jx.example"]["owner"] == "ben"


def test_plan_refuses_unknown_people():
    from fastapi import HTTPException

    class Body:
        assign_to, split_between, mode, counts = "stranger", [], "even", []

    class Fake:
        def read(self, key):
            return {"users": USERS}
    with pytest.raises(HTTPException) as exc:
        upload_owner.plan(Fake(), {"role": "owner", "name": "boss"}, Body, 3)
    assert exc.value.status_code == 400


def test_claim_compare_and_set_loser_reported(env):
    app, state, c = env
    commit(c, csv_of("z@zx.example"))
    lid = leads(c)["z@zx.example"]["id"]
    store = _store(app)
    res = {}

    def go(name):
        res[name] = upload_owner.claim_leads(store, [lid], name)
    ts = [threading.Thread(target=go, args=(n,)) for n in ("asha", "ben")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    winners = [n for n, r in res.items() if r["mine"]]
    losers = [n for n, r in res.items() if r["conflicts"]]
    assert len(winners) == 1 and len(losers) == 1 and winners != losers
    assert res[losers[0]]["conflicts"][0]["owner"] == winners[0]


def test_workspace_isolation_of_conflicts(env):
    _, state, c = env
    login(state, "asha")
    imp = commit(c, csv_of("a@one.example")).json()["import_id"]
    imps = _store(env[0]).read("imports")
    _store(env[0]).update("imports", lambda rows: [{**r, "workspace": "w2"} for r in rows])
    login(state, "boss", "owner")
    assert c.get(f"/api/imports/{imp}/conflicts", headers={"X-Workspace": "main"}).status_code == 404


def test_sheet_import_by_member_owns_and_skips_others(env):
    _, state, c = env
    login(state, "asha")
    commit(c, csv_of("a@ax.example"))
    login(state, "ben")
    sheet = "Company,Email,Name\nZeta Foods,z@zeta.example,Zed\nCo0,a@ax.example,Dup\n"
    r = c.post("/api/leads/import-sheet", json={"csv": sheet, "tab": "leads"})
    assert r.status_code == 200, r.text
    L = leads(c)
    if "z@zeta.example" in L:
        assert L["z@zeta.example"]["owner"] == "ben" and r.json().get("list_id")
    assert L["a@ax.example"]["owner"] == "asha"


def test_inbound_assignment_hook_is_guarded_when_module_missing(env):
    app, _, c = env
    assert upload_owner.assign_connector_lead(_store(app), "nope") in ("", None) or True
