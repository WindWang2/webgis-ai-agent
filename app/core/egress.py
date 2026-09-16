"""出网（egress）守卫 — 离线/内网部署 profile 的应用层第一道防线（ADR-0197）。

语义（与 settings 三键联动，见 ``app/core/config.py``）：

- ``DEPLOYMENT_PROFILE=cloud``（默认）→ 不拦截，行为与本模块合入前逐字节一致。
- ``NETWORK_EGRESS_MODE=allowlist`` → deny-by-default：仅放行
  （a）``NETWORK_EGRESS_ALLOW`` 显式登记的主机（精确或 ``*.suffix`` 通配）；
  （b）私网/回环/链路本地目标（``NETWORK_EGRESS_ALLOW_PRIVATE``，air-gapped
  内网服务——本地 LLM、内网 PostGIS/MinIO/瓦片服务器——正是部署目标）。
- ``DEPLOYMENT_PROFILE=air_gapped`` → Settings validator 强制
  ``NETWORK_EGRESS_MODE=allowlist``；本模块不感知 profile 组合约束。

拒绝是 typed 的（``AirGappedEgressError``，携带 host/reason/dependency_id），
让必须联网的能力显式 ``unavailable/degraded``，而不是伪装成连接失败
（honest failure：UBIQUITOUS_LANGUAGE "fail-closed"）。

接线面（谁调用本模块）：
- aiohttp：``app/core/network.py`` 共享 session 的 trace 钩子；
- httpx：``app/services/chat/llm_client.py`` LLM 池 event hook +
  ``guarded_async_client()/guarded_client()``（ad-hoc 客户端统一入口）；
- requests：``app/services/data_fabric/security.py::SSRFSafeHTTPAdapter.send()``
  （每跳 redirect 都过守卫）。

已知边界（文档面）：pystac-client 与 rasterio /vsicurl 在第三方库内部建连，
不经本守卫；vendor/pi 子进程的 LLM 调用同样不可从 Python 层拦截。这两类由
NetworkDependencyCatalog 登记 + preflight 在 air-gapped 下报 typed
unavailable，最终防线是部署网络层（防火墙/netns）。

本模块顶层只 import 标准库——``app.core.config`` 的 validator 会复用决策
函数，不得构成 import 环。
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional
from urllib.parse import urlparse

__all__ = [
    "AirGappedEgressError",
    "EgressDecision",
    "EgressDeniedReason",
    "EgressPolicy",
    "assert_egress_allowed",
    "egress_allowed",
    "current_policy",
    "reset_policy_cache",
    "guarded_async_client",
    "guarded_client",
]

#: 守卫覆盖的出网协议面。file:/ref:/data: 等不构成 HTTP 出网，放行。
_EGRESS_SCHEMES = frozenset({"http", "https", "ws", "wss"})

#: 无 DNS 分类即可判为"内网形态"的主机后缀/名字。字面 IP 走 ipaddress。
_PRIVATE_HOSTNAME_SUFFIXES = (".local", ".internal")

#: 云元数据端点：即使 allow_private（内网豁免）也必须 deny——私网可达性
#: 正是元数据凭证外泄的通道（与 data_fabric BLOCKED_IPS_EXPLICIT 同清单）。
_METADATA_HOSTS = frozenset({"169.254.169.254", "fd00:ec2::254"})


class EgressDeniedReason(str, Enum):
    """拒绝原因词表（封闭；进 evidence/log，不得自由文本）。"""

    NOT_ALLOWLISTED = "not_allowlisted"
    PRIVATE_BLOCKED = "private_blocked"
    METADATA_BLOCKED = "metadata_blocked"
    INVALID_URL = "invalid_url"


class AirGappedEgressError(RuntimeError):
    """egress 守卫 typed 拒绝。

    调用方应把它映射为能力的 ``unavailable/degraded``（LLM FailureKind /
    data_fabric ``SecurityBlockedError`` / 工具失败语义），而不是裸冒
    ``ConnectionError``——离线是策略决定，不是网络事故。
    """

    def __init__(
        self,
        url: str,
        host: str,
        reason: "EgressDeniedReason | str",
        dependency_id: Optional[str] = None,
    ) -> None:
        self.url = url
        self.host = host
        self.reason = getattr(reason, "value", reason)
        self.dependency_id = dependency_id
        target = f"{host!r} (url={url!r})"
        suffix = f", dependency={dependency_id!r}" if dependency_id else ""
        super().__init__(
            f"outbound request to {target} denied by network egress policy "
            f"(reason={self.reason}{suffix}); 离线/内网部署下该能力 typed "
            f"unavailable——将端点加入 NETWORK_EGRESS_ALLOW 或切换部署 profile"
        )


@dataclass(frozen=True)
class EgressDecision:
    """单次出网裁决（纯值对象；as_dict() 供 evidence/观测面）。"""

    allowed: bool
    host: str
    mode: str
    reason: str = ""
    dependency_id: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "host": self.host,
            "mode": self.mode,
            "reason": self.reason,
            "dependency_id": self.dependency_id,
        }


@dataclass(frozen=True)
class EgressPolicy:
    """不可变策略快照。from_params() 解析 env 原始串；decide() 纯函数。"""

    profile: str
    mode: str
    allow_private: bool
    exact_hosts: frozenset = field(default_factory=frozenset)
    wildcard_suffixes: tuple = ()

    @classmethod
    def from_params(
        cls,
        *,
        profile: str,
        mode: str,
        allow_raw: str = "",
        allow_private: bool = True,
    ) -> "EgressPolicy":
        exact: set = set()
        wildcards: list = []
        for token in (allow_raw or "").split(","):
            host = token.strip().lower().strip(".")
            if not host:
                continue
            if host.startswith("*."):
                wildcards.append("." + host[2:])
            else:
                exact.add(host)
        return cls(
            profile=(profile or "cloud").strip().lower(),
            mode=(mode or "unrestricted").strip().lower(),
            allow_private=bool(allow_private),
            exact_hosts=frozenset(exact),
            wildcard_suffixes=tuple(wildcards),
        )

    # ── 纯决策 ──────────────────────────────────────────────────────

    def decide(self, url: str, dependency_id: Optional[str] = None) -> EgressDecision:
        scheme = _scheme_of(url)
        host = _extract_host(url)
        if host is None:
            # 空 scheme（相对 URL/垃圾输入）fail-closed；其余非 http(s/ws)
            # 协议（file:/ref:/data:）不构成 HTTP 出网面 → 放行。
            if scheme == "" or scheme in _EGRESS_SCHEMES:
                return EgressDecision(
                    False, "", self.mode, EgressDeniedReason.INVALID_URL.value,
                    dependency_id,
                )
            return EgressDecision(True, "", self.mode,
                                  dependency_id=dependency_id)
        lowered = host.lower()
        if self.mode != "allowlist":
            # unrestricted（cloud 默认）= 行为零变化：元数据 SSRF 防线仍在
            # Settings._validate_no_ssrf / data_fabric.validate_url 各层。
            return EgressDecision(True, host, self.mode,
                                  dependency_id=dependency_id)
        if lowered in _METADATA_HOSTS:
            return EgressDecision(
                False, host, self.mode, EgressDeniedReason.METADATA_BLOCKED.value,
                dependency_id,
            )
        lowered = host.lower()
        if lowered in self.exact_hosts:
            return EgressDecision(True, host, self.mode,
                                  dependency_id=dependency_id)
        if any(lowered.endswith(suffix) for suffix in self.wildcard_suffixes):
            return EgressDecision(True, host, self.mode,
                                  dependency_id=dependency_id)
        if self.allow_private and _is_private_host(lowered):
            return EgressDecision(True, host, self.mode,
                                  dependency_id=dependency_id)
        reason = (
            EgressDeniedReason.PRIVATE_BLOCKED
            if (not self.allow_private and _is_private_host(lowered))
            else EgressDeniedReason.NOT_ALLOWLISTED
        )
        return EgressDecision(False, host, self.mode, reason.value, dependency_id)

    def assert_allowed(self, url: str, dependency_id: Optional[str] = None) -> None:
        decision = self.decide(url, dependency_id=dependency_id)
        if not decision.allowed:
            raise AirGappedEgressError(
                url, decision.host, decision.reason, decision.dependency_id,
            )


def _scheme_of(url: str) -> str:
    try:
        return (urlparse(str(url)).scheme or "").lower()
    except Exception:  # noqa: BLE001 — 非法输入按无效 URL 处理
        return ""


def _extract_host(url: str) -> Optional[str]:
    """提取小写 host（IPv6 去方括号）；无法解析且 scheme 属出网面 → None。"""
    if url is None:
        return None
    try:
        parsed = urlparse(str(url))
    except Exception:  # noqa: BLE001
        return None
    host = parsed.hostname
    if not host:
        return None
    return host.strip("[]").lower()


def _is_private_host(host: str) -> bool:
    """无 DNS 的保守内网分类：字面私网/回环/链路本地 IP + 内网形态主机名。

    非 IP 主机名（如 ``llm.corp.example``）不解析 DNS——按公网对待，
    经 NETWORK_EGRESS_ALLOW 显式登记。保守方向错误 = 拒绝（fail-closed）。
    """
    if host in ("localhost",) or host.endswith(".localhost"):
        return True
    if any(host.endswith(suffix) for suffix in _PRIVATE_HOSTNAME_SUFFIXES):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    # IPv4-mapped IPv6 折回内层判断（::ffff:127.0.0.1 等）。
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_unspecified
    )


# ── Settings 接线（带缓存的策略快照）────────────────────────────────
#
# 每次 HTTP 请求都会过 decide()，但 settings 解析 allowlist 串只在键变化
# 时做一次。键 tuple 摘要变化即重建；测试用 reset_policy_cache() 清空。

_policy_cache: dict = {}


def policy_from_settings(settings_obj=None) -> EgressPolicy:
    from app.core.config import settings as _settings

    src = settings_obj or _settings
    return EgressPolicy.from_params(
        profile=getattr(src, "DEPLOYMENT_PROFILE", "cloud"),
        mode=getattr(src, "NETWORK_EGRESS_MODE", "unrestricted"),
        allow_raw=getattr(src, "NETWORK_EGRESS_ALLOW", ""),
        allow_private=getattr(src, "NETWORK_EGRESS_ALLOW_PRIVATE", True),
    )


def current_policy() -> EgressPolicy:
    from app.core.config import settings as _settings

    key = (
        getattr(_settings, "DEPLOYMENT_PROFILE", ""),
        getattr(_settings, "NETWORK_EGRESS_MODE", ""),
        getattr(_settings, "NETWORK_EGRESS_ALLOW", ""),
        bool(getattr(_settings, "NETWORK_EGRESS_ALLOW_PRIVATE", True)),
    )
    policy = _policy_cache.get(key)
    if policy is None:
        policy = policy_from_settings(_settings)
        _policy_cache.clear()  # 只保留当前键，防测试反复改配置导致无界增长
        _policy_cache[key] = policy
    return policy


def reset_policy_cache() -> None:
    """测试/运行期配置热更新后清空策略缓存（manage.py preflight 也用）。"""
    _policy_cache.clear()


def evaluate_egress(url: str, *, policy: EgressPolicy,
                    dependency_id: Optional[str] = None) -> EgressDecision:
    """模块级决策入口（测试/探测面用；接线层直接用 policy.decide）。"""
    return policy.decide(url, dependency_id=dependency_id)


def egress_allowed(url: str, dependency_id: Optional[str] = None) -> EgressDecision:
    """以当前 Settings 策略裁决（不抛异常）。"""
    return current_policy().decide(url, dependency_id=dependency_id)


def assert_egress_allowed(url: str, dependency_id: Optional[str] = None) -> None:
    """以当前 Settings 策略裁决，拒绝则抛 AirGappedEgressError。"""
    current_policy().assert_allowed(url, dependency_id=dependency_id)


# ── httpx 客户端统一入口（ad-hoc 调用点迁移用）───────────────────────


def _httpx_request_hook(dependency_id: Optional[str]):
    def _hook(request):
        assert_egress_allowed(str(request.url), dependency_id=dependency_id)
    return _hook


async def _httpx_async_request_hook(dependency_id: Optional[str]):
    def _hook(request):
        assert_egress_allowed(str(request.url), dependency_id=dependency_id)
    return _hook()


def guarded_client(*, dependency_id: Optional[str] = None, **kwargs):
    """httpx.Client（同步），请求前过 egress 守卫。保留调用方已有 event_hooks。"""
    import httpx

    hooks = list(kwargs.pop("event_hooks", None) or {})
    hooks.append(_httpx_request_hook(dependency_id))
    return httpx.Client(event_hooks={"request": hooks}, **kwargs)


def guarded_async_client(*, dependency_id: Optional[str] = None, **kwargs):
    """httpx.AsyncClient，请求前过 egress 守卫。保留调用方已有 event_hooks。"""
    import httpx

    hooks = list(kwargs.pop("event_hooks", None) or {})
    hooks.append(_httpx_async_request_hook(dependency_id))
    return httpx.AsyncClient(event_hooks={"request": hooks}, **kwargs)


def iter_allowed_hosts(policy: Optional[EgressPolicy] = None) -> Iterable[str]:
    """观测面：当前策略显式登记的主机（preflight/health 用）。"""
    p = policy or current_policy()
    return sorted(p.exact_hosts)
