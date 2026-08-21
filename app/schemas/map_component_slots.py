"""
Pydantic Schemas for 5 Orthogonal Map Theme Component Slots
"""
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class BasemapSlot(BaseModel):
    """Slot 1: Basemap Provider & Raster/Vector Filter Specs"""
    template_id: Optional[str] = Field(None, description="Source template ID if created from preset")
    provider_id: str = Field("carto-positron", description="Provider identifier (e.g. carto-positron, carto-dark, esri-imagery, osm-standard)")
    vector_style_url: Optional[str] = Field(None, description="Vector style JSON endpoint URL")
    raster_filters: Optional[Dict[str, float]] = Field(None, description="Raster filter adjustments: grayscale, contrast, brightness")
    overlays: Optional[List[Dict[str, Any]]] = Field(None, description="Overlays (e.g. vector label layers)")


class SymbologySlot(BaseModel):
    """Slot 2: Geometry Vector Symbology & Style Specs"""
    template_id: Optional[str] = Field(None, description="Source template ID")
    mode: Literal["single", "categorical"] = Field("single", description="Symbology mode")
    geometry: Optional[Literal["Point", "LineString", "Polygon", "MultiPolygon", "MultiLineString"]] = None
    color: Optional[str] = Field(None, description="Primary fill/stroke color in hex or rgb")
    stroke_color: Optional[str] = Field(None, description="Border/stroke color")
    stroke_width: Optional[float] = Field(1.5, ge=0)
    fill_opacity: Optional[float] = Field(0.7, ge=0.0, le=1.0)
    radius: Optional[float] = Field(6.0, ge=0)
    line_dash: Optional[List[int]] = Field(None, description="Dash array pattern e.g. [4, 2]")
    color_map: Optional[Dict[str, str]] = Field(None, description="Categorical value-to-color mapping dictionary")
    base_style: Optional[Dict[str, Any]] = Field(None, description="Base fallback style dictionary")
    field: Optional[str] = Field(None, description="Target feature property field name for categorical symbology")


class ThematicSlot(BaseModel):
    """Slot 3: Statistical Thematic Classification & Heatmap Specs"""
    template_id: Optional[str] = Field(None, description="Source template ID")
    variant: Literal["choropleth", "heatmap", "none"] = Field("choropleth", description="Thematic map type")
    field: Optional[str] = Field(None, description="Data attribute field used for thematic mapping")
    method: Literal["quantiles", "equal_interval", "natural_breaks", "std_dev", "head_tail", "lisa"] = Field("quantiles", description="Classification algorithm")
    k: int = Field(5, ge=2, le=10, description="Number of statistical classes")
    palette: str = Field("YlOrRd", description="ColorBrewer / Viridis palette identifier")
    intensity: float = Field(0.8, ge=0.0, le=1.0, description="Heatmap intensity parameter")
    radius: int = Field(25, ge=1, le=100, description="Heatmap kernel radius in pixels")
    heat_palette: List[str] = Field(
        default_factory=lambda: ["#0000ff", "#00ff00", "#ffff00", "#ff0000"],
        description="Heatmap gradient color stops"
    )


class LayoutSlot(BaseModel):
    """Slot 4: Cartographic Print & Page Layout Specs"""
    template_id: Optional[str] = Field(None, description="Source template ID")
    paper_size: str = Field("A4", description="Page dimensions e.g. A4, A3, 16:9")
    orientation: Literal["landscape", "portrait"] = Field("landscape", description="Page orientation")
    show_legend: bool = Field(True, description="Toggle map legend visibility")
    show_north_arrow: bool = Field(True, description="Toggle north arrow indicator")
    show_scale_bar: bool = Field(True, description="Toggle scale bar")
    show_grid: bool = Field(False, description="Toggle graticule grid lines")
    font_family: str = Field("Inter, sans-serif", description="Font stack for titles & labels")
    title_color: Optional[str] = Field("#0f172a", description="Main title text color")
    accent_color: Optional[str] = Field("#3b82f6", description="Accent element color")
    margin_px: int = Field(16, ge=0, description="Outer margin size in pixels")
    watermark_text: Optional[str] = Field(None, description="Optional watermark string")


class ViewportSlot(BaseModel):
    """Slot 5: Map Camera Extent & Bounding View Specs"""
    template_id: Optional[str] = Field(None, description="Source preset ID")
    center: Optional[List[float]] = Field(None, description="[longitude, latitude] center coordinates")
    zoom: Optional[float] = Field(None, ge=0.0, le=24.0, description="Zoom level")
    pitch: float = Field(0.0, ge=0.0, le=85.0, description="Camera pitch angle")
    bearing: float = Field(0.0, ge=-180.0, le=180.0, description="Camera bearing/rotation angle")
    bbox: Optional[List[float]] = Field(None, description="[min_lng, min_lat, max_lng, max_lat] bounding box")
    name: Optional[str] = Field(None, description="Viewport location descriptive name")
