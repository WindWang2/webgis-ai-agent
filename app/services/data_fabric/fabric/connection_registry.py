"""Connection Registry V7（ADR-0119 W1-2）：连接的生产治理层。

职责（Epic 03 Must-have A）：
- **作用域**：``TenantScope``（org/owner/project）键控；不同 owner 互不可见；
  显式全局域（全部组件 None = legacy 语义）保留且对所有会话可见（V5 文化），
  但全局域连接**禁止**进入结果缓存（跨会话串结果风险归零，R-M2）。
- **revision**：content-addressed —— 对 redacted profile + secret_ref 做
  sha256（R-M1：不修改共享 DB 表即获得跨进程一致的修订语义；进程内 CAS 用
  锁内比较）。
- **secret 分离**：凭证进 ``SecretStore``（接口可替换 Redis/KMS；进程内实现
  TTL+容量双界）；record 只存 opaque ``secret_ref``；hash 输入永不含明文。
- **健康状态机**：unknown → healthy/degraded/unreachable/expired；探测走
  既有 breaker；过期连接 typed ``ConnectionExpiredError``（懒惰判定）。
- **生命周期**：条目上限 + idle TTL 双界 LRU；驱逐时 best-effort 释放
  adapter；``sweep()`` 显式清扫 + 访问路径懒惰清扫。

包装而非替换 ``DataFabricConnectionManager``（其 dict 存储与工具语义位级
保留）：registry 负责治理元数据与生命周期，adapter 构建仍走
``connection_manager`` 同一工厂链（AdapterRegistry 单一真相不变）。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from pydantic import BaseModel, Field

from app.services.data_fabric.errors import DataFabricError

logger = logging.getLogger(__name__)

#: ConnectionRecord.health 的合法状态（显式状态机，不做自由字符串）。
HEALTH_UNKNOWN = "unknown"
HEALTH_HEALTHY = "healthy"
HEALTH_DEGRADED = "degraded"
HEALTH_UNREACHABLE = "unreachable"
HEALTH_EXPIRED = "expired"
_VALID_HEALTH = frozenset(
    {HEALTH_UNKNOWN, HEALTH_HEALTHY, HEALTH_DEGRADED, HEALTH_UNREACHABLE, HEALTH_EXPIRED}
)

#: 全局域 scope_key（org/owner/project 全 None —— legacy 兼容语义）。
_GLOBAL_SCOPE_KEY = "org:_|owner:_|proj:_"


class ConnectionExpiredError(DataFabricError):
    code = "CONNECTION_EXPIRED"


class ScopeViolationError(DataFabricError):
    """跨作用域访问被拒（内部防御；正常路径返回 None 而非抛错）。"""

    code = "SCOPE_VIOLATION"


@dataclass(frozen=True)
class TenantScope:
    """租户作用域（org/owner/project 三元；None 分量记 "_" 无歧义拼接）。"""

    org_id: Optional[int] = None
    owner: Optional[str] = None
    project_id: Optional[str] = None

    def __post_init__(self) -> None:
        # 防 "_" 哨兵碰撞：显式拒绝以下划线哨兵为值的构造。
        for name in ("owner", "project_id"):
            v = getattr(self, name)
            if isinstance(v, str) and (v == "_" or v.strip() == "_"):
                raise ValueError(f"TenantScope.{name} must not be the '_' sentinel")

    @property
    def is_global(self) -> bool:
        return (
            self.org_id is None and self.owner is None and self.project_id is None
        )

    def scope_key(self) -> str:
        # R2-Mi-2：分量 percent-encode（含 "|" 与 "%"）—— 防止 owner/project
        # 值伪造分隔符构造越权 scope 键（cache/registry/feedback 共用此键）。
        from urllib.parse import quote

        def _enc(v) -> str:
            return "_" if v in (None, "") else quote(str(v), safe="")

        org = str(self.org_id) if self.org_id is not None else "_"
        return f"org:{org}|owner:{_enc(self.owner)}|proj:{_enc(self.project_id)}"


class SecretStore(Protocol):
    """Secret 分离 seam（R-M1）。实现必须：有界、TTL、ref 不可逆推明文。"""

    def put(self, secret: Dict[str, Any]) -> str: ...

    def get(self, ref: str) -> Optional[Dict[str, Any]]: ...

    def evict(self, ref: str) -> bool: ...


#: ConnectionProfile 中需要分离进 SecretStore 的顶层凭证键。
_SECRET_KEYS = ("password", "secret_key", "access_key", "session_token")
#: 嵌套 ``credentials`` dict 整体视为 secret。
_NESTED_SECRET_KEYS = ("credentials",)
#: 嵌套树（options 等）内的敏感键判定串（与 security.sanitize_profile_dict
#: 的 sensitive_keys 同集 —— 近形键连字符形态一并覆盖）。
_SENSITIVE_KEY_MATCHERS = (
    "password", "secret", "token", "api_key", "api-key", "apikey",
    "credential", "authorization", "auth", "passwd", "pwd", "private_key",
)


def extract_profile_secrets(profile_dict: Dict[str, Any]) -> Dict[str, Any]:
    """profile dict → (剩余明文无妨的 dict，被摘除的 secret 子 dict)。"""
    rest = dict(profile_dict)
    secret: Dict[str, Any] = {}
    for k in _SECRET_KEYS:
        v = rest.pop(k, None)
        if v:
            secret[k] = v
    for k in _NESTED_SECRET_KEYS:
        v = rest.pop(k, None)
        if isinstance(v, dict) and v:
            secret[k] = v
    return rest, secret


def _is_sensitive_key(key: str) -> bool:
    """与 security.sanitize_profile_dict 同一敏感键判定（近形键含连字符）。"""
    lowered = str(key).lower()
    return any(s in lowered for s in _SENSITIVE_KEY_MATCHERS)


def _move_sensitive_entries(node: Any, sink: Dict[str, Any]) -> None:
    """就地摘除 dict/list 树中的敏感键值进 sink（同路径；原位置置 None）。

    V8：``create_data_source`` 的凭证经 ``options`` 传入（其签名无顶层
    password 字段）—— 只摘顶层键会让 ``options.password`` 落进 record 的
    redacted_profile 明文面。摘除后 redacted_profile 构造上无凭证；重建时
    经 ``_merge_sensitive_entries`` 从 SecretStore 深合并回填（保真）。
    list 内的 dict 树同样遍历（review P2-2：``options.layers=[{"password":
    …}]`` 与 sanitize_profile_dict 语义对齐）；sink 以 str(index) 记路径。
    """
    if isinstance(node, dict):
        for k in list(node.keys()):
            v = node[k]
            if _is_sensitive_key(k):
                if v is not None:
                    sink[k] = v
                    node[k] = None
            elif isinstance(v, (dict, list)):
                child: Dict[str, Any] = {}
                _move_sensitive_entries(v, child)
                if child:
                    sink[k] = child
    elif isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, (dict, list)):
                child = {}
                _move_sensitive_entries(item, child)
                if child:
                    sink[str(i)] = child


def _merge_sensitive_entries(node: Any, sink: Dict[str, Any]) -> None:
    """``_move_sensitive_entries`` 的逆操作：sink 值按路径回填 None 槽位。"""
    if isinstance(sink, dict) and isinstance(node, dict):
        for k, v in sink.items():
            if isinstance(v, dict):
                child = node.get(k)
                if isinstance(child, list):
                    # 摘除时 sink 以 str(index) 记 list 路径 —— node 保持
                    # list 形态（绝不顶成 dict，否则重建产物走形）。
                    _merge_sensitive_entries(child, v)
                else:
                    if not isinstance(child, dict):
                        child = {}
                        node[k] = child
                    _merge_sensitive_entries(child, v)
            elif node.get(k) is None:
                node[k] = v
    elif isinstance(sink, dict) and isinstance(node, list):
        for k, v in sink.items():
            try:
                idx = int(k)
            except ValueError:  # noqa: BLE001 - 非法索引跳过（不抛）
                continue
            if 0 <= idx < len(node):
                _merge_sensitive_entries(node[idx], v)


class InMemorySecretStore:
    """进程内 secret 存储：TTL + 条目双界，ref = 随机句柄。

    边界（R-M1）：ref 不承载任何明文信息；``get`` 返回副本；相同 secret
    内容复用同一 ref（content-dedupe，内部键仅为寻址不外泄）—— 这使
    content-addressed revision 在重复注册下保持稳定。进程退出即失
    （durable secret provider 是显式 follow-up —— DataSourceModel 既有明文
    JSON 列保持不变，本文档披露 at-rest 加密为 hookable provider）。
    """

    def __init__(self, *, ttl_s: float = 3600.0, max_entries: int = 1024):
        import secrets as _secrets
        from collections import OrderedDict

        self._secrets = _secrets
        self._ttl = float(ttl_s)
        self._max = int(max_entries)
        self._entries: "OrderedDict[str, tuple[float, Dict[str, Any]]]" = OrderedDict()
        self._by_content: Dict[str, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _content_key(secret: Dict[str, Any]) -> str:
        payload = json.dumps(secret, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def put(self, secret: Dict[str, Any]) -> str:
        if not isinstance(secret, dict) or not secret:
            raise ValueError("secret must be a non-empty dict")
        now = time.monotonic()
        ckey = self._content_key(secret)
        with self._lock:
            existing = self._by_content.get(ckey)
            if existing is not None and existing in self._entries:
                # 刷新 TTL 时刻 + LRU 位置（ref 稳定）—— 长活连接的幂等
                # attach 不会让 secret 惰性过期（R1-MINOR 10）。
                self._entries[existing] = (now, self._entries[existing][1])
                self._entries.move_to_end(existing)
                return existing
            ref = "sec_" + self._secrets.token_hex(12)
            self._entries[ref] = (now, dict(secret))
            self._by_content[ckey] = ref
            while len(self._entries) > self._max:
                _evicted_ref, (_ts, evicted_secret) = self._entries.popitem(last=False)
                self._by_content.pop(self._content_key(evicted_secret), None)
            return ref

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(ref)
            if entry is None:
                return None
            ts, secret = entry
            if now - ts > self._ttl:
                del self._entries[ref]
                self._by_content.pop(self._content_key(secret), None)
                return None
            self._entries.move_to_end(ref)
            return dict(secret)

    def evict(self, ref: str) -> bool:
        with self._lock:
            entry = self._entries.pop(ref, None)
            if entry is None:
                return False
            self._by_content.pop(self._content_key(entry[1]), None)
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


class ConnectionRecord(BaseModel):
    """一条受治理连接的元数据（**不含任何明文凭证**）。"""

    profile_id: str
    scope_key: str
    source_type: str
    #: redacted endpoint（egress 安全；sanitize/redact 沿 security.py）。
    endpoint_ref: str = ""
    #: SecretStore 句柄；None = 无凭证连接。
    secret_ref: Optional[str] = None
    #: content-addressed revision（redacted profile + secret_ref 的 sha256 前 16）。
    revision: str
    name: str = ""
    #: redacted profile 全量视图（V8：rehydrate 忠实重建的前提 —— 此前仅
    #: 存 endpoint 四字段，options 形态的源（PostGIS host/port、本地文件
    #: path）重建必失败）。构造上不含 secret：由 ``extract_profile_secrets``
    #: 摘除后的 rest 直接落位。
    redacted_profile: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    #: None = 不过期（由 idle TTL 驱逐兜底）。
    expires_at: Optional[float] = None
    health: str = HEALTH_UNKNOWN
    last_health_at: Optional[float] = None
    #: 惰性维护的最后访问时刻（LRU 驱逐依据）。
    last_access_at: float = Field(default_factory=time.monotonic)

    def is_expired(self, *, now: Optional[float] = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) >= self.expires_at


def profile_revision(redacted_profile: Dict[str, Any], secret_ref: Optional[str]) -> str:
    """content-addressed revision：redacted profile + secret_ref 的稳定哈希。

    输入**永不含明文 secret**（redacted 视图 + 不透明 ref），同连接重复注册
    产生稳定 revision；跨进程一致（R-M1）。
    """
    payload = json.dumps(
        {"profile": redacted_profile, "secret_ref": secret_ref},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class ConnectionRegistry:
    """连接注册表（治理元数据 + 生命周期）；adapter 构建委托既有 manager。

    线程模型：一把 RLock 保护元数据 dict；adapter 构建发生在锁外（远程
    probe/网络绝不持锁）。
    """

    def __init__(
        self,
        *,
        secret_store: Optional[SecretStore] = None,
        max_entries: Optional[int] = None,
        idle_ttl_s: Optional[float] = None,
    ):
        self._secret_store = secret_store or InMemorySecretStore()
        self._max_entries = int(
            max_entries
            if max_entries is not None
            else _setting("DATA_FABRIC_V7_CONNECTION_MAX_ENTRIES", 1024)
        )
        self._idle_ttl_s = float(
            idle_ttl_s
            if idle_ttl_s is not None
            else _setting("DATA_FABRIC_V7_CONNECTION_IDLE_TTL_S", 1800.0)
        )
        # (scope_key, profile_id) -> (record, adapter)
        self._entries: Dict[tuple, tuple] = {}
        self._lock = threading.RLock()

    # ── 注册 / 解析 ──────────────────────────────────────────────────

    def attach(
        self,
        profile: Any,
        scope: TenantScope,
        *,
        manager: Any = None,
        ttl_s: Optional[float] = None,
        build_adapter: bool = True,
        prebuilt_adapter: Any = None,
    ) -> tuple:
        """注册（或以 CAS 语义更新）一条连接，返回 ``(record, adapter|None)``。

        - SSRF 门沿用 connection_manager 同一校验（host/port 形态同样覆盖）；
        - 凭证摘除进 SecretStore，record 存 redacted 视图 + ref；
        - 已存在同 (scope, profile_id) 且 revision 相同 → 幂等返回（adapter
          复用）；revision 不同 → 原子替换（旧 secret 被逐出）；
        - ``prebuilt_adapter``（V8）：调用方已有 adapter 实例时直接注册它，
          不再经工厂二次构建（legacy manager 桥接路径单构建；与
          ``build_adapter=False`` 组合使用）。
        """
        from app.services.data_fabric.connection_manager import (
            _ssrf_validate_profile,
            create_adapter_for_profile,
        )
        from app.services.data_fabric.security import DataFabricSecurity

        _ssrf_validate_profile(profile)
        profile_dict = profile.model_dump()
        redacted, secret = extract_profile_secrets(profile_dict)
        # V8：url 字段的 userinfo 摘除（DSN 内嵌凭证不落 record —— 与
        # endpoint_ref 同一脱敏原语）。被摘除的原文进 SecretStore：重建时
        # 回填（否则 basic-auth URL 形态的源重建后静默无凭证），且仅
        # userinfo 不同的重复注册产生不同 secret_ref/revision（轮换可感知）。
        url_secrets: Dict[str, Any] = {}
        for _url_key in ("url", "endpoint", "endpoint_url"):
            _v = redacted.get(_url_key)
            if _v:
                _redacted = DataFabricSecurity.redact_url(_v)
                if _redacted != _v:
                    url_secrets[_url_key] = _v
                redacted[_url_key] = _redacted
        if url_secrets:
            secret["url_fields"] = url_secrets
        # V8：options 等嵌套树中的敏感键值摘入 SecretStore（REST 创建路径
        # 的凭证就在 options 里 —— 只摘顶层会明文落 record）。
        if isinstance(redacted.get("options"), dict) and redacted["options"]:
            opts_secret: Dict[str, Any] = {}
            _move_sensitive_entries(redacted["options"], opts_secret)
            if opts_secret:
                secret["options"] = opts_secret
        endpoint_ref = DataFabricSecurity.redact_url(
            profile_dict.get("url") or profile_dict.get("endpoint") or ""
        )
        secret_ref = self._secret_store.put(secret) if secret else None
        rev = profile_revision(redacted, secret_ref)

        key = (scope.scope_key(), str(profile.id))
        adapter = None
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                old_record, old_adapter = existing
                if old_record.revision == rev:
                    # content-dedupe 保证同内容 → 同 ref：无重复条目可逐。
                    old_record.last_access_at = time.monotonic()
                    if old_adapter is None and prebuilt_adapter is not None:
                        # V8：幂等命中但 adapter 已被驱逐 → 回填调用方实例。
                        old_adapter = prebuilt_adapter
                        self._entries[key] = (old_record, old_adapter)
                    return old_record, old_adapter
                # revision 变化：原子替换 + 旧 secret 逐出（仅当无其他
                # 条目仍引用 —— content-dedupe 共享 ref 的连坐防御，V8）。
                self._evict_locked(key, release_secret=False)
                if old_record.secret_ref and old_record.secret_ref != secret_ref:
                    still_referenced = any(
                        rec.secret_ref == old_record.secret_ref
                        for other_key, (rec, _a) in self._entries.items()
                        if other_key != key
                    )
                    if not still_referenced:
                        self._secret_store.evict(old_record.secret_ref)
            record = ConnectionRecord(
                profile_id=str(profile.id),
                scope_key=scope.scope_key(),
                source_type=str(profile.source_type or ""),
                endpoint_ref=endpoint_ref or "",
                secret_ref=secret_ref,
                revision=rev,
                name=str(profile.name or profile.id),
                redacted_profile=redacted,
                expires_at=(time.time() + ttl_s) if ttl_s else None,
            )
            self._entries[key] = (record, None)
        # 锁外构建 adapter（probe/网络）；调用方预构建实例直接注册（V8 ——
        # 含 build_adapter=False + prebuilt 组合：registry 只登记不构建）。
        if build_adapter or prebuilt_adapter is not None:
            if prebuilt_adapter is not None:
                adapter = prebuilt_adapter
            else:
                try:
                    adapter = create_adapter_for_profile(profile)
                except Exception:
                    # 构建失败不留半条目（仅回滚**本次**写入 —— revision 变化
                    # 说明并发方已替换，绝不误删他人条目，R1-MINOR 9）。
                    with self._lock:
                        current = self._entries.get(key)
                        if current is not None and current[0].revision == rev:
                            self._entries.pop(key, None)
                    if secret_ref:
                        self._secret_store.evict(secret_ref)
                    raise
            with self._lock:
                current = self._entries.get(key)
                if current is not None and current[0].revision == rev:
                    self._entries[key] = (current[0], adapter)
                self._enforce_capacity_locked()
        if manager is not None:
            # 兼容桥：既有 manager 的存储/目录同步语义保留（best-effort）。
            try:
                manager.connect(profile, owner=scope.owner)
            except Exception as exc:  # noqa: BLE001 - 治理层不阻断已成功的注册
                logger.warning(
                    "[ConnectionRegistry] legacy manager bridge failed for %s: %s",
                    profile.id,
                    exc,
                )
        return record, adapter

    def rehydrate_profile(self, record: ConnectionRecord) -> Dict[str, Any]:
        """redacted record → 完整 profile dict（仅 adapter 构建瞬间注回 secret）。

        V8：优先 ``record.redacted_profile``（attach 时的全量无凭证视图 ——
        options/allow_private 等结构化字段保真）；legacy 最小四字段形状仅作
        旧记录回退。secret 最后合并（顶层凭证字段优先级与原 profile 一致）。
        """
        if record.redacted_profile:
            rest: Dict[str, Any] = json.loads(
                json.dumps(record.redacted_profile, default=str)
            )
        else:
            rest = json.loads(
                json.dumps(
                    {
                        "id": record.profile_id,
                        "name": record.name,
                        "source_type": record.source_type,
                        "url": record.endpoint_ref,
                    }
                )
            )
        if record.secret_ref:
            secret = self._secret_store.get(record.secret_ref)
            if secret:
                opts_secret = secret.pop("options", None)
                url_fields = secret.pop("url_fields", None)
                rest.update(secret)
                # V8：被 userinfo 摘除的 url 原文回填（重建后凭证完整）。
                if isinstance(url_fields, dict):
                    rest.update(url_fields)
                # V8：options 内敏感键值按路径深合并回填（与摘除配对）。
                if isinstance(opts_secret, dict) and opts_secret:
                    opts = rest.setdefault("options", {})
                    if not isinstance(opts, dict):
                        opts = {}
                        rest["options"] = opts
                    _merge_sensitive_entries(opts, opts_secret)
        return rest

    def ensure_adapter(self, record: ConnectionRecord) -> Optional[Any]:
        """record → adapter（丢失时按 redacted profile 重建并回填条目）。

        V8 生产解析路径（fabric/runtime.py）使用：LRU 驱逐或 legacy 桥接
        注册后 adapter 引用为 None 的条目可由此恢复。重建失败返回 None
        （调用方走 DB/legacy 回退），**绝不**让治理层重建失败升级为查询
        错误 —— 条目原样保留供诊断。
        """
        key = (record.scope_key, record.profile_id)
        with self._lock:
            current = self._entries.get(key)
            if current is None:
                return None  # 条目已消失：调用方走 DB/legacy 回退
            if current[0].revision != record.revision:
                return None  # 并发替换：以条目内最新 record 为准
            if current[0].is_expired():
                # V8（review P2）：过期连接不复活（resolve 语义一致 ——
                # 过期条目逐出 adapter；此处同样拒绝重建回填）。
                current[0].health = HEALTH_EXPIRED
                self._entries[key] = (current[0], None)
                return None
            if current[1] is not None:
                current[0].last_access_at = time.monotonic()
                return current[1]
        try:
            from app.schemas.data_fabric_schema import ConnectionProfile
            from app.services.data_fabric.registry import build_adapter

            profile = ConnectionProfile(**self.rehydrate_profile(record))
            adapter = build_adapter(profile)
        except Exception as exc:  # noqa: BLE001 - 重建失败不升级为查询错误
            logger.warning(
                "[ConnectionRegistry] adapter rebuild failed for %s: %s",
                record.profile_id,
                exc,
            )
            return None
        with self._lock:
            current = self._entries.get(key)
            if current is not None and current[0].revision == record.revision:
                current[0].last_access_at = time.monotonic()
                self._entries[key] = (current[0], adapter)
                return adapter
            # 并发方已替换/驱逐：不回填旧 revision 的 adapter（新鲜条目
            # 自带自己的构建路径）。
            return None

    def resolve(
        self,
        profile_id: str,
        scope: TenantScope,
        *,
        allow_global_fallback: bool = True,
    ) -> Any:
        """解析作用域内 adapter；过期/不可达按语义处理。

        - 作用域精确命中 → 命中；
        - 未命中且 allow_global_fallback 且 scope 非全局 → 显式全局域回退
          （V5 legacy 可见性；全局条目调用方禁止进结果缓存，R-M2）；
        - 过期条目：标 ``expired`` + 逐出 adapter，抛 ``ConnectionExpiredError``
          （调用方可捕获降级；None=从未注册）。
        """
        key = (scope.scope_key(), str(profile_id))
        now_mono = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            fallback = None
            if entry is None and allow_global_fallback and not scope.is_global:
                fallback = self._entries.get((_GLOBAL_SCOPE_KEY, str(profile_id)))
            target = entry or fallback
            if target is None:
                return None
            record, adapter = target
            record.last_access_at = now_mono
            if record.is_expired():
                record.health = HEALTH_EXPIRED
                target_key = key if entry is not None else (_GLOBAL_SCOPE_KEY, str(profile_id))
                self._entries[target_key] = (record, None)  # 丢弃 adapter 引用
                raise ConnectionExpiredError(
                    f"connection '{profile_id}' expired",
                    details={"profile_id": profile_id, "scope": scope.scope_key()},
                )
            return adapter

    def peek(self, profile_id: str, scope: TenantScope) -> Optional[ConnectionRecord]:
        """只读元数据（不触碰 LRU/健康；EXPLAIN/诊断用）。"""
        with self._lock:
            entry = self._entries.get((scope.scope_key(), str(profile_id)))
            return entry[0] if entry else None

    # ── 健康 / 修订 / 生命周期 ────────────────────────────────────────

    def record_health(self, profile_id: str, scope: TenantScope, health: str) -> bool:
        if health not in _VALID_HEALTH:
            raise ValueError(f"invalid health state {health!r}")
        with self._lock:
            entry = self._entries.get((scope.scope_key(), str(profile_id)))
            if entry is None:
                return False
            record = entry[0]
            record.health = health
            record.last_health_at = time.time()
            return True

    def update(
        self,
        profile: Any,
        scope: TenantScope,
        *,
        expected_revision: str,
    ) -> tuple:
        """CAS 更新：``expected_revision`` 不匹配抛 ``ScopeViolationError``
        （复用内部防御错误族；details 携带 expected/actual）。"""
        key = (scope.scope_key(), str(profile.id))
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                raise ScopeViolationError(
                    f"connection '{profile.id}' not registered in scope",
                    details={"scope": scope.scope_key()},
                )
            if entry[0].revision != expected_revision:
                raise ScopeViolationError(
                    "revision conflict",
                    details={
                        "expected": expected_revision,
                        "actual": entry[0].revision,
                    },
                )
        return self.attach(profile, scope)

    def revoke(self, profile_id: str, scope: TenantScope) -> bool:
        with self._lock:
            return self._evict_locked((scope.scope_key(), str(profile_id)))

    def sweep(self, *, now: Optional[float] = None) -> int:
        """显式清扫：过期 + idle 超限条目（驱逐 adapter 引用 + secret）。"""
        now_t = time.time() if now is None else now
        now_mono = time.monotonic()
        removed = 0
        with self._lock:
            for key in list(self._entries.keys()):
                record, _adapter = self._entries[key]
                idle_over = (
                    self._idle_ttl_s > 0
                    and (now_mono - record.last_access_at) > self._idle_ttl_s
                )
                if record.is_expired(now=now_t) or idle_over:
                    removed += self._evict_locked(key)
        return removed

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "max_entries": self._max_entries,
            }

    def clear(self) -> None:
        with self._lock:
            for key in list(self._entries.keys()):
                self._evict_locked(key)

    # ── 内部 ─────────────────────────────────────────────────────────

    def _evict_locked(self, key: tuple, *, release_secret: bool = True) -> bool:
        entry = self._entries.pop(key, None)
        if entry is None:
            return False
        record, _adapter = entry
        if release_secret and record.secret_ref:
            # V8（review P2）：content-dedupe 使多个 record 可共享同一
            # secret_ref —— 仅当无其他条目引用时才逐出，避免驱逐 A 连坐 B
            # （B 的重建将静默无凭证）。
            still_referenced = any(
                rec.secret_ref == record.secret_ref
                for other_key, (rec, _a) in self._entries.items()
                if other_key != key
            )
            if not still_referenced:
                try:
                    self._secret_store.evict(record.secret_ref)
                except Exception:  # noqa: BLE001 - 驱逐路径绝不抛
                    pass
        return True

    def _enforce_capacity_locked(self) -> None:
        while len(self._entries) > self._max_entries:
            # LRU：逐出 last_access_at 最旧（确定性 tie-break by key）。
            victim = min(
                self._entries.items(),
                key=lambda kv: (kv[1][0].last_access_at, kv[0]),
            )[0]
            self._evict_locked(victim)


def _setting(name: str, default):
    """延迟读取 settings（未登记配置回落默认 —— 测试/嵌入式部署可用）。"""
    try:
        from app.core.config import settings

        return getattr(settings, name, default)
    except Exception:  # noqa: BLE001 - 配置层不可用时用默认值
        return default


#: 进程级 registry 单例（工具联邦路径的 adapter 治理入口）。
_registry: Optional[ConnectionRegistry] = None
_registry_lock = threading.Lock()


def get_connection_registry() -> ConnectionRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = ConnectionRegistry()
        return _registry


def reset_connection_registry() -> None:
    """测试隔离用：丢弃进程级单例（生产路径禁止调用）。"""
    global _registry
    with _registry_lock:
        _registry = None
