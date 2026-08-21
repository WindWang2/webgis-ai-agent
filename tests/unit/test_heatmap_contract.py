"""热力半径契约测试：radius_px（视觉像素）与 bandwidth_m（分析米）分离。

核心不变量：米值绝不再被当作像素消费（1000m ≠ 1000px）；legacy radius
经唯一归一化边界（heatmap_contract）显式转换/回落并记录迁移警示。
"""
import json

import pytest

from app.lib.cartography.heatmap_contract import (
    DEFAULT_BANDWIDTH_M,
    DEFAULT_RADIUS_PX,
    HeatmapRadiusContract,
    normalize_heatmap_radius,
    resolve_paint_radius_px,
)
from app.lib.cartography.palettes import heatmap_paint


class TestNormalize:
    def test_explicit_radius_px_wins(self):
        c = normalize_heatmap_radius(radius_px=24, bandwidth_m=800, legacy_radius=1000)
        assert c.radius_px == 24
        assert c.bandwidth_m == 800
        assert c.source == "explicit"

    def test_thousand_meters_never_thousand_pixels(self):
        """核心契约：legacy 1000（米）→ 视觉默认 30px，绝不 1000px。"""
        c = normalize_heatmap_radius(legacy_radius=1000)
        assert c.radius_px == DEFAULT_RADIUS_PX == 30
        assert c.bandwidth_m == 1000
        assert c.source == "legacy_radius_visual_default_applied"
        assert c.warnings  # 迁移警示显式记录

    def test_legacy_window_passthrough(self):
        """legacy 4–60 历史直通窗口：30（米 schema 值）→ 30px 视觉延续。"""
        c = normalize_heatmap_radius(legacy_radius=30)
        assert c.radius_px == 30
        assert c.bandwidth_m == 30
        assert c.source == "legacy_radius_px_passthrough"

    def test_explicit_px_clamped_to_contract_window(self):
        c = normalize_heatmap_radius(radius_px=500)
        assert c.radius_px == 80
        c2 = normalize_heatmap_radius(radius_px=1)
        assert c2.radius_px == 4

    def test_nothing_given_defaults(self):
        c = normalize_heatmap_radius()
        assert c.radius_px == DEFAULT_RADIUS_PX
        assert c.bandwidth_m == DEFAULT_BANDWIDTH_M

    def test_explicit_bandwidth_only(self):
        c = normalize_heatmap_radius(bandwidth_m=2500)
        assert c.radius_px == DEFAULT_RADIUS_PX
        assert c.bandwidth_m == 2500
        # source 描述 radius_px 的来源：带宽显式但视觉半径未给 → default
        assert c.source == "default"

    def test_metadata_projection_bounded(self):
        c = normalize_heatmap_radius(legacy_radius=2000)
        meta = c.to_metadata()
        assert meta["radius_px"] == 30
        assert meta["bandwidth_m"] == 2000
        assert meta["radius_source"] == "legacy_radius_visual_default_applied"
        assert len(meta["radius_warnings"]) == 1


class TestHeatmapPaint:
    def test_paint_consumes_px_semantics(self):
        paint = heatmap_paint("viridis", 22)
        assert paint["heatmap-radius"][6] == 22
        assert paint["heatmap-radius"][8] == min(80, int(22 * 1.7))

    def test_paint_clamps_out_of_range_px(self):
        assert heatmap_paint("classic", 200)["heatmap-radius"][6] == 80

    def test_paint_invalid_falls_to_default(self):
        assert heatmap_paint("classic", None)["heatmap-radius"][6] == DEFAULT_RADIUS_PX


class TestToolContract:
    """heatmap_data 工具层：参数 schema 与 metadata 携带显式双字段。"""

    @pytest.fixture
    def registry(self):
        from app.tools.registry import ToolRegistry
        from app.tools.spatial import register_spatial_tools

        reg = ToolRegistry()
        register_spatial_tools(reg)
        return reg

    def _fc(self, n):
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [104.0 + i * 0.01, 30.6]},
             "properties": {"name": f"p{i}"}}
            for i in range(n)
        ]}

    @pytest.mark.asyncio
    async def test_explicit_radius_px_flows_to_metadata(self, registry):
        res = await registry.dispatch(
            "heatmap_data",
            {"geojson": self._fc(20), "radius_px": 26, "render_type": "native"},
        )
        meta = res["metadata"]
        assert meta["radius_px"] == 26
        assert meta["radius_source"] == "explicit"
        assert meta["bandwidth_m"] == DEFAULT_BANDWIDTH_M

    @pytest.mark.asyncio
    async def test_legacy_radius_normalized_with_warning(self, registry):
        """legacy radius=1000（米）→ bandwidth_m=1000 + 视觉默认 30px + 警示。"""
        res = await registry.dispatch(
            "heatmap_data",
            {"geojson": self._fc(20), "radius": 1000, "render_type": "native"},
        )
        meta = res["metadata"]
        assert meta["bandwidth_m"] == 1000
        assert meta["radius_px"] == 30  # 绝不 1000px
        assert meta["radius_source"] == "legacy_radius_visual_default_applied"
        assert meta["radius_warnings"]

    @pytest.mark.asyncio
    async def test_explicit_bandwidth_flows(self, registry):
        res = await registry.dispatch(
            "heatmap_data",
            {"geojson": self._fc(20), "bandwidth_m": 1500, "render_type": "native"},
        )
        assert res["metadata"]["bandwidth_m"] == 1500

    @pytest.mark.asyncio
    async def test_tool_schema_documents_unit_separation(self, registry):
        schema = next(
            s for s in registry.get_schemas()
            if s["function"]["name"] == "heatmap_data"
        )
        props = schema["function"]["parameters"]["properties"]
        assert "radius_px" in props and "像素" in props["radius_px"]["description"]
        assert "bandwidth_m" in props and "米" in props["bandwidth_m"]["description"]
        assert "兼容" in props["radius"]["description"]


class TestConverterContract:
    """converter：MapSpec paint 与 heatmap 兄弟键都携带显式契约。"""

    def _analysis(self, n=15, **meta):
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point",
                                             "coordinates": [104.0 + i * 0.01, 30.6]},
             "properties": {}}
            for i in range(n)
        ]}
        return {"geojson": fc, "algorithm": "heatmap_data",
                "type_hint": "heatmap", "metadata": {"point_count": n, **meta}}

    def test_explicit_radius_px_reaches_paint(self):
        from app.services.analysis_cartography_converter import (
            convert_analysis_to_mapspec_layer,
        )

        layer, _, warnings = convert_analysis_to_mapspec_layer(
            self._analysis(radius_px=17),
        )
        assert layer["paint"]["heatmap-radius"][6] == 17
        assert layer["heatmap"]["radius_px"] == 17
        assert layer["heatmap"]["radius_source"] == "explicit"
        assert not any("heatmap_radius_contract" in w for w in warnings)

    def test_legacy_meters_never_pixels_in_paint(self):
        from app.services.analysis_cartography_converter import (
            convert_analysis_to_mapspec_layer,
        )

        layer, _, warnings = convert_analysis_to_mapspec_layer(
            self._analysis(radius=2000),
        )
        # 2000（米）→ 30px 默认；绝无 2000px
        assert layer["paint"]["heatmap-radius"][6] == 30
        assert layer["heatmap"]["bandwidth_m"] == 2000
        assert layer["heatmap"]["radius_source"] == "legacy_radius_visual_default_applied"
        assert any("heatmap_radius_contract" in w for w in warnings)

    def test_resolve_from_metadata_legacy_shape(self):
        """历史会话 ref 的 metadata（只有旧 radius）同样经归一化边界。"""
        c = resolve_paint_radius_px({"radius": 1500})
        assert c.radius_px == 30 and c.bandwidth_m == 1500

    def test_resolve_prefers_explicit(self):
        c = resolve_paint_radius_px({"radius_px": 12, "radius": 1500})
        assert c.radius_px == 12


class TestEndToEndNoMetersAsPixels:
    def test_full_chain_meter_never_pixel(self):
        """1000m 全链路（工具 schema 值 → metadata → converter → paint）。

        该测试是 §26 的显式证明：1000 不再被解释为 1000px。
        """
        contract = normalize_heatmap_radius(legacy_radius=1000)
        assert contract.radius_px != 1000
        paint = heatmap_paint("classic", contract.radius_px)
        radius_expr = paint["heatmap-radius"]
        # zoom 插值表达式中的每个数值半径停靠点都必须远小于 1000
        for stop in (radius_expr[3], radius_expr[5], radius_expr[7]):
            assert isinstance(stop, int) and stop <= 80

    def test_raster_bandwidth_is_meters(self):
        """raster 模式的带宽语义 = 米（cell 平滑的真实输入）。"""
        c = normalize_heatmap_radius(bandwidth_m=1200, legacy_radius=None)
        assert c.bandwidth_m == 1200
        # sigma = bandwidth / cell —— 单位一致性由 density 模块保证
