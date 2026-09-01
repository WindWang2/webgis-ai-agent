"""MapSpecLifecycleEngine - 核心 MapSpec 意图声明与生命周期引擎。

深入封装 MapSpec 意图变迁 (InitProject, SetView, UpsertLayer, RemoveLayer, SetLayout)、
自动 Spatial Profiling、Pre-compile Structure Validation、Redis map_state 双写与 Checkpoint 物理快照。

可靠性契约（REL-06 / REL-07）：
- 事务语义：先在内存构建 candidate，校验通过后才落盘。引入新的 blocking 校验
  错误的 mutation 被拒绝，last-known-good 不被污染。
- 落盘顺序：checkpoint → save_mapspec(disk+redis mapspec) → 同步 redis layers。
  任一步失败 → rollback 恢复旧 mapspec + layers，返回 is_error。杜绝半提交。
- process_layer_ingestion（GeoJSON profiling / raster PNG）经 asyncio.to_thread
  卸载，不阻塞 event loop（大 inline GeoJSON 不再冻结所有 session 的 I/O）。
"""
import asyncio
import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

MutationOrigin = Literal["agent", "user", "system"]

from app.services.session_data import session_data_manager
from app.services.mapspec.store import mapspec_store_instance, _should_remove_layer
from app.services.mapspec.pipeline import process_layer_ingestion
from app.services.mapspec.coordinator import validate as validate_mapspec
from app.lib.cartography.quality_loop import (
    cartographic_fingerprint,
    review_and_repair_cartography,
)
from app.services.mapspec.checkpoint import (
    snapshot as create_checkpoint,
    rollback as rollback_checkpoint,
    discard_checkpoint,
)
from app.services.distributed_lock import session_lock_registry

logger = logging.getLogger(__name__)

# Blocking 校验错误码：这些代表 mutation 引入的真实语义缺陷（引用不存在的
# source、stops 不足/非单调）。引入此类错误的 mutation 必须被拒绝。
# MISSING_SOURCES 不在其中：空 source 集合是"项目尚未成熟"的基线状态（如
# InitProject / 仅有 view 的会话），不是某次 mutation 的缺陷，保留为 warning。
BLOCKING_VALIDATION_CODES = {
    "INVALID_SOURCE_REF",
    "INVALID_STOPS_COUNT",
    "NON_INCREASING_STOPS",
}


@dataclass
class MapSpecResult:
    """MapSpec 意图变迁统一结果 Domain 值对象"""
    mapspec: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    is_compiled: bool = False
    checkpoint_id: Optional[str] = None
    ref_count: int = 0
    is_error: bool = False
    error_msg: str = ""
    correction_hint: str = ""
    # ADR-0078: deterministic cartography-semantic findings (paint ↔ legend
    # equivalence, cardinality, domain coverage, no-data, …). Structural
    # validity (is_compiled) ≠ thematic correctness — these findings are the
    # evidence the Harness surfaces so "structurally valid but legend/paint
    # drift" is detectable. Checks needing a source profile are NOT_EVALUATED.
    cartography_findings: List[Dict[str, Any]] = field(default_factory=list)
    # Desired-state cartographic review is intentionally separate from
    # ``is_compiled``. A structurally valid mutation may still have a failed or
    # not-evaluated quality review, and neither implies frontend convergence.
    cartographic_review: Optional[Dict[str, Any]] = None
    mapspec_fingerprint: Optional[str] = None
    # Latest frontend observation already present when this mutation began.
    # A runtime snapshot must carry a strictly newer sequence to certify it.
    runtime_observation_seq: int = 0
    # Monotonic session revision assigned while holding the distributed
    # lifecycle lock. Durable harness context uses it to reject late writes.
    mutation_revision: int = 0
    origin: Optional[MutationOrigin] = None
    # Stale expected_revision: not a validation error and not a commit.
    superseded: bool = False

    def to_dict(self) -> Dict[str, Any]:
        if self.superseded:
            res = {
                "success": False,
                "status": "superseded",
                "message": self.error_msg,
                "mutation_revision": self.mutation_revision,
                "mapspec": self.mapspec,
            }
            if self.origin is not None:
                res["origin"] = self.origin
            if self.correction_hint:
                res["correction_hint"] = self.correction_hint
            return res
        if self.is_error:
            res = {"success": False, "message": self.error_msg}
            if self.origin is not None:
                res["origin"] = self.origin
            if self.correction_hint:
                res["correction_hint"] = self.correction_hint
            return res
        res = {
            "success": True,
            "mapspec": self.mapspec,
            "warnings": self.warnings,
            "is_compiled": self.is_compiled,
            "checkpoint_id": self.checkpoint_id,
            "cartography_findings": self.cartography_findings,
            "cartographic_review": self.cartographic_review,
            "mapspec_fingerprint": self.mapspec_fingerprint,
            "runtime_observation_seq": self.runtime_observation_seq,
            "mutation_revision": self.mutation_revision,
        }
        if self.origin is not None:
            res["origin"] = self.origin
        return res


@dataclass
class BatchIntentOutcome:
    """GISMutationBatch 中单个 intent 的裁决（applied / refused / not_found）。"""
    layer_id: str
    status: str
    visible: Optional[bool] = None
    error_msg: Optional[str] = None


@dataclass
class MapSpecBatchResult:
    """GISMutationBatch 统一结果：一次锁/一次读/一次校验/一次 revision+1。

    v2(Phase 7)：finalize_display 等收口此前逐层 apply_gis_mutation ——
    N 层 = N 个完整事务（N 次锁循环 + N 次 checkpoint（每次物化全部 ref）
    + N 次 revision 递增 + N×4 次全量 parse），既是性能根因也是 409 风暴
    根因。batch 把 N 个 presentation patch 合并为一个事务；refused/
    not_found 的 intent 被跳过并逐项上报，不影响其余 intent 提交。
    """
    mapspec: Optional[Dict[str, Any]] = None
    outcomes: List[BatchIntentOutcome] = field(default_factory=list)
    applied_count: int = 0
    refused_count: int = 0
    not_found_count: int = 0
    mutation_revision: int = 0
    is_error: bool = False
    error_msg: str = ""
    correction_hint: str = ""
    superseded: bool = False
    origin: Optional[MutationOrigin] = None
    checkpoint_id: Optional[str] = None
    mapspec_fingerprint: Optional[str] = None
    cartographic_review: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def committed(self) -> bool:
        """至少一个 intent 落盘（revision 已递增）。"""
        return self.applied_count > 0 and not self.is_error and not self.superseded

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mapspec": self.mapspec,
            "outcomes": [
                {
                    "layer_id": o.layer_id,
                    "status": o.status,
                    "visible": o.visible,
                    "error_msg": o.error_msg,
                }
                for o in self.outcomes
            ],
            "applied_count": self.applied_count,
            "refused_count": self.refused_count,
            "not_found_count": self.not_found_count,
            "mutation_revision": self.mutation_revision,
            "is_error": self.is_error,
            "error_msg": self.error_msg,
            "correction_hint": self.correction_hint,
            "superseded": self.superseded,
            "committed": self.committed,
        }


# ─── Discriminated Intent Value Objects ──────────────────────────────────────


@dataclass
class InitProjectIntent:
    view: Optional[Dict[str, Any]] = None
    thresholds: Optional[Dict[str, Any]] = None


@dataclass
class SetViewIntent:
    center: Optional[List[float]] = None
    zoom: Optional[float] = None
    pitch: Optional[float] = None
    bearing: Optional[float] = None


@dataclass
class UpsertLayerIntent:
    layer: Dict[str, Any]
    source_data: Optional[Any] = None


@dataclass
class UpsertSourceIntent:
    source_id: str
    source: Dict[str, Any]


@dataclass
class RemoveLayerIntent:
    layer_id: str


@dataclass
class ReorderLayersIntent:
    layer_ids: List[str]


@dataclass
class SetLayoutIntent:
    legend: Optional[Dict[str, Any]] = None
    controls: Optional[List[Dict[str, Any]]] = None
    margins: Optional[Dict[str, Any]] = None
    # CartographyComponent 列表（app/services/gis_harness/components）。
    # live 渲染与 export 共用同一份组件描述；None = 不触碰既有组件。
    components: Optional[List[Dict[str, Any]]] = None


@dataclass
class CheckpointIntent:
    checkpoint_id: Optional[str] = None


@dataclass
class RollbackIntent:
    checkpoint_id: str


@dataclass
class PatchLayerStyleIntent:
    """#1077：spec 承载层的持久样式突变（paint 顶层键合并）。

    layer_style_update 此前只改 MapLibre 运行时 paint + HUD 行（不进
    committed MapSpec）—— 下一次同层 recompile 即回滚，「UI 已改色但
    地图随后复原」既是体验缺陷也是观察/修复环的噪声源。该意图把样式
    写入权威 spec；origin=agent 的工具路径与 origin=user 的面板路径
    共用（样式不属于 user-wins 守卫的 presentation 面）。
    """
    layer_id: str
    paint: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PatchLayerPresentationIntent:
    """User/agent chrome: visibility and opacity without re-ingesting data."""

    layer_id: str
    visible: Optional[bool] = None
    opacity: Optional[float] = None


@dataclass
class PatchComponentIntent:
    """Component-local mutation (UI drag/resize/collapse or agent chrome edit).

    与 SetLayoutIntent（整表替换）相对：只改命中的单个组件，其余组件不动。
    校验/突变逻辑复用 gis_harness.components.mutate_component —— 同一入口
    服务 user route 与 agent 工具，不出现第二套组件突变实现。
    """

    component_id: str
    component_type: Optional[str] = None
    enabled: Optional[bool] = None
    position: Optional[str] = None
    placement: Optional[Dict[str, Any]] = None
    variant: Optional[str] = None
    style: Optional[Dict[str, Any]] = None
    options: Optional[Dict[str, Any]] = None
    upsert: bool = False


@dataclass
class RemoveComponentIntent:
    """Component Lifecycle V3（Runtime V4 §18）：组件真删除。

    与 enabled=False（隐藏）相对：从 layout.components 移除实例。删除后
    dock/selection/renderer 清理由消费侧按「id 离开 spec」既有语义收敛；
    finalize 对「契约仍要求该族」的场景会按 component_missing 重新披露
    （删除单例契约组件是 agent/用户决策，重评估由完成度运行时承接）。
    """

    component_id: str


@dataclass
class DuplicateComponentIntent:
    """Component Lifecycle V3（§19）：复制多实例组件（新 id + floating 偏移）。"""

    component_id: str
    new_id: Optional[str] = None


@dataclass
class RebindComponentIntent:
    """Component Lifecycle V3（§19）：重绑定引用字段（chartRef/tableRef/layerId）。

    目标存在性（artifact ref 活性 / 图层在场）由调用方锁内守卫校验 ——
    引擎只承接 schema 白名单与互斥纪律（纯函数 rebind_component）。
    """

    component_id: str
    bindings: Dict[str, str] = field(default_factory=dict)


@dataclass
class SetBasemapIntent:
    """Basemap chrome mutation (#722): keeps the persisted spec tracking the
    BASE_LAYER_CHANGE command the legacy basemap tools emit, so desired state
    and the runtime map cannot diverge on provider switches."""
    provider_id: Optional[str] = None
    raster_filters: Optional[Dict[str, Any]] = None
    overlays: Optional[List[Any]] = None
    vector_style_url: Optional[str] = None


@dataclass
class SetTimeIntent:
    enabled: Optional[bool] = None
    field: Optional[str] = None
    type: Optional[str] = None
    extent: Optional[List[Any]] = None
    current: Optional[Any] = None
    window: Optional[Any] = None
    playback: Optional[Dict[str, Any]] = None
    step: Optional[float] = None
    speed: Optional[float] = None


_OPACITY_PAINT_KEYS = {
    "circle": "circle-opacity",
    "fill": "fill-opacity",
    "line": "line-opacity",
    "raster": "raster-opacity",
    "heatmap": "heatmap-opacity",
    "fill-extrusion": "fill-extrusion-opacity",
    "symbol": "icon-opacity",
}


# layout.components 单条目载荷上限（QA-2026-08-26：LLM 直塞 FeatureCollection）
_MAX_COMPONENT_BYTES = 96 * 1024


def _estimate_component_bytes(component: Dict[str, Any]) -> int:
    """组件条目序列化尺寸估算（失败 → 超限，宁可拒绝不放大）。"""
    try:
        from app.lib.json_size import estimate_json_bytes
        return estimate_json_bytes(component)
    except Exception:  # noqa: BLE001
        return _MAX_COMPONENT_BYTES + 1


def _patch_layer_presentation(
    layer: Dict[str, Any],
    visible: Optional[bool],
    opacity: Optional[float],
    origin: str = "agent",
) -> Dict[str, Any]:
    patched = dict(layer)
    if visible is not None:
        layout = dict(patched.get("layout") or {})
        layout["visibility"] = "visible" if visible else "none"
        patched["layout"] = layout
        # CA-P1-1（意图投影）：presentation 是一次**显式决策**——它改写该层的
        # cartographic_intent.expected_visible，QA（RESULT_VISIBILITY）由此区分
        # "故意隐藏"（用户/agent 收口）与"结果层被误藏"（auto_safe 修复）。
        # 用户隐藏 → expected_visible=False → QA pass 且 user-wins 一致。
        # #1070: presentation_owner 持久落层 —— 用户决策的权威从 64 条环形
        # provenance（一次 finalize 写 30+ 条即驱逐）移到权威 spec 本身。
        intent = dict(patched.get("cartographic_intent") or {})
        same_value = (
            intent.get("presentation_owner") == "user"
            and intent.get("expected_visible") is not None
            and bool(intent.get("expected_visible")) == bool(visible)
        )
        intent["expected_visible"] = bool(visible)
        # v2(review R2-P2-1)：幂等同值重放不改写归属 —— agent finalize 的
        # show/hide 与用户既有决策同值时，owner 保持 user（每次 finalize
        # 把用户决策洗成 agent 会让 spec 印记系统性失真，user-wins 退化到
        # 只剩 64 条 ring 兜底）。值翻转才伴随归属转移。
        intent["presentation_owner"] = "user" if same_value else str(origin)
        patched["cartographic_intent"] = intent
    if opacity is not None:
        paint = dict(patched.get("paint") or {})
        paint["opacity"] = opacity
        type_key = _OPACITY_PAINT_KEYS.get(str(patched.get("type") or ""))
        if type_key:
            paint[type_key] = opacity
        patched["paint"] = paint
    return patched


def _project_cartographic_intent(layer: Dict[str, Any]) -> None:
    """Upsert 落意图（CA-P1-1）：authoring 决策写进 cartographic_intent。

    expected_visible = authoring 时的可见性决策（布局无 none 即默认展示）；
    role 从 context_role/role 透传，不发明。调用方显式给出的
    cartographic_intent 优先（planner 携带 product plan 的角色裁决）。
    """
    if isinstance(layer.get("cartographic_intent"), dict):
        return
    layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
    intent: Dict[str, Any] = {
        "expected_visible": layout.get("visibility", "visible") != "none",
    }
    role = layer.get("context_role") or layer.get("role")
    if isinstance(role, str) and role:
        intent["role"] = role
    layer["cartographic_intent"] = intent


def _preserve_durable_presentation(
    existing: Dict[str, Any],
    incoming: Dict[str, Any],
) -> None:
    """同 id 整层 upsert 替换时的 durable presentation 继承（user wins）。

    用户隐藏/调透明度的层被 agent 重跑查询后整层 upsert：数据、样式与
    分类以 agent 新结果为准，但 durable 的显隐/透明度决策属于用户——
    agent 本次未显式给出时必须保留（否则用户隐藏的层静默回默认可见，
    reload 后用户决策彻底丢失）。图层类型改变时只继承 visibility
    （类型专属 opacity 键对新类型无效）。

    #1070(F-3): 既有层的 cartographic_intent.presentation_owner=="user" 且
    用户决策为隐藏时，agent upsert 显式携带的 layout.visibility 也一并
    剥离（此前只在 incoming 未给 visibility 时保留 —— 显式给出即绕过，
    且 expected_visible 被洗成 True 让下游 AUTO_SAFE 修复无从分辨）。
    持久 owner 印记随之继承，守卫据此长期有效。
    """
    existing_layout = existing.get("layout") if isinstance(existing.get("layout"), dict) else {}
    incoming_layout = incoming.get("layout") if isinstance(incoming.get("layout"), dict) else {}
    existing_intent = (
        existing.get("cartographic_intent")
        if isinstance(existing.get("cartographic_intent"), dict) else {}
    )
    user_owned_hidden = (
        existing_intent.get("presentation_owner") == "user"
        and existing_layout.get("visibility") == "none"
    )
    if user_owned_hidden:
        # agent 的 visibility 覆写被剥离，用户隐藏保留；owner 印记继承。
        merged_layout = {**incoming_layout, "visibility": "none"}
        incoming["layout"] = merged_layout
        incoming_intent = dict(incoming.get("cartographic_intent") or {})
        incoming_intent["expected_visible"] = False
        incoming_intent["presentation_owner"] = "user"
        incoming["cartographic_intent"] = incoming_intent
    elif (
        existing_layout.get("visibility") == "none"
        and incoming_layout.get("visibility") is None
    ):
        incoming["layout"] = {**incoming_layout, "visibility": "none"}

    existing_paint = existing.get("paint") if isinstance(existing.get("paint"), dict) else {}
    incoming_paint = incoming.get("paint") if isinstance(incoming.get("paint"), dict) else {}
    if not existing_paint:
        return
    existing_opacity = existing_paint.get("opacity")
    if (
        existing_opacity is not None
        and incoming_paint.get("opacity") is None
        and str(existing.get("type") or "") == str(incoming.get("type") or "")
    ):
        merged = dict(incoming_paint)
        merged["opacity"] = existing_opacity
        type_key = _OPACITY_PAINT_KEYS.get(str(incoming.get("type") or ""))
        if type_key and type_key not in merged:
            merged[type_key] = existing_opacity
        incoming["paint"] = merged


MutationIntent = Union[
    InitProjectIntent,
    SetViewIntent,
    UpsertSourceIntent,
    UpsertLayerIntent,
    PatchLayerPresentationIntent,
    PatchComponentIntent,
    RemoveComponentIntent,
    DuplicateComponentIntent,
    RebindComponentIntent,
    RemoveLayerIntent,
    ReorderLayersIntent,
    SetLayoutIntent,
    CheckpointIntent,
    RollbackIntent,
    SetBasemapIntent,
    SetTimeIntent,
    PatchLayerStyleIntent,
]


class MapSpecLifecycleEngine:
    """深层 MapSpec 意图与生命周期引擎"""

    def __init__(self):
        self.store = mapspec_store_instance
        # Per-session serialization is provided by session_lock_registry
        # (Redis-backed in prod → cross-pod; in-process fallback in tests).
        # The previous in-engine asyncio.Lock table evicted "unlocked" locks,
        # which could hand two concurrent same-session coroutines different lock
        # objects (lost update) — review P1-2. The registry lock is the sole
        # serializer; no in-engine lock table.
        # #1082(F-10): prior spec blocking-codes 的指纹缓存（有界 256）。
        self._prior_blocking_cache: Dict[str, set] = {}

    @staticmethod
    def _blocking_error_codes(validation: Dict[str, Any]) -> set:
        return {
            e.get("code") for e in validation.get("errors", [])
            if e.get("code") in BLOCKING_VALIDATION_CODES
        }

    @staticmethod
    def _review_failure(mapspec: Dict[str, Any], exc: Exception) -> Dict[str, Any]:
        """Represent an evaluator failure as missing evidence, never as PASS."""
        fingerprint = cartographic_fingerprint(mapspec)
        check = {
            "rule": "CARTOGRAPHIC_REVIEW_EXECUTION",
            "status": "not_evaluated",
            "severity": "error",
            "message": "Cartographic review could not be evaluated.",
            "evidence_class": "deterministic",
            "evidence": {"error_type": type(exc).__name__},
            "repairability": "not_repairable",
            "suggested_fix": None,
        }
        return {
            "stage": "desired_state",
            "status": "not_evaluated",
            "review": {
                "status": "not_evaluated",
                "passed": False,
                "evaluated_count": 0,
                "findings": [],
                "checks": [check],
            },
            "initial_fingerprint": fingerprint,
            "final_fingerprint": fingerprint,
            "attempts": [],
            "repair_count": 0,
            "termination_reason": "review_error",
            "counters": {
                "review_invocations": 1,
                "rule_invocations": 1,
                "metadata_sources": len(mapspec.get("sources") or {}),
                "full_data_loads": 0,
                "repair_attempts": 0,
            },
        }

    async def apply_mutation(
        self,
        session_id: str,
        intent: MutationIntent,
        *,
        origin: MutationOrigin = "agent",
        expected_revision: Optional[int] = None,
        pre_commit_check: Optional[Callable] = None,
    ) -> MapSpecResult:
        """原子执行 MapSpec 意图变迁，带 per-session 分布式锁 + 事务 rollback。

        锁：session_lock_registry.lock(session_id) — Redis 跨 pod 互斥（生产），
        in-process asyncio.Lock（单 worker / 测试）。该锁序列化同 session 的并发
        mutation，避免 lost update。

        origin 为 agent|user|system。user 必须带 expected_revision；缺省 origin=agent
        且省略 expected_revision 时仍提交（既有 tool 兼容）。expected_revision 与当前
        revision 不一致则 superseded，MapSpec 不变（ADR-0058）。

        pre_commit_check：锁内、prior spec 载入后调用的异步回调
        ``(session_id, intent, origin, prior_mapspec) -> Optional[MapSpecResult]``；
        返回非 None 即拒绝提交（守卫复检语义）。None 保持既有行为。

        #1071: 持久写路径对锁降级/丢失 fail-closed —— 降级获取（锁 SET
        2s 超时 vs 数据面 5s 的不对称窗口）+ 活数据面 = 两 pod 各持进程
        内锁并发提交，revision 相等的丢更新；TTL 过期丢失（事件循环停顿
        >30s）后本持有者仍会覆盖他 pod 的提交。
        """
        _lock = session_lock_registry.lock(
            session_id, fail_on_degraded=True, fail_on_lost=True,
        )
        async with _lock:
            invalidate = getattr(session_data_manager, "invalidate_local_cache", None)
            if callable(invalidate):
                invalidate(session_id)
            # V3 Performance: copy-on-write candidate to eliminate O(sources) deepcopy
            # for small mutations (SetView/SetLayout). Snapshot is deferred until after
            # intent dispatch so we can capture ONLY what the intent will touch.
            # For Redis backend (returns fresh copies), this is zero-copy. For in-memory
            # backend, shallow copy is enough since we mutate only top-level keys.
            pre_state = await session_data_manager.get_map_state(session_id)
            if pre_state.get("_cartographic_deleted") is True:
                return MapSpecResult(
                    is_error=True,
                    origin=origin,
                    error_msg="Session was deleted; stale MapSpec mutation rejected.",
                )
            if origin == "user" and expected_revision is None:
                return MapSpecResult(
                    is_error=True,
                    origin=origin,
                    error_msg="User MapSpec mutations require expected_revision.",
                    correction_hint=(
                        "Re-read MapSpec and retry with the current mutation_revision."
                    ),
                )
            # PERF-F8: defer the layers deepcopy — view/layout/time intents
            # never touch layers, and the COW work already avoids copying the
            # mapspec for them; this unconditional copy was left behind.
            _layers_touching = isinstance(
                intent, (
                    UpsertLayerIntent,
                    PatchLayerPresentationIntent,
                    RemoveLayerIntent,
                    ReorderLayersIntent,
                    InitProjectIntent,
                    RollbackIntent,
                )
            )
            old_layers_snapshot = (
                copy.deepcopy(pre_state.get("layers", []) or [])
                if _layers_touching
                # 669: non-layer intents share no mutation of layers; shallow-copy
                # each layer dict to prove rollback cannot leak via shared refs
                # while keeping cost O(#layers) << payload. Tighten over bare
                # list() which shared dict refs.
                else [dict(layer) if isinstance(layer, dict) else layer for layer in (pre_state.get("layers", []) or [])]
            )
            observation = pre_state.get("_cartographic_observation")
            try:
                runtime_observation_seq = int(
                    observation.get("sequence", 0)
                    if isinstance(observation, dict) else 0
                )
            except (TypeError, ValueError):
                runtime_observation_seq = 0
            # v2(audit F1): 权威载入必须先于 revision 捕获/CAS ——
            # get_mapspec 的磁盘复活路径（Redis 过期、盘上 spec 存活）会把
            # CAS 令牌随 spec 恢复进 Redis 并回写 hint（store.py）。此前
            # prior 从复活前的 pre_state 捕获（stale 0）：commit 以 0+1
            # 覆盖世代 N 破坏单调性，且重放的 expected_revision=0 开世
            # mutation 会通过针对新 spec 的 CAS。superseded 返回也直接复用
            # loaded，省掉旧路径的第二次全量 get_mapspec。
            loaded = await self.store.get_mapspec(session_id, state_hint=pre_state)
            try:
                prior_mutation_revision = int(
                    pre_state.get("_cartographic_mutation_revision", 0)
                )
            except (TypeError, ValueError):
                prior_mutation_revision = 0
            if (
                expected_revision is not None
                and expected_revision != prior_mutation_revision
            ):
                return MapSpecResult(
                    superseded=True,
                    is_error=False,
                    origin=origin,
                    mapspec=loaded,
                    mutation_revision=prior_mutation_revision,
                    error_msg="MapSpec revision has changed.",
                    correction_hint=(
                        "Re-read MapSpec and retry with the current mutation_revision."
                    ),
                )
            # #1074(F-14): except 处理器引用 checkpoint_id_created —— 初始化
            # 必须先于 try（异常发生在原初始化行之前时不得 UnboundLocal）。
            checkpoint_id_created: Optional[str] = None
            ckpt_ref_count = 0
            try:
                prior_mapspec = loaded
                # #1070(F-1): 锁内守卫复检 seam —— apply_gis_mutation 的
                # user-wins 检查此前在锁外求值，等锁窗口内落地的用户决策
                # 不可见（TOCTOU）。回调返回非 None 即拒绝（不提交）。
                if pre_commit_check is not None:
                    guard_result = await pre_commit_check(
                        session_id, intent, origin, prior_mapspec
                    )
                    if guard_result is not None:
                        return guard_result
                # CORR-2 companion: whether the session had a persisted spec
                # BEFORE the auto-init skeleton below. Rollback of a first
                # mutation must DISCARD the candidate, not "restore" the
                # in-memory skeleton as a residual spec.
                session_was_fresh = loaded is None
                
                # V3: Defer the deep snapshot until AFTER we know the intent type.
                # SetView/SetLayout/CheckpointIntent only touch top-level keys, so
                # a shallow copy + copy-on-write for the touched branch is O(1).
                # UpsertLayer/RemoveLayer/InitProject touch sources/layers, so we
                # still need a working copy but can do it offloaded.
                old_mapspec_snapshot = None  # deferred
                mapspec = None  # candidate, assigned per intent type

                # 1. 针对未初始化会话自动构建根框架（仅内存；commit 阶段才落盘 —
                #    Review P2-3: 此前在 reject 前就 save_mapspec，reject 会残留骨架）
                #    审计修正：骨架必须写入 `loaded`。此前写入 `mapspec`，而下方
                #    每个意图分支都用 `mapspec = {**loaded} if loaded else {}` 重建
                #    candidate —— 骨架被静默丢弃，新会话落盘的 spec 丢失
                #    version/layout/thresholds（空 dict 起步）。
                if not loaded and not isinstance(intent, (InitProjectIntent, RollbackIntent)):
                    loaded = {
                        "version": "1.0",
                        "view": {},
                        "sources": {},
                        "layers": [],
                        "layout": {
                            "legend": {"visible": True, "position": "top-right"},
                            "controls": [{"type": "navigation", "position": "top-right"}],
                        },
                        "thresholds": {"maxFeatures": 50000, "timeoutMs": 30000},
                    }
                    prior_mapspec = None
                    old_mapspec_snapshot = None

                # 2. 在内存构建 candidate；记录 deferred redis layer 操作。
                #    重 IO/CPU 的 process_layer_ingestion 卸载到线程，不阻塞 event loop。
                auto_checkpoint = False
                # pending_layer_op: (op, layer_id, layer?) — 提交时才写 redis layers
                pending_layer_op: Optional[Tuple[str, str, Optional[Dict[str, Any]]]] = None
                # rollback 意图需要恢复 refs，单独走快路径（不经 candidate 校验拒绝）
                is_rollback = False

                if isinstance(intent, InitProjectIntent):
                    # Full spec build, no snapshot needed (nothing to roll back)
                    old_mapspec_snapshot = None
                    mapspec = {
                        "version": "1.0",
                        "view": (
                            {**intent.view, "framed": True} if intent.view else {}
                        ),
                        "sources": {},
                        "layers": [],
                        "layout": {
                            "legend": {"visible": True, "position": "top-right"},
                            "controls": [{"type": "navigation", "position": "top-right"}],
                        },
                        "thresholds": intent.thresholds or {"maxFeatures": 50000, "timeoutMs": 30000},
                    }

                elif isinstance(intent, SetViewIntent):
                    # V3 COW: view-only mutation, shallow copy + copy touched branch
                    old_mapspec_snapshot = loaded  # shallow snapshot (for rollback)
                    mapspec = {**loaded} if loaded else {}
                    view = dict(mapspec.get("view", {}))  # copy view branch
                    if intent.center is not None:
                        view["center"] = intent.center
                    if intent.zoom is not None:
                        view["zoom"] = intent.zoom
                    if intent.pitch is not None:
                        view["pitch"] = intent.pitch
                    if intent.bearing is not None:
                        view["bearing"] = intent.bearing
                    view["framed"] = True
                    mapspec["view"] = view

                elif isinstance(intent, UpsertLayerIntent):
                    # V3 COW: shallow copy + per-branch copy for sources and layers.
                    # process_layer_ingestion never mutates mapspec in-place (it copies
                    # existing_entry at pipeline.py:56 before any write). The view update
                    # suggested_view is an in-place write on mapspec["view"], so we copy
                    # that branch too. Source entry objects in sources are shared but
                    # not mutated (only replaced by key). This avoids full deepcopy for
                    # the rollback snapshot (which just needs the prior reference).
                    old_mapspec_snapshot = loaded  # prior reference for rollback
                    mapspec = {**loaded} if loaded else {}
                    mapspec["sources"] = dict(loaded.get("sources", {})) if loaded else {}
                    mapspec["layers"] = list(loaded.get("layers", [])) if loaded else []
                    mapspec["view"] = dict(loaded.get("view", {})) if loaded else {}
                    
                    session_dir = self.store.get_session_dir(session_id)
                    # 卸载重计算（GeoJSON profiling / raster PNG 渲染）到线程，
                    # 释放 event loop 给其它 session 的 I/O（REL-07）。
                    processed_layer, source_entry, suggested_view = await asyncio.to_thread(
                        process_layer_ingestion,
                        mapspec, intent.layer, intent.source_data, session_dir,
                    )
                    source_id = processed_layer.get("source", "default_source")
                    mapspec["sources"][source_id] = source_entry

                    if suggested_view and not mapspec.get("view", {}).get("framed"):
                        mapspec["view"]["center"] = suggested_view["center"]
                        mapspec["view"]["zoom"] = suggested_view["zoom"]

                    layers = mapspec["layers"]
                    updated = False
                    for i, layer in enumerate(layers):
                        if layer.get("id") == processed_layer.get("id"):
                            # ST-P2-2：重跑同 id upsert 整层替换时保留既有
                            # durable presentation（用户显隐/透明度决策）。
                            _preserve_durable_presentation(layer, processed_layer)
                            layers[i] = processed_layer
                            updated = True
                            break
                    if not updated:
                        layers.append(processed_layer)
                    # CA-P1-1：authoring 决策投影为 cartographic_intent
                    #（QA RESULT_VISIBILITY 的意图证据——此前只读不写，恒
                    # not_evaluated）。
                    _project_cartographic_intent(processed_layer)

                    pending_layer_op = (
                        "upsert",
                        processed_layer.get("id", "layer"),
                        processed_layer,
                    )
                    auto_checkpoint = True

                elif isinstance(intent, PatchLayerPresentationIntent):
                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    mapspec["layers"] = list(
                        loaded.get("layers", []) if loaded else []
                    )
                    matched = False
                    patched_layers: List[Any] = []
                    for layer in mapspec["layers"]:
                        if not isinstance(layer, dict):
                            patched_layers.append(layer)
                            continue
                        if _should_remove_layer(layer, intent.layer_id):
                            matched = True
                            patched_layers.append(
                                _patch_layer_presentation(
                                    layer, intent.visible, intent.opacity,
                                    origin=str(origin),
                                )
                            )
                        else:
                            patched_layers.append(layer)
                    if not matched:
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg=f"Layer {intent.layer_id} not found.",
                            correction_hint=(
                                "Re-read MapSpec and patch an existing layer id."
                            ),
                        )
                    mapspec["layers"] = patched_layers
                    auto_checkpoint = True

                elif isinstance(intent, PatchLayerStyleIntent):
                    # #1077：持久样式突变 —— paint 顶层键合并进 spec 层族
                    # （与 presentation patch 同族谓词；不触碰
                    # cartographic_intent —— 样式不是 presentation 决策）。
                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    mapspec["layers"] = list(
                        loaded.get("layers", []) if loaded else []
                    )
                    styled_layers: List[Any] = []
                    style_matched = False
                    for layer in mapspec["layers"]:
                        if not isinstance(layer, dict):
                            styled_layers.append(layer)
                            continue
                        if _should_remove_layer(layer, intent.layer_id):
                            style_matched = True
                            merged_paint = dict(layer.get("paint") or {})
                            merged_paint.update(intent.paint or {})
                            patched_style = dict(layer)
                            patched_style["paint"] = merged_paint
                            styled_layers.append(patched_style)
                        else:
                            styled_layers.append(layer)
                    if not style_matched:
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg=f"Layer {intent.layer_id} not found.",
                            correction_hint=(
                                "Re-read MapSpec and patch an existing layer id."
                            ),
                        )
                    mapspec["layers"] = styled_layers
                    auto_checkpoint = True

                elif isinstance(intent, UpsertSourceIntent):
                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    mapspec["sources"] = dict(loaded.get("sources", {})) if loaded else {}
                    # 669: immutable hand-off contract — intent.source is treated
                    # as immutable after dispatch. Top-level and `profile` are
                    # shallow-copied (O(1) isolation from caller post-mutation);
                    # nested payload (`inlineData`, typically a large
                    # FeatureCollection) is intentionally shared by reference
                    # (CoW parity with SetView/UpsertLayer) — callers must not
                    # mutate it after dispatch, and the engine never mutates it.
                    _src = dict(intent.source)
                    if isinstance(_src.get("profile"), dict):
                        _src["profile"] = dict(_src["profile"])
                    mapspec["sources"][intent.source_id] = _src
                    auto_checkpoint = True

                elif isinstance(intent, PatchComponentIntent):
                    # 组件局部突变（UI 拖拽收尾 / Agent 组件编辑）——与
                    # SetLayoutIntent 的整表替换不同，只动命中的单个组件。
                    # 突变/校验复用 gis_harness.components.mutate_component，
                    # 不出现第二套组件突变实现。
                    from app.services.gis_harness.components import (
                        CartographyComponent,
                        mutate_component,
                    )

                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    layout = dict(mapspec.get("layout", {}))
                    raw_components = layout.get("components") or []
                    components = [
                        CartographyComponent.model_validate(dict(c))
                        for c in raw_components
                        if isinstance(c, dict)
                    ]
                    if not components and not intent.upsert:
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg="MapSpec has no layout.components to patch.",
                            correction_hint=(
                                "Initialize components via webgis_map_product or "
                                "webgis_layout_set first, or patch with upsert."
                            ),
                        )
                    mutated, change = mutate_component(
                        components,
                        component_id=intent.component_id,
                        component_type=intent.component_type,
                        enabled=intent.enabled,
                        position=intent.position,
                        placement=intent.placement,
                        variant=intent.variant,
                        style=intent.style,
                        options=intent.options,
                        upsert=intent.upsert,
                    )
                    if change is None:
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg=(
                                f"Component {intent.component_id} not found."
                            ),
                            correction_hint=(
                                "Current components: "
                                + ", ".join(f"{c.id}({c.type})" for c in components)
                            ),
                        )
                    # QA 加固对齐 SetLayoutIntent：patch 路径（component_update /
                    # 用户路由）同样不允许组件条目携带大数据（96KB）。
                    oversized = [
                        c.id for c in mutated
                        if _estimate_component_bytes(c.to_mapspec()) > _MAX_COMPONENT_BYTES
                    ]
                    if oversized:
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg=(
                                "patched layout.components entry exceeds "
                                f"{_MAX_COMPONENT_BYTES // 1024}KB: "
                                + ", ".join(oversized[:5])
                            ),
                            correction_hint=(
                                "组件 options 不携带大数据——图表用 chart=ChartData"
                                "（大载荷自动转 ref:chart-* artifact），统计用 "
                                "stats.items 摘要（≤24 条）。"
                            ),
                        )
                    layout["components"] = sorted(
                        [c.to_mapspec() for c in mutated],
                        key=lambda c: (c.get("priority", 0), c.get("id", "")),
                    )
                    mapspec["layout"] = layout

                elif isinstance(intent, RemoveComponentIntent):
                    # Component Lifecycle V3（Runtime V4 §18）：真删除 ——
                    # mutate_component 的 enabled=False 是隐藏；这里是布局
                    # 条目移除。纯函数 remove_component 与 patch 同源。
                    from app.services.gis_harness.components import (
                        CartographyComponent,
                        remove_component,
                    )

                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    layout = dict(mapspec.get("layout", {}))
                    raw_components = layout.get("components") or []
                    components = [
                        CartographyComponent.model_validate(dict(c))
                        for c in raw_components if isinstance(c, dict)
                    ]
                    remaining, change = remove_component(
                        components, component_id=intent.component_id,
                    )
                    if change is None:
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg=(
                                f"Component {intent.component_id} not found."
                            ),
                            correction_hint=(
                                "Current components: "
                                + ", ".join(f"{c.id}({c.type})" for c in components)
                            ),
                        )
                    layout["components"] = sorted(
                        [c.to_mapspec() for c in remaining],
                        key=lambda c: (c.get("priority", 0), c.get("id", "")),
                    )
                    mapspec["layout"] = layout

                elif isinstance(intent, DuplicateComponentIntent):
                    from app.services.gis_harness.components import (
                        CartographyComponent,
                        duplicate_component,
                    )

                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    layout = dict(mapspec.get("layout", {}))
                    raw_components = layout.get("components") or []
                    components = [
                        CartographyComponent.model_validate(dict(c))
                        for c in raw_components if isinstance(c, dict)
                    ]
                    # 注意不能用 ``copy`` 作局部名 —— 本函数上游用 stdlib
                    # copy.deepcopy（同名局部会把它遮蔽成 UnboundLocal）。
                    with_copy, copy_component, dup_error = duplicate_component(
                        components,
                        component_id=intent.component_id,
                        new_id=intent.new_id or "",
                    )
                    if dup_error or copy_component is None:
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg=dup_error or "duplicate failed",
                        )
                    # 与 patch 分支同纪律：组件条目尺寸有界（96KB）。
                    oversized_dup = [
                        c.id for c in with_copy
                        if _estimate_component_bytes(c.to_mapspec()) > _MAX_COMPONENT_BYTES
                    ]
                    if oversized_dup:
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg=(
                                "duplicated layout.components entry exceeds "
                                f"{_MAX_COMPONENT_BYTES // 1024}KB: "
                                + ", ".join(oversized_dup[:5])
                            ),
                        )
                    layout["components"] = sorted(
                        [c.to_mapspec() for c in with_copy],
                        key=lambda c: (c.get("priority", 0), c.get("id", "")),
                    )
                    mapspec["layout"] = layout

                elif isinstance(intent, RebindComponentIntent):
                    from app.services.gis_harness.components import (
                        CartographyComponent,
                        rebind_component,
                    )

                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    layout = dict(mapspec.get("layout", {}))
                    raw_components = layout.get("components") or []
                    components = [
                        CartographyComponent.model_validate(dict(c))
                        for c in raw_components if isinstance(c, dict)
                    ]
                    # review M：layerId 绑定目标在**锁内**对权威 spec 复核
                    # （纯函数零 IO）；ref 活性探测留在调用方 best-effort
                    # （探测是健康证据不是注册真相 —— 文档如实）。
                    if "layerId" in intent.bindings:
                        wanted = str(intent.bindings["layerId"])
                        layer_present = any(
                            isinstance(layer, dict)
                            and (
                                str(layer.get("id") or "") == wanted
                                or str(layer.get("id") or "").startswith(f"{wanted}__")
                                or str(layer.get("id") or "").startswith(f"{wanted}-")
                            )
                            for layer in (loaded or {}).get("layers", [])
                        )
                        if not layer_present:
                            return MapSpecResult(
                                is_error=True,
                                origin=origin,
                                error_msg=(
                                    f"重绑定图层 {wanted} 不在当前 MapSpec"
                                ),
                                correction_hint=(
                                    "先读当前 MapSpec 确认图层族 id 再重绑定。"
                                ),
                            )
                    rebound, change, rebind_error = rebind_component(
                        components,
                        component_id=intent.component_id,
                        bindings=dict(intent.bindings),
                    )
                    if rebind_error or change is None:
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg=rebind_error or "rebind failed",
                        )
                    layout["components"] = sorted(
                        [c.to_mapspec() for c in rebound],
                        key=lambda c: (c.get("priority", 0), c.get("id", "")),
                    )
                    mapspec["layout"] = layout

                elif isinstance(intent, RemoveLayerIntent):
                    # V3 COW: layers mutation, shallow copy + new filtered list
                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    layers = mapspec.get("layers", [])
                    mapspec["layers"] = [
                        lay for lay in layers
                        if not _should_remove_layer(lay, intent.layer_id)
                    ]
                    # F-11（audit5 #1074）复核后维持既有契约：sources 是数据
                    # 登记项，remove_layer 只做图层清扫、不做 source GC（#1014
                    # TE-P1-1，scenario_8 测试锁定 —— ref 生命周期另有治理；
                    # inlineData 死重是已知的权衡而非缺陷）。
                    pending_layer_op = ("remove", intent.layer_id, None)
                    auto_checkpoint = True

                elif isinstance(intent, ReorderLayersIntent):
                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    current = list(mapspec.get("layers", []) if loaded else [])
                    # Support both exact ID and prefix matching for sublayers (__fill, __outline, etc.)
                    matched_ordered: List[Dict[str, Any]] = []
                    matched_ids: set = set()
                    for lid in intent.layer_ids:
                        for layer in current:
                            if not isinstance(layer, dict):
                                continue
                            layer_id = str(layer.get("id") or "")
                            if layer_id in matched_ids:
                                continue
                            if layer_id == lid or layer_id.startswith(f"{lid}__") or layer_id.startswith(f"{lid}-"):
                                matched_ordered.append(layer)
                                matched_ids.add(layer_id)
                    leftover = [
                        layer for layer in current
                        if isinstance(layer, dict) and str(layer.get("id") or "") not in matched_ids
                    ]
                    if not matched_ordered:
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg="Reorder referenced no existing layers.",
                            correction_hint="Re-read MapSpec and reorder current layer ids.",
                        )
                    mapspec["layers"] = matched_ordered + leftover
                    auto_checkpoint = True

                elif isinstance(intent, SetLayoutIntent):
                    # V3 COW: layout-only mutation, shallow copy + copy touched branch
                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    layout = dict(mapspec.get("layout", {}))  # copy layout branch
                    if intent.legend is not None:
                        layout["legend"] = intent.legend
                    if intent.controls is not None:
                        layout["controls"] = intent.controls
                    if intent.margins is not None:
                        layout["margins"] = intent.margins
                    if intent.components is not None:
                        # 组件整体替换（webgis_component_update 先读后写实现
                        # 局部突变）；条目要求唯一 string id + string type，
                        # 非法/重复输入确定性拒绝，不留半更新状态。
                        # QA-2026-08-26 加固：LLM 曾把整份 FeatureCollection 塞进
                        # statistics_panel.options（layout_set 绕过组件 payload
                        # 校验）——组件条目尺寸有界（96KB），大数据走
                        # ref:chart-* artifact / 图层 ref，不进 layout.components。
                        valid = all(
                            isinstance(c, dict) and isinstance(c.get("id"), str)
                            and isinstance(c.get("type"), str)
                            for c in intent.components
                        )
                        oversized = [
                            str(c.get("id"))
                            for c in intent.components
                            if isinstance(c, dict)
                            and _estimate_component_bytes(c) > _MAX_COMPONENT_BYTES
                        ]
                        if oversized:
                            return MapSpecResult(
                                is_error=True,
                                origin=origin,
                                error_msg=(
                                    "layout.components entries exceed "
                                    f"{_MAX_COMPONENT_BYTES // 1024}KB: "
                                    + ", ".join(oversized[:5])
                                ),
                                correction_hint=(
                                    "组件 options 不携带大数据（FeatureCollection/"
                                    "全量记录）——图表经 generate_chart(attach_to_map)"
                                    "或 component_update(chart=…) 走 ref:chart-* "
                                    "artifact；统计数据用 stats.items 摘要（≤24 条）。"
                                ),
                            )
                        ids = [
                            c.get("id") for c in intent.components
                            if isinstance(c, dict)
                        ]
                        if not valid or len(ids) != len(set(ids)):
                            return MapSpecResult(
                                is_error=True,
                                origin=origin,
                                error_msg=(
                                    "layout.components entries require unique "
                                    "string id and string type."
                                ),
                                correction_hint=(
                                    "Each component must be "
                                    "{'id': str, 'type': str, ...} with unique "
                                    "ids — see CartographyComponent "
                                    "(gis_harness.components)."
                                ),
                            )
                        layout["components"] = sorted(
                            intent.components,
                            key=lambda c: (c.get("priority", 0), c.get("id", "")),
                        )
                    mapspec["layout"] = layout

                elif isinstance(intent, CheckpointIntent):
                    # V3 COW: checkpoint reads but doesn't mutate the spec
                    old_mapspec_snapshot = loaded
                    mapspec = loaded  # no mutation, just checkpoint
                    session_dir = self.store.get_session_dir(session_id)
                    ckpt_res = await create_checkpoint(
                        mapspec, session_dir, session_data_manager, intent.checkpoint_id
                    )
                    checkpoint_id_created = ckpt_res.get("checkpoint_id")
                    ckpt_ref_count = ckpt_res.get("ref_count", 0)

                elif isinstance(intent, RollbackIntent):
                    # V3 COW: rollback replaces the entire spec
                    old_mapspec_snapshot = loaded
                    session_dir = self.store.get_session_dir(session_id)
                    rb_res = await rollback_checkpoint(
                        session_dir, intent.checkpoint_id, session_data_manager
                    )
                    if not rb_res.get("success"):
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg=rb_res.get("message", "Rollback failed"),
                        )
                    mapspec = rb_res["mapspec"]
                    ckpt_ref_count = rb_res.get("ref_count", 0)
                    # rollback 恢复了 refs + mapspec；运行时 layers 需整体对齐到
                    # 恢复后的 mapspec.layers（用特殊 op 标记）。
                    is_rollback = True

                elif isinstance(intent, SetBasemapIntent):
                    # V3 COW: basemap-only mutation (#722), same discipline as
                    # SetView/SetTime — shallow copy + copy touched branch.
                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    basemap = dict(mapspec.get("basemap", {}))
                    if intent.provider_id is not None:
                        basemap["providerId"] = intent.provider_id
                    if intent.raster_filters is not None:
                        basemap["rasterFilters"] = intent.raster_filters
                    if intent.overlays is not None:
                        basemap["overlays"] = intent.overlays
                    if intent.vector_style_url is not None:
                        basemap["vectorStyleUrl"] = intent.vector_style_url
                    mapspec["basemap"] = basemap

                elif isinstance(intent, SetTimeIntent):
                    # V3 COW: time-only mutation, shallow copy + copy touched branch
                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    time_cfg = dict(mapspec.get("time", {
                        "enabled": True,
                        "field": "timestamp",
                        "type": "continuous",
                        "extent": [],
                        "current": None,
                        "step": 1.0,
                        "speed": 1.0,
                    }))
                    if intent.enabled is not None:
                        time_cfg["enabled"] = intent.enabled
                    if intent.field is not None:
                        time_cfg["field"] = intent.field
                    if intent.type is not None:
                        time_cfg["type"] = intent.type
                    if intent.extent is not None:
                        time_cfg["extent"] = intent.extent
                    if intent.current is not None:
                        time_cfg["current"] = intent.current
                    if intent.window is not None:
                        time_cfg["window"] = intent.window
                    if intent.playback is not None:
                        time_cfg.setdefault("playback", {}).update(intent.playback)
                    if intent.step is not None:
                        time_cfg["step"] = intent.step
                    if intent.speed is not None:
                        time_cfg["speed"] = intent.speed
                    mapspec["time"] = time_cfg

                # 3. Review the immutable desired state and apply only bounded,
                # presentation-only AUTO_SAFE repairs. This is deliberately
                # before structural validation/commit so the persisted MapSpec
                # and runtime layer projection share one fingerprint. Rollback
                # restores an exact historical snapshot and is review-only.
                try:
                    cartographic_loop = review_and_repair_cartography(
                        mapspec,
                        max_iterations=0 if is_rollback else 2,
                    )
                    mapspec = cartographic_loop.mapspec
                    cartographic_review = cartographic_loop.to_dict()
                except Exception as review_exc:  # noqa: BLE001
                    logger.warning(
                        "Cartographic desired-state review unavailable for session %s: %s",
                        session_id,
                        type(review_exc).__name__,
                    )
                    cartographic_review = self._review_failure(mapspec, review_exc)

                # An upsert's deferred runtime write must use the reviewed and
                # possibly repaired layer, not the pre-review object.
                if pending_layer_op is not None and pending_layer_op[0] == "upsert":
                    layer_id = pending_layer_op[1]
                    repaired_layer = next(
                        (
                            layer for layer in mapspec.get("layers", [])
                            if isinstance(layer, dict) and layer.get("id") == layer_id
                        ),
                        pending_layer_op[2],
                    )
                    pending_layer_op = ("upsert", layer_id, repaired_layer)

                # 4. Pre-compile 校验。Rollback 不走拒绝逻辑（恢复的是历史合法 spec）。
                validation = validate_mapspec(mapspec)
                warnings = [e["message"] for e in validation.get("errors", [])] + validation.get("warnings", [])

                if not is_rollback:
                    # #1082(F-10): 旧 spec 的 blocking codes 按指纹缓存 ——
                    # 每次非回滚变更此前都全量 validate 一遍只为 diff codes。
                    prior_blocking = set()
                    if prior_mapspec:
                        prior_fp = None
                        try:
                            from app.lib.cartography.quality_loop import (
                                cartographic_fingerprint,
                            )
                            prior_fp = cartographic_fingerprint(prior_mapspec)
                        except Exception:  # noqa: BLE001 - 指纹失败回退全量校验
                            prior_fp = None
                        if prior_fp is not None and prior_fp in self._prior_blocking_cache:
                            prior_blocking = self._prior_blocking_cache[prior_fp]
                        else:
                            prior_blocking = self._blocking_error_codes(
                                validate_mapspec(prior_mapspec)
                            )
                            if prior_fp is not None:
                                self._prior_blocking_cache[prior_fp] = prior_blocking
                                while len(self._prior_blocking_cache) > 256:
                                    self._prior_blocking_cache.pop(
                                        next(iter(self._prior_blocking_cache))
                                    )
                    new_blocking = self._blocking_error_codes(validation) - prior_blocking
                    if new_blocking:
                        # 引入新的 blocking 错误：拒绝，不落盘，last-known-good 不变。
                        msg = "; ".join(
                            e["message"] for e in validation.get("errors", [])
                            if e.get("code") in new_blocking
                        )
                        return MapSpecResult(
                            is_error=True,
                            origin=origin,
                            error_msg=f"MapSpec 校验失败: {msg}",
                            correction_hint=(
                                "该意图会引入无效的 source 引用或非法 stops，已拒绝；"
                                "last-known-good MapSpec 保持不变。"
                            ),
                        )

                # 5. 提交（checkpoint → save_mapspec → redis layers），任一失败回滚。
                # #1071: 提交前复检锁所有权 —— TTL 过期丢失（事件循环停顿
                # >30s / Redis 中断后恢复）后本持有者继续写会覆盖他 pod 的
                # 提交；aexit 的 fail_on_lost 只能事后暴露，此处防止发生。
                if getattr(_lock, "lost", False):
                    return MapSpecResult(
                        is_error=True,
                        origin=origin,
                        error_msg="Session lock ownership lost before commit; mutation aborted.",
                        correction_hint=(
                            "锁所有权在提交前丢失（TTL 过期）。请重读 MapSpec 后重试。"
                        ),
                    )
                if auto_checkpoint and not checkpoint_id_created:
                    session_dir = self.store.get_session_dir(session_id)
                    ckpt_res = await create_checkpoint(mapspec, session_dir, session_data_manager)
                    checkpoint_id_created = ckpt_res.get("checkpoint_id")
                    ckpt_ref_count = ckpt_res.get("ref_count", 0)

                mutation_revision = prior_mutation_revision + 1
                # #1073: spec 与 CAS 令牌单事务原子落地（crash 窗口不再产生
                # spec=世代 N+1 而令牌=N 的错配）。save 返回未携带时（后端缺
                # set_map_state_fields 的测试替身）退回旧的双写。
                # v2(audit F4): runtime layers 的 upsert/remove/replace 一并
                # 并入同一提交事务（layer_op → commit_mapspec_state 单
                # WATCH/MULTI）—— 此前 spec 落地与 layers 落地是两笔事务，
                # crash 落在中间会留下 spec=世代 N+1 而 layers=世代 N。
                commit_layer_op: Optional[Tuple[str, str, Optional[Any]]] = None
                if pending_layer_op is not None:
                    commit_layer_op = pending_layer_op
                elif is_rollback:
                    # 把运行时 layers 对齐到恢复后的 mapspec.layers。
                    commit_layer_op = ("replace", "", list(mapspec.get("layers", [])))
                save_res = await self.store.save_mapspec(
                    session_id, mapspec, mutation_revision=mutation_revision,
                    layer_op=commit_layer_op,
                )
                revision_persisted = bool(
                    save_res.get("revision_persisted")
                ) if isinstance(save_res, dict) else False
                layers_persisted = bool(
                    save_res.get("layers_persisted")
                ) if isinstance(save_res, dict) else False

                if pending_layer_op is not None and not layers_persisted:
                    op, layer_id, layer = pending_layer_op
                    if op == "upsert":
                        persisted = await session_data_manager.update_layer_in_state(
                            session_id, layer_id, layer
                        )
                        if persisted is False:
                            raise RuntimeError("runtime layer persistence rejected")
                    elif op == "remove":
                        persisted = await session_data_manager.remove_layer_from_state(
                            session_id, layer_id
                        )
                        if persisted is False:
                            raise RuntimeError("runtime layer removal rejected")
                elif is_rollback and not layers_persisted:
                    persisted = await session_data_manager.set_map_state(
                        session_id, "layers", list(mapspec.get("layers", []))
                    )
                    if persisted is False:
                        raise RuntimeError("rollback runtime layer persistence rejected")

                if not revision_persisted:
                    persisted = await session_data_manager.set_map_state(
                        session_id,
                        "_cartographic_mutation_revision",
                        mutation_revision,
                    )
                    if persisted is False:
                        raise RuntimeError("cartographic mutation revision persistence rejected")

                cartography_findings = (
                    cartographic_review.get("review", {}).get("findings", [])
                )
                return MapSpecResult(
                    mapspec=mapspec,
                    warnings=warnings,
                    is_compiled=validation.get("success", False),
                    checkpoint_id=checkpoint_id_created,
                    ref_count=ckpt_ref_count,
                    is_error=False,
                    cartography_findings=cartography_findings,
                    cartographic_review=cartographic_review,
                    mapspec_fingerprint=cartographic_review.get("final_fingerprint"),
                    runtime_observation_seq=runtime_observation_seq,
                    mutation_revision=mutation_revision,
                    origin=origin,
                )

            except Exception as e:
                logger.error(f"MapSpec mutation failed for session {session_id}: {e}", exc_info=True)
                # 事务 rollback：恢复 mapspec + redis layers 到 mutation 前。
                # Fresh-session semantics: the intent branches snapshot
                # `loaded`, which the auto-init skeleton replaced — a
                # failed FIRST mutation must DISCARD the candidate, not
                # "restore" the skeleton as a residual spec.
                try:
                    rollback_ok = await self._rollback_to_snapshot(
                        session_id,
                        None if session_was_fresh else old_mapspec_snapshot,
                        old_layers_snapshot,
                        revision=prior_mutation_revision + 1,
                    )
                except Exception as rb_err:  # noqa: BLE001
                    # _rollback_to_snapshot isolates its own failures, but a
                    # raise here must not mask the honest is_error result.
                    logger.error(
                        "MapSpec transaction rollback raised for session %s: %s",
                        session_id, rb_err, exc_info=True,
                    )
                    rollback_ok = False
                # #1074(F-14): 提交前创建的 auto-checkpoint 描述的是从未
                # commit 的候选世代 —— 孤儿目录会让后续 rollback "恢复"到
                # 未提交状态并占用 20 槽保留额。清理 best-effort。
                if checkpoint_id_created:
                    try:
                        await discard_checkpoint(
                            self.store.get_session_dir(session_id),
                            checkpoint_id_created,
                        )
                    except Exception as ck_err:  # noqa: BLE001
                        logger.warning(
                            "orphan auto-checkpoint cleanup failed for %s/%s: %s",
                            session_id, checkpoint_id_created, ck_err,
                        )
                # #748: never claim a consistency guarantee the rollback did
                # not verify — during a sustained Redis outage commit AND
                # rollback fail together, and the old fixed text told the
                # agent the state was consistent.
                return MapSpecResult(
                    is_error=True,
                    origin=origin,
                    error_msg=f"MapSpec 意图更新失败: {e}",
                    correction_hint=(
                        "事务已回滚，last-known-good MapSpec 与运行时状态保持一致。"
                        if rollback_ok else
                        "回滚尝试失败——状态可能不一致：请先重新读取当前 MapSpec "
                        "（webgis_state_get）再重试，不要假设 last-known-good。"
                    ),
                )

    async def apply_presentation_batch(
        self,
        session_id: str,
        intents: List[PatchLayerPresentationIntent],
        *,
        origin: MutationOrigin = "agent",
        expected_revision: Optional[int] = None,
        pre_commit_check: Optional[Callable] = None,
    ) -> MapSpecBatchResult:
        """GISMutationBatch：N 个 presentation patch 一个事务。

        单锁 / 单 pre_state 读 / 单 spec 载入（含 F1 复活序）/ 逐 intent
        锁内守卫 / 单次校验 / 单次 review / 单 checkpoint / revision 恰 +1 /
        单次 save。per-intent 裁决：
        - pre_commit_check 返回非 None → refused（守卫拒绝，user-wins）；
        - 层族不命中 → not_found（跳过，不阻断其余）；
        - 其余 applied（_patch_layer_presentation 印记 presentation_owner）。

        全部 refused/not_found → 不落盘不递增（no-op 批）。异常 → 与
        apply_mutation 同款回滚（spec 未变时丢弃候选）。
        """
        _lock = session_lock_registry.lock(
            session_id, fail_on_degraded=True, fail_on_lost=True,
        )
        async with _lock:
            invalidate = getattr(session_data_manager, "invalidate_local_cache", None)
            if callable(invalidate):
                invalidate(session_id)
            pre_state = await session_data_manager.get_map_state(session_id)
            if pre_state.get("_cartographic_deleted") is True:
                return MapSpecBatchResult(
                    is_error=True, origin=origin,
                    error_msg="Session was deleted; stale MapSpec mutation rejected.",
                )
            if origin == "user" and expected_revision is None:
                return MapSpecBatchResult(
                    is_error=True, origin=origin,
                    error_msg="User MapSpec mutations require expected_revision.",
                )
            loaded = await self.store.get_mapspec(session_id, state_hint=pre_state)
            try:
                prior_mutation_revision = int(
                    pre_state.get("_cartographic_mutation_revision", 0)
                )
            except (TypeError, ValueError):
                prior_mutation_revision = 0
            if (
                expected_revision is not None
                and expected_revision != prior_mutation_revision
            ):
                return MapSpecBatchResult(
                    superseded=True, origin=origin, mapspec=loaded,
                    mutation_revision=prior_mutation_revision,
                    error_msg="MapSpec revision has changed.",
                    correction_hint=(
                        "Re-read MapSpec and retry with the current mutation_revision."
                    ),
                )
            session_was_fresh = loaded is None
            if not loaded:
                loaded = {
                    "version": "1.0", "view": {}, "sources": {}, "layers": [],
                    "layout": {
                        "legend": {"visible": True, "position": "top-right"},
                        "controls": [{"type": "navigation", "position": "top-right"}],
                    },
                    "thresholds": {"maxFeatures": 50000, "timeoutMs": 30000},
                }
            checkpoint_id_created: Optional[str] = None
            outcomes: List[BatchIntentOutcome] = []
            candidate: Optional[Dict[str, Any]] = None
            try:
                # 1. 逐 intent：锁内守卫 → family 命中 → patch。
                for intent in intents:
                    if pre_commit_check is not None:
                        guard_result = await pre_commit_check(
                            session_id, intent, origin, loaded
                        )
                        if guard_result is not None:
                            outcomes.append(BatchIntentOutcome(
                                layer_id=intent.layer_id, status="refused",
                                visible=intent.visible,
                                error_msg=str(getattr(guard_result, "error_msg", "") or ""),
                            ))
                            continue
                    matched = False
                    base = candidate if candidate is not None else loaded
                    patched_layers: List[Any] = []
                    for layer in base.get("layers", []) or []:
                        if not isinstance(layer, dict):
                            patched_layers.append(layer)
                            continue
                        if _should_remove_layer(layer, intent.layer_id):
                            matched = True
                            patched_layers.append(
                                _patch_layer_presentation(
                                    layer, intent.visible, intent.opacity,
                                    origin=str(origin),
                                )
                            )
                        else:
                            patched_layers.append(layer)
                    if not matched:
                        outcomes.append(BatchIntentOutcome(
                            layer_id=intent.layer_id, status="not_found",
                            visible=intent.visible,
                        ))
                        continue
                    if candidate is None:
                        candidate = {**base}
                    candidate["layers"] = patched_layers
                    outcomes.append(BatchIntentOutcome(
                        layer_id=intent.layer_id, status="applied",
                        visible=intent.visible,
                    ))

                applied = sum(1 for o in outcomes if o.status == "applied")
                refused = sum(1 for o in outcomes if o.status == "refused")
                not_found = sum(1 for o in outcomes if o.status == "not_found")
                if candidate is None or applied == 0:
                    # no-op 批：不落盘、不递增 revision、不建 checkpoint。
                    return MapSpecBatchResult(
                        mapspec=loaded, outcomes=outcomes,
                        applied_count=0, refused_count=refused,
                        not_found_count=not_found,
                        mutation_revision=prior_mutation_revision,
                        origin=origin,
                    )

                mapspec = candidate

                # 2. review（AUTO_SAFE ≤2 iter）—— 整批一次。
                cartographic_review: Dict[str, Any] = {}
                try:
                    cartographic_loop = review_and_repair_cartography(
                        mapspec, max_iterations=2,
                    )
                    mapspec = cartographic_loop.mapspec
                    cartographic_review = cartographic_loop.to_dict()
                except Exception as review_exc:  # noqa: BLE001
                    logger.warning(
                        "Cartographic desired-state review unavailable for batch %s: %s",
                        session_id, type(review_exc).__name__,
                    )
                    cartographic_review = self._review_failure(mapspec, review_exc)

                # 3. 校验 + prior-blocking 指纹缓存（与单笔同款）。
                validation = validate_mapspec(mapspec)
                warnings = [e["message"] for e in validation.get("errors", [])] + \
                    validation.get("warnings", [])
                prior_blocking: set = set()
                prior_fp = None
                if loaded:
                    try:
                        from app.lib.cartography.quality_loop import (
                            cartographic_fingerprint,
                        )
                        prior_fp = cartographic_fingerprint(loaded)
                    except Exception:  # noqa: BLE001
                        prior_fp = None
                if prior_fp is not None and prior_fp in self._prior_blocking_cache:
                    prior_blocking = self._prior_blocking_cache[prior_fp]
                elif loaded:
                    prior_blocking = self._blocking_error_codes(validate_mapspec(loaded))
                    if prior_fp is not None:
                        self._prior_blocking_cache[prior_fp] = prior_blocking
                        while len(self._prior_blocking_cache) > 256:
                            self._prior_blocking_cache.popitem(next(iter(self._prior_blocking_cache)))
                new_blocking = self._blocking_error_codes(validation) - prior_blocking
                if new_blocking:
                    msg = "; ".join(
                        e["message"] for e in validation.get("errors", [])
                        if e.get("code") in new_blocking
                    )
                    return MapSpecBatchResult(
                        is_error=True, origin=origin,
                        error_msg=f"MapSpec 校验失败: {msg}",
                        correction_hint="批量意图会引入无效引用；last-known-good 保持不变。",
                        outcomes=outcomes,
                    )

                # 4. lost 复检 → checkpoint → revision+1 → save（单事务）。
                if getattr(_lock, "lost", False):
                    return MapSpecBatchResult(
                        is_error=True, origin=origin,
                        error_msg="Session lock ownership lost before commit; batch aborted.",
                        correction_hint="锁所有权在提交前丢失（TTL 过期）。请重读 MapSpec 后重试。",
                    )
                session_dir = self.store.get_session_dir(session_id)
                ckpt_res = await create_checkpoint(
                    mapspec, session_dir, session_data_manager,
                )
                checkpoint_id_created = ckpt_res.get("checkpoint_id")
                mutation_revision = prior_mutation_revision + 1
                await self.store.save_mapspec(
                    session_id, mapspec, mutation_revision=mutation_revision,
                )
                return MapSpecBatchResult(
                    mapspec=mapspec, outcomes=outcomes,
                    applied_count=applied, refused_count=refused,
                    not_found_count=not_found,
                    mutation_revision=mutation_revision,
                    origin=origin,
                    checkpoint_id=checkpoint_id_created,
                    mapspec_fingerprint=cartographic_review.get("final_fingerprint"),
                    cartographic_review=cartographic_review,
                    warnings=warnings,
                )
            except Exception as e:
                logger.error(
                    f"MapSpec batch mutation failed for session {session_id}: {e}",
                    exc_info=True,
                )
                try:
                    rollback_ok = await self._rollback_to_snapshot(
                        session_id,
                        None if session_was_fresh else loaded,
                        [dict(layer) if isinstance(layer, dict) else layer
                         for layer in (pre_state.get("layers", []) or [])],
                        revision=prior_mutation_revision + 1,
                    )
                except Exception as rb_err:  # noqa: BLE001
                    logger.error(
                        "MapSpec batch rollback raised for %s: %s",
                        session_id, rb_err, exc_info=True,
                    )
                    rollback_ok = False
                if checkpoint_id_created:
                    try:
                        await discard_checkpoint(
                            self.store.get_session_dir(session_id),
                            checkpoint_id_created,
                        )
                    except Exception as ck_err:  # noqa: BLE001
                        logger.warning(
                            "orphan batch checkpoint cleanup failed for %s/%s: %s",
                            session_id, checkpoint_id_created, ck_err,
                        )
                return MapSpecBatchResult(
                    is_error=True, origin=origin,
                    error_msg=f"MapSpec 批量意图更新失败: {e}",
                    correction_hint=(
                        "事务已回滚，last-known-good MapSpec 与运行时状态保持一致。"
                        if rollback_ok else
                        "回滚尝试失败——状态可能不一致：请先重新读取当前 MapSpec 再重试。"
                    ),
                    outcomes=outcomes,
                )

    async def _rollback_to_snapshot(
        self,
        session_id: str,
        old_mapspec: Optional[Dict[str, Any]],
        old_layers: List[Any],
        revision: Optional[int] = None,
    ) -> bool:
        """恢复 mutation 前的 mapspec + redis layers，避免半提交。

        ``old_mapspec`` / ``old_layers`` are deep-copied snapshots captured at
        load time (review P1-1): they are independent of the live store state, so
        restoring them is not a silent no-op even under the in-memory backend's
        reference aliasing.

        v2(audit F4): ``revision``（prior+1）随恢复的旧 spec 一并落地 —— 回滚
        绝不把令牌拨回旧值：失败尝试可能已把 N+1 暴露给读者，回退会让持有
        N+1 的客户端在旧 spec 上通过相等 CAS。方向约束：rev ≥ spec 世代是
        安全（客户端被 superseded 后重读），spec 世代 > rev 才是危险方向。
        layers 恢复经 layer_op 并入同一提交事务（后端支持时）。
        """
        try:
            if old_mapspec is not None:
                saved = await self.store.save_mapspec(
                    session_id, old_mapspec,
                    mutation_revision=revision,
                    layer_op=("replace", "", list(old_layers)),
                )
                if not (isinstance(saved, dict) and saved.get("layers_persisted")):
                    # 测试替身缺 commit_mapspec_state —— layers 恢复退回独立写。
                    await session_data_manager.set_map_state(session_id, "layers", old_layers)
            else:
                # First mutation: there is no last-known-good spec. Discard the
                # candidate that may already have been written so rollback does
                # not invent a residual MapSpec.
                discard = getattr(self.store, "discard_mapspec", None)
                if callable(discard):
                    await discard(session_id)
                await session_data_manager.set_map_state(session_id, "layers", old_layers)
        except Exception as rb_err:
            # rollback 自身失败必须大声报错——绝不静默。
            logger.error(
                f"MapSpec transaction rollback FAILED for session {session_id}: {rb_err}",
                exc_info=True,
            )
            return False
        return True


mapspec_lifecycle_engine = MapSpecLifecycleEngine()
