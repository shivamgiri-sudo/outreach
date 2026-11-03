"""SSRF guard: private ranges, metadata, pinned IPs (DNS rebinding), redirects, generic errors. No real network."""
import io
import socket
import ssl

import pytest
from fastapi.testclient import TestClient

from app import scraper
from app.channels import http as channel_http
from app.channels import netguard
from app.main import create_app

PUBLIC_IP, OTHER_PUBLIC_IP = "93.184.216.34", "151.101.1.69"


class FakeSocket:
    def __init__(self, response: bytes):
        self.response, self.sent = response, b""

    def sendall(self, data):
        self.sent += data

    def makefile(self, *args, **kwargs):
        return io.BytesIO(self.response)

    def close(self):
        pass


class FakeNet:
    """Stands in for socket.create_connection: records where each connection goes and answers from `pages`."""

    def __init__(self, pages: dict):
        self.pages, self.connections, self.sockets = pages, [], []

    def __call__(self, address, timeout=None, *args, **kwargs):
        self.connections.append(address)
        if address[0] not in self.pages:
            raise ConnectionRefusedError(111, "Connection refused")
        sock = FakeSocket(self.pages[address[0]])
        self.sockets.append(sock)
        return sock


def http_reply(status=200, body=b"<p>hr@acme.in</p>", headers=None) -> bytes:
    head = [f"HTTP/1.1 {status} X", f"Content-Length: {len(body)}", *[f"{k}: {v}" for k, v in (headers or {}).items()]]
    return ("\r\n".join(head) + "\r\n\r\n").encode() + body


class Resolver:
    """A DNS that can change its answer (rebinding): returns answers[i] on the i-th call, then the last one."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, host):
        self.calls.append(host)
        return self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "100.64.0.1",
                                "0.0.0.0", "224.0.0.1", "::1", "fd00::1", "fd00:ec2::254", "fe80::1", "::ffff:127.0.0.1",
                                "::ffff:169.254.169.254", "64:ff9b::a9fe:a9fe", "2002:7f00:1::1", "198.18.0.1"])
def test_private_and_metadata_addresses_are_not_public(ip):
    assert netguard.is_public_ip(ip) is False


@pytest.mark.parametrize("ip", [PUBLIC_IP, "8.8.8.8", "2606:4700:4700::1111", "64:ff9b::808:808"])
def test_public_addresses_pass(ip):
    assert netguard.is_public_ip(ip) is True


def test_admin_typed_addresses_need_https_and_a_public_host():
    with pytest.raises(ValueError, match="private"):
        netguard.check_public_url("https://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError, match="private"):
        netguard.check_public_url("https://rebind.example.com/x", Resolver([PUBLIC_IP, "10.0.0.1"]))
    with pytest.raises(ValueError, match="https"):
        netguard.check_public_url("http://api.example.com/x", Resolver([PUBLIC_IP]))
    with pytest.raises(ValueError, match="username"):
        netguard.check_public_url("https://u:p@api.example.com/x", Resolver([PUBLIC_IP]))


def test_crawler_refuses_typed_private_addresses_with_a_clear_message():
    for url in ("http://127.0.0.1:8000/", "http://[::1]/", "http://localhost/admin", "http://169.254.169.254/latest"):
        with pytest.raises(PermissionError, match="not a public website"):
            scraper.fetch_page(url)
    with pytest.raises(ValueError, match="http/https"):
        scraper.fetch_page("file:///etc/passwd")


def test_crawler_pins_the_checked_ip_so_dns_rebinding_cannot_redirect_the_connection(monkeypatch):
    net = FakeNet({PUBLIC_IP: http_reply()})
    monkeypatch.setattr(netguard.socket, "create_connection", net)
    dns = Resolver([PUBLIC_IP], ["127.0.0.1"])             # first answer public, every later answer private
    html = scraper._get("http://acme.example/contact", dns)
    assert "hr@acme.in" in html
    assert net.connections == [(PUBLIC_IP, 80)] and len(dns.calls) == 1   # resolved once, connected to that IP
    assert b"Host: acme.example\r\n" in net.sockets[0].sent                # the site still sees its own name
    with pytest.raises(netguard.FetchFailed):                              # a second fetch sees the private answer
        scraper._get("http://acme.example/contact", dns)
    assert net.connections == [(PUBLIC_IP, 80)]                            # and never connects to it


def test_a_name_resolving_to_a_private_address_gets_only_the_generic_error(monkeypatch):
    net = FakeNet({})
    monkeypatch.setattr(netguard.socket, "create_connection", net)
    with pytest.raises(OSError) as err:
        scraper._get("http://intranet.example/", Resolver(["10.0.0.5"]))
    assert str(err.value) == "Couldn't fetch that page." and net.connections == []


def test_redirect_to_a_private_address_is_refused_hop_by_hop(monkeypatch):
    net = FakeNet({PUBLIC_IP: http_reply(302, b"", {"Location": "http://169.254.169.254/latest/meta-data/"})})
    monkeypatch.setattr(netguard.socket, "create_connection", net)
    with pytest.raises(OSError, match="Couldn't fetch that page"):
        scraper._get("http://acme.example/", Resolver([PUBLIC_IP]))
    assert net.connections == [(PUBLIC_IP, 80)]


def test_redirects_to_public_hosts_are_followed_and_re_checked_up_to_a_limit(monkeypatch):
    net = FakeNet({PUBLIC_IP: http_reply(301, b"", {"Location": "https://www.acme.example/contact"}),
                   OTHER_PUBLIC_IP: http_reply(200, b"<p>jobs@acme.in</p>")})
    monkeypatch.setattr(netguard.socket, "create_connection", net)
    monkeypatch.setattr(netguard.PinnedHTTPSConnection, "connect",
                        lambda self: setattr(self, "sock", netguard.socket.create_connection((self._pinned_ip, self.port))))
    dns = lambda host: [PUBLIC_IP] if host == "acme.example" else [OTHER_PUBLIC_IP]
    assert "jobs@acme.in" in scraper._get("http://acme.example/", dns)
    assert net.connections == [(PUBLIC_IP, 80), (OTHER_PUBLIC_IP, 443)]
    loop = FakeNet({PUBLIC_IP: http_reply(302, b"", {"Location": "/again"})})
    monkeypatch.setattr(netguard.socket, "create_connection", loop)
    with pytest.raises(OSError, match="Couldn't fetch that page"):
        scraper._get("http://acme.example/", Resolver([PUBLIC_IP]))
    assert len(loop.connections) == scraper.MAX_REDIRECTS + 1


def test_connection_failures_do_not_reveal_ports_or_services(monkeypatch):
    monkeypatch.setattr(netguard.socket, "create_connection", FakeNet({}))
    with pytest.raises(OSError) as refused:
        scraper._get("http://acme.example:8443/", Resolver([PUBLIC_IP]))

    def no_dns(host):
        raise socket.gaierror(-2, "Name or service not known")
    with pytest.raises(OSError) as unknown:
        scraper._get("http://nope.example/", no_dns)
    assert str(refused.value) == str(unknown.value) == "Couldn't fetch that page."


def test_https_keeps_the_real_name_for_sni_and_the_certificate(monkeypatch):
    seen = {}
    default = netguard.PinnedHTTPSConnection("api.example.com", 443, PUBLIC_IP, 5)._context
    assert default.check_hostname and default.verify_mode == ssl.CERT_REQUIRED      # certificates are verified
    context = ssl.create_default_context()
    context.wrap_socket = lambda sock, server_hostname=None: seen.update(sni=server_hostname) or sock
    monkeypatch.setattr(netguard.socket, "create_connection", lambda addr, timeout=None: seen.setdefault("addr", addr) and FakeSocket(b""))
    conn = netguard.PinnedHTTPSConnection("api.example.com", 443, PUBLIC_IP, 5, context=context)
    conn.connect()
    assert seen == {"addr": (PUBLIC_IP, 443), "sni": "api.example.com"}


def test_channel_http_never_follows_redirects_and_refuses_private_targets(monkeypatch):
    net = FakeNet({PUBLIC_IP: http_reply(302, b"", {"Location": "http://10.0.0.1/"})})
    monkeypatch.setattr(netguard.socket, "create_connection", net)
    monkeypatch.setattr(netguard.PinnedHTTPSConnection, "connect",
                        lambda self: setattr(self, "sock", netguard.socket.create_connection((self._pinned_ip, self.port))))
    with pytest.raises(OSError, match="HTTP 302"):
        channel_http.request("https://api.example.com/send", {}, {"a": 1}, resolver=lambda h: [PUBLIC_IP])
    assert net.connections == [(PUBLIC_IP, 443)]
    with pytest.raises(OSError, match="private"):
        channel_http.request("https://internal.example.com/send", {}, {"a": 1}, resolver=lambda h: ["192.168.0.10"])
    ok = FakeNet({PUBLIC_IP: http_reply(200, b'{"id": "m1"}')})
    monkeypatch.setattr(netguard.socket, "create_connection", ok)
    reply = channel_http.request("https://api.example.com/send", {"Authorization": "Bearer k"}, {"a": 1}, resolver=lambda h: [PUBLIC_IP])
    assert reply["id"] == "m1" and reply["_status"] == 200
    assert b"POST /send HTTP/1.1" in ok.sockets[0].sent and b'{"a": 1}' in ok.sockets[0].sent


def test_firecrawl_address_goes_through_the_guard_with_an_owner_allowlist(monkeypatch):
    net = FakeNet({"127.0.0.1": http_reply(200, b'{"success": true, "data": {"html": "ok"}}')})
    monkeypatch.setattr(netguard.socket, "create_connection", net)
    monkeypatch.delenv("OUTREACH_ALLOW_PRIVATE_HOSTS", raising=False)
    with pytest.raises(netguard.BlockedAddress):
        netguard.guarded_post_json("http://localhost:3002/v1/scrape", {}, {"url": "https://acme.in"})
    with pytest.raises(netguard.BlockedAddress):
        netguard.guarded_post_json("http://169.254.169.254/v1/scrape", {}, {})
    monkeypatch.setenv("OUTREACH_ALLOW_PRIVATE_HOSTS", "localhost:3002")
    monkeypatch.setattr(netguard, "_resolve", lambda host: ["127.0.0.1"])
    assert netguard.guarded_post_json("http://localhost:3002/v1/scrape", {}, {"url": "https://acme.in"})["success"] is True
    with pytest.raises(netguard.BlockedAddress):                      # only the exact host:port that was allowed
        netguard.guarded_post_json("http://localhost:6379/", {}, {})


def test_scrape_api_answers_with_the_generic_error_for_hidden_private_hosts(tmp_path, monkeypatch):
    monkeypatch.setattr(netguard, "_resolve", lambda host: ["10.20.30.40"])
    monkeypatch.setattr(netguard.socket, "create_connection", FakeNet({}))
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    r = c.post("/api/scrape/extract", json={"url": "http://intranet.acme.example/"})
    assert r.status_code == 400 and r.json()["detail"] == "Couldn't fetch that page."
    typed = c.post("/api/scrape/extract", json={"url": "http://10.20.30.40/"})
    assert typed.status_code == 400 and "not a public website" in typed.json()["detail"]
