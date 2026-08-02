"""
CompositeMapSpecBuilder - Assembly Engine for 5 Orthogonal Map Component Slots
"""
import logging
from typing import Any, Dict, Optional, Union

from app.schemas.map_component_slots import (
    BasemapSlot,
    SymbologySlot,
    ThematicSlot,
    LayoutSlot,
    ViewportSlot,
)
from app.schemas.template_schema import SEED_TEMPLATES
from app.services.mapspec.coordinator import validate as validate_mapspec

logger = logging.getLogger(__name__)

# 5 Curated Preset Combinations
PRESET_COMBINATIONS: Dict[str, Dict[str, str]] = {
    "academic_research": {
        "basemap": "carto-positron",
        "symbology": "single",
        "thematic": "choropleth_quantiles",
        "layout": "tmpl_ly_academic",
        "name": "学术论文风 (Academic Research)"
    },
    "cyber_dark": {
        "basemap": "carto-dark",
        "symbology": "single",
        "thematic": "choropleth_viridis",
        "layout": "tmpl_ly_dark_report",
        "name": "深色科技风 (Cyber Dark)"
    },
    "natural_terra": {
        "basemap": "esri-imagery",
        "symbology": "single",
        "thematic": "choropleth_spectral",
        "layout": "tmpl_ly_minimal",
        "name": "自然地理风 (Natural Terra)"
    },
    "heat_density": {
        "basemap": "carto-dark",
        "symbology": "single",
        "thematic": "heatmap_density",
        "layout": "tmpl_ly_dark_report",
        "name": "热力密度风 (Heat Density)"
    },
    "engineering_survey": {
        "basemap": "osm-standard",
        "symbology": "single",
        "thematic": "choropleth_equal",
        "layout": "tmpl_ly_engineering",
        "name": "工程勘测风 (Engineering Survey)"
    }
}


class CompositeMapSpecBuilder:
    """
    Assembles complete, pre-compile validated MapSpec objects from 5 orthogonal component slots:
    1. BasemapSlot
    2. SymbologySlot
    3. ThematicSlot
    4. LayoutSlot
    5. ViewportSlot
    """

    def __init__(self):
        self._basemap: Optional[BasemapSlot] = None
        self._symbology: Optional[SymbologySlot] = None
        self._thematic: Optional[ThematicSlot] = None
        self._layout: Optional[LayoutSlot] = None
        self._viewport: Optional[ViewportSlot] = None
        self._templates_cache: Dict[str, Dict[str, Any]] = {
            t["id"]: t for t in SEED_TEMPLATES
        }

    def with_basemap(self, slot_or_id: Union[BasemapSlot, str, Dict[str, Any]]) -> "CompositeMapSpecBuilder":
        if isinstance(slot_or_id, BasemapSlot):
            self._basemap = slot_or_id
        elif isinstance(slot_or_id, str):
            tmpl = self._templates_cache.get(slot_or_id)
            if tmpl and tmpl.get("kind") == "basemap":
                payload = tmpl["payload"]
                self._basemap = BasemapSlot(
                    template_id=slot_or_id,
                    provider_id=payload.get("providerId", slot_or_id),
                    vector_style_url=payload.get("vectorStyleUrl"),
                    raster_filters=payload.get("rasterFilters"),
                    overlays=payload.get("overlays"),
                )
            else:
                self._basemap = BasemapSlot(provider_id=slot_or_id)
        elif isinstance(slot_or_id, dict):
            self._basemap = BasemapSlot(**slot_or_id)
        return self

    def with_symbology(self, slot_or_id: Union[SymbologySlot, str, Dict[str, Any]]) -> "CompositeMapSpecBuilder":
        if isinstance(slot_or_id, SymbologySlot):
            self._symbology = slot_or_id
        elif isinstance(slot_or_id, str):
            tmpl = self._templates_cache.get(slot_or_id)
            if tmpl and tmpl.get("kind") == "symbology":
                p = tmpl["payload"]
                style = p.get("style", {})
                self._symbology = SymbologySlot(
                    template_id=slot_or_id,
                    mode=p.get("mode", "single"),
                    geometry=p.get("geometry"),
                    color=style.get("color"),
                    stroke_color=style.get("strokeColor"),
                    stroke_width=style.get("strokeWidth", 1.5),
                    fill_opacity=style.get("fillOpacity", 0.7),
                    radius=style.get("radius", 6.0),
                    line_dash=style.get("lineDash"),
                    color_map=p.get("colorMap"),
                    base_style=p.get("baseStyle"),
                )
            else:
                self._symbology = SymbologySlot(mode="single")
        elif isinstance(slot_or_id, dict):
            self._symbology = SymbologySlot(**slot_or_id)
        return self

    def with_thematic(self, slot_or_id: Union[ThematicSlot, str, Dict[str, Any]]) -> "CompositeMapSpecBuilder":
        if isinstance(slot_or_id, ThematicSlot):
            self._thematic = slot_or_id
        elif isinstance(slot_or_id, str):
            tmpl = self._templates_cache.get(slot_or_id)
            if tmpl and tmpl.get("kind") == "thematic":
                p = tmpl["payload"]
                self._thematic = ThematicSlot(
                    template_id=slot_or_id,
                    variant=p.get("variant", "choropleth"),
                    method=p.get("method", "quantiles"),
                    k=p.get("k", 5),
                    palette=p.get("palette", "YlOrRd"),
                    intensity=p.get("intensity", 0.8),
                    radius=p.get("radius", 25),
                    heat_palette=p.get("heatPalette", ["#0000ff", "#00ff00", "#ffff00", "#ff0000"]),
                )
            else:
                self._thematic = ThematicSlot(variant="choropleth")
        elif isinstance(slot_or_id, dict):
            self._thematic = ThematicSlot(**slot_or_id)
        return self

    def with_layout(self, slot_or_id: Union[LayoutSlot, str, Dict[str, Any]]) -> "CompositeMapSpecBuilder":
        if isinstance(slot_or_id, LayoutSlot):
            self._layout = slot_or_id
        elif isinstance(slot_or_id, str):
            tmpl = self._templates_cache.get(slot_or_id)
            if tmpl and tmpl.get("kind") == "layout":
                p = tmpl["payload"]
                st = p.get("style", {})
                self._layout = LayoutSlot(
                    template_id=slot_or_id,
                    paper_size=p.get("paperSize", "A4"),
                    orientation=p.get("orientation", "landscape"),
                    show_legend=p.get("showLegend", True),
                    show_north_arrow=p.get("showNorthArrow", True),
                    show_scale_bar=p.get("showScaleBar", True),
                    show_grid=p.get("showGrid", False),
                    font_family=st.get("fontFamily", "Inter, sans-serif"),
                    title_color=st.get("titleColor"),
                    accent_color=st.get("accentColor"),
                    margin_px=st.get("marginPx", 16),
                )
            else:
                self._layout = LayoutSlot()
        elif isinstance(slot_or_id, dict):
            self._layout = LayoutSlot(**slot_or_id)
        return self

    def with_viewport(self, slot_or_data: Union[ViewportSlot, Dict[str, Any]]) -> "CompositeMapSpecBuilder":
        if isinstance(slot_or_data, ViewportSlot):
            self._viewport = slot_or_data
        elif isinstance(slot_or_data, dict):
            self._viewport = ViewportSlot(**slot_or_data)
        return self

    def assemble(self, combination_ids: dict, layer_id: str = "") -> Dict[str, Any]:
        """
        Assemble a complete MapSpec dict from orthogonal slot combinations.

        :param combination_ids: Dictionary mapping slot names to template IDs or slot parameters.
        :param layer_id: Target layer ID to apply symbology & thematic styles to.
        :return: Synthesized MapSpec dictionary.
        """
        if "preset" in combination_ids and combination_ids["preset"] in PRESET_COMBINATIONS:
            preset = PRESET_COMBINATIONS[combination_ids["preset"]]
            combination_ids = {**preset, **combination_ids}

        if "basemap" in combination_ids:
            self.with_basemap(combination_ids["basemap"])
        if "symbology" in combination_ids:
            self.with_symbology(combination_ids["symbology"])
        if "thematic" in combination_ids:
            self.with_thematic(combination_ids["thematic"])
        if "layout" in combination_ids:
            self.with_layout(combination_ids["layout"])
        if "viewport" in combination_ids:
            self.with_viewport(combination_ids["viewport"])

        bm = self._basemap or BasemapSlot(provider_id="carto-positron")
        ly = self._layout or LayoutSlot()
        vp = self._viewport or ViewportSlot(center=[0.0, 0.0], zoom=2.0)

        mapspec: Dict[str, Any] = {
            "version": "1.0",
            "view": {
                "center": vp.center or [0.0, 0.0],
                "zoom": vp.zoom if vp.zoom is not None else 2.0,
                "pitch": vp.pitch,
                "bearing": vp.bearing,
            },
            "basemap": {
                "providerId": bm.provider_id,
            },
            "sources": {},
            "layers": [],
            "layout": {
                "paperSize": ly.paper_size,
                "orientation": ly.orientation,
                "legend": {"visible": ly.show_legend, "position": "top-right"},
                "controls": [
                    {"type": "navigation", "position": "top-right"},
                    {"type": "scale", "visible": ly.show_scale_bar},
                    {"type": "north", "visible": ly.show_north_arrow},
                ],
                "margins": {"marginPx": ly.margin_px},
                "style": {
                    "fontFamily": ly.font_family,
                    "titleColor": ly.title_color,
                    "accentColor": ly.accent_color,
                },
            },
            "thresholds": {"maxFeatures": 50000, "timeoutMs": 30000},
        }

        if bm.vector_style_url:
            mapspec["basemap"]["vectorStyleUrl"] = bm.vector_style_url
        if bm.raster_filters:
            mapspec["basemap"]["rasterFilters"] = bm.raster_filters
        if bm.overlays:
            mapspec["basemap"]["overlays"] = bm.overlays

        target_id = layer_id or "default_layer"
        source_id = f"source_{target_id}"

        mapspec["sources"][source_id] = {
            "type": "geojson",
            "inlineData": {"type": "FeatureCollection", "features": []},
        }

        geom_type = self._symbology.geometry if self._symbology else None
        if geom_type in ("Polygon", "MultiPolygon"):
            layer_type = "fill"
        elif geom_type in ("LineString", "MultiLineString"):
            layer_type = "line"
        else:
            layer_type = "circle"

        layer_def: Dict[str, Any] = {
            "id": target_id,
            "source": source_id,
            "type": layer_type,
            "paint": {},
        }


        # Override Hierarchy Rule: Thematic palette overrides Symbology fill color, while preserving stroke & radius
        if self._symbology:
            sym = self._symbology
            if sym.mode == "single":
                if sym.color and not (self._thematic and self._thematic.field):
                    layer_def["paint"]["fill-color" if layer_def["type"] == "fill" else "circle-color"] = sym.color
                if sym.fill_opacity is not None:
                    layer_def["paint"]["fill-opacity" if layer_def["type"] == "fill" else "circle-opacity"] = sym.fill_opacity
                if sym.stroke_color:
                    layer_def["paint"]["fill-outline-color" if layer_def["type"] == "fill" else "circle-stroke-color"] = sym.stroke_color
                if sym.radius:
                    layer_def["paint"]["circle-radius"] = sym.radius
            elif sym.mode == "categorical" and sym.color_map and sym.field and not (self._thematic and self._thematic.field):
                stops = [[val, col] for val, col in sym.color_map.items()]
                layer_def["paint"]["fill-color" if layer_def["type"] == "fill" else "circle-color"] = {
                    "property": sym.field,
                    "type": "categorical",
                    "stops": stops,
                }

        if self._thematic and self._thematic.field and self._thematic.variant != "none":
            th = self._thematic
            if th.variant == "choropleth":
                layer_def["thematic"] = {
                    "field": th.field,
                    "method": th.method,
                    "k": th.k,
                    "palette": th.palette,
                }
            elif th.variant == "heatmap":
                layer_def["type"] = "heatmap"
                layer_def["paint"] = {
                    "heatmap-weight": ["interpolate", ["linear"], ["get", th.field], 0, 0, 1, 1],
                    "heatmap-intensity": th.intensity,
                    "heatmap-radius": th.radius,
                }

        mapspec["layers"].append(layer_def)

        val_res = validate_mapspec(mapspec)
        if not val_res.get("success", False):
            logger.warning("CompositeMapSpecBuilder validation warnings: %s", val_res.get("errors"))

        return mapspec
