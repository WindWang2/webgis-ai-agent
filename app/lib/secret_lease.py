"""SecretProvider / credential lease 契约(H05,F06 遗留注入点的兑现)。

F06 把「凭证已配置」的 presence 事实桥进了资格上下文,但真实 secret 供应链
只留了 provider 注入点(``set_credential_presence_provider`` 的注释承诺)。
本模块补上 **material 供给面**:

- 真实 secret 仍然只在 provider 内部(process 内存 / 部署侧 secret manager)。
  运行时传递的只有 :class:`SecretLease` —— ref + digest + ttl,**永无 material**。
- lease 的 ``repr``/``str``/``to_dict`` 结构性排除 material(有性质测试钉死);
  material 只能经 :meth:`SecretLeaseManager.resolve` 按 lease 取用,取用面
  绝不记日志。
- 默认零配置 = presence-only 世界逐位不变:没人注入 provider 时 acquire 返回
  typed miss(credential_missing),不破坏 F06 的 fail-open 直通纪律。
- hermetic 适配器(:class:`EnvSecretProvider` 读 ``GIS_TOOL_SECRET_<ID>`` /
  :class:`InMemorySecretProvider`)让无 Vault/KMS 部署与测试全部本地闭环;
  Vault/KMS 适配器 = 实现 :class:`SecretProvider` 协议后
  :func:`set_secret_provider` 注入 —— 本模块是唯一注入点,不建第二套凭证体系。
- 与 redaction 的关系:material 一旦被调用方自己放进 dict,由既有
  ``app/lib/redaction.py`` 的 scrub 兜底;本模块的义务是**自己永远不发射**。
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Tuple

__all__ = [
    "SECRET_ENV_PREFIX",
    "MAX_ACTIVE_LEASES",
    "DEFAULT_LEASE_TTL_S",
    "LEASE_OK",
    "LEASE_CREDENTIAL_MISSING",
    "LEASE_PROVIDER_FAILED",
    "SecretProvider",
    "SecretLease",
    "LeaseAcquireResult",
    "EnvSecretProvider",
    "InMemorySecretProvider",
    "SecretLeaseManager",
    "set_secret_provider",
    "get_secret_lease_manager",
    "set_secret_lease_manager",
    "bind_secret_leases",
    "current_secret_leases",
    "resolve_secret_lease",
]

#: hermetic env 适配器的变量前缀(``GIS_TOOL_SECRET_<ID大写>``)。
SECRET_ENV_PREFIX = "GIS_TOOL_SECRET_"

#: 活跃 lease 上界(LRU 溢出逐出最旧;泄漏面有界)。
MAX_ACTIVE_LEASES = 256

#: 默认租约时长(秒;短期租约 —— 运行时只传 ref 的语义基础)。
DEFAULT_LEASE_TTL_S = 300.0

#: acquire 结果词表。
LEASE_OK = "ok"
LEASE_CREDENTIAL_MISSING = "credential_missing"
LEASE_PROVIDER_FAILED = "provider_failed"

_STR_MAX = 128


def secret_digest(material: str) -> str:
    """material 的稳定摘要(sha256[:16];postgis ``_credential_digest`` 同构)。

    digest 可以进证据/日志/pool key —— 它不泄漏 material(16 hex 字符)。
    """
    return hashlib.sha256((material or "").encode("utf-8")).hexdigest()[:16]


class SecretProvider(Protocol):
    """secret material provider 契约(实现方绝不抛、绝不记日志、绝不缓存过期值)。"""

    def resolve_material(self, credential_id: str) -> Optional[str]:
        """返回 material;未配置返回 None(fail-typed,不 fail-noise)。"""
        ...


@dataclass(frozen=True)
class SecretLease:
    """短期租约句柄(安全投影:结构性无 material)。

    ``digest`` 可进证据/pool key;``expires_at`` 是单调时钟秒(与 manager
    的注入 clock 同源);跨进程无意义 —— lease 是进程内短期事实,不是持久令牌。
    """

    lease_id: str
    credential_id: str
    digest: str
    scope: str = ""
    expires_at: float = 0.0
    ttl_s: float = DEFAULT_LEASE_TTL_S

    def __repr__(self) -> str:  # pragma: no cover - trivial formatting
        return (
            f"SecretLease(lease_id={self.lease_id[:16]!r}, "
            f"credential_id={self.credential_id[:64]!r}, "
            f"digest={self.digest[:16]!r}, scope={self.scope[:32]!r})"
        )

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        return self.__repr__()

    def to_dict(self) -> Dict[str, object]:
        """证据/快照投影(结构性无 material 字段)。"""
        return {
            "lease_id": self.lease_id[:32],
            "credential_id": self.credential_id[:64],
            "digest": self.digest[:16],
            "scope": self.scope[:32],
            "ttl_s": round(max(0.0, self.ttl_s), 3),
        }


@dataclass(frozen=True)
class LeaseAcquireResult:
    """acquire 结果(lease 或 typed miss;绝不抛)。"""

    lease: Optional[SecretLease]
    reason_code: str = LEASE_OK

    @property
    def ok(self) -> bool:
        return self.lease is not None


class EnvSecretProvider:
    """hermetic env 适配器(默认 provider)。

    ``GIS_TOOL_SECRET_<ID>``(id 非字母数字 → ``_`` 后大写)声明 material;
    未声明返回 None。值面只被本类持有,绝不进任何投影。
    """

    def __init__(self, env: Optional[Dict[str, str]] = None):
        self._env = env  # None = 真 os.environ(测试注入 dict)

    def resolve_material(self, credential_id: str) -> Optional[str]:
        cid = str(credential_id or "").strip()[:_STR_MAX]
        if not cid:
            return None
        var = SECRET_ENV_PREFIX + "".join(
            ch if ch.isalnum() else "_" for ch in cid
        ).upper()
        try:
            raw = (self._env or os.environ).get(var, "")
        except Exception:  # noqa: BLE001 — 读取失败 = typed miss
            return None
        return raw or None


class InMemorySecretProvider:
    """测试/hermetic fixture 用内存 provider。"""

    def __init__(self, materials: Optional[Dict[str, str]] = None):
        self._materials: Dict[str, str] = dict(materials or {})

    def set(self, credential_id: str, material: str) -> None:
        self._materials[str(credential_id)] = material

    def remove(self, credential_id: str) -> None:  # 凭证 revoke 故障注入面
        self._materials.pop(str(credential_id), None)

    def resolve_material(self, credential_id: str) -> Optional[str]:
        return self._materials.get(str(credential_id or "")) or None


class SecretLeaseManager:
    """进程内 lease 台账(线程安全;clock 注入;活跃面 LRU 有界)。"""

    def __init__(
        self,
        provider: Optional[SecretProvider] = None,
        *,
        clock=time.monotonic,
        max_active: int = MAX_ACTIVE_LEASES,
    ):
        self._provider = provider
        self._clock = clock
        self._max_active = max(1, int(max_active))
        self._lock = threading.Lock()
        self._leases: Dict[str, SecretLease] = {}
        self._materials: Dict[str, str] = {}
        self._seq = 0

    # ── provider 注入 ────────────────────────────────────────────────
    def set_provider(self, provider: Optional[SecretProvider]) -> None:
        self._provider = provider

    # ── acquire / resolve / revoke ───────────────────────────────────
    def acquire(
        self,
        credential_id: str,
        *,
        scope: str = "",
        ttl_s: float = DEFAULT_LEASE_TTL_S,
    ) -> LeaseAcquireResult:
        """按 id 取短期租约;未配置/读取失败 = typed miss(绝不抛)。

        同一 (credential, scope) 的重复 acquire 产新 lease(旧 lease 到期
        自然失效);ttl 钳到 (0, 3600] —— 短期租约语义,不给长持句柄。
        """
        cid = str(credential_id or "").strip()[:_STR_MAX]
        if not cid:
            return LeaseAcquireResult(None, LEASE_CREDENTIAL_MISSING)
        provider = self._provider
        if provider is None:
            # 零配置部署:presence-only 世界不变,typed miss 直通。
            return LeaseAcquireResult(None, LEASE_CREDENTIAL_MISSING)
        try:
            material = provider.resolve_material(cid)
        except Exception:  # noqa: BLE001 — provider 故障 = typed miss
            return LeaseAcquireResult(None, LEASE_PROVIDER_FAILED)
        if not material:
            return LeaseAcquireResult(None, LEASE_CREDENTIAL_MISSING)
        ttl = min(3600.0, max(0.001, float(ttl_s)))
        now = self._clock()
        with self._lock:
            self._seq += 1
            lease = SecretLease(
                lease_id=f"lease-{self._seq:08d}",
                credential_id=cid,
                digest=secret_digest(material),
                scope=str(scope or "")[:32],
                expires_at=now + ttl,
                ttl_s=ttl,
            )
            self._leases[lease.lease_id] = lease
            self._materials[lease.lease_id] = material
            while len(self._leases) > self._max_active:
                oldest = next(iter(self._leases))
                self._leases.pop(oldest, None)
                self._materials.pop(oldest, None)
        return LeaseAcquireResult(lease, LEASE_OK)

    def resolve(self, lease: SecretLease) -> Optional[str]:
        """按 lease 取 material(expired/unknown → None;绝不抛、绝不记日志)。"""
        if not isinstance(lease, SecretLease) or not lease.lease_id:
            return None
        with self._lock:
            known = self._leases.get(lease.lease_id)
            material = self._materials.get(lease.lease_id)
        if known is None or material is None:
            return None
        if known.digest != lease.digest or known.credential_id != lease.credential_id:
            return None  # 句柄与台账不一致(伪造/串号)→ 拒绝
        if self._clock() >= known.expires_at:
            return None  # 过期:调用方应重新 acquire
        return material

    def revoke(self, lease_id: str) -> bool:
        """主动吊销(凭证 rotate / 作用域结束;幂等)。"""
        with self._lock:
            existed = self._leases.pop(str(lease_id or ""), None)
            self._materials.pop(str(lease_id or ""), None)
        return existed is not None

    def sweep_expired(self) -> int:
        """清过期台账(返回清理数;低频调用面)。"""
        now = self._clock()
        swept = 0
        with self._lock:
            for lid in list(self._leases.keys()):
                if self._leases[lid].expires_at <= now:
                    self._leases.pop(lid, None)
                    self._materials.pop(lid, None)
                    swept += 1
        return swept

    def active_leases(self) -> Tuple[SecretLease, ...]:
        with self._lock:
            return tuple(self._leases.values())


# ── 进程单例 + 作用域 ────────────────────────────────────────────────────

_PROVIDER_LOCK = threading.Lock()
_MANAGER: Optional[SecretLeaseManager] = None

_current_leases: ContextVar[Tuple[SecretLease, ...]] = ContextVar(
    "current_secret_leases", default=()
)


def _default_manager() -> SecretLeaseManager:
    """默认 manager:provider 来自进程级注入(缺省 env adapter)。"""
    return SecretLeaseManager(EnvSecretProvider())


def get_secret_lease_manager() -> SecretLeaseManager:
    global _MANAGER
    with _PROVIDER_LOCK:
        if _MANAGER is None:
            _MANAGER = _default_manager()
        return _MANAGER


def set_secret_lease_manager(manager: Optional[SecretLeaseManager]) -> None:
    """注入进程级 manager(测试缝;None = 回落默认)。"""
    global _MANAGER
    with _PROVIDER_LOCK:
        _MANAGER = manager


def set_secret_provider(provider: Optional[SecretProvider]) -> None:
    """注入进程级 secret provider(唯一注入点;None = 回落 env adapter)。"""
    get_secret_lease_manager().set_provider(provider)


def current_secret_leases() -> Tuple[SecretLease, ...]:
    """当前作用域已授予的 lease(只读;无 material)。"""
    return _current_leases.get()


def bind_secret_leases(leases: Tuple[SecretLease, ...]):
    """作用域内挂载 lease(返回 contextmanager;与 present_tool_credentials 同构)。"""
    from contextlib import contextmanager

    @contextmanager
    def _scope():
        token = _current_leases.set(tuple(leases))
        try:
            yield tuple(leases)
        finally:
            _current_leases.reset(token)

    return _scope()


def resolve_secret_lease(credential_id: str) -> Optional[str]:
    """工具取用面:当前作用域内该凭证的 material(无 lease/过期 → None)。

    消费纪律:返回值只允许被工具直接用于其自身安全通道(请求头/连接串),
    绝不进日志/trace/SSE/evidence/返回 payload —— 调用方违约由既有 redaction
    scrub 兜底 + 审计追责;本函数自身零日志零投影。
    """
    for lease in _current_leases.get():
        if lease.credential_id == str(credential_id or "").strip()[:_STR_MAX]:
            return get_secret_lease_manager().resolve(lease)
    return None
