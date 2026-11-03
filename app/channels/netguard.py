"""SSRF guard for any address an admin types in (custom connectors, self-hosted API bases).

Only https, no credentials in the address, and every IP the host resolves to must be on the public internet:
loopback, private (10.x, 172.16-31.x, 192.168.x), link-local (169.254.x, including the cloud metadata
address 169.254.169.254), carrier-grade NAT and other reserved ranges are refused. Redirects are never
followed by `channels.http.request`, so a public URL cannot bounce the request inside.
Known gap: DNS could change between this check and the connection (rebinding); see docs/PRD.md section 9.
"""
import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_HOSTS = {"localhost", "metadata", "metadata.google.internal", "instance-data"}
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain")
PRIVATE_MESSAGE = "That address points to a private or internal network, which is not allowed."


def _resolve(host: str) -> list:
    return [info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)]


def _public(ip) -> bool:
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_public_url(url: str, resolver=None) -> str:
    """Return the cleaned URL, or raise ValueError with a reason a person can read."""
    text = (url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme != "https":
        raise ValueError("The address must start with https://")
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise ValueError("The address has no server name.")
    if parsed.username or parsed.password:
        raise ValueError("Do not put a username or password inside the address; add it as a header instead.")
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES):
        raise ValueError(PRIVATE_MESSAGE)
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(str(a).split("%")[0]) for a in (resolver or _resolve)(host)]
        except (OSError, ValueError):
            raise ValueError(f"Could not find the server {host}. Check the spelling.") from None
    if not addresses or not all(_public(ip) for ip in addresses):
        raise ValueError(PRIVATE_MESSAGE)
    return text


def check_https(url: str) -> str:
    """Links people open in their browser (booking pages): https only, no SSRF check needed."""
    text = (url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("The link must start with https://")
    return text
