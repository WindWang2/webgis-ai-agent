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
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

MutationOrigin = Literal["agent", "user", "system"]

from app.services.session_data import session_data_manager
from app.services.mapspec.store import mapspec_store_instance, _should_remove_layer
from app.services.mapspec.pipeline import process_layer_ingestion
from app.services.mapspec.coordinator import validate as validate_mapspec
from app.lib.cartography.quality_loop import (
    cartographic_fingerprint,
    review_and_repair_cartography,
)
from app.services.mapspec.checkpoint import snapshot as create_checkpoint, rollback as rollback_checkpoint
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
    # ADR-0052: deterministic cartography-semantic findings (paint ↔ legend
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
) -> Dict[str, Any]:
    patched = dict(layer)
    if visible is not None:
        layout = dict(patched.get("layout") or {})
        layout["visibility"] = "visible" if visible else "none"
        patched["layout"] = layout
    if opacity is not None:
        paint = dict(patched.get("paint") or {})
        paint["opacity"] = opacity
        type_key = _OPACITY_PAINT_KEYS.get(str(patched.get("type") or ""))
        if type_key:
            paint[type_key] = opacity
        patched["paint"] = paint
    return patched


MutationIntent = Union[
    InitProjectIntent,
    SetViewIntent,
    UpsertSourceIntent,
    UpsertLayerIntent,
    PatchLayerPresentationIntent,
    PatchComponentIntent,
    RemoveLayerIntent,
    ReorderLayersIntent,
    SetLayoutIntent,
    CheckpointIntent,
    RollbackIntent,
    SetBasemapIntent,
    SetTimeIntent,
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
    ) -> MapSpecResult:
        """原子执行 MapSpec 意图变迁，带 per-session 分布式锁 + 事务 rollback。

        锁：session_lock_registry.lock(session_id) — Redis 跨 pod 互斥（生产），
        in-process asyncio.Lock（单 worker / 测试）。该锁序列化同 session 的并发
        mutation，避免 lost update。

        origin 为 agent|user|system。user 必须带 expected_revision；缺省 origin=agent
        且省略 expected_revision 时仍提交（既有 tool 兼容）。expected_revision 与当前
        revision 不一致则 superseded，MapSpec 不变（ADR-0058）。
        """
        async with session_lock_registry.lock(session_id):
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
                current_mapspec = await self.store.get_mapspec(session_id)
                return MapSpecResult(
                    superseded=True,
                    is_error=False,
                    origin=origin,
                    mapspec=current_mapspec,
                    mutation_revision=prior_mutation_revision,
                    error_msg="MapSpec revision has changed.",
                    correction_hint=(
                        "Re-read MapSpec and retry with the current mutation_revision."
                    ),
                )
            try:
                loaded = await self.store.get_mapspec(session_id)
                prior_mapspec = loaded
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
                checkpoint_id_created: Optional[str] = None
                ckpt_ref_count = 0
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
                            layers[i] = processed_layer
                            updated = True
                            break
                    if not updated:
                        layers.append(processed_layer)

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
                                    layer, intent.visible, intent.opacity
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

                elif isinstance(intent, RemoveLayerIntent):
                    # V3 COW: layers mutation, shallow copy + new filtered list
                    old_mapspec_snapshot = loaded
                    mapspec = {**loaded} if loaded else {}
                    layers = mapspec.get("layers", [])
                    mapspec["layers"] = [lay for lay in layers if not _should_remove_layer(lay, intent.layer_id)]
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
                    prior_blocking = (
                        self._blocking_error_codes(validate_mapspec(prior_mapspec))
                        if prior_mapspec else set()
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
                if auto_checkpoint and not checkpoint_id_created:
                    session_dir = self.store.get_session_dir(session_id)
                    ckpt_res = await create_checkpoint(mapspec, session_dir, session_data_manager)
                    checkpoint_id_created = ckpt_res.get("checkpoint_id")
                    ckpt_ref_count = ckpt_res.get("ref_count", 0)

                await self.store.save_mapspec(session_id, mapspec)

                if pending_layer_op is not None:
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
                elif is_rollback:
                    # 把运行时 layers 对齐到恢复后的 mapspec.layers。
                    persisted = await session_data_manager.set_map_state(
                        session_id, "layers", list(mapspec.get("layers", []))
                    )
                    if persisted is False:
                        raise RuntimeError("rollback runtime layer persistence rejected")

                mutation_revision = prior_mutation_revision + 1
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
                    )
                except Exception as rb_err:  # noqa: BLE001
                    # _rollback_to_snapshot isolates its own failures, but a
                    # raise here must not mask the honest is_error result.
                    logger.error(
                        "MapSpec transaction rollback raised for session %s: %s",
                        session_id, rb_err, exc_info=True,
                    )
                    rollback_ok = False
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

    async def _rollback_to_snapshot(
        self,
        session_id: str,
        old_mapspec: Optional[Dict[str, Any]],
        old_layers: List[Any],
    ) -> bool:
        """恢复 mutation 前的 mapspec + redis layers，避免半提交。

        ``old_mapspec`` / ``old_layers`` are deep-copied snapshots captured at
        load time (review P1-1): they are independent of the live store state, so
        restoring them is not a silent no-op even under the in-memory backend's
        reference aliasing.
        """
        try:
            if old_mapspec is not None:
                await self.store.save_mapspec(session_id, old_mapspec)
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
