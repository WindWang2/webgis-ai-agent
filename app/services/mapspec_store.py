"""MapSpecStore Adapter (app/services/mapspec_store.py).

向后兼容 Adapter，内部统一委派给 deep `MapSpecLifecycleEngine` (app.services.mapspec)。
保持所有旧的导出的方法签名与逻辑完全兼容。
"""
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.mapspec import (
    mapspec_lifecycle_engine,
    InitProjectIntent,
    SetViewIntent,
    UpsertLayerIntent,
    RemoveLayerIntent,
    SetLayoutIntent,
    CheckpointIntent,
    RollbackIntent,
)
from app.services.mapspec.store import (
    BASE_STORAGE_DIR,
    PROJECT_ROOT,
    LABEL_LAYER_SUFFIX,
    _should_remove_layer,
    view_has_center,
)

logger = logging.getLogger(__name__)


def _with_evidence(res, base: Dict[str, Any]) -> Dict[str, Any]:
    """Merge real MapSpec-result evidence into an adapter return dict.

    HARNESS-V2 / CARTO-LOOP: the harness MapSpecValidity ladder reads
    ``is_compiled`` (real validate() outcome) and ``success`` from the recorded
    tool result. Without this forwarding the harness is starved of evidence and
    every production run scores 0% validity. Also surfaces ``correction_hint`` /
    ``message`` on rejection so the agent gets actionable guidance (not a bare
    failure). Review P1-3 / P2-2.
    """
    base["is_compiled"] = res.is_compiled
    if res.warnings:
        base["warnings"] = res.warnings
    if res.checkpoint_id:
        base["checkpoint_id"] = res.checkpoint_id
    if res.is_error:
        base["message"] = res.error_msg
        if res.correction_hint:
            base["correction_hint"] = res.correction_hint
    return base


class MapSpecStore:
    """MapSpecStore 兼容 Adapter 代理"""

    def __init__(self):
        self.engine = mapspec_lifecycle_engine
        self.raw_store = self.engine.store

    def get_session_dir(self, session_id: str) -> Path:
        return self.raw_store.get_session_dir(session_id)

    async def get_mapspec(self, session_id: str) -> Optional[Dict[str, Any]]:
        return await self.raw_store.get_mapspec(session_id)

    async def save_mapspec(self, session_id: str, mapspec: Dict[str, Any]) -> Dict[str, Any]:
        return await self.raw_store.save_mapspec(session_id, mapspec)

    async def init_project(
        self,
        session_id: str,
        view: Optional[Dict[str, Any]] = None,
        thresholds: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        res = await self.engine.apply_mutation(session_id, InitProjectIntent(view=view, thresholds=thresholds))
        return _with_evidence(res, {
            "success": not res.is_error,
            "mapspec": res.mapspec,
        })

    async def set_view(
        self,
        session_id: str,
        center: Optional[List[float]] = None,
        zoom: Optional[float] = None,
        pitch: Optional[float] = None,
        bearing: Optional[float] = None,
    ) -> Dict[str, Any]:
        res = await self.engine.apply_mutation(
            session_id, SetViewIntent(center=center, zoom=zoom, pitch=pitch, bearing=bearing)
        )
        return _with_evidence(res, {
            "success": not res.is_error,
            "mapspec": res.mapspec,
        })

    async def source_profile(
        self,
        session_id: str,
        source_id: str,
        geojson_data: Any,
    ) -> Dict[str, Any]:
        from app.services.spatial_meta_profiler import profile_geojson_source
        from app.services.mapspec_source import store_data

        mapspec = await self.get_mapspec(session_id)
        if not mapspec:
            res = await self.init_project(session_id)
            mapspec = res["mapspec"]

        # profile_geojson_source loops every feature in pure Python — offload
        # so a large inline GeoJSON can't block the event loop.
        profile = await asyncio.to_thread(profile_geojson_source, geojson_data)

        if "sources" not in mapspec:
            mapspec["sources"] = {}
        if source_id not in mapspec["sources"]:
            mapspec["sources"][source_id] = {"type": "geojson"}

        mapspec["sources"][source_id]["profile"] = profile
        if isinstance(geojson_data, dict):
            store_data(mapspec["sources"][source_id], geojson_data)
        elif isinstance(geojson_data, str) and (geojson_data.startswith("http") or geojson_data.startswith("/")):
            store_data(mapspec["sources"][source_id], geojson_data)

        await self.save_mapspec(session_id, mapspec)
        return profile

    async def layer_upsert(
        self,
        session_id: str,
        layer: Dict[str, Any],
        source_data: Optional[Any] = None,
    ) -> Dict[str, Any]:
        res = await self.engine.apply_mutation(
            session_id, UpsertLayerIntent(layer=layer, source_data=source_data)
        )
        # Find processed layer in updated mapspec
        processed_layer = layer
        if res.mapspec and "layers" in res.mapspec:
            for existing_layer in res.mapspec["layers"]:
                if existing_layer.get("id") == layer.get("id"):
                    processed_layer = existing_layer
                    break

        return _with_evidence(res, {
            "success": not res.is_error,
            "mapspec": res.mapspec,
            "layer": processed_layer,
        })

    async def layer_remove(self, session_id: str, layer_id: str) -> Dict[str, Any]:
        res = await self.engine.apply_mutation(session_id, RemoveLayerIntent(layer_id=layer_id))
        return _with_evidence(res, {
            "success": not res.is_error,
            "mapspec": res.mapspec,
            "removed_id": layer_id,
        })

    async def compile_mapspec_cli(self, session_id: str, out_dir: Optional[Path] = None) -> Dict[str, Any]:
        mapspec = await self.get_mapspec(session_id)
        if not mapspec:
            return {"success": False, "message": "MapSpec not found"}
        session_dir = self.get_session_dir(session_id)
        target_out_dir = out_dir or (session_dir / "compiled")
        mapspec_file = session_dir / "mapspec.json"
        from app.services.mapspec.coordinator import compile_via_cli
        return await compile_via_cli(mapspec_file, target_out_dir)

    async def validate_mapspec(self, session_id: str) -> Dict[str, Any]:
        mapspec = await self.get_mapspec(session_id)
        if not mapspec:
            return {"success": False, "message": "MapSpec not found", "errors": ["MapSpec not initialized"]}
        from app.services.mapspec.coordinator import validate
        return validate(mapspec)

    async def layout_set(
        self,
        session_id: str,
        legend: Optional[Dict[str, Any]] = None,
        controls: Optional[List[Dict[str, Any]]] = None,
        margins: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        res = await self.engine.apply_mutation(
            session_id, SetLayoutIntent(legend=legend, controls=controls, margins=margins)
        )
        return _with_evidence(res, {
            "success": not res.is_error,
            "layout": res.mapspec.get("layout", {}) if res.mapspec else {},
            "mapspec": res.mapspec,
        })

    async def checkpoint(self, session_id: str, checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
        res = await self.engine.apply_mutation(session_id, CheckpointIntent(checkpoint_id=checkpoint_id))
        return _with_evidence(res, {
            "success": not res.is_error,
            "checkpoint_id": res.checkpoint_id,
            "ref_count": res.ref_count,
            "summary": f"Checkpoint '{res.checkpoint_id}' created",
        })

    async def rollback(self, session_id: str, checkpoint_id: str) -> Dict[str, Any]:
        res = await self.engine.apply_mutation(session_id, RollbackIntent(checkpoint_id=checkpoint_id))
        return _with_evidence(res, {
            "success": not res.is_error,
            "checkpoint_id": checkpoint_id,
            "mapspec": res.mapspec,
            "summary": f"Rolled back to checkpoint '{checkpoint_id}'",
        })


mapspec_store = MapSpecStore()
