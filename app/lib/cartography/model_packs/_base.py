"""model_packs 共享构造 helper（ADR-0101 D1/D2）。"""
from __future__ import annotations

from typing import List

from app.lib.cartography.model_library import MapModel

# 前端 mapspec-compiler/types.ts 图层 type union 支持的族 —— native 模型的
# maplibre_layer_type 必须落在这里（机器真值，测试锁定；hillshade /
# color-relief 不在内，因此声明这些图层类型的模型只能是 planned）。
FRONTEND_RUNTIME_LAYER_TYPES = frozenset({
    "fill", "line", "circle", "symbol", "heatmap", "raster", "fill-extrusion",
})

_MAPLIBRE_SPEC_URL = "https://maplibre.org/maplibre-style-spec/layers/"
_QGIS_URL = ("https://docs.qgis.org/latest/en/docs/user_manual/"
             "working_with_vector/vector_properties.html")
_GEODA_URL = "https://geodacenter.github.io/workbook/3a_mapping/lab3a.html"
_DECKGL_URL = "https://deck.gl/docs/api-reference/layers"
_KEPLER_URL = "https://docs.kepler.gl/docs/user-guides/c-types-of-layers"


def m(
    *,
    id: str, name_zh: str, purpose_zh: str,
    geometry_kinds: List[str], maplibre_layer_type: str,
    classification: str = "none",
    color_scheme_kind: str = "none", default_palette: str = "",
    recommended_classifiers: List[str] | None = None,
    default_class_count: int | None = None,
    runtime_status: str = "native",
    aliases: List[str] | None = None,
    accepted_artifact_types: List[str] | None = None,
    recommended_components: List[str] | None = None,
    supported_template_kinds: List[str] | None = None,
    export_compatibility: List[str] | None = None,
    geometry_layer_types: dict | None = None,
    fallback_model_id: str = "",
    pitfalls_zh: List[str] | None = None,
    sources: List[str] | None = None,
    deck_gl_layer: str = "", kepler_layer: str = "", qgis_renderer: str = "",
) -> MapModel:
    """紧凑构造 MapModel（pack 文件可读性；字段语义见 MapModel）。"""
    return MapModel(
        id=id, name_zh=name_zh, purpose_zh=purpose_zh,
        geometry_kinds=geometry_kinds, maplibre_layer_type=maplibre_layer_type,
        classification=classification,  # type: ignore[arg-type]
        default_class_count=default_class_count,
        color_scheme_kind=color_scheme_kind,  # type: ignore[arg-type]
        default_palette=default_palette,
        recommended_classifiers=recommended_classifiers or [],
        deck_gl_layer=deck_gl_layer, kepler_layer=kepler_layer,
        qgis_renderer=qgis_renderer,
        runtime_status=runtime_status,  # type: ignore[arg-type]
        aliases=aliases or [],
        pitfalls_zh=pitfalls_zh or [],
        sources=sources or [],
        accepted_artifact_types=accepted_artifact_types or [],
        recommended_components=recommended_components or [],
        supported_template_kinds=supported_template_kinds or ["thematic"],
        export_compatibility=export_compatibility or [],
        geometry_layer_types=geometry_layer_types or {},
        fallback_model_id=fallback_model_id,
    )


__all__ = [
    "FRONTEND_RUNTIME_LAYER_TYPES",
    "m",
    "MapModel",
    "_MAPLIBRE_SPEC_URL",
    "_QGIS_URL",
    "_GEODA_URL",
    "_DECKGL_URL",
    "_KEPLER_URL",
]
