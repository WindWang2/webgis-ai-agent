"""开放服务/远程资产的数据资格映射（GOAL-02 / M5，G01 双轨）。

把 OGC 服务与远程物化资产的 descriptor 映射为 G01 语义的资格证据
（``role_class``），供 ``gis_harness.data_qualification`` 的 role 裁决消费
—— **display_only 与 numeric_raster 严格分开**：仅能显示的瓦片服务绝不
冒充可做栅格统计的数值源（诚实边界）。

映射规则（封闭）：

====================================  =======================  ============
descriptor 形态                        role_class               数值分析
====================================  =======================  ============
ogc_api_tiles（矢量/栅格瓦片集）       display_only             ✗（瓦片 ≠
                                                               数值栅格）
wms/wmts/pmtiles（既有瓦片协议）       display_only             ✗
ogc_api_coverages（subset_read_ready） numeric_raster           ✓
ref:fabric-cog/（物化 COG ref）        numeric_raster           ✓
其他（矢量源/ref）                     vector_features          ✓（矢量分析）
未知                                   unknown                  ✗（先发现）
====================================  =======================  ============
"""
from __future__ import annotations

from typing import Any, Dict

DISPLAY_ONLY = "display_only"
NUMERIC_RASTER = "numeric_raster"
VECTOR_FEATURES = "vector_features"
UNKNOWN = "unknown"

_TILE_PROTOCOLS = {"ogc_api_tiles", "wms", "wmts", "pmtiles", "arcgis_mapserver"}


def qualification_for_descriptor(descriptor: Any) -> Dict[str, Any]:
    """descriptor（DatasetDescriptor/dict/ref 字符串）→ 资格证据。"""
    source_type = _source_type_of(descriptor)
    metadata = _metadata_of(descriptor)
    if isinstance(descriptor, str):
        if descriptor.startswith("ref:fabric-cog/"):
            return _evidence(NUMERIC_RASTER, "materialized COG asset ref")
        return _evidence(UNKNOWN, "unrecognized ref form")
    if source_type in _TILE_PROTOCOLS:
        cap = (metadata or {}).get("capability") or {}
        state = cap.get("state") or "unknown"
        return _evidence(
            DISPLAY_ONLY,
            f"tile service ({source_type}); capability={state} — "
            "tiles are a display representation, NOT a numeric raster source",
            capability=cap,
        )
    if source_type == "ogc_api_coverages":
        ready = (metadata or {}).get("subset_read_ready")
        if ready:
            return _evidence(
                NUMERIC_RASTER,
                "coverage subset reads are budgeted and value-faithful",
                capability=(metadata or {}).get("capability") or {},
            )
        return _evidence(
            UNKNOWN,
            "coverage not subset-read-ready (see capability reasons)",
            capability=(metadata or {}).get("capability") or {},
        )
    if source_type in {"cog", "cog_tiff", "geotiff"}:
        return _evidence(
            NUMERIC_RASTER,
            "COG asset (materialize for numeric reads: "
            "materialize_remote_raster_asset)",
        )
    if source_type:
        return _evidence(VECTOR_FEATURES, f"vector source ({source_type})")
    return _evidence(UNKNOWN, "descriptor without source_type")


def _evidence(role_class: str, rationale: str, **extra: Any) -> Dict[str, Any]:
    ev: Dict[str, Any] = {
        "role_class": role_class,
        "rationale": rationale,
        "numeric_raster_eligible": role_class == NUMERIC_RASTER,
        "display_eligible": role_class in (DISPLAY_ONLY, NUMERIC_RASTER, VECTOR_FEATURES),
    }
    ev.update(extra)
    return ev


def _source_type_of(descriptor: Any) -> Any:
    if isinstance(descriptor, dict):
        return descriptor.get("source_type") or descriptor.get("type")
    return getattr(descriptor, "source_type", None)


def _metadata_of(descriptor: Any) -> Dict[str, Any]:
    if isinstance(descriptor, dict):
        return descriptor.get("metadata") or {}
    return getattr(descriptor, "metadata", None) or {}


__all__ = [
    "DISPLAY_ONLY",
    "NUMERIC_RASTER",
    "UNKNOWN",
    "VECTOR_FEATURES",
    "qualification_for_descriptor",
]
