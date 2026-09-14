"""场景分支管理器（ADR-0193 §D1/D2）——What-If 推演分支的 fork/干预/回滚。

隔离模型（结构性保证，非约定式）：每个 ScenarioBranch 映射到一个**派生会话
id** ``{parent_sid}__wif_{branch_id}``，完整复用 ADR-0183 的 MapSpec 事务机器
—— 分支会话拥有独立的 MapSpec 文档、修订计数（CAS）、分布式锁、幂等去重、
auto checkpoint 与 provenance 环。因此任一分支上的修改/回滚**不可能**影响
另一分支或父会话（不同 Redis hash key、不同磁盘目录、不同锁作用域）。

- fork = 父权威 MapSpec 深拷贝 materialize 到分支会话（快照物化，不走
  intent 分发），分支 map_state 落 ``_whatif_fork`` 存证；
- 注册表 = 父会话 map_state 键 ``_whatif_branches``（写入持父会话锁）；
- 分支干预一律经 ``apply_gis_mutation(branch_sid, origin="agent",
  actor="whatif")`` —— user-wins 守卫/事务/检查点全量继承；
- 删除 = ``clear_session(branch_sid)``（内存 + Redis + 磁盘联动回收）+
  注册表移除（内存释放机制，见 review/AGENT-09-REVIEW.md）。

防重复施工：单次型 What-If 评估在 ``app/tools/what_if_simulate.py``（不动）；
发布谱系 fork 在 ``map_product_service.fork_version``（ADR-0099，语义不互通）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.services.distributed_lock import session_lock_registry
from app.services.gis_world_state import apply_gis_mutation, build_world_state
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    MapSpecResult,
    MutationIntent,
    RollbackIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.session_data import session_data_manager

from .prescriptive_advisor import BranchDiffResult, PrescriptiveAdvisor, ScenarioComparison
from .spatial_diff_engine import (
    COVERAGE_KEY,
    DEFAULT_SERVICE_RADIUS_M,
    GeometryDiff,
    build_diff_overlay,
    compute_metric_deltas,
    cost_proxy_of,
    diff_layers,
    evaluate_metrics,
    extract_layer_payloads,
)

logger = logging.getLogger(__name__)

#: 活跃分支上限（有界：防 Redis/磁盘随分支数放大）。
MAX_ACTIVE_BRANCHES = 5
#: 注册表键（父会话 map_state）。
REGISTRY_KEY = "_whatif_branches"
#: fork 存证键（分支会话 map_state）。
FORK_EVIDENCE_KEY = "_whatif_fork"
#: 分支 id 词表（文件系统安全段名；checkpoint_id 同款纪律）。
BRANCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

_FINGERPRINT_CACHE_TTL_S = 0.0  # 预留：指纹无缓存（每次 fork 一次 O(spec)）


class BranchError(Exception):
    """分支管理错误（机器可读 code + 中文消息；绝不静默）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"[{self.code}] {self.message}"


class ScenarioBranchMeta(BaseModel):
    """分支元数据（注册表条目；不含任何 payload）。"""

    branch_id: str
    title: str = ""
    hypothesis: str = ""
    parent_session_id: str
    branch_session_id: str
    fork_revision: int
    forked_at: str
    baseline_fingerprint: str
    status: str = "active"
    intervention_count: int = 0
    last_revision: int = 0
    last_intervention_summary: str = ""


def branch_session_id(parent_session_id: str, branch_id: str) -> str:
    """派生会话 id（纯函数）：``{parent}__wif_{branch}``。

    分隔符 ``__wif_``：Windows 文件名安全（无 ``:``/``/``），且能通过
    store 层会话目录名校验（``[A-Za-z0-9._-]`` 词表）。
    """
    if not isinstance(parent_session_id, str) or not parent_session_id:
        raise BranchError("parent_session_invalid", "父会话 id 非法")
    if not isinstance(branch_id, str) or not BRANCH_ID_PATTERN.match(branch_id or ""):
        raise BranchError(
            "branch_id_invalid",
            f"branch_id 必须匹配 {BRANCH_ID_PATTERN.pattern}，收到 {branch_id!r}",
        )
    return f"{parent_session_id}__wif_{branch_id}"


def fingerprint_mapspec(mapspec: Dict[str, Any]) -> str:
    """spec canonical SHA256 指纹（与 store._fingerprint_sync 同口径）。"""
    payload = json.dumps(
        mapspec, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _latest_checkpoint_id(session_id: str) -> Optional[str]:
    """扫描分支会话最近一个 checkpoint（mtime 最新；无 → None）。"""
    ckpt_root = BASE_STORAGE_DIR / session_id / "checkpoints"
    if not ckpt_root.is_dir():
        return None
    best: Optional[tuple[float, str]] = None
    for entry in ckpt_root.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, entry.name)
    return best[1] if best else None


class ScenarioBranchManager:
    """场景分支管理器：fork / 干预 / 回滚 / 注册表 / 对比 / 释放。"""

    def __init__(self, engine: Optional[MapSpecLifecycleEngine] = None) -> None:
        self._engine = engine
        self._advisor = PrescriptiveAdvisor()

    # ── 注册表 ────────────────────────────────────────────────────────

    async def _load_registry(self, parent_session_id: str) -> Dict[str, Any]:
        state = await session_data_manager.get_map_state(parent_session_id)
        registry = state.get(REGISTRY_KEY) if isinstance(state, dict) else None
        return dict(registry) if isinstance(registry, dict) else {}

    async def _save_registry(
        self, parent_session_id: str, registry: Dict[str, Any]
    ) -> None:
        await session_data_manager.set_map_state(
            parent_session_id, REGISTRY_KEY, registry
        )

    async def list_branches(self, parent_session_id: str) -> List[ScenarioBranchMeta]:
        registry = await self._load_registry(parent_session_id)
        return [
            ScenarioBranchMeta.model_validate(entry)
            for entry in registry.values()
            if isinstance(entry, dict)
        ]

    async def get_branch_meta(
        self, parent_session_id: str, branch_id: str
    ) -> ScenarioBranchMeta:
        registry = await self._load_registry(parent_session_id)
        entry = registry.get(branch_id)
        if not isinstance(entry, dict):
            raise BranchError(
                "branch_not_found", f"分支 {branch_id!r} 不存在（会话 {parent_session_id}）"
            )
        return ScenarioBranchMeta.model_validate(entry)

    async def _refresh_meta_revision(self, meta: ScenarioBranchMeta) -> ScenarioBranchMeta:
        state = await session_data_manager.get_map_state(meta.branch_session_id)
        try:
            meta.last_revision = int(
                state.get("_cartographic_mutation_revision", meta.last_revision) or 0
            )
        except (TypeError, ValueError):
            pass
        return meta

    # ── fork ──────────────────────────────────────────────────────────

    async def create_branch(
        self,
        parent_session_id: str,
        branch_id: str,
        title: str = "",
        hypothesis: str = "",
        engine: Optional[MapSpecLifecycleEngine] = None,
    ) -> ScenarioBranchMeta:
        """从父会话当前权威 spec 分叉分支（fork_revision 锚定现状）。"""
        branch_sid = branch_session_id(parent_session_id, branch_id)
        async with session_lock_registry.lock(parent_session_id):
            registry = await self._load_registry(parent_session_id)
            if branch_id in registry:
                raise BranchError(
                    "branch_exists", f"分支 {branch_id!r} 已存在（先 delete_branch 或改名）"
                )
            active_count = sum(
                1
                for entry in registry.values()
                if isinstance(entry, dict) and entry.get("status", "active") == "active"
            )
            if active_count >= MAX_ACTIVE_BRANCHES:
                raise BranchError(
                    "branch_limit",
                    f"活跃分支已达上限 {MAX_ACTIVE_BRANCHES}（先归档/删除闲置分支）",
                )
            spec = await mapspec_store_instance.get_mapspec(parent_session_id)
            if not isinstance(spec, dict) or not spec:
                raise BranchError(
                    "baseline_missing",
                    f"父会话 {parent_session_id} 无权威 MapSpec，无 Baseline 可分叉",
                )
            fingerprint = fingerprint_mapspec(spec)
            parent_state = await session_data_manager.get_map_state(parent_session_id)
            try:
                fork_revision = int(
                    parent_state.get("_cartographic_mutation_revision", 0) or 0
                )
            except (TypeError, ValueError):
                fork_revision = 0
            meta = ScenarioBranchMeta(
                branch_id=branch_id,
                title=title or f"方案 {branch_id}",
                hypothesis=hypothesis,
                parent_session_id=parent_session_id,
                branch_session_id=branch_sid,
                fork_revision=fork_revision,
                forked_at=_utc_now_iso(),
                baseline_fingerprint=fingerprint,
                last_revision=1,
            )
            # 快照物化：深拷贝防两命名空间共享可变引用（COW 边界）。
            save_res = await mapspec_store_instance.save_mapspec(
                branch_sid, copy.deepcopy(spec), mutation_revision=1
            )
            if not save_res.get("mapspec"):
                raise BranchError("fork_persist_failed", "分支快照物化失败")
            await session_data_manager.set_map_state(
                branch_sid,
                FORK_EVIDENCE_KEY,
                {
                    "branch_id": branch_id,
                    "parent_session_id": parent_session_id,
                    "fork_revision": fork_revision,
                    "baseline_fingerprint": fingerprint,
                    "forked_at": meta.forked_at,
                },
            )
            # 显式清零分支 provenance 环（fork 不是 agent mutation，环从空开始）
            await session_data_manager.set_map_state(branch_sid, "_gis_provenance", [])
            registry[branch_id] = meta.model_dump()
            await self._save_registry(parent_session_id, registry)
            return meta

    # ── 干预 / 回滚 ───────────────────────────────────────────────────

    async def apply_intervention(
        self,
        parent_session_id: str,
        branch_id: str,
        intents: List[MutationIntent],
        *,
        actor: str = "whatif",
        reason: Optional[str] = None,
    ) -> List[MapSpecResult]:
        """对分支施加干预序列（逐 intent 全事务；单条失败不回滚已成功项）。"""
        meta = await self.get_branch_meta(parent_session_id, branch_id)
        results: List[MapSpecResult] = []
        applied_kinds: List[str] = []
        for intent in intents:
            result = await apply_gis_mutation(
                meta.branch_session_id,
                intent,
                origin="agent",
                actor=actor,
                engine=self._engine,
                reason=reason,
            )
            results.append(result)
            if not result.is_error and not result.superseded:
                applied_kinds.append(type(intent).__name__)
        if applied_kinds or results:
            registry = await self._load_registry(parent_session_id)
            entry = registry.get(branch_id)
            if isinstance(entry, dict):
                meta = ScenarioBranchMeta.model_validate(entry)
                meta.intervention_count += len(applied_kinds)
                meta.last_intervention_summary = (
                    ", ".join(applied_kinds) if applied_kinds else meta.last_intervention_summary
                )
                await self._refresh_meta_revision(meta)
                entry.update(meta.model_dump())
                await self._save_registry(parent_session_id, registry)
        return results

    async def rollback_branch(
        self,
        parent_session_id: str,
        branch_id: str,
        checkpoint_id: Optional[str] = None,
    ) -> MapSpecResult:
        """回滚分支到指定 checkpoint（None = 分支最近一个 auto checkpoint）。"""
        meta = await self.get_branch_meta(parent_session_id, branch_id)
        target = checkpoint_id or _latest_checkpoint_id(meta.branch_session_id)
        if not target:
            raise BranchError(
                "checkpoint_missing", f"分支 {branch_id} 没有可回滚的 checkpoint"
            )
        result = await apply_gis_mutation(
            meta.branch_session_id,
            RollbackIntent(checkpoint_id=target),
            origin="agent",
            actor="whatif",
            engine=self._engine,
            reason=f"whatif rollback branch {branch_id} -> {target}",
        )
        registry = await self._load_registry(parent_session_id)
        entry = registry.get(branch_id)
        if isinstance(entry, dict) and not result.is_error:
            meta = ScenarioBranchMeta.model_validate(entry)
            await self._refresh_meta_revision(meta)
            entry.update(meta.model_dump())
            await self._save_registry(parent_session_id, registry)
        return result

    # ── 感知 ──────────────────────────────────────────────────────────

    async def get_branch_world_state(
        self, parent_session_id: str, branch_id: str
    ) -> Dict[str, Any]:
        """分支世界状态快照（GISWorldState 投影 + 分支元信息头）。"""
        meta = await self.get_branch_meta(parent_session_id, branch_id)
        state = await build_world_state(meta.branch_session_id)
        state["whatif_branch"] = meta.model_dump()
        return state

    # ── 释放 ──────────────────────────────────────────────────────────

    async def delete_branch(self, parent_session_id: str, branch_id: str) -> bool:
        """删除分支：clear_session 联动内存/Redis/磁盘回收 + 注册表移除。

        幂等：分支不存在返回 False（不抛）。返回 True = 状态已释放。
        """
        registry = await self._load_registry(parent_session_id)
        entry = registry.get(branch_id)
        if not isinstance(entry, dict):
            return False
        meta = ScenarioBranchMeta.model_validate(entry)
        await session_data_manager.clear_session(meta.branch_session_id)
        registry.pop(branch_id, None)
        await self._save_registry(parent_session_id, registry)
        return True

    # ── 多方案对比 ────────────────────────────────────────────────────

    async def compare_branches(
        self,
        parent_session_id: str,
        branch_ids: List[str],
        *,
        optimization_goals: Optional[Dict[str, str]] = None,
        service_radius_m: float = DEFAULT_SERVICE_RADIUS_M,
    ) -> ScenarioComparison:
        """逐分支 diff against 父会话当前 spec（对比口径 = 比较时刻的现状）。

        披露：``baseline_revision`` 记录比较锚点；fork 时点之后父会话若继续
        演进，diff 反映的是**当前**基线（用户对比的是现状，不是历史快照）。
        """
        if not branch_ids:
            raise BranchError("no_branches", "至少提供一个待比较分支")
        base_spec = await mapspec_store_instance.get_mapspec(parent_session_id)
        if not isinstance(base_spec, dict) or not base_spec:
            raise BranchError(
                "baseline_missing", f"父会话 {parent_session_id} 无权威 MapSpec 可作 Baseline"
            )
        parent_state = await session_data_manager.get_map_state(parent_session_id)
        try:
            baseline_revision = int(
                parent_state.get("_cartographic_mutation_revision", 0) or 0
            )
        except (TypeError, ValueError):
            baseline_revision = 0
        baseline_fingerprint = fingerprint_mapspec(base_spec)
        base_payloads = extract_layer_payloads(base_spec)
        baseline_metrics = evaluate_metrics(base_payloads, service_radius_m)

        branch_results: List[BranchDiffResult] = []
        for branch_id in branch_ids:
            meta = await self.get_branch_meta(parent_session_id, branch_id)
            spec = await mapspec_store_instance.get_mapspec(meta.branch_session_id)
            if not isinstance(spec, dict) or not spec:
                raise BranchError(
                    "branch_state_missing", f"分支 {branch_id} 的 MapSpec 缺失（已被清除？）"
                )
            branch_payloads = extract_layer_payloads(spec)
            diffs = diff_layers(
                base_payloads, branch_payloads, service_radius_m=service_radius_m
            )
            deltas = compute_metric_deltas(
                base_payloads, branch_payloads, service_radius_m=service_radius_m
            )
            overlay = build_diff_overlay(diffs)
            geometry_summary: Dict[str, Any] = {
                layer_id: value.summary()
                for layer_id, value in diffs.items()
                if isinstance(value, GeometryDiff)
            }
            if COVERAGE_KEY in diffs:
                geometry_summary[COVERAGE_KEY] = diffs[COVERAGE_KEY]
            branch_results.append(
                BranchDiffResult(
                    branch_id=branch_id,
                    title=meta.title,
                    hypothesis=meta.hypothesis,
                    metric_deltas=deltas,
                    geometry_summary=geometry_summary,
                    overlay_features=overlay.get("features", []),
                    cost_proxy=cost_proxy_of(diffs),
                )
            )

        comparison = self._advisor.build_comparison(
            baseline_metrics=baseline_metrics,
            branches=branch_results,
            optimization_goals=optimization_goals,
            parent_session_id=parent_session_id,
            baseline_revision=baseline_revision,
        )
        comparison.baseline_fingerprint = baseline_fingerprint
        return comparison
