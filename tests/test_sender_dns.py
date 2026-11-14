"""Sender domain records check (app/sender_dns.py, GET /api/deliverability/dns): injected resolver, no network."""
import json

import pytest
from fastapi.testclient import TestClient

from app import sender_dns
from app.main import create_app
from tests.flow_helpers import connect_email

D = "mail.example.in"


class Dns:
    """A fake resolver: records[(name, type)] -> list; names in `down` raise OSError."""

    def __init__(self, records=None, down=()):
        self.records, self.down, self.calls = records or {}, set(down), []

    def __call__(self, name, rdtype):
        self.calls.append((name, rdtype))
        if name in self.down:
            raise OSError("timeout")
        return list(self.records.get((name, rdtype), []))


GOOD = {(D, "MX"): ["10 mx.example.in"], (D, "TXT"): ["v=spf1 include:amazonses.com ~all"],
        (f"resend._domainkey.{D}", "TXT"): ["p=MIGfMA0"], (f"_dmarc.{D}", "TXT"): ["v=DMARC1; p=quarantine; rua=mailto:d@example.in"],
        (f"send.{D}", "MX"): ["10 feedback-smtp.ap-northeast-1.amazonses.com"]}


@pytest.fixture(autouse=True)
def clear_cache():
    sender_dns._CACHE.clear()


def by_name(report):
    return {c["name"].split(" (")[0].split(" /")[0]: c for c in report["checks"]}


def rep(records, provider="resend", **kw):
    r = Dns(records, **kw)
    return {c["name"]: c for c in [sender_dns.check_mx(r, D), sender_dns.check_spf(r, D, provider), sender_dns.check_dkim(r, D, provider),
                                   sender_dns.check_dmarc(r, D), sender_dns.check_mail_from(r, D, provider)]}


def status(checks, prefix):
    return next(c for n, c in checks.items() if n.startswith(prefix))


def test_everything_ok():
    checks = rep(GOOD)
    assert {c["status"] for c in checks.values()} == {"ok"}
    assert status(checks, "DKIM")["found"] == ["resend"]


def test_spf_cases():
    s = lambda rec: status(rep({**GOOD, (D, "TXT"): rec}), "SPF")
    assert s([])["status"] == "fail" and "No SPF" in s([])["advice"]
    assert s(["v=spf1 include:a.com ~all", "v=spf1 include:b.com ~all"])["status"] == "fail" and "more than one" in s(["v=spf1 a ~all", "v=spf1 mx ~all"])["advice"]
    assert s(["v=spf1 +all"])["status"] == "fail" and s(["v=spf1 all"])["status"] == "fail"
    many = "v=spf1 " + " ".join(f"include:s{i}.com" for i in range(11)) + " ~all"
    assert s([many])["status"] == "warn" and "more than 10" in s([many])["advice"]
    assert s(["v=spf1 include:google.com ~all"])["status"] == "warn" and "check with your provider" in s(["v=spf1 include:google.com ~all"])["advice"].lower()
    assert s(["v=spf1 include:amazonses.com -all", "google-site-verification=abc"])["status"] == "ok"      # other TXT records are ignored
    other_txt = {**GOOD, (D, "TXT"): ["v=spf1 ~all"], (f"send.{D}", "TXT"): ["v=spf1 include:amazonses.com ~all"]}
    assert status(rep(other_txt), "SPF")["status"] == "ok"                                                 # Resend's SPF can live on send.<domain>


def test_dkim_cases():
    d = lambda recs, prov="resend": status(rep({k: v for k, v in {**GOOD, **recs}.items() if v}, prov), "DKIM")
    assert d({(f"resend._domainkey.{D}", "TXT"): []})["status"] == "fail"
    assert d({(f"resend._domainkey.{D}", "TXT"): [], (f"resend._domainkey.{D}", "CNAME"): ["x.dkim.amazonses.com"]})["status"] == "ok"   # CNAME form
    g = d({(f"resend._domainkey.{D}", "TXT"): [], (f"google._domainkey.{D}", "TXT"): ["v=DKIM1"], (f"selector1._domainkey.{D}", "CNAME"): ["s1.x"]}, "sendgrid")
    assert g["status"] == "ok" and g["found"] == ["google", "selector1"]
    n = d({(f"resend._domainkey.{D}", "TXT"): []}, "sendgrid")
    assert n["status"] == "warn" and n["found"] == [] and "check with your provider" in n["advice"].lower()
    resend_other = d({(f"resend._domainkey.{D}", "TXT"): [], (f"default._domainkey.{D}", "TXT"): ["v=DKIM1"]})
    assert resend_other["status"] == "fail" and resend_other["found"] == ["default"]


def test_dmarc_cases():
    m = lambda rec: status(rep({**GOOD, (f"_dmarc.{D}", "TXT"): rec}), "DMARC")
    assert m([])["status"] == "fail" and "No DMARC" in m([])["advice"]
    none = m(["v=DMARC1; p=none; rua=mailto:a@x.in"])
    assert none["status"] == "warn" and "p=none" in none["advice"]
    assert m(["v=DMARC1; p=none"])["status"] == "warn" and "no rua" in m(["v=DMARC1; p=none"])["advice"]
    rej = m(["v=DMARC1; p=reject"])
    assert rej["status"] == "ok" and "no rua" in rej["advice"]
    assert m(["v=DMARC1; p=quarantine; rua=mailto:a@x.in"])["status"] == "ok"
    assert m(["v=DMARC1; pct=100"])["status"] == "warn"
    assert m(["v=spf1 -all"])["status"] == "fail"                                                            # not a DMARC record


def test_mx_and_null_mx_and_mail_from():
    assert status(rep({**GOOD, (D, "MX"): []}), "Mail servers")["status"] == "warn"
    nul = status(rep({**GOOD, (D, "MX"): ["0 ."]}), "Mail servers")
    assert nul["status"] == "fail" and "null MX" in nul["advice"]
    assert status(rep({**GOOD, (f"send.{D}", "MX"): []}), "Return-path")["status"] == "warn"
    assert status(rep(GOOD, "postmark"), "Return-path")["status"] == "unknown"


def test_dns_failures_are_unknown_not_errors():
    checks = rep({}, down=[D, f"resend._domainkey.{D}", f"google._domainkey.{D}", f"selector1._domainkey.{D}", f"selector2._domainkey.{D}",
                           f"default._domainkey.{D}", f"_dmarc.{D}", f"send.{D}"])
    assert {c["status"] for c in checks.values()} == {"unknown"}


def test_route_not_configured_says_so(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    body = c.get("/api/deliverability/dns").json()
    assert body == {"configured": False, "message": "No sending email is connected yet.", "domain": "", "provider": "", "checks": []}


def test_report_uses_the_from_domain_never_returns_the_key_and_caches(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    connect_email(c)
    store = c.app.state.store
    r = Dns(GOOD)
    out = sender_dns.report(store, resolver=r)
    assert out["configured"] and out["domain"] == D and out["provider"] == "resend" and len(out["checks"]) == 5
    assert "re_secret" not in json.dumps(out) and "hello@" not in json.dumps(out)
    n = len(r.calls)
    assert sender_dns.report(store, resolver=r) is out and len(r.calls) == n                               # cached
    sender_dns._CACHE.clear()
    sender_dns.report(store, resolver=r, now=lambda: 0.0)
    n = len(r.calls)
    sender_dns.report(store, resolver=r, now=lambda: 599.0)
    assert len(r.calls) == n
    sender_dns.report(store, resolver=r, now=lambda: 601.0)
    assert len(r.calls) > n                                                                                 # expired after 10 minutes


def test_route_roles(tmp_path):
    from tests.ownership_helpers import rig
    r = rig(tmp_path)
    connect_email_owner = r.c.put("/api/integrations/sending_domain", headers=r.h["owner"], json={"mode": "api", "values": {
        "domain": D, "from_name": "P", "from_email": f"hello@{D}", "api_key": "re_secret"}})
    assert connect_email_owner.status_code == 200
    for who in ("priya", "view", "ravi"):
        assert r.c.get("/api/deliverability/dns", headers=r.h[who]).status_code == 403
    sender_dns._CACHE[(D, "resend", id(None))] = (10 ** 12, {"configured": True, "message": "", "domain": D, "provider": "resend", "checks": []})
    for who in ("owner", "adm"):
        body = r.c.get("/api/deliverability/dns", headers=r.h[who])
        assert body.status_code == 200 and body.json()["domain"] == D and "re_secret" not in body.text


def test_real_resolver_function_shape(monkeypatch):
    """dns_resolver turns dnspython answers into plain strings and DNS failures into OSError (no network: dnspython is faked)."""
    import dns.exception
    import dns.resolver

    class Txt:
        strings = [b"v=spf1 ", b"-all"]

    class Mx:
        preference, exchange = 10, "mx.example.in."

    class Null:
        preference, exchange = 0, "."

    class R:
        lifetime = 0
        answers = {"TXT": [Txt()], "MX": [Mx(), Null()]}

        def resolve(self, name, kind):
            if name == "gone.in":
                raise dns.resolver.NXDOMAIN()
            if name == "slow.in":
                raise dns.exception.Timeout()
            return self.answers[kind]

    monkeypatch.setattr(dns.resolver, "Resolver", R)
    assert sender_dns.dns_resolver("a.in", "TXT") == ["v=spf1 -all"]
    assert sender_dns.dns_resolver("a.in", "MX") == ["10 mx.example.in", "0 ."]
    assert sender_dns.dns_resolver("gone.in", "TXT") == []
    with pytest.raises(OSError):
        sender_dns.dns_resolver("slow.in", "TXT")
