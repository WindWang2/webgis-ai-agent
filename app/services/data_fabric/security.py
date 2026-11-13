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
import re
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
    # #1694 SEC-11：阿里云/腾讯云 IMDS（与 app/core/egress.py _METADATA_HOSTS
    # 同清单）—— 缺项时内网豁免下这些元数据端点被当普通私网目标放行。
    ipaddress.ip_address("100.100.100.200"),   # 阿里云 IMDS
    ipaddress.ip_address("169.254.0.23"),      # 腾讯云 IMDS
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
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.sixtofour is not None and _is_blocked_ip(str(ip.sixtofour)):
            return True
        if ip in ipaddress.ip_network("64:ff9b::/96") and _is_blocked_ip(str(ipaddress.IPv4Address(int(ip) & 0xffffffff))):
            return True
    return not ip.is_global or ip.is_multicast or any(ip in net for net in BLOCKED_NETWORKS)


def _is_metadata_ip(ip_str: str) -> bool:
    """云元数据地址。allow_private 可以放行普通内网，但不能放行这些地址。"""
    try:
        ip = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip in BLOCKED_IPS_EXPLICIT


def _assert_not_metadata(hostname: str) -> None:
    lowered = hostname.lower().strip("[]")
    if lowered in {"metadata.google.internal", "metadata"} or lowered.endswith(
        ".metadata.google.internal"
    ):
        raise DataFabricSecurityError(
            f"SSRF Protection: metadata host '{hostname}' is blocked"
        )
    literal = DataFabricSecurity._try_parse_ip(hostname)
    candidates = [literal] if literal else DataFabricSecurity._resolve_all(hostname)
    for ip_str in candidates:
        if ip_str and _is_metadata_ip(ip_str):
            raise DataFabricSecurityError(
                f"SSRF Protection: metadata address '{ip_str}' is blocked"
            )


class DataFabricSecurity:
    @staticmethod
    def validate_url(url: str, allow_private: bool = False) -> str:
        """
        SSRF Defense: Validates remote data source URLs.
        Blocks loopback, RFC1918 private subnets, IPv6 loopback/ULA/link-local,
        IPv4-mapped IPv6, and cloud metadata IPs across ALL resolved addresses.

        Accepted schemes: http, https, s3, minio, gs, and postgresql/postgres
        (the latter two allow host/port-only DB connections to reuse this gate).

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
        # postgresql/postgres: host/port DB profiles (#1107) construct a synthetic
        # URL so the same private/loopback/metadata IP gates apply.
        if scheme not in ("http", "https", "s3", "minio", "gs", "postgresql", "postgres"):
            raise DataFabricSecurityError(f"Unsupported or unsafe scheme '{scheme}'")

        hostname = parsed.hostname
        if not hostname:
            raise DataFabricSecurityError("URL missing valid hostname")

        hostname_lower = hostname.lower().strip("[]")
        if allow_private:
            _assert_not_metadata(hostname)

        # ADR-0202：部署层出网策略先行（probe 面拒绝 = 数据面原生 typed
        # SecurityBlockedError，reliability 层按 permanent 不重试）。
        # unrestricted（cloud 默认）零行为变化；send 面的逐跳兜底由
        # SSRFSafeHTTPAdapter 的 AirGappedEgressError 承担。
        if scheme in ("http", "https", "postgresql", "postgres", "s3", "minio", "gs"):
            from app.core.egress import current_policy

            policy = current_policy()
            decision = policy.decide(url, dependency_id="data_fabric")
            if not decision.allowed:
                from app.services.data_fabric.errors import SecurityBlockedError

                raise SecurityBlockedError(
                    f"outbound request to '{decision.host}' denied by network "
                    f"egress policy (reason={decision.reason})",
                    details={
                        "policy": "egress_allowlist",
                        "reason": decision.reason,
                        "host": decision.host,
                    },
                )


        if scheme in ("s3", "minio", "gs"):
            # Object-store schemes may be bare bucket names ("s3://my-bucket")
            # or endpoint URLs. If there is a resolvable host, still apply the
            # SSRF gate so a bucket name cannot smuggle through a private endpoint.
            if hostname_lower in BLOCKED_HOSTNAMES:
                raise DataFabricSecurityError(f"SSRF Protection: hostname '{hostname}' is blocked")
            # F-5 修复（ADR-0094 §10）：s3/minio endpoint 携带端口或非 bucket 形态
            # 主机（MinIO/Wasabi 自建 endpoint）时，与 http 相同的
            # literal-IP/解析 IP 门控必须生效——此前 `s3://169.254.169.254/bucket`
            # 直接放行。
            # audit ISSUE-002（#1346）：此前只对 host_looks_like_endpoint
            # （带点/带端口）做解析门控——无点内网主机名
            # `s3://minio-server/bucket` 可整体绕过私网门控。现对一切
            # 非常量 IP 的主机名做解析检查：真 bucket 名不会解析（NXDOMAIN）
            # → warn+allow 语义不变；解析到私网 IP 的主机名一律拒绝。
            literal_ip = DataFabricSecurity._try_parse_ip(hostname)
            if literal_ip is not None and not allow_private:
                if _is_blocked_ip(literal_ip):
                    raise DataFabricSecurityError(
                        f"SSRF Protection: Access to private IP '{hostname}' is blocked"
                    )
            elif not allow_private:
                resolved = DataFabricSecurity._resolve_all(hostname)
                if not resolved:
                    logger.warning(
                        f"Unable to resolve hostname '{hostname}' for SSRF check; "
                        f"private-IP gate will still apply if it later resolves."
                    )
                for ip_str in resolved:
                    if _is_blocked_ip(ip_str):
                        raise DataFabricSecurityError(
                            f"SSRF Protection: hostname '{hostname}' resolves to blocked IP '{ip_str}'"
                        )
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
                    raise DataFabricSecurityError(
                        f"SSRF Protection: unable to resolve hostname '{hostname}'"
                    )
                for ip_str in resolved:
                    if _is_blocked_ip(ip_str):
                        raise DataFabricSecurityError(
                            f"SSRF Protection: hostname '{hostname}' resolves to "
                            f"blocked private IP '{ip_str}'"
                        )

        return url

    @staticmethod
    def validated_hostaddrs(host: str, allow_private: bool = False) -> list[str]:
        """Return the IPs from one lookup that passed the same gate as ``validate_url``.

        PostGIS must pass these as libpq ``hostaddr`` and keep the original
        hostname in ``host``. A second lookup would reopen the DNS-rebinding
        window this pre-flight check cannot close by itself.
        """
        if not host or not isinstance(host, str):
            raise DataFabricSecurityError("SSRF Protection: missing database host")
        bare = host.strip()
        if bare.startswith("[") and bare.endswith("]"):
            bare = bare[1:-1]
        if not bare:
            raise DataFabricSecurityError("SSRF Protection: missing database host")
        hostname_lower = bare.lower()
        if not allow_private and (
            hostname_lower in BLOCKED_HOSTNAMES
            or hostname_lower.endswith(".local")
            or hostname_lower.endswith(".internal")
        ):
            raise DataFabricSecurityError(
                f"SSRF Protection: Access to hostname '{bare}' is blocked"
            )
        literal = DataFabricSecurity._try_parse_ip(bare)
        if literal is not None:
            if not allow_private and _is_blocked_ip(literal):
                raise DataFabricSecurityError(
                    f"SSRF Protection: Access to private IP '{bare}' is blocked"
                )
            return [literal]
        resolved = DataFabricSecurity._resolve_all(bare)
        if not resolved:
            raise DataFabricSecurityError(
                f"SSRF Protection: unable to resolve hostname '{bare}'"
            )
        deduped: list[str] = []
        for ip_str in resolved:
            if ip_str not in deduped:
                deduped.append(ip_str)
        if not allow_private:
            for ip_str in deduped:
                if _is_blocked_ip(ip_str):
                    raise DataFabricSecurityError(
                        f"SSRF Protection: hostname '{bare}' resolves to "
                        f"blocked private IP '{ip_str}'"
                    )
        return deduped

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
        """Strip authority userinfo from an absolute ``scheme://`` URL.

        ``postgres://user:pass@host/db`` connection strings and HTTP URLs with
        embedded tokens were returned verbatim in source egress responses
        (list/get/create). The userinfo never needs to reach the client.
        Preserve everything else, including IPv6 brackets and malformed legacy
        host/port values: egress redaction must not validate or normalize URLs.
        Non-URLs and URLs without authority userinfo are returned unchanged.
        """
        if not url or not isinstance(url, str):
            return url
        # Read the raw authority instead of hostname/port properties: urlparse
        # can reject invalid brackets, .port can raise, and .hostname drops IPv6
        # brackets. Recognize urlparse's ignored C0 prefix and tabs/CR/LF
        # without normalizing the original text or skipping its credentials.
        match = re.match(
            r"^([\x00-\x20]*[a-zA-Z][a-zA-Z0-9+.\t\r\n-]*:"
            r"[\t\r\n]*/[\t\r\n]*/)([^/?#]*)",
            url,
        )
        if not match or "@" not in match.group(2):
            return url
        hostport = match.group(2).rsplit("@", 1)[1]
        return match.group(1) + hostport + url[match.end():]

    @staticmethod
    def sanitize_profile_dict(profile_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Redacts credentials before returning a profile to LLM or frontend.

        Recurses into nested dicts (``options``, ``credentials``, …) so that a
        password supplied via ``options={"password": ...}`` — the path used by
        ``create_data_source`` (its signature has no top-level password field) —
        is redacted too. The previous shallow redaction left ``options.password``
        in plaintext on every egress response. Recurse through lists too and
        strip URL userinfo from string values, including the ``url``,
        ``endpoint`` and ``endpoint_url`` aliases emitted by ConnectionProfile.
        Build a copy: stored credentials must remain usable by the adapters.
        """
        # 审计 F-2（ADR-0094 §10）：补齐常见凭证键（Authorization/x-api-key/
        # apikey/passwd/pwd/private_key/client_secret）；headers 子树整体视为
        # 敏感（认证头无法穷举键名）。
        sensitive_keys = {
            "password", "secret", "secret_key", "token", "api_key", "access_key",
            "credential", "authorization", "auth", "x-api-key", "apikey",
            # Wave 15 审计近形键补齐：连字符形 "api-key"（原集只有 x-api-key /
            # 下划线形 api_key/apikey，裸 {"api-key": …} 会原样外泄）。
            "api-key",
            "passwd", "pwd", "private_key", "client_secret", "session_token",
            "cookie",
        }
        header_subtrees = {"headers", "http_headers"}

        def _redact(value: Any) -> Any:
            if isinstance(value, dict):
                out: Dict[str, Any] = {}
                for k, v in value.items():
                    key_l = str(k).lower()
                    # headers 子树整体敏感：Cookie/Set-Cookie/自定义认证头无法穷举键名。
                    if key_l in header_subtrees or any(s in key_l for s in sensitive_keys):
                        out[k] = "********"
                    else:
                        out[k] = _redact(v)
                return out
            if isinstance(value, list):
                return [_redact(v) for v in value]
            if isinstance(value, str):
                return DataFabricSecurity.redact_url(value)
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


# ── HTTP client SSRF hardening ──────────────────────────────────────
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
# IPv6). Each direct urllib3 connection resolves, validates and pins its own
# numeric address while preserving the original hostname for TLS verification.
# No process-global resolver mutation is needed. Native GDAL /vsicurl is a
# separate transport and remains governed by deployment network isolation.


class _SafeConnectionMixin:
    """Pin only this connection to a vetted address; preserve the TLS hostname."""

    def _new_conn(self) -> socket.socket:
        original_host = self._dns_host
        try:
            infos = socket.getaddrinfo(original_host, self.port, 0, socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise DataFabricSecurityError(f"Unable to resolve hostname '{original_host}'") from exc
        blocked = getattr(self, "_ip_blocked", _is_blocked_ip)
        if not infos or any(blocked(info[4][0].split("%", 1)[0]) for info in infos):
            raise DataFabricSecurityError(f"SSRF Protection: blocked DNS result for '{original_host}'")
        last_error = None
        for info in infos:
            self._dns_host = info[4][0]
            try:
                # urllib3 connects to the numeric address using the original
                # destination port. HTTPS.connect sees the restored hostname
                # for SNI/certificate verification after this method returns.
                return super()._new_conn()
            except OSError as exc:
                last_error = exc
            finally:
                self._dns_host = original_host
        raise last_error


async def safe_fsspec_http_client(*, allow_private: bool = False, **kwargs: Any) -> Any:
    """fsspec HTTP sessions validate the actual DNS answers used to connect.

    Callers disable redirects separately so a new target never bypasses the
    initial URL/egress gate. A connector owns its resolver for this session.
    """
    import aiohttp

    class SafeResolver(aiohttp.resolver.DefaultResolver):
        async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[dict[str, Any]]:
            addresses = await super().resolve(host, port, family)
            blocked = _is_metadata_ip if allow_private else _is_blocked_ip
            if not addresses or any(blocked(item["host"]) for item in addresses):
                raise DataFabricSecurityError("SSRF Protection: blocked HTTP DNS result")
            return addresses

    kwargs["connector"] = aiohttp.TCPConnector(resolver=SafeResolver())
    return aiohttp.ClientSession(**kwargs)


def _safe_pool_classes(ip_blocked=_is_blocked_ip) -> dict[str, type]:
    from urllib3.connection import HTTPConnection, HTTPSConnection
    from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

    class SafeHTTPConnection(_SafeConnectionMixin, HTTPConnection):
        _ip_blocked = staticmethod(ip_blocked)

    class SafeHTTPSConnection(_SafeConnectionMixin, HTTPSConnection):
        _ip_blocked = staticmethod(ip_blocked)

    class SafeHTTPPool(HTTPConnectionPool):
        ConnectionCls = SafeHTTPConnection

    class SafeHTTPSPool(HTTPSConnectionPool):
        ConnectionCls = SafeHTTPSConnection

    return {"http": SafeHTTPPool, "https": SafeHTTPSPool}


class SSRFSafeHTTPAdapter(requests.adapters.HTTPAdapter):
    """``requests`` HTTPAdapter that enforces SSRF policy on every send.

    Mount on both ``http://`` and ``https://`` (see ``make_safe_session``) so
    initial requests AND every redirect hop are validated.
    """

    def __init__(self, *args, allow_private: bool = False, **kwargs) -> None:
        self._allow_private = allow_private
        super().__init__(*args, **kwargs)

    def _ip_predicate(self):
        return _is_metadata_ip if self._allow_private else _is_blocked_ip

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = _safe_pool_classes(self._ip_predicate())

    def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
        manager = super().proxy_manager_for(proxy, **proxy_kwargs)
        manager.pool_classes_by_scheme = _safe_pool_classes(self._ip_predicate())
        return manager

    def send(self, request, **kwargs):  # type: ignore[override]
        url = getattr(request, "url", None)
        if url:
            from requests.utils import select_proxy

            if not self._allow_private and select_proxy(str(url), kwargs.get("proxies") or {}):
                raise DataFabricSecurityError(
                    "SSRF guarded data connections require direct transport; "
                    "proxy-side DNS cannot be validated by the application"
                )
            # ADR-0202：egress 守卫先于 SSRF 校验——离线/内网部署下出网
            # 是策略拒绝（typed AirGappedEgressError），必须先于"可达性"
            # 检查给出正确失败语义。requests 对每跳 redirect 重挂 adapter，
            # redirect 目标同样过守卫。unrestricted（cloud 默认）走快速
            # 路径：仅一次缓存策略查询 + 模式比较，无 URL 解析开销。
            from app.core.egress import assert_egress_allowed, current_policy

            if current_policy().mode == "allowlist":
                assert_egress_allowed(str(url), dependency_id="data_fabric")
            DataFabricSecurity.validate_url(url, allow_private=self._allow_private)

        return super().send(request, **kwargs)


def make_safe_session(allow_private: bool = False, rate_limit=None):
    """Return a ``requests.Session`` whose every request (incl. redirects) is
    SSRF-validated. Adapters MUST use this instead of a bare ``requests.Session``.

    G3D-V2-A A5（#1771 D11 清偿）：``rate_limit=(key, rpm)`` 给**每一次出站
    HTTP 往返**（含重定向的每一跳 —— requests 的 resolve_redirects 逐跳经
    ``session.send``，不经 ``session.request``，评审 P2 修正）挂滑动窗限流，
    超限抛 ``SourceRateLimitedError``（typed，绝不静默抓取）。键建议带
    source_id（如 ``"outbound:<source_id>"``），配额来自
    SourceDefinition.QuotaDecl 或 operator 配置 —— 摄入方（adapter 构造处）
    负责传入；未传 = 不限流（既有行为），接线进度见
    planning/g3dv2-a-geo-data-supply/RISKS.md。
    """
    session = requests.Session()
    adapter = SSRFSafeHTTPAdapter(allow_private=allow_private)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    if rate_limit is not None:
        from app.services.data_fabric.errors import SourceRateLimitedError
        from app.services.data_fabric.tile_rate_limiter import check_rate

        key, rpm = rate_limit
        _orig_send = session.send

        def _rate_limited_send(request, **kwargs):
            allowed, retry_after = check_rate(f"outbound:{key}", int(rpm))
            if not allowed:
                raise SourceRateLimitedError(
                    f"outbound rate limit ({rpm}/min) exceeded for source "
                    f"'{key}'; retry after {retry_after}s"
                )
            return _orig_send(request, **kwargs)

        session.send = _rate_limited_send
    return session


# 解压后字节上限下限（防止配置归零）。stream 读取逐块累计，超限即断流——
# 压缩炸弹在解析之前就被拒（审计 F-3 / ADR-0094 §10）。
_MIN_HTTP_BODY_CAP = 16 * 1024 * 1024


def bounded_get(session, url, *, params=None, timeout=15, max_bytes=None, headers=None):
    """流式 + 有界 GET：Content-Length 预检 + 解压后逐块字节上限。

    返回 ``bytes``；超限抛 ``SourceBadResponseError``（typed）。
    所有 HTTP adapter 的 GET 都应经此（禁止裸 ``resp.content``）。
    """
    from app.services.data_fabric.errors import SourceBadResponseError
    from app.services.data_fabric.limits import max_response_bytes

    cap = max_bytes or max(max_response_bytes(), _MIN_HTTP_BODY_CAP)
    resp = session.get(
        url, params=params, timeout=timeout, stream=True,
        headers=headers, allow_redirects=True,
    )
    resp.raise_for_status()
    content_length = resp.headers.get("Content-Length")
    if content_length and content_length.isdigit() and int(content_length) > cap:
        resp.close()
        raise SourceBadResponseError(
            f"response Content-Length {content_length} exceeds cap {cap}",
            details={"content_length": int(content_length), "cap": cap},
        )
    chunks = []
    total = 0
    try:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > cap:
                raise SourceBadResponseError(
                    f"decompressed response exceeded cap {cap}",
                    details={"cap": cap, "bytes_so_far": total},
                )
            chunks.append(chunk)
    finally:
        resp.close()
    return b"".join(chunks)


def safe_json_get(session, url, *, params=None, timeout=15, max_bytes=None, headers=None):
    """bounded_get + JSON 解析（depth/size 由字节上限间接约束）。"""
    import json as _json

    body = bounded_get(session, url, params=params, timeout=timeout,
                       max_bytes=max_bytes, headers=headers)
    return _json.loads(body.decode("utf-8", errors="strict"))


def _configured_sqlite_db_path() -> Optional[str]:
    """Resolved path of the configured sqlite ``DATABASE_URL`` file, if any.

    SEC-01 residual: with the default dev/self-host config the platform
    database is ``sqlite:///./data/webgis.db`` and the default
    ``DATA_FABRIC_LOCAL_FILE_ROOTS`` (``./data``) *contains* it — passing the
    roots to the adapters is not enough to stop credential/session exfiltration.
    Postgres deployments resolve to ``None`` (no local file to protect).
    """
    try:
        from app.core.config import settings

        url = str(getattr(settings, "DATABASE_URL", "") or "")
    except Exception:  # noqa: BLE001 — the guard must not fail open on config errors
        return None
    if "sqlite" not in url or ":///" not in url:
        return None
    raw = url.split(":///", 1)[1]
    if not raw or raw.startswith(":memory:") or "mode=memory" in raw:
        return None
    try:
        from pathlib import Path

        return str(Path(raw).expanduser().resolve())
    except OSError:
        return None


def _is_under(child: str, parent: str) -> bool:
    """Path containment that is separator- and case-correct on Windows too.

    The previous ``startswith(parent + "/")`` check failed on Windows: resolved
    paths use ``\\``, so in-root paths were rejected and POSIX sensitive-dir
    prefixes never matched at all.
    """
    if not parent:
        return False
    from pathlib import Path

    try:
        Path(child).relative_to(Path(parent))
        return True
    except ValueError:
        return False


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
    if not allowed_roots:
        raise DataFabricSecurityError("local file access requires nonempty allowed roots")
    raw = Path(path)
    real = raw.resolve() if raw.is_absolute() else (Path.cwd() / raw).resolve()
    real_str = str(real)

    # Always block sensitive system locations + the user's SSH dir.
    home_ssh = str(Path.home() / ".ssh")
    blocked = list(SENSITIVE_SYSTEM_DIRS) + [home_ssh]
    for sens in blocked:
        if _is_under(real_str, sens):
            raise DataFabricSecurityError(
                f"local file path '{path}' is in a blocked system directory"
            )

    # SEC-01 residual: never serve the configured sqlite platform DB (or its
    # WAL/SHM/journal sidecars, which carry the same rows) through an adapter,
    # even when it sits inside an allowed root such as the default ``./data``.
    db_path = _configured_sqlite_db_path()
    if db_path:
        db_norm = db_path.casefold()
        real_norm = real_str.casefold()
        if (
            _is_under(real_str, db_path)
            or real_norm.startswith(db_norm + "-")
            or real_norm.startswith(db_norm + ".")
        ):
            raise DataFabricSecurityError(
                f"local file path '{path}' is the platform database (blocked)"
            )

    if allowed_roots:
        roots = []
        for r in allowed_roots:
            rp = Path(r).expanduser()
            roots.append(str(rp.resolve()))
        if not any(_is_under(real_str, rr) for rr in roots if rr):
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


def _local_file_roots_from_settings() -> list[str]:
    """Resolve the configured local-file roots list from settings (helper for adapters)."""
    from app.core.config import settings

    raw = getattr(settings, "DATA_FABRIC_LOCAL_FILE_ROOTS", "") or ""
    return [r.strip() for r in raw.split(",") if r.strip()]


def _local_file_max_bytes_from_settings() -> int:
    from app.core.config import settings

    return int(getattr(settings, "DATA_FABRIC_LOCAL_FILE_MAX_BYTES", 1024 * 1024 * 1024))


def ensure_same_origin_url(candidate: str, base_url: str) -> str:
    """校验 candidate 与 base 同 scheme+host+port（R3-M2/M3 cursor 防护）。

    cursor/next-link 可能被调用方伪造为任意 URL——若直接以其发起携带
    profile 凭证头的请求，会造成凭证外带 / SSRF 代理滥用。不同源抛
    ``SourceBadResponseError``。
    """
    from urllib.parse import urlparse as _urlparse

    from app.services.data_fabric.errors import SourceBadResponseError

    c = _urlparse(str(candidate))
    b = _urlparse(str(base_url))
    c_origin = (c.scheme.lower(), (c.hostname or "").lower(), c.port)
    b_origin = (b.scheme.lower(), (b.hostname or "").lower(), b.port or (443 if b.scheme == "https" else 80))
    if c_origin[0] != b_origin[0] or c_origin[1] != b_origin[1]:
        raise SourceBadResponseError(
            "next-link cursor points to a different origin than the registered source",
            details={"origin": f"{c_origin[0]}://{c_origin[1]}"},
        )
    c_port = c.port or (443 if c.scheme == "https" else 80)
    if c_port != b_origin[2]:
        raise SourceBadResponseError(
            "next-link cursor port differs from the registered source"
        )
    # 同源还不够：cursor 可伪造 POST 到源主机任意路径。注册 endpoint 有路径时，
    # next-link 必须落在该路径前缀下（根路径无法再收紧）。
    base_path = b.path or "/"
    if base_path not in ("", "/"):
        prefix = base_path.rstrip("/")
        cand_path = c.path or "/"
        if cand_path != prefix and not cand_path.startswith(prefix + "/"):
            raise SourceBadResponseError(
                "next-link cursor path is outside the registered endpoint path",
                details={"origin": f"{c_origin[0]}://{c_origin[1]}"},
            )
    return str(candidate)
