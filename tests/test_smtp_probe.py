"""Mailbox probe acceptance tests with a mocked mail server: no network, no DATA, caching, catch-all, blocks."""
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import verify_emails  # noqa: E402

from app import smtp_probe as sp  # noqa: E402
from app.sources import smtp_probe_src  # noqa: E402

NOW = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)


class FakeServer:
    def __init__(self, rcpt):
        self.rcpt_fn, self.commands = rcpt, []

    def ehlo(self, name):
        self.commands.append("EHLO")
        return 250, b"ok"

    helo = ehlo

    def mail(self, sender):
        self.commands.append("MAIL")
        return 250, b"ok"

    def rcpt(self, addr):
        self.commands.append(f"RCPT {addr}")
        return self.rcpt_fn(addr)

    def quit(self):
        self.commands.append("QUIT")


class World:
    """Domains -> (mx list or None, rcpt behaviour). Records every connection and command."""
    def __init__(self, domains):
        self.domains, self.servers, self.connects = domains, [], 0

    def resolve(self, domain):
        return self.domains[domain][0]

    def connect(self, host, helo, timeout=10):
        self.connects += 1
        domain = host.replace("mx.", "")
        server = FakeServer(self.domains[domain][1])
        self.servers.append(server)
        return server


def good(addr):          # only "real@" exists
    return (250, b"2.1.5 OK") if addr.startswith("real@") else (550, b"5.1.1 User unknown")


def accept_all(addr):
    return 250, b"OK"


def blocked(addr):
    return 550, b"5.7.1 Service unavailable; client host blocked using Spamhaus"


def greylist(addr):
    return 451, b"4.7.1 Greylisted, try again later"


def prober(world, **kw):
    return sp.Prober("mail.mydomain.com", "verify@mydomain.com", kw.pop("log", None), resolve=world.resolve, connect=world.connect,
                     sleep=lambda s: None, **kw)


WORLD = lambda: World({"plain.com": ([(10, "mx.plain.com")], good), "catch.com": ([(10, "mx.catch.com")], accept_all),
                       "blocked.com": ([(10, "mx.blocked.com")], blocked), "grey.com": ([(10, "mx.grey.com")], greylist),
                       "nomx.com": (None, good)})


def test_known_good_and_made_up_at_same_server():
    p = prober(WORLD())
    assert p.check("real@plain.com")["status"] == "confirmed"
    made_up = p.check("ghost@plain.com")
    assert made_up["status"] == "rejected" and made_up["smtp_code"] == 550


def test_catch_all_domain_means_nothing():
    r = prober(WORLD()).check("anyone@catch.com")
    assert r["status"] == "catch_all" and r["is_catch_all"] is True


def test_no_mail_server_and_bad_syntax():
    p = prober(WORLD())
    assert p.check("a@nomx.com")["status"] == "no_mail_server"
    assert p.check("not an email")["status"] == "invalid_syntax"


def test_blocks_and_greylisting_are_unknown_never_rejected():
    p = prober(WORLD())
    assert p.check("a@blocked.com")["status"] == "unknown_blocked"
    assert p.check("a@grey.com")["status"] == "unknown_temporary"


def test_data_command_is_never_issued_and_every_probe_is_logged(tmp_path):
    world, log = WORLD(), tmp_path / "probe.log"
    p = prober(world, log=str(log))
    for addr in ("real@plain.com", "x@plain.com", "a@blocked.com"):
        p.check(addr)
    assert all(not c.startswith("DATA") for s in world.servers for c in s.commands)
    assert all(s.commands[-1] == "QUIT" for s in world.servers)
    assert len(log.read_text().strip().splitlines()) == 3


def test_domain_dropped_after_two_policy_blocks_in_a_row():
    world = WORLD()
    p = prober(world)
    results = [p.check(f"u{i}@blocked.com") for i in range(4)]
    assert world.connects == 2 and "skipped" in results[2]["smtp_message"]


def test_run_limits_and_pacing():
    world = WORLD()
    sleeps = []
    p = sp.Prober("mail.mydomain.com", "verify@mydomain.com", None, resolve=world.resolve, connect=world.connect,
                  sleep=sleeps.append, clock=lambda: 0.0, max_per_run=2)
    p.check("a@plain.com"); p.check("b@plain.com")
    assert sleeps and min(sleeps) >= 1.0                                  # at least one second between probes
    try:
        p.check("c@plain.com")
        assert False, "third probe must be refused"
    except sp.ProbeRefused:
        pass
    assert sp.Prober("h.example.com", "a@b.com", max_per_run=9999).max_per_run == sp.MAX_PER_RUN


def test_csv_run_caches_for_seven_days_and_keeps_extra_columns(tmp_path):
    world = WORLD()
    p = prober(world)
    rows = [{"email": "real@plain.com", "name": "R"}, {"email": "ghost@plain.com", "name": "G"}]
    cache = {}
    out = verify_emails.run(rows, p, cache, NOW, retry_wait=0)
    assert [r["status"] for r in out] == ["confirmed", "rejected"] and out[0]["name"] == "R"
    first = world.connects
    out2 = verify_emails.run(rows, p, cache, NOW, retry_wait=0)
    assert world.connects == first and [r["status"] for r in out2] == ["confirmed", "rejected"]


def test_greylisted_address_is_retried_once():
    answers = iter([(451, b"4.7.1 later"), (250, b"ok"), (550, b"no")])   # first probe greylisted, retry accepted, catch-all test rejected
    world = World({"grey.com": ([(10, "mx.grey.com")], lambda a: next(answers))})
    waited = []
    out = verify_emails.run([{"email": "a@grey.com"}], prober(world), {}, NOW, retry_wait=300, sleep=waited.append)
    assert waited == [300] and out[0]["status"] == "confirmed"


def test_source_maps_statuses_without_overstating(monkeypatch):
    class Ctx:
        values = {"helo": "mail.mydomain.com", "mail_from": "verify@mydomain.com"}
        def cached(self, key, fn, cost=0):
            return fn()
    ctx = Ctx()
    world = WORLD()
    ctx._prober = prober(world)
    assert smtp_probe_src.verify(ctx, "real@plain.com")["status"] == "valid"
    assert smtp_probe_src.verify(ctx, "ghost@plain.com")["status"] == "invalid"
    assert smtp_probe_src.verify(ctx, "x@catch.com")["status"] == "risky"
    assert smtp_probe_src.verify(ctx, "x@blocked.com")["status"] == "unknown"


def test_source_refuses_when_port_25_is_blocked(monkeypatch):
    monkeypatch.setattr(sp, "port_25_open", lambda *a, **k: False)
    from app.campaign_sources import KeyRejected

    class Ctx:
        values = {"helo": "mail.mydomain.com", "mail_from": "verify@mydomain.com"}
        def cached(self, key, fn, cost=0):
            return fn()
    try:
        smtp_probe_src.verify(Ctx(), "a@b.com")
        assert False
    except KeyRejected as exc:
        assert "port 25" in str(exc)


def test_probe_live_stops_a_lead_at_its_first_confirmed_mailbox():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import probe_live
    world = WORLD()
    wanted = [{"lead_id": "l1", "guesses": [{"email": "ghost@plain.com"}, {"email": "real@plain.com"}, {"email": "later@plain.com"}]},
              {"lead_id": "l2", "guesses": [{"email": "x@blocked.com"}]}]
    results, counts = probe_live.probe_all(wanted, prober(world), 50)
    assert [c["status"] for c in results[0]["checks"]] == ["rejected", "confirmed"] and results[1]["checks"][0]["status"] == "unknown_blocked"
    assert counts == {"rejected": 1, "confirmed": 1, "unknown_blocked": 1}
    assert world.connects == 3


def test_replies_as_bytes_from_a_real_smtplib_are_classified():
    assert sp.classify_reply(550, b"5.1.1 <x@y.com>: Recipient address rejected: User unknown") == "rejected"
    assert sp.classify_reply(550, b"5.7.1 Service unavailable; client host blocked using Spamhaus") == "unknown_blocked"
    assert sp.classify_reply(250, b"2.1.5 OK") == "confirmed" and sp.classify_reply(451, b"4.7.1 try later") == "unknown_temporary"
