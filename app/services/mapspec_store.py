"""MapSpecStore Adapter (app/services/mapspec_store.py).

向后兼容 Adapter，内部统一委派给 deep `MapSpecLifecycleEngine` (app.services.mapspec)。
保持所有旧的导出的方法签名与逻辑完全兼容。
"""
import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.mapspec import (
    mapspec_lifecycle_engine,
    InitProjectIntent,
    SetViewIntent,
    UpsertSourceIntent,
    UpsertLayerIntent,
    PatchComponentIntent,
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
from app.services.session_data import session_data_manager
from app.services.session_data_protocol import is_unavailable_ref

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
    # ADR-0078: forward deterministic cartography-semantic findings (paint↔legend
    # drift, cardinality, domain, …) so the Harness semantic_errors evidence
    # channel is not starved in production. Structural validity (is_compiled) ≠
    # thematic correctness; these findings are what make drift detectable.
    if getattr(res, "cartography_findings", None):
        base["cartography_findings"] = res.cartography_findings
    if getattr(res, "cartographic_review", None) is not None:
        base["cartographic_review"] = res.cartographic_review
    if getattr(res, "mapspec_fingerprint", None) is not None:
        base["mapspec_fingerprint"] = res.mapspec_fingerprint
    base["runtime_observation_seq"] = getattr(res, "runtime_observation_seq", 0)
    base["mutation_revision"] = getattr(res, "mutation_revision", 0)
    if getattr(res, "superseded", False):
        base["success"] = False
        base["status"] = "superseded"
        base["mapspec"] = res.mapspec
        if res.error_msg:
            base["message"] = res.error_msg
        if res.correction_hint:
            base["correction_hint"] = res.correction_hint
        return base
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
        if isinstance(geojson_data, str) and geojson_data.startswith("ref:"):
            canonical_ref = await session_data_manager.resolve_alias(session_id, geojson_data)
            descriptor = await session_data_manager.get_ref_descriptor(
                session_id, canonical_ref
            )
            if not isinstance(descriptor, dict):
                raise ValueError("source ref is missing or not owned by this session")
            # #688 收敛：descriptor→profile 投影与授权路径同源，委托
            # spatial_meta_profiler.profile_from_descriptor（形状属主）；
            # feature_count 缺失的 legacy descriptor 走原内联形状兜底。
            from app.services.spatial_meta_profiler import profile_from_descriptor

            profile = profile_from_descriptor(descriptor) or {
                "bbox": descriptor.get("bbox"),
                "crs": None,
                "crs_status": "unknown",
                "featureCount": descriptor.get("feature_count"),
                "geometryTypes": descriptor.get("geometry_types") or [],
                "fields": {},
                "fields_status": "unknown",
                "suggestedView": {},
                "temporalProfile": None,
            }
            ref_id = canonical_ref
        else:
            # Profiling is the one authorized full-data scan. Persist the body
            # once behind a session-owned ref; MapSpec/review retain metadata.
            profile = await asyncio.to_thread(profile_geojson_source, geojson_data)
            ref_id = (
                await session_data_manager.store(session_id, geojson_data, prefix="geojson")
                if isinstance(geojson_data, dict)
                else None
            )
            # R3-3 (sibling path): a Redis outage makes store() return the
            # unavailable-ref sentinel. UpsertSourceIntent would persist a
            # source pointing at a ref with no payload anywhere — and unlike
            # the tool path there is no result_ref for the dispatch-level
            # check to catch. Refuse before the MapSpec write.
            if is_unavailable_ref(ref_id):
                raise RuntimeError(
                    "session store unavailable: refusing to persist source "
                    "with an unreadable ref; retry shortly"
                )

        profile_payload = json.dumps(
            profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        source: Dict[str, Any] = {
            "type": "geojson",
            "profile": profile,
            "profile_fingerprint": "profile-sha256:" + hashlib.sha256(profile_payload).hexdigest(),
        }
        if ref_id:
            source.update({
                "ref": ref_id,
                "ref_id": ref_id,
                "data_fingerprint": "ref-sha256:" + hashlib.sha256(ref_id.encode()).hexdigest(),
            })
        elif isinstance(geojson_data, str):
            source["url"] = geojson_data
            source["data_fingerprint"] = (
                "url-sha256:" + hashlib.sha256(geojson_data.encode()).hexdigest()
            )

        res = await self.engine.apply_mutation(
            session_id, UpsertSourceIntent(source_id=source_id, source=source)
        )
        if res.is_error:
            raise RuntimeError(res.error_msg)
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
        from app.lib.cartography.quality_loop import review_cartography
        result = validate(mapspec)
        cartographic_review = review_cartography(mapspec).to_dict()
        result["mapspec_fingerprint"] = cartographic_review["final_fingerprint"]
        result["cartographic_review"] = cartographic_review
        return result

    async def set_basemap(self, session_id: str, provider_id: str) -> Dict[str, Any]:
        """#722: sanctioned basemap mutation so the persisted spec tracks
        BASE_LAYER_CHANGE commands emitted by the legacy basemap tools."""
        from app.services.mapspec.lifecycle_engine import SetBasemapIntent
        res = await self.engine.apply_mutation(
            session_id, SetBasemapIntent(provider_id=provider_id),
        )
        return _with_evidence(res, {
            "success": not res.is_error,
            "basemap": {"providerId": provider_id},
        })

    async def layout_set(
        self,
        session_id: str,
        legend: Optional[Dict[str, Any]] = None,
        controls: Optional[List[Dict[str, Any]]] = None,
        margins: Optional[Dict[str, Any]] = None,
        components: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        res = await self.engine.apply_mutation(
            session_id,
            SetLayoutIntent(
                legend=legend, controls=controls, margins=margins,
                components=components,
            ),
        )
        return _with_evidence(res, {
            "success": not res.is_error,
            "layout": res.mapspec.get("layout", {}) if res.mapspec else {},
            "mapspec": res.mapspec,
        })

    async def patch_component(
        self,
        session_id: str,
        *,
        component_id: str,
        component_type: Optional[str] = None,
        enabled: Optional[bool] = None,
        position: Optional[str] = None,
        placement: Optional[Dict[str, Any]] = None,
        variant: Optional[str] = None,
        style: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
        upsert: bool = False,
        expected_revision: Optional[int] = None,
    ) -> Dict[str, Any]:
        """组件局部突变（单组件事务；Agent 工具与用户 UI 同一入口）。

        与 layout_set 的整表替换相对；expected_revision 提供乐观并发
        （落后 → superseded，用户最新交互优先于旧 Agent 决策）。
        """
        res = await self.engine.apply_mutation(
            session_id,
            PatchComponentIntent(
                component_id=component_id,
                component_type=component_type,
                enabled=enabled,
                position=position,
                placement=placement,
                variant=variant,
                style=style,
                options=options,
                upsert=upsert,
            ),
            expected_revision=expected_revision,
        )
        return _with_evidence(res, {
            "success": not res.is_error and not res.superseded,
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
