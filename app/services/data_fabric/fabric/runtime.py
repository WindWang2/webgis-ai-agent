"""FabricRuntime V8（ADR-0130）：联邦数据面的**单一生产解析路径**。

V7（ADR-0120）把治理面（ConnectionRegistry / CapabilityProbeService /
SourceFactsService / FabricFeedbackStore）交付为库 + 测试，但生产工具路径
仍经 legacy ``connection_manager`` 直接解析 adapter —— 治理元数据（作用域、
revision、健康、secret 分离）对生产流量不可见。V8 收口：

- ``resolve()``：registry 优先 → legacy 会话回退（首次命中即**注册进
  registry**，治理视图统一，不做双份真相的新写入方）→ DB 注册源按需
  attach（此前 DB 注册源必须先经 REST connect 进会话才能使用）。
  adapter 构建永远走 ``AdapterRegistry`` 单一工厂链，本模块不新增工厂。
- ``enrich()``：把治理面数据（探测能力 / 持久事实 / 反馈修正因子）拉平成
  **纯数据提示**注入规划请求 —— 规划器保持纯函数（V6 契约），全部 IO
  收敛在 runtime 层。任何治理数据缺失都 fail-open：宁可少优化，绝不阻断
  执行（V7 诚实回退文化）。
- ``describe_source()``：EXPLAIN/诊断视图（endpoint_ref 而非明文 URL；
  永不含 secret）。

线程模型：全部底层服务自带锁；本模块无状态（方法可并发）。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_GLOBAL_SCOPE_KEY = "org:_|owner:_|proj:_"


@dataclass
class ResolvedSource:
    """一次解析的结果（adapter + 治理元数据快照；不含明文凭证）。"""

    adapter: Any
    profile_id: str
    source_type: str = ""
    scope_key: str = _GLOBAL_SCOPE_KEY
    #: ConnectionRegistry revision（"" = 未经 registry 治理的遗留路径）。
    revision: str = ""
    #: probed | default | stale（None = 未探测，EXPLAIN 如实缺省）。
    caps_basis: Optional[str] = None
    #: 探测覆盖（仅与静态矩阵不同的字段）；None = 无探测记录。
    caps_overrides: Optional[Dict[str, Any]] = None
    #: 持久 SourceFacts 行数事实（None = 无事实）。
    facts_row_count: Optional[int] = None
    facts_row_count_basis: str = "unknown"
    facts_ndv: Dict[str, int] = field(default_factory=dict)
    #: 反馈衰减修正因子（None = 样本不足/关闭）。
    feedback_factor: Optional[float] = None
    feedback_samples: int = 0
    feedback_basis: str = "insufficient_samples"

    @property
    def governed(self) -> bool:
        return bool(self.revision)


def _setting(name: str, default):
    try:
        from app.core.config import settings

        return getattr(settings, name, default)
    except Exception:  # noqa: BLE001 - 配置层不可用时用默认值
        return default


def _tenant_scope(owner: Optional[str]):
    from app.services.data_fabric.fabric.connection_registry import TenantScope

    return TenantScope(owner=owner)


class FabricRuntime:
    """生产路径的 adapter 解析 + 治理数据富集入口（进程级单例见模块尾）。"""

    # ── 解析 ─────────────────────────────────────────────────────────

    def resolve(
        self,
        profile_id: Optional[str],
        *,
        owner: Optional[str] = None,
        db: Any = None,
        dataset_id: Optional[str] = None,
    ) -> Optional[ResolvedSource]:
        """解析一个数据源（registry 优先 → legacy 会话 → DB 按需 attach）。

        - ``profile_id`` 为空时尝试 ``dataset_id → profile_id``（catalog 本地
          读）；仍为空返回 None（调用方 typed 报错，行为与 V7 一致）。
        - registry 命中但 adapter 已被驱逐（record 在、adapter 为 None）→
          按 record 的 profile 重建（工厂链单一）。
        - legacy 会话命中 → 把 profile 注册进 registry（治理视图统一），
          复用既有 adapter（不重复构建、不触发二次 connect 副作用）。
        - DB 注册源（此前必须先 REST connect 才能用）→ ``registry.attach``
          按需构建 —— SSRF 门、secret 分离、revision 全部生效。
        """
        from app.services.data_fabric.fabric.connection_registry import (
            get_connection_registry,
        )

        pid = profile_id
        if not pid and dataset_id:
            pid = self._profile_id_for_dataset(dataset_id, owner=owner)
        if not pid:
            return None

        scope = _tenant_scope(owner)
        scope_key = scope.scope_key()
        registry = get_connection_registry()

        # 1) registry 精确域（含全局域回退语义 —— resolve 内部处理）。
        try:
            adapter = registry.resolve(str(pid), scope)
        except Exception:
            # ConnectionExpiredError = 过期条目（typed）；与 None 同样走
            # 回退链（DB 重建可能拿到更新后的 profile）。
            adapter = None
        record = registry.peek(str(pid), scope)
        if adapter is None and record is not None:
            # record 在、adapter 被 LRU 驱逐 → 按 redacted profile 重建
            # （secret 仅工厂构建瞬间注回；registry 契约）。
            adapter = registry.ensure_adapter(record)
        if adapter is not None:
            return self._wrap(
                adapter, profile_id=str(pid), source_type=record.source_type if record else "",
                scope_key=scope_key, revision=record.revision if record else "",
            )

        # 2) legacy 会话回退（包装式收敛：首次使用即注册进 registry）。
        legacy = self._legacy_adapter(str(pid), owner=owner)
        if legacy is not None:
            legacy_adapter, legacy_profile = legacy
            record = None
            if legacy_profile is not None:
                try:
                    registry.attach(
                        legacy_profile, scope, build_adapter=False,
                        prebuilt_adapter=legacy_adapter,
                    )
                    record = registry.peek(str(pid), scope)
                except Exception as exc:  # noqa: BLE001 - 治理注册失败不阻断执行
                    logger.debug(
                        "[runtime] legacy attach failed for %s: %s", pid, exc)
            return self._wrap(
                legacy_adapter, profile_id=str(pid),
                source_type=str(getattr(legacy_profile, "source_type", "") or ""),
                scope_key=scope_key,
                revision=record.revision if record else "",
            )
        # 3) DB 注册源按需 attach（owner 取 ds_model 的归属域 —— 行级权威）。
        if db is not None:
            resolved = self._resolve_from_db(str(pid), db)
            if resolved is not None:
                return resolved
        return None

    def attach_profile(
        self, profile: Any, *, owner: Optional[str] = None
    ) -> Optional[ResolvedSource]:
        """已知完整 profile → 注册进 registry（幂等）并返回解析视图。

        供已持有 profile 对象的调用方（``DataFabricManager._governed_adapter``
        从 DB 行重建后）使用 —— 不再经 profile_id 二次查 DB。attach 失败
        （SSRF 拒绝/工厂不支持等）原样抛出，由调用方按既有契约处理。
        """
        from app.services.data_fabric.fabric.connection_registry import (
            get_connection_registry,
        )

        scope = _tenant_scope(owner)
        scope_key = scope.scope_key()
        registry = get_connection_registry()
        try:
            adapter = registry.resolve(str(profile.id), scope)
        except Exception:
            adapter = None
        record = registry.peek(str(profile.id), scope)
        if adapter is None and record is not None:
            adapter = registry.ensure_adapter(record)
        if adapter is None:
            record, adapter = registry.attach(profile, scope)
        return self._wrap(
            adapter,
            profile_id=str(profile.id),
            source_type=str(getattr(profile, "source_type", "") or ""),
            scope_key=scope_key,
            revision=record.revision,
        )

    def attach_prebuilt(
        self, profile: Any, adapter: Any, *, owner: Optional[str] = None
    ) -> Optional[ResolvedSource]:
        """调用方已经工厂构建的 adapter → 注册进 registry（幂等；单构建）。

        与 ``attach_profile`` 的差别：构建发生在外部（保持 ``cls.get_adapter``
        工厂 seam 单一 —— 测试 monkeypatch 与子类定制不被 registry 内部工厂
        绕过）。best-effort 语义：attach 失败返回 None，不抛（调用方已持有
        可用 adapter，治理注册只是增益）。
        """
        from app.services.data_fabric.fabric.connection_registry import (
            get_connection_registry,
        )

        if adapter is None or profile is None:
            return None
        scope = _tenant_scope(owner)
        try:
            record, _ = get_connection_registry().attach(
                profile, scope, build_adapter=False, prebuilt_adapter=adapter
            )
        except Exception as exc:  # noqa: BLE001 - 治理注册失败不阻断执行
            logger.debug(
                "[runtime] attach_prebuilt failed for %s: %s", profile.id, exc
            )
            return None
        return self._wrap(
            adapter,
            profile_id=str(profile.id),
            source_type=str(getattr(profile, "source_type", "") or ""),
            scope_key=scope.scope_key(),
            revision=record.revision,
        )

    # ── 富集（探测 / 事实 / 反馈 → 纯数据提示）────────────────────────

    def enrich(
        self,
        resolved: ResolvedSource,
        *,
        fingerprint: Optional[str] = None,
        descriptor: Any = None,
        probe: bool = True,
    ) -> ResolvedSource:
        """就地补齐治理数据（全部 fail-open；返回同一对象便于链式）。

        - 探测：``CapabilityProbeService``（scoped TTL 缓存；失败回落默认
          矩阵 basis=default，绝不编造）。仅对有探测原语的源类型发起。
        - 事实：``SourceFactsService.get``（缓存 → durable → descriptor 现场采集；
          **plumb, not scrape** —— descriptor 缺席时不发起新扫描）。
        - 反馈：``FabricFeedbackStore.correction``（衰减加权因子；样本不足
          恒 1.0 不修正）。
        """
        if probe and resolved.governed:
            self._probe_into(resolved)
        if fingerprint:
            self._facts_into(resolved, fingerprint=fingerprint, descriptor=descriptor)
            self._feedback_into(resolved, fingerprint=fingerprint)
        return resolved

    def _probe_into(self, resolved: ResolvedSource) -> None:
        try:
            from app.services.data_fabric.fabric.probing import (
                get_capability_probe_service,
            )

            if not _has_probe_primitive(resolved.source_type):
                return
            profile = self._profile_stub(resolved)
            if profile is None:
                return
            svc = get_capability_probe_service()
            rec = svc.probe(
                resolved.adapter, profile, resolved.scope_key, resolved.revision
            )
            resolved.caps_basis = rec.caps_basis
            overrides = {
                k: v
                for k, v in _overrides_of(rec.caps, resolved.source_type)
            }
            resolved.caps_overrides = overrides or None
        except Exception as exc:  # noqa: BLE001 - 富集绝不阻断
            logger.debug("[runtime] probe enrich failed: %s", exc)

    def _facts_into(self, resolved: ResolvedSource, *, fingerprint: str, descriptor: Any) -> None:
        try:
            from app.services.data_fabric.fabric.source_facts import (
                get_source_facts_service,
            )

            svc = get_source_facts_service()
            rec = svc.get(
                scope_key=resolved.scope_key,
                fingerprint=str(fingerprint),
                descriptor=descriptor,
                profile_id=resolved.profile_id,
            )
            if rec is None:
                return
            resolved.facts_row_count = rec.row_count
            resolved.facts_row_count_basis = rec.row_count_basis
            resolved.facts_ndv = dict(rec.ndv or {})
        except Exception as exc:  # noqa: BLE001
            logger.debug("[runtime] facts enrich failed: %s", exc)

    def _feedback_into(self, resolved: ResolvedSource, *, fingerprint: str) -> None:
        try:
            from app.services.data_fabric.fabric.feedback import get_feedback_store

            store = get_feedback_store()
            if not store.enabled:
                return
            corr = store.correction(resolved.scope_key, str(fingerprint))
            if corr.get("basis") == "feedback_decayed":
                resolved.feedback_factor = float(corr.get("factor", 1.0))
                resolved.feedback_samples = int(corr.get("samples", 0))
                resolved.feedback_basis = str(corr.get("basis", "insufficient_samples"))
            else:
                resolved.feedback_basis = str(corr.get("basis", "insufficient_samples"))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[runtime] feedback enrich failed: %s", exc)

    # ── 诊断视图 ─────────────────────────────────────────────────────

    def describe_source(self, resolved: ResolvedSource) -> Dict[str, Any]:
        """EXPLAIN/诊断投影：endpoint_ref（redacted）+ 治理元数据，无 secret。"""
        out: Dict[str, Any] = {
            "profile_id": resolved.profile_id,
            "source_type": resolved.source_type,
            "scope_key": resolved.scope_key,
            "governed": resolved.governed,
        }
        if resolved.governed:
            from app.services.data_fabric.fabric.connection_registry import (
                get_connection_registry,
            )

            record = get_connection_registry().peek(
                resolved.profile_id, _tenant_scope_from_key(resolved.scope_key)
            )
            if record is not None:
                out["endpoint_ref"] = record.endpoint_ref
                out["revision"] = record.revision
                out["health"] = record.health
        return out

    # ── 内部 ─────────────────────────────────────────────────────────

    def _wrap(
        self,
        adapter: Any,
        *,
        profile_id: str,
        source_type: str,
        scope_key: str,
        revision: str,
    ) -> ResolvedSource:
        st = str(source_type or "").strip()
        if not st:
            st = str(getattr(adapter, "profile", None) and getattr(adapter.profile, "source_type", "") or "")
        return ResolvedSource(
            adapter=adapter,
            profile_id=str(profile_id),
            source_type=st,
            scope_key=scope_key,
            revision=str(revision or ""),
        )

    def _legacy_adapter(
        self, profile_id: str, *, owner: Optional[str]
    ) -> Optional[tuple]:
        """legacy ``connection_manager`` 会话查找（无网络；纯内存）。

        覆盖 legacy 两种可见性语义：owner 作用域 + 全局回退（get_adapter/
        get_profile 内部处理）；profile 在而 adapter 缺席（inspect 旧 miss
        路径）→ 工厂链补建 —— 运行时统一，调用方无需各写一份。
        """
        try:
            from app.services.data_fabric.connection_manager import connection_manager
        except Exception:  # noqa: BLE001
            return None
        adapter = connection_manager.get_adapter(profile_id, owner=owner)
        if adapter is not None:
            profile = connection_manager.get_profile(profile_id, owner=owner)
            if profile is None:
                profile = connection_manager.get_profile(profile_id)
            return adapter, profile
        # profile-only 条目（旧 inspect miss 路径语义）→ 补建 adapter。
        profile = connection_manager.get_profile(profile_id, owner=owner)
        if profile is None:
            profile = connection_manager.get_profile(profile_id)
        if profile is None:
            return None
        try:
            from app.services.data_fabric.registry import build_adapter

            return build_adapter(profile), profile
        except Exception:  # noqa: BLE001 - 补建失败按未解析处理
            return None

    def _resolve_from_db(self, profile_id: str, db: Any) -> Optional[ResolvedSource]:
        """DB 注册源 → registry.attach（SSRF/secret/revision 全治理）。"""
        try:
            from app.models.data_fabric import DataSourceModel
            from app.services.data_fabric.fabric.connection_registry import (
                get_connection_registry,
            )
            from app.services.data_fabric.manager import _profile_from_model

            ds_model = (
                db.query(DataSourceModel).filter(DataSourceModel.id == profile_id).first()
            )
            if ds_model is None:
                return None
            profile = _profile_from_model(ds_model)
            scope = _tenant_scope(ds_model.owner_id)
            registry = get_connection_registry()
            record, adapter = registry.attach(profile, scope)
            return self._wrap(
                adapter,
                profile_id=str(profile_id),
                source_type=str(profile.source_type or ds_model.source_type or ""),
                scope_key=scope.scope_key(),
                revision=record.revision,
            )
        except Exception as exc:  # noqa: BLE001 - DB 回退失败 → 上层 typed 报错
            logger.debug("[runtime] db resolve failed for %s: %s", profile_id, exc)
            return None

    def _rebuild_from_record(self, registry: Any, record: Any) -> Optional[Any]:
        """（已由 ``registry.ensure_adapter`` 取代；保留占位便于测试打点。）"""
        return registry.ensure_adapter(record)

    def _profile_id_for_dataset(self, dataset_id: str, *, owner: Optional[str]) -> Optional[str]:
        try:
            from app.services.data_fabric.spatial_catalog import spatial_catalog_service

            return spatial_catalog_service.get_profile_id(dataset_id, owner=owner)
        except Exception:  # noqa: BLE001
            return None

    def _profile_stub(self, resolved: ResolvedSource) -> Any:
        """探测服务需要的最小 profile 视图（id/source_type；无凭证）。"""
        from app.schemas.data_fabric_schema import ConnectionProfile

        try:
            return ConnectionProfile(
                id=resolved.profile_id,
                name=resolved.profile_id,
                source_type=resolved.source_type,
            )
        except Exception:  # noqa: BLE001
            return None


#: 有探测原语的源类型（probing._overrides_for 的覆盖面；其余类型探测只会
#: 回落默认矩阵 —— 不发无意义缓存条目）。
_PROBEABLE_TYPES = frozenset({"arcgis", "ogc_api", "stac"})


def _has_probe_primitive(source_type: str) -> bool:
    return str(source_type or "").strip().lower() in _PROBEABLE_TYPES


def _overrides_of(caps: Any, source_type: str) -> list:
    """探测后 caps 与静态默认矩阵的 diff（[(k, v), …]；供 planner 覆盖注入）。"""
    try:
        from app.services.data_fabric.query.capabilities import get_capabilities

        default = get_capabilities(str(source_type or ""))
        return [
            (k, getattr(caps, k))
            for k in default.model_dump()
            if getattr(caps, k, None) != getattr(default, k)
        ]
    except Exception:  # noqa: BLE001
        return []


def _tenant_scope_from_key(scope_key: str):
    """scope_key 反解（仅诊断路径；org 分量保留，owner/project percent-decode）。"""
    from app.services.data_fabric.fabric.connection_registry import TenantScope
    from urllib.parse import unquote

    org: Optional[int] = None
    owner: Optional[str] = None
    project: Optional[str] = None
    for part in str(scope_key).split("|"):
        if part.startswith("org:"):
            v = part[4:]
            org = int(v) if v.isdigit() else None
        elif part.startswith("owner:"):
            v = part[6:]
            owner = unquote(v) if v != "_" else None
        elif part.startswith("proj:"):
            v = part[5:]
            project = unquote(v) if v != "_" else None
    return TenantScope(org_id=org, owner=owner, project_id=project)


#: 进程级 runtime 单例（生产解析入口；测试用 ``reset_fabric_runtime`` 隔离）。
_runtime: Optional[FabricRuntime] = None
_runtime_lock = threading.Lock()


def get_fabric_runtime() -> FabricRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = FabricRuntime()
        return _runtime


def reset_fabric_runtime() -> None:
    """测试隔离用（生产路径禁止调用）。"""
    global _runtime
    with _runtime_lock:
        _runtime = None


__all__ = [
    "FabricRuntime",
    "ResolvedSource",
    "get_fabric_runtime",
    "reset_fabric_runtime",
]
