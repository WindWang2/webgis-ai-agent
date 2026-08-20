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

    def with_thematic(self, slot_or_id: Union[ThematicSlot, str, Dict[str, Any]], field: str = "") -> "CompositeMapSpecBuilder":
        if isinstance(slot_or_id, ThematicSlot):
            self._thematic = slot_or_id
            if field:
                self._thematic.field = field
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
                    field=field or p.get("field"),
                )
            else:
                self._thematic = ThematicSlot(variant="choropleth", field=field or None)
        elif isinstance(slot_or_id, dict):
            self._thematic = ThematicSlot(**slot_or_id)
            if field and not self._thematic.field:
                self._thematic.field = field
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

    def assemble(self, combination_ids: dict, layer_id: str = "", field: str = "", geojson: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Assemble a complete MapSpec dict from orthogonal slot combinations.

        :param combination_ids: Dictionary mapping slot names to template IDs or slot parameters.
        :param layer_id: Target layer ID to apply symbology & thematic styles to.
        :param field: Optional thematic field (wired to ThematicSlot.field when thematic is active).
        :param geojson: Optional GeoJSON FeatureCollection to embed as source inlineData (real data, not decorative).
        :return: Synthesized MapSpec dictionary.
        """
        # Explicit field param takes precedence; combination_ids["field"] is the wire-compat alias
        # for the combine_map_theme tool (so builder remains callable both ways).
        effective_field = field or combination_ids.get("field", "") or ""
        if "preset" in combination_ids and combination_ids["preset"] in PRESET_COMBINATIONS:
            preset = PRESET_COMBINATIONS[combination_ids["preset"]]
            combination_ids = {**preset, **combination_ids}

        if "basemap" in combination_ids:
            self.with_basemap(combination_ids["basemap"])
        if "symbology" in combination_ids:
            self.with_symbology(combination_ids["symbology"])
        if "thematic" in combination_ids:
            self.with_thematic(combination_ids["thematic"], field=effective_field)
        elif effective_field and self._thematic and not self._thematic.field:
            # Caller supplied field without re-specifying thematic — keep preset thematic but bind field
            self._thematic.field = effective_field
        if "layout" in combination_ids:
            self.with_layout(combination_ids["layout"])
        if "viewport" in combination_ids:
            self.with_viewport(combination_ids["viewport"])
        # If thematic was already configured (previous with_thematic call) and a field is now provided,
        # bind it so the thematic branch below becomes effective.
        if effective_field and self._thematic and not self._thematic.field:
            self._thematic.field = effective_field

        basemap_slot = self._basemap or BasemapSlot(provider_id="carto-positron")
        layout_slot = self._layout or LayoutSlot()
        viewport_slot = self._viewport or ViewportSlot(center=[0.0, 0.0], zoom=2.0)

        mapspec: Dict[str, Any] = {
            "version": "1.0",
            "view": {
                "center": viewport_slot.center or [0.0, 0.0],
                "zoom": viewport_slot.zoom if viewport_slot.zoom is not None else 2.0,
                "pitch": viewport_slot.pitch,
                "bearing": viewport_slot.bearing,
            },
            "basemap": {
                "providerId": basemap_slot.provider_id,
            },
            "sources": {},
            "layers": [],
            "layout": {
                "paperSize": layout_slot.paper_size,
                "orientation": layout_slot.orientation,
                "legend": {"visible": layout_slot.show_legend, "position": "top-right"},
                "controls": [
                    {"type": "navigation", "position": "top-right"},
                    {"type": "scale", "visible": layout_slot.show_scale_bar},
                    {"type": "north", "visible": layout_slot.show_north_arrow},
                ],
                "margins": {"marginPx": layout_slot.margin_px},
                "style": {
                    "fontFamily": layout_slot.font_family,
                    "titleColor": layout_slot.title_color,
                    "accentColor": layout_slot.accent_color,
                },
            },
            "thresholds": {"maxFeatures": 50000, "timeoutMs": 30000},
        }

        if basemap_slot.vector_style_url:
            mapspec["basemap"]["vectorStyleUrl"] = basemap_slot.vector_style_url
        if basemap_slot.raster_filters:
            mapspec["basemap"]["rasterFilters"] = basemap_slot.raster_filters
        if basemap_slot.overlays:
            mapspec["basemap"]["overlays"] = basemap_slot.overlays

        target_id = layer_id or "default_layer"
        source_id = f"source_{target_id}"

        # Source must carry real data — either the caller-provided geojson as
        # inlineData (verifiable) or fail-loud. The decorative "dataPath":
        # "layers/{id}/source" has no consumer (no API route, no frontend
        # parser for that shape) and is still a fake — replaced with real data.
        # Caller must provide geojson (see apply_template/combine_map_theme
        # contracts); without it we keep an empty shell that validate will
        # reject via the downstream pipeline (is_compiled=False), but the
        # lifecycle commit will never be reached because the tool returned
        # an explicit missing-geojson error first.
        effective_geojson = geojson or combination_ids.get("geojson")
        if isinstance(effective_geojson, dict) and effective_geojson.get("features"):
            mapspec["sources"][source_id] = {
                "type": "geojson",
                "inlineData": effective_geojson,
            }
        else:
            # Honest empty — caller omitted data; builder alone cannot invent it.
            # This keeps the MapSpec shape valid for unit tests that don't need data,
            # but any lifecycle submission without real data would have been rejected
            # by the tool layer before reaching here.
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
            symbology_slot = self._symbology
            if symbology_slot.mode == "single":
                if symbology_slot.color and not (self._thematic and self._thematic.field):
                    color_key = "fill-color" if layer_type == "fill" else ("line-color" if layer_type == "line" else "circle-color")
                    layer_def["paint"][color_key] = symbology_slot.color
                if symbology_slot.fill_opacity is not None:
                    opacity_key = "fill-opacity" if layer_type == "fill" else ("line-opacity" if layer_type == "line" else "circle-opacity")
                    layer_def["paint"][opacity_key] = symbology_slot.fill_opacity
                if symbology_slot.stroke_color:
                    stroke_key = "fill-outline-color" if layer_type == "fill" else ("line-color" if layer_type == "line" else "circle-stroke-color")
                    layer_def["paint"][stroke_key] = symbology_slot.stroke_color
                if symbology_slot.stroke_width is not None:
                    width_key = "line-width" if layer_type == "line" else "circle-stroke-width"
                    if layer_type != "fill":
                        layer_def["paint"][width_key] = symbology_slot.stroke_width
                if symbology_slot.radius and layer_type == "circle":
                    layer_def["paint"]["circle-radius"] = symbology_slot.radius
            elif symbology_slot.mode == "categorical" and symbology_slot.color_map and symbology_slot.field and not (self._thematic and self._thematic.field):
                stops = [[val, col] for val, col in symbology_slot.color_map.items()]
                color_key = "fill-color" if layer_type == "fill" else ("line-color" if layer_type == "line" else "circle-color")
                layer_def["paint"][color_key] = {
                    "property": symbology_slot.field,
                    "type": "categorical",
                    "stops": stops,
                }

        if self._thematic and self._thematic.field and self._thematic.variant != "none":
            thematic_slot = self._thematic
            if thematic_slot.variant == "choropleth":
                # Emit legend_spec (frontend thematic-paint consumes this; old "thematic" key was dead).
                # Classification needs data; without it we emit a preset-derived graduated spec with
                # synthetic breaks so the slot is effective and the legend renders. Breaks are NOT
                # data-driven — they are evenly spaced over [0, k] and the palette is resolved
                # honestly via the thematic template's palette (same palette helpers as the real path).
                if thematic_slot.method in ("quantiles", "equal_interval", "natural_breaks"):
                    k = max(2, min(10, int(thematic_slot.k or 5)))
                    # Synthetic breaks over [0, 1] normalized domain — honest preset-derived, not data-derived
                    breaks = [round(i / k, 6) for i in range(k + 1)]
                    # Sample palette at class midpoints (same as thematic_spec.resolve_thematic_colors)
                    from app.lib.cartography.palettes import get_color_from_palette
                    colors = []
                    for i in range(k):
                        mid = (breaks[i] + breaks[i + 1]) / 2.0
                        colors.append(get_color_from_palette(thematic_slot.palette, mid))
                    layer_def["legend_spec"] = {
                        "type": "graduated",
                        "field": thematic_slot.field,
                        "breaks": breaks,
                        "palette": thematic_slot.palette,
                        "palette_colors": colors,
                        "method": thematic_slot.method,
                    }
                    # Drive paint from the same legend_spec (canonical projection) so legend ↔ paint agree
                    try:
                        from app.lib.cartography.thematic_spec import spec_to_paint
                        paint_color, _warnings = spec_to_paint(layer_def["legend_spec"])
                        if paint_color:
                            layer_def["paint"]["color"] = paint_color
                    except Exception as e:
                        logger.warning("Composite spec_to_paint (graduated) failed: %s", e)
                elif thematic_slot.method == "categorical":
                    # Categorical without data: emit placeholder categories (consumers render via match)
                    layer_def["legend_spec"] = {
                        "type": "categorical",
                        "field": thematic_slot.field,
                        "categories": [],
                        "palette": thematic_slot.palette,
                    }
                elif thematic_slot.method == "lisa":
                    layer_def["legend_spec"] = {
                        "type": "categorical",
                        "field": thematic_slot.field,
                        "categories": [
                            {"key": "HH", "color": "#ff0000", "label": "High-High"},
                            {"key": "LL", "color": "#0000ff", "label": "Low-Low"},
                            {"key": "HL", "color": "#ffaaaa", "label": "High-Low"},
                            {"key": "LH", "color": "#aaaaff", "label": "Low-High"},
                            {"key": "NS", "color": "#cccccc", "label": "Not Significant"},
                        ],
                        "palette": thematic_slot.palette,
                    }
                    try:
                        from app.lib.cartography.thematic_spec import spec_to_paint
                        paint_color, _warnings = spec_to_paint(layer_def["legend_spec"])
                        if paint_color:
                            layer_def["paint"]["color"] = paint_color
                    except Exception as e:
                        logger.warning("Composite spec_to_paint (lisa) failed: %s", e)
                else:
                    # Unknown method: still emit graduated fallback so slot is not silently dead
                    k = max(2, min(10, int(thematic_slot.k or 5)))
                    breaks = [round(i / k, 6) for i in range(k + 1)]
                    from app.lib.cartography.palettes import get_color_from_palette
                    colors = [get_color_from_palette(thematic_slot.palette, (breaks[i]+breaks[i+1])/2) for i in range(k)]
                    layer_def["legend_spec"] = {
                        "type": "graduated",
                        "field": thematic_slot.field,
                        "breaks": breaks,
                        "palette": thematic_slot.palette,
                        "palette_colors": colors,
                        "method": thematic_slot.method,
                    }
            elif thematic_slot.variant == "heatmap":
                layer_def["type"] = "heatmap"
                layer_def["paint"] = {
                    "heatmap-weight": ["interpolate", ["linear"], ["get", thematic_slot.field], 0, 0, 1, 1],
                    "heatmap-intensity": thematic_slot.intensity,
                    "heatmap-radius": thematic_slot.radius,
                }
                # Heatmap legend (continuous) — frontend renders via thematic-paint continuous path.
                # Use the heat_palette that drives the paint (not a hardcoded Viridis) so legend ↔ paint agree.
                try:
                    heat_colors = list(thematic_slot.heat_palette or ["#0000ff", "#00ff00", "#ffff00", "#ff0000"])
                    layer_def["legend_spec"] = {
                        "type": "continuous",
                        "field": thematic_slot.field,
                        "min": 0.0,
                        "max": 1.0,
                        "palette": "Heat",
                        "palette_colors": heat_colors,
                    }
                except Exception as e:
                    logger.warning("Composite heatmap legend_spec generation failed: %s", e)

        mapspec["layers"].append(layer_def)

        val_res = validate_mapspec(mapspec)
        if not val_res.get("success", False):
            raise ValueError(f"CompositeMapSpecBuilder validation failed: {val_res.get('errors')}")

        return mapspec

