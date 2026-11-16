"""Find & enrich's free steps: email format guess, Gravatar, team / press pages. Fake transports only (no network, no
real DNS / SMTP / Gravatar); every name and domain is made up."""
from fastapi.testclient import TestClient

from app import pattern_guess as PG
from app import prospect_waterfall as W
from app.main import create_app
from tests.campaign_fakes import FakeIO
from tests.test_find_leads import add_leads, go

GRAVATAR = {"entry": [{"profileUrl": "https://gravatar.com/kdesai", "thumbnailUrl": "https://0.gravatar.com/avatar/kd",
                       "name": {"formatted": "Kiran Desai"},
                       "accounts": [{"shortname": "linkedin", "url": "https://www.linkedin.com/in/kiran-example"}]}]}
KNOWN = ("company,website,contact_name,email\n"
         "Gamma Works,https://gamma-group.in,Asha Rao,asha.rao@gamma-works.in\n"
         "Gamma Works Two,https://gamma-group.in,Vikram Shah,vikram.shah@gamma-works.in\n"
         "Gamma Works Three,https://gamma-group.in,Neha Iyer,niyer@gamma-works.in\n"             # bounced: never learned from
         "Gamma Works Four,https://gamma-group.in,Om Puri,info@gamma-works.in\n"                 # shared inbox
         "Gamma Works Five,https://gamma-group.in,Lata Bose,latab@gmail.com\n")                  # free mail
TARGET = "company,website,contact_name\nGamma Target,https://gamma-works.in,Kiran Desai\n"


def make(tmp_path, smtp=None, gravatar=None, pages=None):
    io = FakeIO(pages=pages or {})
    extra = {k: v for k, v in (("smtp_probe", smtp), ("gravatar", gravatar)) if v is not None}
    return TestClient(create_app(data_file=tmp_path / "store.json", campaign_transports={**io.transports(), **extra})), io


def seed_known(c):
    add_leads(c, KNOWN)
    s = c.app.state.store
    s.update("leads", lambda rows: [{**l, "email_check": {"status": "valid", "email": l["email"]}} if l.get("email") else l for l in rows])
    s.update("delivery_events", lambda rows: [*rows, {"type": "bounce", "email": "niyer@gamma-works.in", "at": "2026-11-01T00:00:00+00:00"}])


def lead(c, company):
    return next(l for l in c.app.state.store.read("leads") if l["company"] == company)


class Smtp:
    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def __call__(self, email):
        self.asked.append(email)
        return self.answers.get(email, "unknown_blocked")


# ---------- learning ----------
def test_book_learns_only_from_verified_person_addresses(tmp_path):
    c, _ = make(tmp_path)
    seed_known(c)
    learned = PG.PatternBook(c.app.state.store).learned("gamma-works.in")
    assert learned == [{"pattern": "first.last", "count": 2, "share": 1.0, "confidence": 0.67}]       # no flast (bounced), no info@, no gmail


def test_learnable_rejects_role_free_mail_bounced_and_not_for_sales():
    history = {"bounced": {"x.y@acme.in"}}
    assert PG.learnable("Asha.Rao@acme.in", history) == "asha.rao@acme.in"
    for bad in ("x.y@acme.in", "hr@acme.in", "press@acme.in", "privacy@acme.in", "asha@gmail.com", "asha@rediffmail.com"):
        assert PG.learnable(bad, history) is None, bad


def test_own_guesses_are_never_learned_from(tmp_path):
    c, _ = make(tmp_path)
    add_leads(c, "company,website,contact_name,email\nG1,https://gamma-group.in,Asha Rao,arao@gamma-works.in\n")
    c.app.state.store.update("leads", lambda rows: [{**l, "email_check": {"status": "valid"}, "email_source": "guess:flast"} for l in rows])
    assert PG.PatternBook(c.app.state.store).learned("gamma-works.in") == []


# ---------- checking (no sending) ----------
CANDS = [{"email": "kiran.desai@acme.in", "pattern": "first.last", "basis": "a common format", "confidence": 0},
         {"email": "kdesai@acme.in", "pattern": "flast", "basis": "a common format", "confidence": 0}]
EMPTY = {"bounced": set(), "delivered": set(), "domains": {}}


def test_probe_rejection_moves_to_the_next_candidate_and_confirmed_wins():
    smtp = Smtp({"kiran.desai@acme.in": "rejected", "kdesai@acme.in": "confirmed"})
    out = PG.Checker(EMPTY, lambda d: "ok", smtp).pick(CANDS)
    assert out["candidate"]["email"] == "kdesai@acme.in" and out["status"] == "confirmed" and smtp.asked == ["kiran.desai@acme.in", "kdesai@acme.in"]


def test_catch_all_and_unclear_answers_stay_unresolved():
    out = PG.Checker(EMPTY, lambda d: "ok", Smtp({"kiran.desai@acme.in": "catch_all"})).pick(CANDS)
    assert out["status"] == "catch_all" and out["candidate"]["email"] == "kiran.desai@acme.in"
    assert PG.Checker(EMPTY, lambda d: "ok", Smtp({})).pick(CANDS)["status"] == "unconfirmed"
    assert PG.Checker(EMPTY, lambda d: "ok", None).pick(CANDS)["status"] == "unconfirmed"


def test_all_rejected_bounced_or_no_mail_domain_is_rejected():
    smtp = Smtp({"kiran.desai@acme.in": "rejected", "kdesai@acme.in": "rejected"})
    assert PG.Checker(EMPTY, lambda d: "ok", smtp).pick(CANDS) == {"status": "rejected", "reason": "every likely address was refused"}
    assert PG.Checker(EMPTY, lambda d: "no_mail", smtp).pick(CANDS)["status"] == "rejected"
    bounced = {**EMPTY, "bounced": {"kiran.desai@acme.in"}}
    assert PG.Checker(bounced, lambda d: "ok", None).pick(CANDS)["candidate"]["email"] == "kdesai@acme.in"


# ---------- the whole run ----------
def test_guess_is_stored_as_guess_never_as_email_even_when_confirmed(tmp_path):
    smtp = Smtp({"kiran.desai@gamma-works.in": "confirmed"})
    c, _ = make(tmp_path, smtp=smtp)
    seed_known(c)
    add_leads(c, TARGET)
    go(c, mode="enrich_existing", titles=["HR Head"])
    t = lead(c, "Gamma Target")
    assert not t.get("email") and t["email_guess"] == "kiran.desai@gamma-works.in"
    assert t["email_guess_status"] == "confirmed" and t["email_guess_source"] == "pattern:first.last"
    assert "the company's own format (2 known addresses)" in t["email_guess_reason"]
    assert t["provenance"]["email_guess"] == "pattern:first.last" and smtp.asked == ["kiran.desai@gamma-works.in"]
    assert t["dedupe_key"] != "email:kiran.desai@gamma-works.in"


def test_catch_all_guess_is_kept_unresolved(tmp_path):
    c, _ = make(tmp_path, smtp=Smtp({"kiran.desai@gamma-works.in": "catch_all"}))
    add_leads(c, TARGET)
    go(c, mode="enrich_existing", titles=["HR Head"])
    t = lead(c, "Gamma Target")
    assert t["email_guess_status"] == "catch_all" and not t.get("email") and t["email_guess_status"] != "valid"


def test_gravatar_fills_photo_and_linkedin_but_not_an_existing_name(tmp_path):
    asked = []

    def fetch(url):
        asked.append(url)
        return GRAVATAR
    c, _ = make(tmp_path, gravatar=fetch)
    add_leads(c, TARGET)
    go(c, mode="enrich_existing", titles=["HR Head"])
    t = lead(c, "Gamma Target")
    assert t["contact_name"] == "Kiran Desai" and t["photo_url"] == "https://0.gravatar.com/avatar/kd"
    assert t["linkedin_url"] == "https://www.linkedin.com/in/kiran-example" and t["gravatar_url"] == "https://gravatar.com/kdesai"
    assert t["provenance"]["photo_url"] == "gravatar" and "provenance" in t and "contact_name" not in t["provenance"]
    assert "a public Gravatar profile uses this address" in t["email_guess_reason"] and len(asked) == 1
    assert not t.get("email")


def test_steps_can_be_switched_off_and_show_in_spend_settings(tmp_path):
    c, _ = make(tmp_path, gravatar=lambda url: GRAVATAR)
    ids = {p["id"]: p for p in W.provider_config(c.app.state.store)}
    assert {"team_pages", "pattern_guess", "gravatar"} <= set(ids) and ids["gravatar"]["enabled"] and ids["gravatar"]["cost"] == 0
    c.app.state.store.update("settings", lambda s: {**s, "prospect_db": {"providers": {"pattern_guess": {"enabled": False},
                                                                                       "gravatar": {"enabled": False}}}})
    add_leads(c, TARGET)
    go(c, mode="enrich_existing", titles=["HR Head"])
    t = lead(c, "Gamma Target")
    assert not t.get("email_guess") and not t.get("email_guess_status") and not t.get("photo_url")


def test_team_page_pairs_name_with_email_and_skips_privacy_addresses(tmp_path):
    home = '<a href="/about/leadership">Leadership</a> Write to privacy@delta-co.in or security@delta-co.in'
    team = ('<ul><li class="card"><h3>Meera Nair</h3><p>Head of People</p><a href="mailto:meera.nair@delta-co.in">Email</a></li>'
            '<li><strong>Legal desk</strong><a href="mailto:legal@delta-co.in">legal</a></li></ul>')
    c, io = make(tmp_path, pages={"https://delta-co.in/": home, "https://delta-co.in/about/leadership": team})
    add_leads(c, "company,website\nDelta Co,https://delta-co.in\n")
    go(c, mode="enrich_existing", titles=["HR Head"])
    t = lead(c, "Delta Co")
    assert t["email"] == "meera.nair@delta-co.in" and t["contact_name"] == "Meera Nair"
    assert t["email_source"] == "website:https://delta-co.in/about/leadership"
    assert "https://delta-co.in/about/leadership" in io.urls() and len(io.urls("delta-co.in")) <= 10
