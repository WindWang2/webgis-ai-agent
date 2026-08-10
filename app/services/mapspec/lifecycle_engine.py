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
from typing import Any, Dict, List, Optional, Tuple, Union

from app.services.session_data import session_data_manager
from app.services.mapspec.store import mapspec_store_instance, _should_remove_layer
from app.services.mapspec.pipeline import process_layer_ingestion
from app.services.mapspec.coordinator import validate as validate_mapspec
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

    def to_dict(self) -> Dict[str, Any]:
        if self.is_error:
            res = {"success": False, "message": self.error_msg}
            if self.correction_hint:
                res["correction_hint"] = self.correction_hint
            return res
        return {
            "success": True,
            "mapspec": self.mapspec,
            "warnings": self.warnings,
            "is_compiled": self.is_compiled,
            "checkpoint_id": self.checkpoint_id,
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
class RemoveLayerIntent:
    layer_id: str


@dataclass
class SetLayoutIntent:
    legend: Optional[Dict[str, Any]] = None
    controls: Optional[List[Dict[str, Any]]] = None
    margins: Optional[Dict[str, Any]] = None


@dataclass
class CheckpointIntent:
    checkpoint_id: Optional[str] = None


@dataclass
class RollbackIntent:
    checkpoint_id: str


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


MutationIntent = Union[
    InitProjectIntent,
    SetViewIntent,
    UpsertLayerIntent,
    RemoveLayerIntent,
    SetLayoutIntent,
    CheckpointIntent,
    RollbackIntent,
    SetTimeIntent,
]


class MapSpecLifecycleEngine:
    """深层 MapSpec 意图与生命周期引擎"""

    def __init__(self):
        self.store = mapspec_store_instance
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._MAX_LOCKS = 200

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        if len(self._session_locks) > self._MAX_LOCKS:
            evict_count = self._MAX_LOCKS // 4
            for sid in list(self._session_locks.keys())[:evict_count]:
                lock_to_evict = self._session_locks[sid]
                if not lock_to_evict.locked():
                    self._session_locks.pop(sid, None)
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    @staticmethod
    def _blocking_error_codes(validation: Dict[str, Any]) -> set:
        return {
            e.get("code") for e in validation.get("errors", [])
            if e.get("code") in BLOCKING_VALIDATION_CODES
        }

    async def apply_mutation(
        self,
        session_id: str,
        intent: MutationIntent,
    ) -> MapSpecResult:
        """原子执行 MapSpec 意图变迁，带 per-session 互斥锁 + 事务 rollback。

        双层锁：外层 distributed lock（Redis，跨 pod 互斥；单 worker/测试降级为
        in-process），内层 asyncio.Lock（同进程内协程序列化）。两层都持有才能
        避免跨 pod 的同 session 并发 mutation 产生 lost update。
        """
        lock = self._get_lock(session_id)
        # Two independent locks acquired in one async-with (no extra indent):
        # outer = distributed (Redis, cross-pod; falls back to in-process),
        # inner = asyncio (in-process coroutine serialization).
        async with session_lock_registry.lock(session_id), lock:
            # 事务快照：失败时回滚 mapspec + redis layers 到此刻状态。
            old_map_state = await session_data_manager.get_map_state(session_id)
            old_layers = list(old_map_state.get("layers", []) or [])

            try:
                loaded = await self.store.get_mapspec(session_id)
                # Deep-copy before mutating: the in-memory session store returns
                # REFERENCES, so in-place mutation would also mutate the "prior"
                # snapshot (aliasing) and mask newly-introduced blocking errors.
                # The Redis backend already returns fresh copies; deepcopy is a
                # no-op-equivalent safety there. prior_mapspec stays un-mutated.
                # Offload the deepcopy: for a large inline-GeoJSON mapspec this is
                # O(features) and must not block the event loop (REL-07).
                prior_mapspec = loaded
                mapspec = (
                    await asyncio.to_thread(copy.deepcopy, loaded)
                    if loaded is not None else None
                )

                # 1. 针对未初始化会话自动构建根框架
                if not mapspec and not isinstance(intent, (InitProjectIntent, RollbackIntent)):
                    init_res = await self.store.save_mapspec(session_id, {
                        "version": "1.0",
                        "view": {},
                        "sources": {},
                        "layers": [],
                        "layout": {
                            "legend": {"visible": True, "position": "top-right"},
                            "controls": [{"type": "navigation", "position": "top-right"}],
                        },
                        "thresholds": {"maxFeatures": 50000, "timeoutMs": 30000},
                    })
                    mapspec = copy.deepcopy(init_res["mapspec"])
                    prior_mapspec = None

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
                    mapspec = {
                        "version": "1.0",
                        "view": intent.view or {},
                        "sources": {},
                        "layers": [],
                        "layout": {
                            "legend": {"visible": True, "position": "top-right"},
                            "controls": [{"type": "navigation", "position": "top-right"}],
                        },
                        "thresholds": intent.thresholds or {"maxFeatures": 50000, "timeoutMs": 30000},
                    }

                elif isinstance(intent, SetViewIntent):
                    view = mapspec.setdefault("view", {})
                    if intent.center is not None:
                        view["center"] = intent.center
                    if intent.zoom is not None:
                        view["zoom"] = intent.zoom
                    if intent.pitch is not None:
                        view["pitch"] = intent.pitch
                    if intent.bearing is not None:
                        view["bearing"] = intent.bearing

                elif isinstance(intent, UpsertLayerIntent):
                    session_dir = self.store.get_session_dir(session_id)
                    # 卸载重计算（GeoJSON profiling / raster PNG 渲染）到线程，
                    # 释放 event loop 给其它 session 的 I/O（REL-07）。
                    processed_layer, source_entry, suggested_view = await asyncio.to_thread(
                        process_layer_ingestion,
                        mapspec, intent.layer, intent.source_data, session_dir,
                    )
                    source_id = processed_layer.get("source", "default_source")
                    mapspec.setdefault("sources", {})[source_id] = source_entry

                    if suggested_view:
                        mapspec.setdefault("view", {})
                        mapspec["view"]["center"] = suggested_view["center"]
                        mapspec["view"]["zoom"] = suggested_view["zoom"]

                    layers = mapspec.setdefault("layers", [])
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

                elif isinstance(intent, RemoveLayerIntent):
                    layers = mapspec.get("layers", [])
                    filtered_layers = [layer for layer in layers if not _should_remove_layer(layer, intent.layer_id)]
                    mapspec["layers"] = filtered_layers
                    pending_layer_op = ("remove", intent.layer_id, None)
                    auto_checkpoint = True

                elif isinstance(intent, SetLayoutIntent):
                    layout = mapspec.setdefault("layout", {})
                    if intent.legend is not None:
                        layout["legend"] = intent.legend
                    if intent.controls is not None:
                        layout["controls"] = intent.controls
                    if intent.margins is not None:
                        layout["margins"] = intent.margins

                elif isinstance(intent, CheckpointIntent):
                    session_dir = self.store.get_session_dir(session_id)
                    ckpt_res = await create_checkpoint(
                        mapspec, session_dir, session_data_manager, intent.checkpoint_id
                    )
                    checkpoint_id_created = ckpt_res.get("checkpoint_id")
                    ckpt_ref_count = ckpt_res.get("ref_count", 0)

                elif isinstance(intent, RollbackIntent):
                    session_dir = self.store.get_session_dir(session_id)
                    rb_res = await rollback_checkpoint(
                        session_dir, intent.checkpoint_id, session_data_manager
                    )
                    if not rb_res.get("success"):
                        return MapSpecResult(
                            is_error=True,
                            error_msg=rb_res.get("message", "Rollback failed"),
                        )
                    mapspec = rb_res["mapspec"]
                    ckpt_ref_count = rb_res.get("ref_count", 0)
                    # rollback 恢复了 refs + mapspec；运行时 layers 需整体对齐到
                    # 恢复后的 mapspec.layers（用特殊 op 标记）。
                    is_rollback = True

                elif isinstance(intent, SetTimeIntent):
                    time_cfg = mapspec.setdefault("time", {
                        "enabled": True,
                        "field": "timestamp",
                        "type": "continuous",
                        "extent": [],
                        "current": None,
                        "step": 1.0,
                        "speed": 1.0,
                    })
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

                # 3. Pre-compile 校验。Rollback 不走拒绝逻辑（恢复的是历史合法 spec）。
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
                            error_msg=f"MapSpec 校验失败: {msg}",
                            correction_hint=(
                                "该意图会引入无效的 source 引用或非法 stops，已拒绝；"
                                "last-known-good MapSpec 保持不变。"
                            ),
                        )

                # 4. 提交（checkpoint → save_mapspec → redis layers），任一失败回滚。
                if auto_checkpoint and not checkpoint_id_created:
                    session_dir = self.store.get_session_dir(session_id)
                    ckpt_res = await create_checkpoint(mapspec, session_dir, session_data_manager)
                    checkpoint_id_created = ckpt_res.get("checkpoint_id")
                    ckpt_ref_count = ckpt_res.get("ref_count", 0)

                await self.store.save_mapspec(session_id, mapspec)

                if pending_layer_op is not None:
                    op, layer_id, layer = pending_layer_op
                    if op == "upsert":
                        await session_data_manager.update_layer_in_state(
                            session_id, layer_id, layer
                        )
                    elif op == "remove":
                        await session_data_manager.remove_layer_from_state(session_id, layer_id)
                elif is_rollback:
                    # 把运行时 layers 对齐到恢复后的 mapspec.layers。
                    await session_data_manager.set_map_state(
                        session_id, "layers", list(mapspec.get("layers", []))
                    )

                return MapSpecResult(
                    mapspec=mapspec,
                    warnings=warnings,
                    is_compiled=validation.get("success", False),
                    checkpoint_id=checkpoint_id_created,
                    ref_count=ckpt_ref_count,
                    is_error=False,
                )

            except Exception as e:
                logger.error(f"MapSpec mutation failed for session {session_id}: {e}", exc_info=True)
                # 事务 rollback：恢复 mapspec + redis layers 到 mutation 前。
                await self._rollback_to_snapshot(session_id, old_map_state, old_layers)
                return MapSpecResult(
                    is_error=True,
                    error_msg=f"MapSpec 意图更新失败: {e}",
                    correction_hint="事务已回滚，last-known-good MapSpec 与运行时状态保持一致。",
                )

    async def _rollback_to_snapshot(
        self,
        session_id: str,
        old_map_state: Dict[str, Any],
        old_layers: List[Any],
    ) -> None:
        """恢复 mutation 前的 mapspec + redis layers，避免半提交。"""
        try:
            old_mapspec = old_map_state.get("mapspec")
            if old_mapspec is not None:
                await self.store.save_mapspec(session_id, old_mapspec)
            await session_data_manager.set_map_state(session_id, "layers", old_layers)
        except Exception as rb_err:
            # rollback 自身失败必须大声报错——绝不静默。
            logger.error(
                f"MapSpec transaction rollback FAILED for session {session_id}: {rb_err}",
                exc_info=True,
            )


mapspec_lifecycle_engine = MapSpecLifecycleEngine()
