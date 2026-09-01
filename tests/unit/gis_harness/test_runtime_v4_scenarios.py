"""Runtime V4 场景测试（§50 Scenario A/D/F 子集 —— harness 可锁定面）。

- Scenario A（chart 联动零手工配置）：GeoJSON 字段映射图表自动携带
  selectionField；data ref ↔ MapSpec source 自动解析 layerId；
- Scenario D（组件生命周期闭环）：create → duplicate → rebind → remove 全链
  MapSpec 一致（条目、幂等、CAS）；
- Scenario F（raster 一等产物）：注册 → sweep 状态 → spec 引用保护。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


class TestScenarioASelectionFieldAutogen:
    def test_geojson_mapping_chart_carries_selection_field(self):
        from app.tools.chart import generate_chart

        out = generate_chart(
            chart_type="bar",
            title="各区学校数量",
            data=[
                {"properties": {"ct_name": "武侯区", "count": 88}},
                {"properties": {"ct_name": "锦江区", "count": 62}},
            ],
            x_field="ct_name",
            y_field="count",
        )
        assert "error" not in out, out
        assert out["chart"]["selectionField"] == "ct_name"
        assert out["chart"]["data"][0]["name"] == "武侯区"

    def test_plain_name_value_chart_omits_selection_field(self):
        from app.tools.chart import generate_chart

        out = generate_chart(
            chart_type="bar",
            title="t",
            data=[{"name": "a", "value": 1}],
        )
        assert "selectionField" not in out["chart"]

    def test_scatter_omits_selection_field(self):
        from app.tools.chart import generate_chart

        out = generate_chart(
            chart_type="scatter", title="t",
            data=[{"properties": {"x": 1, "y": 2, "n": "a"}}],
            x_field="x", y_field="y", name_field="n",
        )
        assert "selectionField" not in out["chart"]


class TestScenarioDComponentLifecycleLoop:
    async def test_full_lifecycle_through_tool_actions(self, monkeypatch, tmp_path):
        """webgis_component_update 的 action 通道：create→duplicate→rebind→remove。"""
        from app.services.gis_harness.tools import register_gis_harness_tools
        from app.tools.registry import ToolRegistry

        monkeypatch.setattr(
            "app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path,
        )
        registry = ToolRegistry()
        register_gis_harness_tools(registry)
        # 直接走注册表执行面（与 dispatch 一致）。
        schema_names = {s["function"]["name"] for s in registry.get_schemas()}
        assert "webgis_component_update" in schema_names

        # create
        out_create = await _invoke(
            registry, "webgis_component_update",
            session_id="scenario-d",
            component_id="chart-panel-1",
            component_type="chart_panel",
            chart={"type": "bar", "title": "各区数量", "data": [{"name": "武侯区", "value": 10}]},
            create=True,
        )
        assert out_create["success"], out_create

        # duplicate（多实例 chart_panel）
        out_dup = await _invoke(
            registry, "webgis_component_update",
            session_id="scenario-d", component_id="chart-panel-1", action="duplicate",
        )
        assert out_dup["success"], out_dup

        # rebind 到新 chartRef（ref 存在性探测：session_data 里先存一个）
        from app.services.session_data import session_data_manager

        ref = await session_data_manager.store(
            "scenario-d", {"chart": {"type": "bar", "title": "x", "data": [{"name": "a", "value": 1}]}},
            prefix="chart",
        )
        out_rebind = await _invoke(
            registry, "webgis_component_update",
            session_id="scenario-d", component_id="chart-panel-1",
            action="rebind", rebind_chart_ref=ref,
        )
        assert out_rebind["success"], out_rebind
        assert any(
            c.get("options", {}).get("chartRef") == ref
            for c in out_rebind.get("components", [])
        )

        # remove（真删除）
        out_remove = await _invoke(
            registry, "webgis_component_update",
            session_id="scenario-d", component_id="chart-panel-1", action="remove",
        )
        assert out_remove["success"], out_remove
        ids = {c.get("id") for c in out_remove.get("components", [])}
        assert "chart-panel-1" not in ids
        assert "chart-panel-1-copy" in ids  # 副本仍在

    async def test_rebind_rejects_unknown_layer(self, monkeypatch, tmp_path):
        from app.services.gis_harness.tools import register_gis_harness_tools
        from app.tools.registry import ToolRegistry

        monkeypatch.setattr(
            "app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path,
        )
        registry = ToolRegistry()
        register_gis_harness_tools(registry)
        await _invoke(
            registry, "webgis_component_update",
            session_id="scenario-d2", component_id="c1",
            component_type="chart_panel", create=True,
            chart={"type": "bar", "title": "t", "data": [{"name": "a", "value": 1}]},
        )
        out = await _invoke(
            registry, "webgis_component_update",
            session_id="scenario-d2", component_id="c1",
            action="rebind", rebind_layer_id="no-such-layer",
        )
        assert not out["success"]
        assert "不在当前 MapSpec" in out["message"]

    async def test_duplicate_rejects_singleton_via_tool(self, monkeypatch, tmp_path):
        from app.services.gis_harness.tools import register_gis_harness_tools
        from app.tools.registry import ToolRegistry

        monkeypatch.setattr(
            "app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path,
        )
        registry = ToolRegistry()
        register_gis_harness_tools(registry)
        await _invoke(
            registry, "webgis_component_update",
            session_id="scenario-d3", component_id="arrow",
            component_type="north_arrow", create=True, options={"variant": "compass_rose"},
        )
        out = await _invoke(
            registry, "webgis_component_update",
            session_id="scenario-d3", component_id="arrow", action="duplicate",
        )
        assert not out["success"]


class TestScenarioFRasterFirstClass:
    async def test_spec_referenced_raster_survives_sweep(self, tmp_path, monkeypatch):
        """F：spec 持续供着的 raster surface 在 sweep 后仍 valid（样式改动/
        重绘不得牵连数据面生存期）。"""
        from app.services.artifact_registry import (
            register_artifact,
            sweep_statuses,
            list_artifacts,
        )

        sid = "scenario-f"
        raster_dir = tmp_path / sid / "raster"
        raster_dir.mkdir(parents=True)
        (raster_dir / "heat1.png").write_bytes(b"png")
        monkeypatch.setattr("app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path)

        mapspec = {
            "sources": {"heat": {"type": "raster", "imageRef": "ref:raster/heat1"}},
            "layers": [{"id": "heat", "source": "heat", "type": "raster"}],
        }
        await register_artifact(
            sid, artifact_id="ref:raster/heat1", artifact_type="raster_surface",
        )
        result = await sweep_statuses(sid, mapspec=mapspec)
        assert "ref:raster/heat1" in result["valid"]
        records = {r.artifact_id: r for r in await list_artifacts(sid)}
        assert records["ref:raster/heat1"].status == "valid"


async def _invoke(registry, name: str, /, **kwargs):
    """经 registry 执行面调用工具（与 dispatch 的 resolve+execute 同构）。"""
    import inspect

    meta = registry.all_metadata()
    assert name in meta
    func = getattr(registry, "_tools", {}).get(name)
    assert func is not None, f"tool {name} not registered"
    result = func(**kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result
