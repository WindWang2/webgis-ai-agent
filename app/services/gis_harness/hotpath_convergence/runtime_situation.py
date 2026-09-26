"""RuntimeSituation — dispatch 期资格上下文的生产供给（F06, ADR-0215 候选）.

#1482 把 capability bind 移到 dispatch 单点后，``situation`` 形参在全部 4 条
调用面（Pi / legacy pipeline / legacy engine / workflow step）都传 None ——
bind 只能在 bare context 下运行，缺席面不裁决。本模块补上生产构造器：

- 从 **有可靠来源的事实** 组装有界 typed snapshot：runtime context
  （turn/run/request id）、hotpath session ctx（mission/tenant）、凭证
  presence（:mod:`app.lib.tool_security`）、运行时可用性探针（broker /
  durable worker）、显式 offline 开关。
- **断言纪律**：无来源的面（auth_tier / budget / quality_gate / 数据事实）
  保持 unknown —— 不猜。默认部署零配置 ⇒ 产出 situation 与 bare context
  的 bind 裁决逐位一致；凭证/权限声明或运维开关到位后闸自动生效。
- 供给接缝复用 :mod:`app.services.gis_harness.hotpath_convergence.
  capability_bind` 的白名单 dict —— 不新建第二套资格消费面。

Kill-switch：``GIS_SITUATION_SUPPLY``（默认 ON）。构造任何异常 → ``None``
（bare-context 语义兜底），本模块绝不成为第二 dispatch 故障面。
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from app.lib.tool_security import (
    CredentialPresence,
    credentials_present_map,
    resolve_credential_presence,
)

SITUATION_SUPPLY_ENV = "GIS_SITUATION_SUPPLY"
OFFLINE_ENV = "GIS_RUNTIME_OFFLINE"
#: durable worker 探针的进程级开关（默认 OFF —— DB I/O 面显式启用）。
WORKER_PROBE_ENV = "GIS_SITUATION_PROBE_WORKERS"

#: 事实来源 provenance 词表（封闭；证据面披露用）。
SRC_RUNTIME_CONTEXT = "runtime_context"
SRC_SESSION_CTX = "session_ctx"
SRC_SECURITY = "security_bridge"
SRC_AVAILABILITY = "availability_probe"
SRC_CALLER = "caller"

SUPPLY_POLICY_VERSION = "runtime_situation_supply.v1"

_STR_MAX = 64
#: runtime_availability / credentials_present 进入资格上下文的有界上限
#:（与 QualificationContext 消费面一致，超额截断）。
_MAX_DEP_KEYS = 8
_MAX_CRED_KEYS = 8

#: 可用性探针 TTL（秒）—— dispatch 热路径零重复探针。TTL 命中即零成
_AVAILABILITY_TTL_S = 15.0
_AVAILABILITY_CACHE_MAX = 16
#: 注：TTL 冷未命中的并发探测不去重（无 single-flight）；探针本体单
#: 次有界查询，重复成本可忽略 —— 诚实注释优先于伪承诺。

_WORKER_PROBE_LOCK = threading.Lock()
#: {probe_key: (expires_at_monotonic, value)}
_AVAILABILITY_CACHE: Dict[str, tuple] = {}

#: worker 可用性探针注入点（测试/扩展替换；None = 默认 DB 探针）。
WorkerProbe = Callable[[], Optional[bool]]
_worker_probe: Optional[WorkerProbe] = None


def set_worker_availability_probe(probe: Optional[WorkerProbe]) -> None:
    """注入 durable worker 探针（返回 True/False/None=unknown）。"""
    global _worker_probe
    with _WORKER_PROBE_LOCK:
        _worker_probe = probe


def _env_truthy(name: str, default: str) -> bool:
    raw = (os.environ.get(name) or default).strip().lower()
    return raw not in ("0", "false", "off", "no")


def situation_supply_enabled() -> bool:
    return _env_truthy(SITUATION_SUPPLY_ENV, "1")


def _env_tristate(name: str) -> Optional[bool]:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


def _cached_availability(key: str, fn: Callable[[], Optional[bool]]) -> Optional[bool]:
    """TTL 缓存的可用性探针（None = unknown，不入缓存）。"""
    now = time.monotonic()
    with _WORKER_PROBE_LOCK:
        hit = _AVAILABILITY_CACHE.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
    try:
        value = fn()
    except Exception:  # noqa: BLE001 — 探针失败 = unknown
        return None
    if value is None:
        return None
    with _WORKER_PROBE_LOCK:
        if len(_AVAILABILITY_CACHE) >= _AVAILABILITY_CACHE_MAX:
            drop = list(_AVAILABILITY_CACHE.keys())[:1]
            for k in drop:
                _AVAILABILITY_CACHE.pop(k, None)
        _AVAILABILITY_CACHE[key] = (now + _AVAILABILITY_TTL_S, value)
    return value


def _cached_availability_readonly(key: str) -> Optional[bool]:
    with _WORKER_PROBE_LOCK:
        hit = _AVAILABILITY_CACHE.get(key)
    return hit[1] if (hit is not None and hit[0] > time.monotonic()) else None


def _default_worker_probe() -> Optional[bool]:
    """durable worker 在场探针（只读 WorkflowWorkerRow；查询异常 → None）。

    注意与 cluster.py 内部「查询失败按无 worker」不同：资格面把
    「查询失败」与「确认无 worker」分开 —— 前者 unknown（不断言），
    后者才 False。事件循环线程上**绝不发起 DB I/O**：只读 TTL 缓存
    （:func:`build_runtime_situation_async` 的线程路径负责回填）。
    """
    try:
        import asyncio

        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return _cached_availability_readonly("durable_worker")
    try:
        from app.services.workflow_runtime.cluster import get_worker_registry

        registry = get_worker_registry()
        rows = registry.list_active(limit=1)
        return bool(rows)
    except Exception:  # noqa: BLE001 — 探针缺席/失败 → unknown
        return None


def celery_broker_available() -> Optional[bool]:
    """broker 配置事实（settings/env 读，零 I/O；未知配置 → None）。"""
    try:
        from app.core.config import settings

        return bool(getattr(settings, "USE_REDIS", False))
    except Exception:  # noqa: BLE001
        return None


@dataclass
class RuntimeSituation:
    """dispatch 期资格事实的有界快照（全部字段截断；无 secret 材料）。"""

    session_id: str = ""
    turn_id: str = ""
    run_id: str = ""
    request_id: str = ""
    tenant_id: str = ""
    mission_id: str = ""
    owner_scope_key: str = ""
    #: 凭证 presence（id→bool，≤8）；perm:<p> 条目复用同一消费契约。
    credentials_present: Dict[str, bool] = field(default_factory=dict)
    #: 运行时可用性（键 ≤8）：``celery_broker`` / ``durable_worker`` …
    #: 独立命名空间 —— 不与 dependency_available（#1402 权限门的「已声明
    #: 授予面」消费语义）混用（review P1）。
    runtime_availability: Dict[str, bool] = field(default_factory=dict)
    #: 有界截断披露（例如已授予权限超投影上限时诚实标注）。
    truncations: Dict[str, int] = field(default_factory=dict)
    #: None = 未观察（诚实 unknown）。
    offline: Optional[bool] = None
    #: 凭证元数据（presence 投影，≤8 条；无 secret）。
    credential_metadata: Dict[str, Dict[str, str]] = field(default_factory=dict)
    #: 事实组 provenance（封闭词表）。
    fact_sources: Dict[str, str] = field(default_factory=dict)
    policy_version: str = SUPPLY_POLICY_VERSION

    def to_qualification_dict(self) -> Dict[str, Any]:
        """capability_bind 白名单 dict 投影（缺席面不产出 → unknown）。"""
        out: Dict[str, Any] = {}
        if self.owner_scope_key:
            out["owner_scope_key"] = self.owner_scope_key
        if self.credentials_present:
            out["credentials_present"] = dict(
                list(self.credentials_present.items())[:_MAX_CRED_KEYS])
        if self.runtime_availability:
            out["runtime_availability"] = dict(
                list(self.runtime_availability.items())[:_MAX_DEP_KEYS])
        if self.offline is not None:
            out["offline"] = self.offline
        return out

    def to_bounded_view(self) -> Dict[str, Any]:
        """证据/日志投影（id 级，无参数无凭证材料）。"""
        return {
            "session_id": self.session_id[:_STR_MAX],
            "turn_id": self.turn_id[:_STR_MAX],
            "mission_id": self.mission_id[:_STR_MAX],
            "tenant_id": self.tenant_id[:_STR_MAX],
            "credential_ids": sorted(self.credentials_present.keys())[:_MAX_CRED_KEYS],
            "availability_keys": sorted(self.runtime_availability.keys())[:_MAX_DEP_KEYS],
            "truncations": dict(self.truncations),
            "offline": self.offline,
            "fact_sources": dict(self.fact_sources),
            "policy_version": self.policy_version,
        }

    def facts_digest(self) -> str:
        """资格事实的稳定指纹（planner↔dispatch 等价性断言键）。"""
        return situation_facts_digest(self.to_qualification_dict())


def situation_facts_digest(facts: Any) -> str:
    """资格事实 dict / QualificationContext 的稳定指纹（等价性断言键）。"""
    import hashlib
    import json

    if facts is None:
        facts = {}
    if isinstance(facts, RuntimeSituation):
        facts = facts.to_qualification_dict()
    else:
        try:
            from app.services.gis_harness.qualification_v8 import (
                QualificationContext,
            )

            if isinstance(facts, QualificationContext):
                facts = {
                    "owner_scope_key": facts.owner_scope_key,
                    "credentials_present": dict(facts.credentials_present),
                    "runtime_availability": dict(facts.runtime_availability),
                    "offline": facts.offline,
                }
                # 与 RuntimeSituation.to_qualification_dict 同形：空面不产出。
                if not facts["owner_scope_key"]:
                    facts.pop("owner_scope_key")
                if not facts["credentials_present"]:
                    facts.pop("credentials_present")
                if not facts["runtime_availability"]:
                    facts.pop("runtime_availability")
        except Exception:  # noqa: BLE001 — 投影失败按原值序列化
            pass
    payload = json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _collect_identity(session_id: str, tenant_id: str) -> Dict[str, str]:
    identity: Dict[str, str] = {
        "session_id": str(session_id or "")[:_STR_MAX],
        "tenant_id": str(tenant_id or "")[:_STR_MAX],
    }
    try:
        from app.lib.runtime.context import current_runtime_context

        ctx = current_runtime_context()
        if ctx is not None:
            identity["turn_id"] = str(getattr(ctx, "turn_id", "") or "")[:_STR_MAX]
            identity["run_id"] = str(getattr(ctx, "run_id", "") or "")[:_STR_MAX]
            identity["request_id"] = str(
                getattr(ctx, "request_id", "") or "")[:_STR_MAX]
            identity["session_id"] = str(
                getattr(ctx, "session_id", "") or identity["session_id"]
            )[:_STR_MAX]
    except Exception:  # noqa: BLE001 — 身份面缺席不阻断
        pass
    try:
        from app.services.gis_harness.hotpath_convergence.session_ctx import (
            get_turn_context,
        )

        hot = get_turn_context(session_id, tenant_id=tenant_id)
        identity["mission_id"] = str(getattr(hot, "mission_id", "") or "")[:_STR_MAX]
    except Exception:  # noqa: BLE001
        pass
    return identity


def _collect_security_facts() -> tuple:
    """凭证 presence + 权限 presence（仅 ContextVar **实际授予**面）。

    返回 ``(cred_map, metadata, truncations)``。

    权限投影纪律（user-wins）：只有 tier3/plan-approved 显式授予流写入
    的 ``granted_permissions()`` 才投影为 ``perm:<p>``（qualification 的
    权限消费契约）；env 部署声明（``GIS_TOOL_PERMISSIONS``）是「可授予
    什么」的披露面，**绝不**等同「已授予」—— 否则资格面判 eligible 而
    registry 闸 deny，两套裁决互相矛盾。
    """
    presences: Dict[str, CredentialPresence] = resolve_credential_presence()
    cred_map = credentials_present_map(presences)

    granted = set()
    try:
        from app.tools.registry import granted_permissions

        granted |= {str(p) for p in granted_permissions() if p}
    except Exception:  # noqa: BLE001
        pass
    granted_sorted = sorted(granted)
    for perm in granted_sorted[:_MAX_CRED_KEYS]:
        cred_map[f"perm:{perm}"] = True
    truncations: Dict[str, int] = {}
    if len(granted_sorted) > _MAX_CRED_KEYS:
        # review P3：截断诚实披露（进 bounded view，不静默）。
        truncations["granted_permissions"] = len(granted_sorted)

    metadata = {
        cid: p.to_dict()
        for cid, p in list(sorted(presences.items()))[:_MAX_CRED_KEYS]
    }
    if len(presences) > _MAX_CRED_KEYS:
        truncations["credential_metadata"] = len(presences)
    return cred_map, metadata, truncations


def _collect_availability(worker_probe: Optional[WorkerProbe] = None) -> Dict[str, bool]:
    deps: Dict[str, bool] = {}
    broker = celery_broker_available()
    if broker is not None:
        deps["celery_broker"] = broker
    probe_enabled = _env_truthy(WORKER_PROBE_ENV, "0")
    if probe_enabled or _worker_probe is not None:
        with _WORKER_PROBE_LOCK:
            injected = _worker_probe
        value = _cached_availability(
            "durable_worker", injected or _default_worker_probe)
        if value is not None:
            deps["durable_worker"] = value
    return deps


def build_runtime_situation(
    session_id: str,
    *,
    tenant_id: str = "",
    owner_scope_key: str = "",
) -> Optional[RuntimeSituation]:
    """组装 dispatch 期资格事实快照（kill switch / 异常 → None 兜底）。

    worker 探针启用时本函数含 DB I/O —— 事件循环上应经
    :func:`build_runtime_situation_async`（线程执行）调用。
    """
    if not situation_supply_enabled():
        return None
    try:
        identity = _collect_identity(session_id, tenant_id)
        cred_map, cred_meta, truncations = _collect_security_facts()
        deps = _collect_availability()
        offline = _env_tristate(OFFLINE_ENV)
        situation = RuntimeSituation(
            session_id=identity.get("session_id", ""),
            turn_id=identity.get("turn_id", ""),
            run_id=identity.get("run_id", ""),
            request_id=identity.get("request_id", ""),
            tenant_id=identity.get("tenant_id", ""),
            mission_id=identity.get("mission_id", ""),
            owner_scope_key=str(owner_scope_key or "")[:_STR_MAX],
            credentials_present=cred_map,
            runtime_availability=deps,
            truncations=truncations,
            offline=offline,
            credential_metadata=cred_meta,
            fact_sources={
                "identity": SRC_RUNTIME_CONTEXT,
                "mission": SRC_SESSION_CTX,
                "security": SRC_SECURITY,
                "availability": SRC_AVAILABILITY,
            },
        )
        return situation
    except Exception:  # noqa: BLE001 — 供给面绝不阻断 dispatch
        return None


async def build_runtime_situation_async(
    session_id: str,
    *,
    tenant_id: str = "",
    owner_scope_key: str = "",
) -> Optional[RuntimeSituation]:
    """事件循环安全变体：整个构造（含 worker 探针）放线程执行。

    TTL 缓存命中时为纯内存操作；冷探针受 ``GIS_SITUATION_PROBE_WORKERS``
    门控，未启用时本函数恒为纯内存读。
    """
    if not situation_supply_enabled():
        return None
    try:
        import asyncio

        return await asyncio.to_thread(
            build_runtime_situation, session_id,
            tenant_id=tenant_id, owner_scope_key=owner_scope_key,
        )
    except Exception:  # noqa: BLE001 — 供给面绝不阻断 dispatch
        return None


def merge_situation_facts(base: Any, session_id: str) -> Any:
    """caller situation ⊕ 运行时事实（caller 数据事实 win；缺席面补齐）。

    - ``base is None``：纯运行时快照的白名单 dict（可能为空 dict —— 供给
      关闭/零事实时与 bare context 语义一致）。
    - ``base`` 为 dict：浅拷贝 + 补齐 caller 未设置的 runtime 面。
    - ``base`` 为 QualificationContext：经 :func:`build_situation` 复制后
      补齐缺席面（不覆盖 caller 已断言的事实）。

    事件循环上请用 :func:`merge_situation_facts_async`（探针线程执行）。
    """
    runtime = build_runtime_situation(session_id)
    return _merge_with_runtime(base, runtime)


async def merge_situation_facts_async(base: Any, session_id: str) -> Any:
    """事件循环安全变体：runtime 构造（含探针）放线程，合并语义相同。"""
    runtime = await build_runtime_situation_async(session_id)
    return _merge_with_runtime(base, runtime)


def _merge_with_runtime(base: Any, runtime: Optional[RuntimeSituation]) -> Any:
    runtime_facts = runtime.to_qualification_dict() if runtime else {}
    if base is None:
        return runtime_facts
    if isinstance(base, dict):
        merged = dict(base)
        for key in ("credentials_present", "runtime_availability"):
            if not merged.get(key) and runtime_facts.get(key):
                merged[key] = runtime_facts[key]
        if merged.get("offline") is None and runtime_facts.get("offline") is not None:
            merged["offline"] = runtime_facts["offline"]
        if not merged.get("owner_scope_key") and runtime_facts.get("owner_scope_key"):
            merged["owner_scope_key"] = runtime_facts["owner_scope_key"]
        return merged
    try:
        from dataclasses import replace

        from app.services.gis_harness.qualification_v8 import QualificationContext

        if isinstance(base, QualificationContext):
            # 复制后补齐（caller 对象不被就地修改 —— 上游可能复用）。
            patched = replace(base)
            if not patched.credentials_present and runtime_facts.get(
                    "credentials_present"):
                patched.credentials_present = dict(
                    runtime_facts["credentials_present"])
            if not patched.runtime_availability and runtime_facts.get(
                    "runtime_availability"):
                patched.runtime_availability = dict(
                    runtime_facts["runtime_availability"])
            if patched.offline is None and runtime_facts.get("offline") is not None:
                patched.offline = runtime_facts["offline"]
            if not patched.owner_scope_key and runtime_facts.get("owner_scope_key"):
                patched.owner_scope_key = runtime_facts["owner_scope_key"]
            return patched
        return base
    except Exception:  # noqa: BLE001 — 合并失败退回 caller 原值
        return base


def reset_runtime_situation_cache() -> None:
    """测试隔离 helper（清可用性 TTL 缓存）。"""
    with _WORKER_PROBE_LOCK:
        _AVAILABILITY_CACHE.clear()


__all__ = [
    "SITUATION_SUPPLY_ENV",
    "OFFLINE_ENV",
    "WORKER_PROBE_ENV",
    "SUPPLY_POLICY_VERSION",
    "RuntimeSituation",
    "situation_supply_enabled",
    "build_runtime_situation",
    "build_runtime_situation_async",
    "merge_situation_facts",
    "merge_situation_facts_async",
    "situation_facts_digest",
    "celery_broker_available",
    "set_worker_availability_probe",
    "reset_runtime_situation_cache",
]
