"""#690: heatmap 热力图确定性守卫（小样本/非点几何→拦截 native，带 correction_hint）

覆盖：
- heatmap_data native 工具守卫（小样本、非点几何）返回 success=False + correction_hint
- converter type_hint=heatmap 的 MapSpec 授权守卫（不满足则回退 circle + correction_hint）
- 阈值可配置（HEATMAP_MIN_POINTS，env 可覆盖，max(1,) 防零）不影响 ≥ 阈值的正常点集
- prompt/工具描述恒生效护栏可见
"""
import pytest

from app.core.config import settings
from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
from app.services.analysis_cartography_converter import convert_analysis_to_mapspec_layer


def _point(lng=104.0, lat=30.6):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]}, "properties": {}}


def _line():
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[104.0, 30.6], [104.1, 30.7]]},
        "properties": {},
    }


def _polygon():
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [[[104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.7], [104.0, 30.6]]]},
        "properties": {},
    }


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


@pytest.fixture
def spatial_tools():
    reg = ToolRegistry()
    register_spatial_tools(reg)
    return reg


@pytest.fixture
def _default_threshold(monkeypatch):
    """Ensure default threshold 10 for deterministic tests."""
    monkeypatch.setattr(settings, "HEATMAP_MIN_POINTS", 10)


# ── tool 侧：小样本拦截 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heatmap_tool_rejects_few_points(spatial_tools, _default_threshold):
    result = await spatial_tools.dispatch("heatmap_data", {"geojson": _fc([_point() for _ in range(3)]), "render_type": "native"})
    assert result.get("success") is False
    assert result.get("code") == "INSUFFICIENT_POINTS_FOR_HEATMAP"
    assert result.get("correction_hint")
    assert "h3_binning" in result["correction_hint"] or "点图" in result["correction_hint"]


@pytest.mark.asyncio
async def test_heatmap_tool_rejects_line_geometry(spatial_tools, _default_threshold):
    result = await spatial_tools.dispatch("heatmap_data", {"geojson": _fc([_line() for _ in range(20)]), "render_type": "native"})
    assert result.get("success") is False
    assert result.get("code") == "INVALID_GEOMETRY_FOR_HEATMAP"
    assert result.get("correction_hint")


@pytest.mark.asyncio
async def test_heatmap_tool_rejects_polygon_geometry(spatial_tools, _default_threshold):
    result = await spatial_tools.dispatch("heatmap_data", {"geojson": _fc([_polygon() for _ in range(20)]), "render_type": "native"})
    assert result.get("success") is False
    assert result.get("code") == "INVALID_GEOMETRY_FOR_HEATMAP"
    assert result.get("correction_hint")


@pytest.mark.asyncio
async def test_heatmap_tool_allows_enough_points(spatial_tools, _default_threshold):
    result = await spatial_tools.dispatch("heatmap_data", {"geojson": _fc([_point(104 + i * 0.01, 30.6) for i in range(12)]), "render_type": "native"})
    # 足量点集不被拦截：仍为原生热力图产物
    assert result.get("success") is not False or result.get("command") == "add_native_heatmap" or result.get("type_hint") == "heatmap"
    # dispatch 成功路径下 top-level 即是可渲染载荷（带 type_hint/command）
    assert result.get("type_hint") == "heatmap" or result.get("command") == "add_native_heatmap"


@pytest.mark.asyncio
async def test_heatmap_tool_threshold_override(spatial_tools, monkeypatch):
    monkeypatch.setattr(settings, "HEATMAP_MIN_POINTS", 3)
    # 5 点在阈值 3 时应通过
    ok = await spatial_tools.dispatch("heatmap_data", {"geojson": _fc([_point() for _ in range(5)]), "render_type": "native"})
    assert ok.get("type_hint") == "heatmap" or ok.get("command") == "add_native_heatmap"
    # 2 点在阈值 3 时仍拦截
    bad = await spatial_tools.dispatch("heatmap_data", {"geojson": _fc([_point() for _ in range(2)]), "render_type": "native"})
    assert bad.get("success") is False
    assert bad.get("code") == "INSUFFICIENT_POINTS_FOR_HEATMAP"
    # max(1,) 防零：0 应 clamp 为 1（此时 1 点应通过，不再拦截）
    monkeypatch.setattr(settings, "HEATMAP_MIN_POINTS", 0)
    one = await spatial_tools.dispatch("heatmap_data", {"geojson": _fc([_point()]), "render_type": "native"})
    assert one.get("type_hint") == "heatmap" or one.get("command") == "add_native_heatmap"


@pytest.mark.asyncio
async def test_heatmap_tool_negative_threshold_clamped(spatial_tools, monkeypatch):
    monkeypatch.setattr(settings, "HEATMAP_MIN_POINTS", -5)
    # 负值 clamp→1，1 点应通过
    one = await spatial_tools.dispatch("heatmap_data", {"geojson": _fc([_point()]), "render_type": "native"})
    assert one.get("type_hint") == "heatmap" or one.get("command") == "add_native_heatmap"


# ── converter 侧：type_hint==heatmap 翻转守卫 ──────────────────────────

def test_converter_blocks_few_points(monkeypatch):
    monkeypatch.setattr(settings, "HEATMAP_MIN_POINTS", 10)
    fc = _fc([_point() for _ in range(4)])
    payload = {"geojson": fc, "algorithm": "heatmap_data", "type_hint": "heatmap", "metadata": {"palette": "classic"}}
    layer, inline, warnings = convert_analysis_to_mapspec_layer(payload)
    assert layer["type"] == "circle"  # 回退
    assert layer.get("correction_hint")
    assert any("heatmap_guard" in w for w in warnings)


def test_converter_blocks_non_point_geometry(monkeypatch):
    monkeypatch.setattr(settings, "HEATMAP_MIN_POINTS", 10)
    fc = _fc([_polygon() for _ in range(20)])
    payload = {"geojson": fc, "algorithm": "heatmap_data", "type_hint": "heatmap", "metadata": {"palette": "classic"}}
    layer, inline, warnings = convert_analysis_to_mapspec_layer(payload)
    assert layer["type"] == "fill"  # polygon 推断 fill，不翻为 heatmap
    assert layer.get("correction_hint")
    assert any("heatmap_guard" in w for w in warnings)


def test_converter_blocks_line_geometry(monkeypatch):
    monkeypatch.setattr(settings, "HEATMAP_MIN_POINTS", 10)
    fc = _fc([_line() for _ in range(20)])
    payload = {"geojson": fc, "algorithm": "heatmap_data", "type_hint": "heatmap"}
    layer, _, warnings = convert_analysis_to_mapspec_layer(payload)
    assert layer["type"] == "line"
    assert layer.get("correction_hint")


def test_converter_allows_enough_points(monkeypatch):
    monkeypatch.setattr(settings, "HEATMAP_MIN_POINTS", 10)
    fc = _fc([_point(104 + i * 0.01, 30.6) for i in range(15)])
    payload = {"geojson": fc, "algorithm": "heatmap_data", "type_hint": "heatmap", "metadata": {"palette": "classic"}}
    layer, _, warnings = convert_analysis_to_mapspec_layer(payload)
    assert layer["type"] == "heatmap"
    assert not any("heatmap_guard" in w for w in warnings)


def test_converter_threshold_override(monkeypatch):
    monkeypatch.setattr(settings, "HEATMAP_MIN_POINTS", 3)
    small = _fc([_point() for _ in range(5)])
    layer, _, _ = convert_analysis_to_mapspec_layer({"geojson": small, "algorithm": "heatmap_data", "type_hint": "heatmap"})
    assert layer["type"] == "heatmap"
    # 再调高阈值则拦截
    monkeypatch.setattr(settings, "HEATMAP_MIN_POINTS", 10)
    layer2, _, warnings = convert_analysis_to_mapspec_layer({"geojson": small, "algorithm": "heatmap_data", "type_hint": "heatmap"})
    assert layer2["type"] == "circle"
    assert any("heatmap_guard" in w for w in warnings)


def test_converter_clamps_zero_threshold(monkeypatch):
    """max(1,) 防零：阈值 0/负数时 1 点不应被误拦截。"""
    for v in (0, -3):
        monkeypatch.setattr(settings, "HEATMAP_MIN_POINTS", v)
        fc = _fc([_point()])
        layer, _, warnings = convert_analysis_to_mapspec_layer({"geojson": fc, "algorithm": "heatmap_data", "type_hint": "heatmap"})
        assert layer["type"] == "heatmap", f"threshold={v} should clamp to 1"
        assert not any("heatmap_guard" in w for w in warnings)


# ── 恒生效面定量护栏句存在性 ──────────────────────────────────────────

def test_prompt_and_tool_description_contain_quantitative_guard():
    from app.services.chat.prompt import SYSTEM_PROMPT
    assert "<10" in SYSTEM_PROMPT or "HEATMAP_MIN_POINTS" in SYSTEM_PROMPT
    assert "热力图无统计意义" in SYSTEM_PROMPT or "热力图" in SYSTEM_PROMPT
    from app.tools.registry import ToolRegistry
    from app.tools.spatial import register_spatial_tools
    reg = ToolRegistry()
    register_spatial_tools(reg)
    desc = reg.metadata("heatmap_data").get("description", "")
    assert "HEATMAP_MIN_POINTS" in desc or "10" in desc
    assert "correction_hint" in desc
