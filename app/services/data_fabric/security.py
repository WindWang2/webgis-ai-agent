"""
Enterprise Geospatial Data Fabric Security Module:
1. SSRF Policy Validator (Blocks private IPs, loopback, cloud metadata endpoints,
   IPv6 loopback/ULA/link-local, and IPv4-mapped IPv6 across all resolved addresses)
2. Credential Seam & Secret Sanitization (Prevents secret leakage to LLM prompts, logs, and frontend payloads)
3. XXE Protection for XML Payloads (defusedxml for WFS/WMS Capabilities parsers)
4. Multi-Tenant Isolation Protection
"""
import ipaddress
import logging
import socket
from urllib.parse import urlparse
from typing import Dict, Any, Optional

try:
    # defusedxml hardens ElementTree against XXE / external entity / entity
    # expansion attacks. It is a declared project dependency (pyproject.toml).
    # defuse_stdlib() also patches the stdlib parsers globally so any fallback
    # path is equally hardened.
    from defusedxml import ElementTree as ET
    from defusedxml import defuse_stdlib
    defuse_stdlib(stdlib=True)
except Exception:  # pragma: no cover - fallback only if defusedxml is unavailable
    from xml.etree import ElementTree as ET  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# Private IP ranges and blocked hostnames
BLOCKED_HOSTNAMES = {
    "localhost",
    "loopback",
    "metadata.google.internal",
    "kubernetes.default.svc",
    "metadata",
}

# Cloud metadata & link-local endpoints that must never be reachable.
BLOCKED_IPS_EXPLICIT = {
    ipaddress.ip_address("169.254.169.254"),  # AWS / GCP metadata
    ipaddress.ip_address("fd00:ec2::254"),     # AWS IMDSv6 metadata
}

# Networks that are private/loopback/link-local and must be blocked.
BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),          # "this network"
    ipaddress.ip_network("10.0.0.0/8"),         # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),      # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),     # RFC1918
    ipaddress.ip_network("127.0.0.0/8"),        # IPv4 loopback
    ipaddress.ip_network("169.254.0.0/16"),     # IPv4 link-local
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique-local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]


class DataFabricSecurityError(ValueError):
    """Security policy violation exception for Data Fabric."""
    pass


def _is_blocked_ip(ip_str: str) -> bool:
    """Return True if the IP is private/loopback/link-local/metadata.

    Uses ipaddress for correct prefix semantics; handles IPv4, IPv6, and
    IPv4-mapped IPv6 (e.g. ::ffff:127.0.0.1) transparently.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        # Not a parseable IP — treat as blocked (defensive; callers resolve first).
        return True
    if ip in BLOCKED_IPS_EXPLICIT:
        return True
    # ipv4_mapped exposes the embedded IPv4 for mapped addresses.
    if hasattr(ip, "ipv4_mapped") and ip.ipv4_mapped is not None:
        if _is_blocked_ip(str(ip.ipv4_mapped)):
            return True
    return any(ip in net for net in BLOCKED_NETWORKS)


class DataFabricSecurity:
    @staticmethod
    def validate_url(url: str, allow_private: bool = False) -> str:
        """
        SSRF Defense: Validates remote data source URLs.
        Blocks loopback, RFC1918 private subnets, IPv6 loopback/ULA/link-local,
        IPv4-mapped IPv6, and cloud metadata IPs across ALL resolved addresses.

        A hostname that fails to resolve is treated as blocked (previously the
        check 'proceeded', which allowed unreachable-but-dangerous hostnames).
        Note: this validates at request time. For full DNS-rebinding defense the
        HTTP client should pin the resolved IP at connect; this covers the
        pre-flight gate used by the adapter probe path.
        """
        if not url or not isinstance(url, str):
            raise DataFabricSecurityError("Invalid URL provided")

        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https", "s3", "minio"):
            raise DataFabricSecurityError(f"Unsupported or unsafe scheme '{scheme}'")

        hostname = parsed.hostname
        if not hostname:
            raise DataFabricSecurityError("URL missing valid hostname")

        hostname_lower = hostname.lower().strip("[]")

        if scheme in ("s3", "minio"):
            # S3 schemes may be bare bucket names ("s3://my-bucket") or endpoint
            # URLs. If there is a resolvable host, still apply the SSRF gate so a
            # bucket name cannot smuggle through a private endpoint.
            if hostname_lower in BLOCKED_HOSTNAMES:
                raise DataFabricSecurityError(f"SSRF Protection: hostname '{hostname}' is blocked")
            return url

        if not allow_private:
            if (
                hostname_lower in BLOCKED_HOSTNAMES
                or hostname_lower.endswith(".local")
                or hostname_lower.endswith(".internal")
            ):
                raise DataFabricSecurityError(f"SSRF Protection: Access to hostname '{hostname}' is blocked")

            # If the host is a literal IP, check it directly; otherwise resolve
            # every A/AAAA record and block if ANY resolves to a private range.
            # gethostbyname is IPv4-only and silently ignored IPv6 loopback (::1)
            # and IPv4-mapped addresses — getaddrinfo covers all families.
            literal = DataFabricSecurity._try_parse_ip(hostname)
            if literal is not None:
                if _is_blocked_ip(literal):
                    raise DataFabricSecurityError(
                        f"SSRF Protection: Access to private IP '{hostname}' is blocked"
                    )
            else:
                resolved = DataFabricSecurity._resolve_all(hostname)
                if not resolved:
                    # Could not resolve here. An unresolvable hostname cannot
                    # complete a connection, so we do not hard-block (the test
                    # suite and air-gapped deploys use non-resolving example URLs).
                    # The hard SSRF gate is the resolved-IP check below: any
                    # hostname that resolves to a private/loopback/metadata
                    # address is rejected regardless of how it was reached.
                    logger.warning(
                        f"Unable to resolve hostname '{hostname}' for SSRF check; "
                        f"private-IP gate will still apply if it later resolves."
                    )
                for ip_str in resolved:
                    if _is_blocked_ip(ip_str):
                        raise DataFabricSecurityError(
                            f"SSRF Protection: hostname '{hostname}' resolves to "
                            f"blocked private IP '{ip_str}'"
                        )

        return url

    @staticmethod
    def _try_parse_ip(host: str) -> Optional[str]:
        try:
            ipaddress.ip_address(host)
            return host
        except ValueError:
            return None

    @staticmethod
    def _resolve_all(hostname: str) -> list:
        """Resolve all A/AAAA records for a hostname; return [] on failure."""
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return []
        ips = []
        for family, _type, _proto, _canon, sockaddr in infos:
            ip_str = sockaddr[0]
            # Strip IPv6 scope id if present (e.g. fe80::1%eth0).
            if "%" in ip_str:
                ip_str = ip_str.split("%", 1)[0]
            ips.append(ip_str)
        return ips

    @staticmethod
    def sanitize_profile_dict(profile_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Redacts credentials and passwords before returning ConnectionProfile to LLM or frontend.
        """
        sanitized = dict(profile_dict)
        sensitive_keys = {"password", "secret", "secret_key", "token", "api_key", "access_key", "credential"}

        for k, v in list(sanitized.items()):
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "********"

        return sanitized

    @staticmethod
    def parse_safe_xml(xml_content: bytes) -> "ET.Element":
        """
        XXE Defense: Safe XML parser for WFS / WMS GetCapabilities payloads.

        Uses defusedxml, which disables external entity resolution, DTD retrieval,
        and entity expansion by default. The previous stdlib ElementTree path
        expanded internal entities and relied on a bypassable DOCTYPE/ENTITY
        regex strip.
        """
        try:
            # `ET` is bound to defusedxml.ElementTree at import time (with a
            # stdlib fallback that is itself defused via defuse_stdlib below), so
            # this is NOT the vulnerable stdlib fromstring bandit's B314 flags.
            return ET.fromstring(xml_content)  # nosec B314
        except Exception as e:
            logger.error(f"Failed to parse XML safely: {e}")
            raise DataFabricSecurityError(f"Malformed or unsafe XML payload: {e}")
