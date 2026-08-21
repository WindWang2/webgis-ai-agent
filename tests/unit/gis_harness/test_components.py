"""CartographyComponent + MapSpec layout.components 集成 + 局部突变测试。"""
import pytest
import shutil

from app.services.gis_harness.components import (
    CartographyComponent,
    build_default_components,
    mutate_component,
    north_arrow_component,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = "test-gis-components-session"
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)
    yield sid
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)


class TestComponentSchema:
    def test_component_typed_serializable(self):
        c = north_arrow_component()
        dumped = c.to_mapspec()
        assert dumped["id"] == "north-arrow"
        assert dumped["type"] == "north_arrow"
        assert dumped["enabled"] is True
        assert dumped["position"] == "top-right"
        restored = CartographyComponent.model_validate(dumped)
        assert restored == c

    def test_legend_vs_colorbar_distinct(self):
        """legend（离散）与 colorbar（连续）是两种组件，不混用。"""
        heat = build_default_components(primary_cartography="visual_heatmap")
        choro = build_default_components(primary_cartography="administrative_choropleth")
        heat_types = [c.type for c in heat]
        choro_types = [c.type for c in choro]
        assert "continuous_colorbar" in heat_types and "legend" not in heat_types
        assert "legend" in choro_types and "continuous_colorbar" not in choro_types

    def test_default_components_deterministic_order(self):
        a = build_default_components(primary_cartography="visual_heatmap", title="t")
        b = build_default_components(primary_cartography="visual_heatmap", title="t")
        assert [c.id for c in a] == [c.id for c in b]
        priorities = [c.priority for c in a]
        assert priorities == sorted(priorities)

    def test_report_product_adds_layout_components(self):
        comps = build_default_components(
            primary_cartography="visual_heatmap", report_product=True,
        )
        types = [c.type for c in comps]
        assert "export_layout" in types and "map_border" in types


class TestComponentMutation:
    """§25 组件突变：只动命中组件，不触发数据/分析。"""

    def _base(self):
        return build_default_components(
            primary_cartography="visual_heatmap", title="成都小学分布",
        )

    def test_swap_compass_variant_only_touches_north_arrow(self):
        comps = self._base()
        before = [c.model_copy(deep=True) for c in comps]
        mutated, change = mutate_component(
            comps, component_type="north_arrow",
            options={"variant": "compass_rose"},
        )
        assert change["id"] == "north-arrow"
        assert change["options"]["to"]["variant"] == "compass_rose"
        # 其余组件逐字段不变
        for orig, now in zip(before, mutated):
            if orig.id != "north-arrow":
                assert orig == now
        # 数据层零触碰（无任何分析调用字段）
        assert not any("query" in str(c.options) for c in mutated)

    def test_disable_north_arrow(self):
        mutated, change = mutate_component(
            self._base(), component_type="north_arrow", enabled=False,
        )
        assert change["enabled"] == {"from": True, "to": False}
        north = next(c for c in mutated if c.type == "north_arrow")
        assert north.enabled is False

    def test_scale_bar_reposition(self):
        mutated, change = mutate_component(
            self._base(), component_id="scale-bar", position="bottom-left",
        )
        assert change["position"]["to"] == "bottom-left"

    def test_colorbar_vertical(self):
        mutated, change = mutate_component(
            self._base(), component_type="continuous_colorbar",
            options={"orientation": "vertical"},
        )
        bar = next(c for c in mutated if c.type == "continuous_colorbar")
        assert bar.options["orientation"] == "vertical"

    def test_title_change(self):
        mutated, change = mutate_component(
            self._base(), component_type="title", options={"text": "成都市小学密度图"},
        )
        title = next(c for c in mutated if c.type == "title")
        assert title.options["text"] == "成都市小学密度图"

    def test_miss_returns_none_no_side_effect(self):
        comps = self._base()
        mutated, change = mutate_component(
            comps, component_id="does-not-exist", enabled=False,
        )
        assert change is None
        assert mutated == comps


class TestMapSpecComponentsIntegration:
    """组件进入 MapSpec layout.components（lifecycle 事务 + 稳定排序）。"""

    @pytest.mark.asyncio
    async def test_layout_set_components_roundtrip(self, clean_session):
        comps = build_default_components(
            primary_cartography="visual_heatmap", title="成都小学分布",
        )
        res = await mapspec_store.layout_set(
            clean_session, components=[c.to_mapspec() for c in comps],
        )
        assert res["success"] is True
        layout = res["layout"]
        assert [c["type"] for c in layout["components"]] == [
            c.type for c in sorted(comps, key=lambda c: (c.priority, c.id))
        ]
        # mapspec 落盘可读回
        spec = await mapspec_store.get_mapspec(clean_session)
        assert spec["layout"]["components"][0]["type"] == "title"

    @pytest.mark.asyncio
    async def test_components_merge_keeps_legend_branch(self, clean_session):
        await mapspec_store.layout_set(
            clean_session, legend={"visible": True, "position": "top-right"},
        )
        comps = build_default_components(primary_cartography="visual_heatmap")
        res = await mapspec_store.layout_set(
            clean_session, components=[c.to_mapspec() for c in comps],
        )
        layout = res["layout"]
        assert layout["legend"]["position"] == "top-right"  # 未被组件写入破坏
        assert "components" in layout

    @pytest.mark.asyncio
    async def test_invalid_component_rejected_atomically(self, clean_session):
        """非法条目（缺 id/type）被确定性拒绝，不留半更新状态。"""
        await mapspec_store.layout_set(clean_session, legend={"visible": False})
        res = await mapspec_store.layout_set(
            clean_session, components=[{"type": "title"}],  # 缺 id
        )
        assert res["success"] is False
        spec = await mapspec_store.get_mapspec(clean_session)
        assert "components" not in spec["layout"]  # 原子：未写入
        assert spec["layout"]["legend"]["visible"] is False  # 旧状态保留

    @pytest.mark.asyncio
    async def test_components_survive_layer_upsert(self, clean_session):
        """组件写入后追加图层不会丢组件（COW 只拷贝 touched branch）。"""
        comps = build_default_components(primary_cartography="visual_heatmap")
        await mapspec_store.layout_set(
            clean_session, components=[c.to_mapspec() for c in comps],
        )
        from app.services.analysis_cartography_converter import (
            convert_analysis_to_mapspec_layer,
        )
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
             "properties": {"name": f"p{i}"}}
            for i in range(12)
        ]}
        layer, _, _ = convert_analysis_to_mapspec_layer({
            "geojson": fc, "algorithm": "heatmap_data",
            "type_hint": "heatmap",
            "metadata": {"radius_px": 22, "point_count": 12},
        })
        await mapspec_store.layer_upsert(clean_session, layer, fc)
        spec = await mapspec_store.get_mapspec(clean_session)
        assert len(spec["layout"]["components"]) == len(comps)
        assert any(ly["type"] == "heatmap" for ly in spec["layers"])
