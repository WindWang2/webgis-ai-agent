from app.tools.registry import _normalize_tool_arguments
from pydantic import BaseModel, Field
from typing import Optional, List, Any


class MockHeatmapModel(BaseModel):
    geojson: Any
    radius_px: int = 30
    intensity: float = 1.0
    render_type: str = "native"
    bandwidth_m: Optional[float] = None
    cell_size: Optional[float] = None


class MockPoiModel(BaseModel):
    district: Optional[str] = None
    subtype: Optional[str] = None
    keyword: Optional[str] = None
    adcode: Optional[str] = None


class MockBoundaryModel(BaseModel):
    name: Optional[str] = None
    adcode: Optional[str] = None


class MockAggregateModel(BaseModel):
    points: Any
    polygons: Any


class MockThematicModel(BaseModel):
    geojson: Any
    field: str
    palette: str = "YlOrRd"
    method: str = "quantile"
    n_classes: int = 5


class MockProductModel(BaseModel):
    """镜像真实 MapProductArgs（Pi 兼容修复：mock 必须跟真实签名走 ——
    旧 mock 声明了不存在的 map_title/insight_summary，使别名测试与
    normalizer 互相印证而双双偏离真实工具契约）。"""

    query: str = ""
    session_id: Optional[str] = None
    layer_ids: List[str] = Field(default_factory=list)
    primary_ref: Optional[str] = None
    overlay_refs: List[str] = Field(default_factory=list)
    title: Optional[str] = None
    template_id: Optional[str] = None
    palette: str = "classic"
    radius_px: Optional[int] = None
    recipe_id: Optional[str] = None
    task_hint: Optional[str] = None


def test_heatmap_data_geojson_ref_normalization():
    raw_args = {"geojson_ref": "ref:geojson-34ecef1088b44961", "radius-px": 25, "render-type": "native"}
    normalized = _normalize_tool_arguments("heatmap_data", raw_args, MockHeatmapModel)
    assert normalized["geojson"] == "ref:geojson-34ecef1088b44961"
    assert "geojson_ref" not in normalized
    assert normalized["radius_px"] == 25
    assert normalized["render_type"] == "native"


def test_spatial_tools_data_ref_normalization():
    for tool_name in ["buffer_analysis", "spatial_stats", "kde_surface", "h3_binning"]:
        raw_args = {"data_ref": "ref:geojson-12345"}
        normalized = _normalize_tool_arguments(tool_name, raw_args)
        assert normalized["geojson"] == "ref:geojson-12345"
        assert "data_ref" not in normalized


def test_poi_aliases():
    raw_args = {"city": "成都市", "poi_type": "小学", "query": "实验小学"}
    normalized = _normalize_tool_arguments("query_local_poi", raw_args, MockPoiModel)
    assert normalized["district"] == "成都市"
    assert normalized["subtype"] == "小学"
    assert normalized["keyword"] == "实验小学"


def test_admin_boundary_aliases():
    raw_args = {"admin_name": "成都市", "ad_code": "510100"}
    normalized = _normalize_tool_arguments("get_local_admin_boundary", raw_args, MockBoundaryModel)
    assert normalized["name"] == "成都市"
    assert normalized["adcode"] == "510100"


def test_spatial_aggregate_aliases():
    raw_args = {
        "points_data": "ref:geojson-points",
        "admin_boundary": "ref:geojson-admin",
    }
    normalized = _normalize_tool_arguments("spatial_aggregate", raw_args, MockAggregateModel)
    assert normalized["points"] == "ref:geojson-points"
    assert normalized["polygons"] == "ref:geojson-admin"


def test_thematic_map_aliases():
    raw_args = {
        "geojson_ref": "ref:geojson-poly",
        "classify_field": "density",
        "color_palette": "Blues",
        "classify_method": "natural_breaks",
        "num_classes": 6,
    }
    normalized = _normalize_tool_arguments("create_thematic_map", raw_args, MockThematicModel)
    assert normalized["geojson"] == "ref:geojson-poly"
    assert normalized["field"] == "density"
    assert normalized["palette"] == "Blues"
    assert normalized["method"] == "natural_breaks"
    assert normalized["n_classes"] == 6


def test_product_model_aliases():
    """真实契约：声明的 title 原样保留（不折叠进不存在的 map_title）；
    map_title 作为入向别名折叠到 title；summary 不再折进不存在的
    insight_summary（保持原样，由未知参数门如实拒绝）。"""
    raw_args = {
        "title": "成都市小学热力图分析",
        "primary_layer": "ref:geojson-heatmap",
        "overlays": ["ref:geojson-boundary"],
    }
    normalized = _normalize_tool_arguments("webgis_map_product", raw_args, MockProductModel)
    assert normalized["title"] == "成都市小学热力图分析"
    assert "map_title" not in normalized
    assert normalized["primary_ref"] == "ref:geojson-heatmap"
    assert normalized["overlay_refs"] == ["ref:geojson-boundary"]

    # map_title（旧习惯/历史会话）→ 折叠到真实参数 title。
    legacy = _normalize_tool_arguments(
        "webgis_map_product", {"map_title": "旧标题"}, MockProductModel,
    )
    assert legacy["title"] == "旧标题"

    # summary 别名不再被折叠成不存在的 insight_summary。
    stale = _normalize_tool_arguments(
        "webgis_map_product", {"summary": "x", "query": "q"}, MockProductModel,
    )
    assert stale.get("summary") == "x"
    assert "insight_summary" not in stale
