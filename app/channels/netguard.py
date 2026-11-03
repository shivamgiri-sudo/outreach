"""The one SSRF guard for every address the server fetches: the website crawler, Firecrawl, custom connectors and
provider APIs.

Rules:
  * http(s) only (admin-typed API endpoints: https only), no username or password inside the address;
  * the host is resolved ONCE and every address it resolves to must be on the public internet: loopback,
    private (10.x, 172.16-31.x, 192.168.x, fc00::/7 incl. fd00::/8), link-local (169.254.x incl. the cloud metadata
    address 169.254.169.254, fe80::/10), carrier-grade NAT (100.64/10), multicast, reserved and documentation ranges
    are refused, as are IPv4 addresses hidden inside IPv6 (mapped, NAT64, 6to4, Teredo);
  * the connection goes to that resolved IP ("pinned"), with the real host name kept for the Host header and for
    TLS (SNI and certificate check), so DNS cannot change its answer between the check and the connection;
  * redirects are not followed by API calls; the crawler follows at most a few, re-checking every hop.
Errors a person sees say only "couldn't fetch that page" (or a short reason for addresses they typed), never which
port or service answered, so the guard cannot be used to scan the internal network.

OUTREACH_ALLOW_PRIVATE_HOSTS (environment only, e.g. "localhost:3002") lets the server owner reach a self-hosted
service such as Firecrawl on a private address. It cannot be set from the app.
"""
import http.client
import ipaddress
import os
import socket
import ssl
from collections import namedtuple
from urllib.parse import urljoin, urlparse

BLOCKED_HOSTS = {"localhost", "metadata", "metadata.google.internal", "instance-data"}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain")
PRIVATE_MESSAGE = "That address points to a private or internal network, which is not allowed."
GENERIC_FETCH_ERROR = "Couldn't fetch that page."
REDIRECTS = (301, 302, 303, 307, 308)
BLOCKED_NETWORKS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24",
    "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
    "224.0.0.0/4", "240.0.0.0/4", "::/128", "::1/128", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8",
    "2001:db8::/32", "100::/64")]
NAT64 = ipaddress.ip_network("64:ff9b::/96")
Response = namedtuple("Response", "status headers body url")


class BlockedAddress(ValueError):
    """The address is private, internal or otherwise not allowed."""


class FetchFailed(OSError):
    """Anything went wrong while fetching; the message is deliberately generic."""


def _resolve(host: str) -> list:
    return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


def is_public_ip(ip) -> bool:
    ip = ipaddress.ip_address(str(ip).split("%")[0]) if not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else ip
    if ip.version == 6:
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if ip in NAT64:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return is_public_ip(embedded)
    if any(ip in net for net in BLOCKED_NETWORKS if net.version == ip.version):
        return False
    return ip.is_global and not ip.is_multicast


def is_public_host(host: str) -> bool:
    """False for localhost, *.local / *.internal / *.localhost names and any private literal IP (10.x, 172.16-31.x,
    192.168.x, loopback, link-local ...). No DNS lookup: for judging a configured web address, not for fetching."""
    name = (host or "").strip().strip("[]").rstrip(".").lower()
    if not name or name in BLOCKED_HOSTS or name.endswith(BLOCKED_SUFFIXES) or "." not in name and ":" not in name:
        return False
    try:
        return is_public_ip(name)
    except ValueError:
        return True


def _public(ip) -> bool:                     # kept for older callers
    return is_public_ip(ip)


def allowed_private_hosts() -> set:
    raw = os.environ.get("OUTREACH_ALLOW_PRIVATE_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _private_allowed(host: str, port: int) -> bool:
    allowed = allowed_private_hosts()
    return host in allowed or f"{host}:{port}" in allowed


def _split(url: str, allow_http: bool) -> tuple:
    parsed = urlparse((url or "").strip())
    schemes = ("http", "https") if allow_http else ("https",)
    if parsed.scheme not in schemes:
        raise ValueError("Only http/https URLs are allowed" if allow_http else "The address must start with https://")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise ValueError("The address has no server name.")
    if parsed.username or parsed.password:
        raise ValueError("Do not put a username or password inside the address; add it as a header instead.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise ValueError("The address has an invalid port.") from None
    return parsed, host, port


def resolve_public(host: str, port: int, resolver=None) -> str:
    """The single IP to connect to. Raises BlockedAddress (private / internal) or FetchFailed (does not resolve)."""
    if _private_allowed(host, port):
        try:
            return str(ipaddress.ip_address(host))
        except ValueError:
            pass
        try:
            return (resolver or _resolve)(host)[0]
        except (OSError, IndexError, UnicodeError) as exc:
            raise FetchFailed(GENERIC_FETCH_ERROR) from exc
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES):
        raise BlockedAddress(PRIVATE_MESSAGE)
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(str(a).split("%")[0]) for a in (resolver or _resolve)(host)]
        except (OSError, ValueError, UnicodeError) as exc:
            raise FetchFailed(GENERIC_FETCH_ERROR) from exc
    if not addresses or not all(is_public_ip(ip) for ip in addresses):
        raise BlockedAddress(PRIVATE_MESSAGE)
    return str(addresses[0])


def check_public_url(url: str, resolver=None) -> str:
    """Admin-typed API addresses: https, public. Returns the cleaned URL or raises ValueError a person can read."""
    text = (url or "").strip()
    _, host, port = _split(text, allow_http=False)
    try:
        resolve_public(host, port, resolver)
    except FetchFailed:
        raise ValueError(f"Could not find the server {host}. Check the spelling.") from None
    return text


def check_https(url: str) -> str:
    """Links people open in their browser (booking pages): https only, no SSRF check needed."""
    text = (url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("The link must start with https://")
    return text


# ---------- connections pinned to the checked IP ----------
class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self._pinned_ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, timeout, context=None):
        super().__init__(host, port, timeout=timeout, context=context or ssl.create_default_context())
        self._pinned_ip = ip

    def connect(self):
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)    # SNI + certificate for the real name


def _one_request(url: str, method: str, headers: dict, body, timeout: float, max_bytes: int, resolver, allow_http: bool):
    parsed, host, port = _split(url, allow_http)
    ip = resolve_public(host, port, resolver)
    cls = PinnedHTTPSConnection if parsed.scheme == "https" else PinnedHTTPConnection
    conn = cls(host, port, ip, timeout)
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read(max_bytes)
        return Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, data, url)
    except (OSError, http.client.HTTPException, ssl.SSLError, ValueError) as exc:
        raise FetchFailed(GENERIC_FETCH_ERROR) from exc
    finally:
        conn.close()


def guarded_request(url: str, method: str = "GET", headers: dict = None, body: bytes = None, timeout: float = 20,
                    max_bytes: int = 1_000_000, resolver=None, allow_http: bool = False, max_redirects: int = 0,
                    check_hop=None) -> Response:
    """One HTTP request through the guard. Redirects (up to max_redirects) are re-checked hop by hop.

    Raises ValueError for a bad address, BlockedAddress for a private one and FetchFailed for anything else."""
    current, verb, payload = url, method.upper(), body
    for _ in range(max_redirects + 1):
        resp = _one_request(current, verb, headers, payload, timeout, max_bytes, resolver, allow_http)
        if resp.status not in REDIRECTS or not max_redirects or not resp.headers.get("location"):
            return resp
        current = urljoin(current, resp.headers["location"])
        if check_hop:
            check_hop(current)               # a caller's own rule for every redirect target, before it is requested
        if resp.status == 303 or (resp.status in (301, 302) and verb == "POST"):
            verb, payload = "GET", None
    raise FetchFailed(GENERIC_FETCH_ERROR)


def guarded_post_json(url: str, headers: dict, body: dict) -> dict:
    """Same shape as http_util.post_json, through the guard (used for Firecrawl, whose address the owner types)."""
    import json
    from ..http_util import HttpStatusError
    resp = guarded_request(url, "POST", {"Content-Type": "application/json", **(headers or {})},
                           json.dumps(body).encode("utf-8"), allow_http=True)
    if resp.status in (401, 403):
        raise PermissionError(f"HTTP {resp.status}")
    if resp.status >= 300:
        raise HttpStatusError(resp.status, resp.body[:300].decode("utf-8", "replace"))
    try:
        return json.loads(resp.body.decode("utf-8") or "{}")
    except ValueError as exc:
        raise FetchFailed("The service did not answer with JSON.") from exc
