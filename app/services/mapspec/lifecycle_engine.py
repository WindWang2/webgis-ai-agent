"""MapSpecLifecycleEngine - 核心 MapSpec 意图声明与生命周期引擎。

深入封装 MapSpec 意图变迁 (InitProject, SetView, UpsertLayer, RemoveLayer, SetLayout)、
自动 Spatial Profiling、Pre-compile Structure Validation、Redis map_state 双写与 Checkpoint 物理快照。
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from app.services.session_data import session_data_manager
from app.services.mapspec.store import mapspec_store_instance, _should_remove_layer
from app.services.mapspec.pipeline import process_layer_ingestion
from app.services.mapspec.coordinator import validate as validate_mapspec
from app.services.mapspec.checkpoint import snapshot as create_checkpoint, rollback as rollback_checkpoint

logger = logging.getLogger(__name__)


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


MutationIntent = Union[
    InitProjectIntent,
    SetViewIntent,
    UpsertLayerIntent,
    RemoveLayerIntent,
    SetLayoutIntent,
    CheckpointIntent,
    RollbackIntent,
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
            for sid in list(self._session_locks.keys())[:
                evict_count]:
                lock_to_evict = self._session_locks[sid]
                if not lock_to_evict.locked():
                    self._session_locks.pop(sid, None)
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    async def apply_mutation(
        self,
        session_id: str,
        intent: MutationIntent,
    ) -> MapSpecResult:
        """原子执行 MapSpec 意图变迁，带 per-session 互斥锁 protection"""
        lock = self._get_lock(session_id)
        async with lock:
            try:
                mapspec = await self.store.get_mapspec(session_id)

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
                    mapspec = init_res["mapspec"]

                auto_checkpoint = False
                checkpoint_id_created = None
                ckpt_ref_count = 0

                # 2. 分发具体意图
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
                    processed_layer, source_entry, suggested_view = process_layer_ingestion(
                        mapspec, intent.layer, intent.source_data, session_dir
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

                    # Keep Redis map_state dual-write in sync for runtime state
                    await session_data_manager.update_layer_in_state(
                        session_id,
                        processed_layer.get("id", "layer"),
                        processed_layer,
                    )
                    auto_checkpoint = True

                elif isinstance(intent, RemoveLayerIntent):
                    layers = mapspec.get("layers", [])
                    filtered_layers = [layer for layer in layers if not _should_remove_layer(layer, intent.layer_id)]
                    mapspec["layers"] = filtered_layers
                    await session_data_manager.remove_layer_from_state(session_id, intent.layer_id)
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

                # 3. Pre-compile 校验
                validation = validate_mapspec(mapspec)
                warnings = [e["message"] for e in validation.get("errors", [])] + validation.get("warnings", [])

                # 4. 结构改变时先生成物理 Checkpoint（确保 checkpoint 成功后再持久化）
                if auto_checkpoint and not checkpoint_id_created:
                    session_dir = self.store.get_session_dir(session_id)
                    ckpt_res = await create_checkpoint(mapspec, session_dir, session_data_manager)
                    checkpoint_id_created = ckpt_res.get("checkpoint_id")
                    ckpt_ref_count = ckpt_res.get("ref_count", 0)

                # 5. 双写持久化 (Disk + Redis) — checkpoint 成功后再写入
                await self.store.save_mapspec(session_id, mapspec)

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
                return MapSpecResult(
                    is_error=True,
                    error_msg=f"MapSpec 意图更新失败: {e}",
                    correction_hint="请检查传入的图层配置或图层数据格式。",
                )


mapspec_lifecycle_engine = MapSpecLifecycleEngine()
