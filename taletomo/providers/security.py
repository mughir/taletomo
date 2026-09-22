import ipaddress
import socket
from urllib.parse import urlparse


class SSRFValidationError(ValueError):
    """Raised when an outbound provider URL violates SSRF safety constraints."""
    pass


BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # AWS/GCP metadata 169.254.169.254
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("224.0.0.0/4"),     # Multicast
    ipaddress.ip_network("240.0.0.0/4"),     # Reserved
    ipaddress.ip_network("::1/128"),         # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),        # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),       # IPv6 link-local
]


def validate_provider_endpoint(url: str, allowlist: list[str] = None) -> bool:
    """Validates that a custom endpoint URL is safe from SSRF attacks.

    Rejects non-HTTP(S), loopback, private IP, link-local, and cloud metadata services.
    """
    if not url:
        raise SSRFValidationError("Endpoint URL cannot be empty")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise SSRFValidationError(f"Invalid URL scheme '{parsed.scheme}': only HTTP/HTTPS are permitted")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFValidationError("Missing hostname in endpoint URL")

    # Check explicit allowlist
    if allowlist and hostname in allowlist:
        return True

    # Reject known metadata or local hostnames
    lowered = hostname.lower()
    if lowered in ("localhost", "metadata", "instance-data", "internal"):
        raise SSRFValidationError(f"Host '{hostname}' is a reserved/internal address")

    # Resolve IP addresses for the hostname
    try:
        addr_infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise SSRFValidationError(f"Failed to resolve hostname '{hostname}': {e}")

    for family, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        ip_obj = ipaddress.ip_address(ip_str)

        for blocked in BLOCKED_NETWORKS:
            if ip_obj in blocked:
                raise SSRFValidationError(
                    f"Endpoint resolved to forbidden internal/private network IP: {ip_str} (network: {blocked})"
                )

    return True
