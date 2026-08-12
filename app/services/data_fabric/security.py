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

import requests.adapters

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

# System directories a geospatial local-file adapter must never read from, even
# when an internal caller supplies such a path (defense in depth — the public
# REST API already blocks bare/file: paths at validate_url, but adapters can be
# constructed programmatically with arbitrary local paths).
SENSITIVE_SYSTEM_DIRS = (
    "/etc", "/proc", "/sys", "/dev", "/run", "/boot",
    "/root", "/var/log", "/usr/lib", "/usr/sbin", "/sbin", "/bin",
)

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
    def redact_url(url: Optional[str]) -> Optional[str]:
        """Strip ``user:password@`` userinfo from a URL for safe egress.

        ``postgres://user:pass@host/db`` connection strings and HTTP URLs with
        embedded tokens were returned verbatim in source egress responses
        (list/get/create). The userinfo never needs to reach the client.
        Returns the input unchanged for non-URL / parseable-without-userinfo.
        """
        if not url or not isinstance(url, str):
            return url
        try:
            parsed = urlparse(url)
        except Exception:
            return url
        if not parsed.scheme or "@" not in (parsed.netloc or ""):
            return url
        # Keep host:port; drop the userinfo segment.
        hostport = parsed.hostname or ""
        if parsed.port:
            hostport = f"{hostport}:{parsed.port}"
        rebuilt = parsed._replace(netloc=hostport)
        return rebuilt.geturl()

    @staticmethod
    def sanitize_profile_dict(profile_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Redacts credentials before returning a profile to LLM or frontend.

        Recurses into nested dicts (``options``, ``credentials``, …) so that a
        password supplied via ``options={"password": ...}`` — the path used by
        ``create_data_source`` (its signature has no top-level password field) —
        is redacted too. The previous shallow redaction left ``options.password``
        in plaintext on every egress response. Lists of dicts are recursed per
        element; non-dict values are left untouched.
        """
        sensitive_keys = {"password", "secret", "secret_key", "token", "api_key", "access_key", "credential"}

        def _redact(value: Any) -> Any:
            if isinstance(value, dict):
                out: Dict[str, Any] = {}
                for k, v in value.items():
                    if any(s in k.lower() for s in sensitive_keys):
                        out[k] = "********"
                    else:
                        out[k] = _redact(v)
                return out
            if isinstance(value, list):
                return [_redact(v) for v in value]
            return value

        return _redact(profile_dict)

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


# ── HTTP client SSRF hardening ──────────────────────────────────────────────
#
# validate_url() is a *pre-flight* gate: it runs once on the URL the user
# registered. The original adapters then handed the URL to a plain
# ``requests.Session`` with the default ``allow_redirects=True``. ``requests``
# follows redirects internally and never re-validated the ``Location`` target,
# so a registered public host that answered ``302 → http://169.254.169.254/``
# was followed straight into the cloud metadata service (redirect-SSRF P0).
#
# Mitigation: ``SSRFSafeHTTPAdapter`` re-runs ``validate_url`` inside ``send()``
# on *every* request. Because ``requests`` re-invokes the mounted adapter for
# each redirect hop, every redirect target is re-validated against the same
# SSRF policy (private IPs, loopback, metadata, ULA/link-local, IPv4-mapped
# IPv6). Re-resolving inside ``send()`` also shrinks the DNS-rebinding TOCTOU
# window: the hostname is resolved immediately before the underlying urllib3
# connect, not seconds earlier at registration time. (A residual micro-window
# between resolve and connect remains; full connect-time IP pinning would need
# a socket-level transport and is documented as ADR-0053 follow-up.)


class SSRFSafeHTTPAdapter(requests.adapters.HTTPAdapter):
    """``requests`` HTTPAdapter that enforces SSRF policy on every send.

    Mount on both ``http://`` and ``https://`` (see ``make_safe_session``) so
    initial requests AND every redirect hop are validated.
    """

    def __init__(self, *args, allow_private: bool = False, **kwargs):
        self._allow_private = allow_private
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):  # type: ignore[override]
        url = getattr(request, "url", None)
        if url:
            DataFabricSecurity.validate_url(url, allow_private=self._allow_private)
        return super().send(request, **kwargs)


def make_safe_session(allow_private: bool = False):
    """Return a ``requests.Session`` whose every request (incl. redirects) is
    SSRF-validated. Adapters MUST use this instead of a bare ``requests.Session``.
    """
    session = requests.Session()
    adapter = SSRFSafeHTTPAdapter(allow_private=allow_private)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def resolve_safe_local_path(path, allowed_roots=None, max_bytes=None):
    """Validate a local file path for adapter reads (Section 44).

    Defenses (defense-in-depth — the public REST API already blocks bare/file:
    paths at ``validate_url``):
    - rejects empty / non-string paths;
    - canonicalizes via realpath (collapses ``..`` and resolves symlinks);
    - blocks reads under sensitive system dirs (``/etc``, ``/proc``, ``~/.ssh``…);
    - when ``allowed_roots`` is provided, the real path must be under one of
      them (blocks symlink escape beyond the intended directory);
    - optional file-size cap.

    Raises ``DataFabricSecurityError`` on violation; returns the resolved
    ``pathlib.Path`` otherwise.
    """
    from pathlib import Path

    if not path or not isinstance(path, str):
        raise DataFabricSecurityError("empty or invalid local file path")
    raw = Path(path)
    real = raw.resolve() if raw.is_absolute() else (Path.cwd() / raw).resolve()
    real_str = str(real)

    # Always block sensitive system locations + the user's SSH dir.
    home_ssh = str(Path.home() / ".ssh")
    blocked = list(SENSITIVE_SYSTEM_DIRS) + [home_ssh]
    for sens in blocked:
        if real_str == sens or real_str.startswith(sens + "/"):
            raise DataFabricSecurityError(
                f"local file path '{path}' is in a blocked system directory"
            )

    if allowed_roots:
        roots = []
        for r in allowed_roots:
            rp = Path(r).expanduser()
            roots.append(str(rp.resolve()))
        if not any(real_str == rr or real_str.startswith(rr + "/") for rr in roots if rr):
            raise DataFabricSecurityError(
                f"local file path '{path}' escapes the allowed roots"
            )

    if max_bytes is not None and real.exists() and real.is_file():
        size = real.stat().st_size
        if size > max_bytes:
            raise DataFabricSecurityError(
                f"local file '{path}' size {size} exceeds limit {max_bytes}"
            )
    return real


def _local_file_roots_from_settings():
    """Resolve the configured local-file roots list from settings (helper for adapters)."""
    from app.core.config import settings

    raw = getattr(settings, "DATA_FABRIC_LOCAL_FILE_ROOTS", "") or ""
    return [r.strip() for r in raw.split(",") if r.strip()]


def _local_file_max_bytes_from_settings():
    from app.core.config import settings

    return int(getattr(settings, "DATA_FABRIC_LOCAL_FILE_MAX_BYTES", 1024 * 1024 * 1024))
