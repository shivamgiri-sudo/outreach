"""Three-state email view (app/email_state.py) and its JS mirror (web/src/emailState.js): every status combination,
parity between the two, and `email_state` on GET /api/leads and /api/leads/{id}."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import email_state
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
A = "ravi@acme.com"
H0 = {"bounced": [], "delivered": []}
CASES = [   # (name, lead, history, expected)
    ("guess only", {"email": "", "email_guess": "ravi@acme.com", "email_guess_status": "unconfirmed"}, H0, "candidate"),
    ("guess probe-confirmed is still a guess", {"email": "", "email_guess": A, "email_guess_status": "confirmed"}, H0, "candidate"),
    ("guess rejected", {"email": "", "email_guess": A, "email_guess_status": "rejected"}, H0, "none"),
    ("nothing", {"email": ""}, H0, "none"),
    ("never checked", {"email": A}, H0, "domain_ready"),
    ("catch-all (verifier risky)", {"email": A, "email_check": {"email": A, "status": "risky", "provider": "zerobounce"}}, H0, "domain_ready"),
    ("unknown (unverified)", {"email": A, "email_check": {"email": A, "status": "unverified", "provider": "zerobounce"}}, H0, "domain_ready"),
    ("built-in DNS-only valid", {"email": A, "email_check": {"email": A, "status": "valid", "provider": "builtin", "reasons": []}}, H0, "domain_ready"),
    ("built-in probe confirmed", {"email": A, "email_check": {"email": A, "status": "valid", "provider": "builtin",
                                                              "reasons": ["the mail server confirmed this mailbox"]}}, H0, "confirmed"),
    ("verifier valid", {"email": A, "email_check": {"email": A, "status": "valid", "provider": "zerobounce"}}, H0, "confirmed"),
    ("probe source valid", {"email": A, "email_check": {"email": A, "status": "valid", "provider": "smtp_probe"}}, H0, "confirmed"),
    ("check for another address", {"email": A, "email_check": {"email": "old@acme.com", "status": "valid", "provider": "zerobounce"}}, H0, "domain_ready"),
    ("delivered before", {"email": A}, {"bounced": [], "delivered": [A]}, "confirmed"),
    ("bounced", {"email": A}, {"bounced": [A], "delivered": []}, "bad"),
    ("bounced beats delivered", {"email": A, "email_check": {"status": "valid", "provider": "zerobounce"}}, {"bounced": [A], "delivered": [A]}, "bad"),
    ("invalid", {"email": A, "email_check": {"email": A, "status": "invalid", "provider": "builtin"}}, H0, "bad"),
    ("email from a format guess", {"email": A, "email_source": "guess:first"}, H0, "candidate"),
    ("pattern email, verifier valid", {"email": A, "email_source": "pattern:first", "email_check": {"email": A, "status": "valid", "provider": "zerobounce"}}, H0, "confirmed"),
    ("upper-case address", {"email": "Ravi@Acme.com", "email_check": {"email": "ravi@acme.com", "status": "valid", "provider": "neverbounce"}}, H0, "confirmed"),
]


def _py(lead, history):
    return email_state.state_of(lead, {k: set(v) for k, v in history.items()})


@pytest.mark.parametrize("name,lead,history,expected", CASES, ids=[c[0] for c in CASES])
def test_state_for_every_combination(name, lead, history, expected):
    assert _py(lead, history) == expected
    if expected != "none":
        assert email_state.why(lead, expected, {k: set(v) for k, v in history.items()})       # a plain tooltip for every badge


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_js_helper_gives_the_same_answers():
    cases = [{"lead": lead, "history": history} for _, lead, history, _ in CASES]
    out = subprocess.run(["node", str(ROOT / "tests" / "email_state_parity.mjs"), str(ROOT / "web" / "src" / "emailState.js")],
                         input=json.dumps(cases), capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [_py(c["lead"], c["history"]) for c in cases]


def test_leads_api_carries_the_state(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nAcme,Ravi,ravi@acme.com\nBeta,Sita,sita@beta.in\nCora,Tej,tej@cora.io\n",
                                      "source": "apollo"})
    store = c.app.state.store
    ids = {l["company"]: l["id"] for l in c.get("/api/leads").json()}
    store.update_lead(ids["Acme"], lambda l: {**l, "email_check": {"email": "ravi@acme.com", "status": "valid", "provider": "zerobounce"}})
    store.update("delivery_events", lambda rows: [*rows, {"key": "b", "type": "bounce", "email": "sita@beta.in", "at": "2026-11-01T00:00:00+00:00"}])
    states = {l["company"]: l["email_state"] for l in c.get("/api/leads").json()}
    assert states == {"Acme": "confirmed", "Beta": "bad", "Cora": "domain_ready"}
    one = c.get(f"/api/leads/{ids['Cora']}").json()
    assert one["email_state"] == "domain_ready" and "checked" in one["email_state_why"]
