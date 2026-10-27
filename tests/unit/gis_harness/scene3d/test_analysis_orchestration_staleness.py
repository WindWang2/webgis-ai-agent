"""G3D-V2-C 三维分析编排全算法族 + staleness 生产接线（ADR-0276）。

#1829 断链修复的单元级锚：

1. **run_3d_analysis 全算法族**：六类分析（viewshed/line_of_sight/
   terrain_profile/surface_distance/surface_area/cut_fill_volume）经真实
   registry dispatch（合成 DEM GeoTIFF，data_dir 内）；skyline 仍 typed
   decline；
2. **失效裁决生产闭环**（此前 evaluate_staleness 零生产调用）：
   - run_3d_analysis 成功 → 绑定登记（_scene3d_analysis_bindings）；
   - 观察点漂移（feature_selected 事件）→ apply_scene_representation
     的 stale_analyses ACK 判旧（SCENE_STALE_OBSERVER_MOVED）；
   - **camera-only 不判旧**：fly_camera 视角变化后裁决仍 fresh
     （viewport_settled 不进入失效判定——范畴错误红线）；
   - DEM 源移除 → SCENE_STALE_DEM_SOURCE_GONE；
3. 绑定存储有界/幂等（FIFO ≤8、同 goal+layer+observer 覆盖）；
4. profile 分析经编排层同样产出 profile_ref + 组件（M2c 缝的编排入口）。
"""

from __future__ import annotations

import os
import uuid

import numpy as np
import pytest

from app.tools.registry import ToolRegistry
from app.tools import init_tools
from app.tools.scene_orchestration_tools import (
    register_scene_orchestration_tools,
)

pytestmark = pytest.mark.unit

_REPO_DATA = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data")
)


@pytest.fixture
def registry():
    reg = ToolRegistry()
    init_tools(reg)
    register_scene_orchestration_tools(reg)
    from app.agent_pi_bridge import set_tool_registry

    set_tool_registry(reg)
    return reg


@pytest.fixture
def session_id():
    return f"test-scene3d-orch-{uuid.uuid4().hex[:12]}"


async def _seed(session_id: str) -> None:
    from app.services.mapspec import UpsertLayerIntent, UpsertSourceIntent
    from app.services.mapspec_store import mapspec_store

    res = await mapspec_store.init_project(session_id)
    assert res["success"] is True
    res = await mapspec_store.engine.apply_mutation(session_id, UpsertSourceIntent(
        source_id="dem",
        source={"type": "raster-dem",
                "url": "https://example.test/dem/{z}/{x}/{y}.png"},
    ))
    assert res.is_error is False, res.error_msg
    res = await mapspec_store.engine.apply_mutation(session_id, UpsertLayerIntent(
        layer={"id": "bldg", "type": "fill-extrusion", "source": "features",
               "extrusion": {"height_field": "height",
                             "stats": {"valid": 25}}},
    ))
    assert res.is_error is False, res.error_msg


def _write_cone_dem(path: str, n: int = 61) -> None:
    import rasterio

    r, c = np.mgrid[0:n, 0:n].astype(float)
    cone = np.clip(100.0 - 2.0 * np.hypot(r - 30, c - 30), 0.0, None)
    with rasterio.open(
        path, "w", driver="GTiff", height=n, width=n, count=1,
        dtype="float32",
        transform=rasterio.transform.from_origin(500000.0, 4_000_000.0, 10.0, 10.0),
        crs="EPSG:32648",
    ) as dst:
        dst.write(cone.astype("float32"), 1)


@pytest.fixture()
def dem_path():
    os.makedirs(_REPO_DATA, exist_ok=True)
    path = os.path.join(_REPO_DATA, f"test_orch_dem_{uuid.uuid4().hex[:8]}.tif")
    _write_cone_dem(path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


async def _ledger_write(session_id: str, events: list) -> None:
    from app.services.session_data import session_data_manager

    ok = await session_data_manager.set_map_state_fields(
        session_id, {"_interaction_ir_ledger": events}
    )
    assert ok


# ── 1. 全算法族真实 dispatch ────────────────────────────────────────


class TestRun3DAnalysisAllFamilies:
    @pytest.mark.asyncio
    async def test_skyline_still_typed_decline(self, registry, session_id):
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "skyline"})
        assert res["success"] is False and res["declined"] is True
        assert res["reason_code"] == "SCENE_CAP_ALGORITHM_PENDING_G3D03"

    @pytest.mark.asyncio
    async def test_missing_params_typed_decline(self, registry, session_id):
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "line_of_sight",
            "raster_path": "dem.tif", "observer_x": 1.0, "observer_y": 1.0})
        assert res["declined"] is True
        assert res["reason_code"] == "SCENE_ANALYSIS_MISSING_PARAMS"

    @pytest.mark.asyncio
    async def test_line_of_sight_real_dispatch(self, registry, session_id, dem_path):
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "line_of_sight",
            "raster_path": dem_path,
            "observer_x": 500100.0, "observer_y": 3_999_900.0,
            "target_x": 500500.0, "target_y": 3_999_500.0})
        assert res["success"] is True, res
        assert res["analysis"] == "line_of_sight"
        assert res["result"]["success"] is True
        # LOS 工具载荷顶层展开 stats（visible/obstruction/min_clearance_m）
        assert isinstance(res["result"]["visible"], bool)
        assert res["method_use_contract"]["algorithm_id"] == "terrain.line_of_sight"
        assert res["staleness_binding"]["analysis_goal"] == "line_of_sight"
        assert res["staleness_binding_ack"]["success"] is True

    @pytest.mark.asyncio
    async def test_terrain_profile_real_dispatch_with_profile_ref(
        self, registry, session_id, dem_path
    ):
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "terrain_profile",
            "raster_path": dem_path,
            "vertices": [[500050.0, 3999950.0], [500450.0, 3999950.0]]})
        assert res["success"] is True, res
        # 编排层直接调 registry（不经 dispatch service 的 ref 缝）——
        # 绑定与契约摘要仍在；profile_ref 只经 ToolDispatchService 缝
        #（M2c 测试已钉）。
        assert res["result"]["success"] is True
        assert res["result"]["horizontal_m"] > 0
        assert res["method_use_contract"]["algorithm_id"] == "terrain.profile"
        assert res["staleness_binding"]["analysis_goal"] == "terrain_profile"

    @pytest.mark.asyncio
    async def test_surface_area_real_dispatch(self, registry, session_id, dem_path):
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "surface_area",
            "raster_path": dem_path,
            "polygon": [[500050.0, 3999950.0], [500450.0, 3999950.0],
                        [500450.0, 3999550.0], [500050.0, 3999550.0],
                        [500050.0, 3999950.0]]})
        assert res["success"] is True, res
        assert res["result"]["surface_area_m2"] >= res["result"]["planar_area_m2"]

    @pytest.mark.asyncio
    async def test_cut_fill_volume_real_dispatch(self, registry, session_id, dem_path):
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "cut_fill_volume",
            "raster_path": dem_path,
            "polygon": [[500100.0, 3999900.0], [500400.0, 3999900.0],
                        [500400.0, 3999600.0], [500100.0, 3999600.0],
                        [500100.0, 3999900.0]],
            "reference": "mean"})
        assert res["success"] is True, res
        assert res["result"]["net_volume_m3"] == pytest.approx(0.0, abs=1e-3)
        assert res["method_use_contract"]["algorithm_id"] == "terrain.cut_fill_volume"

    @pytest.mark.asyncio
    async def test_options_json_passthrough(self, registry, session_id, dem_path):
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "viewshed",
            "raster_path": dem_path,
            "observer_x": 500100.0, "observer_y": 3_999_900.0,
            "options": '{"max_distance": 300.0, "observer_height": 5}'})
        assert res["success"] is True, res
        assert res["result"]["meta"]["observer_height"] == 5.0
        assert res["result"]["meta"]["max_distance_m"] == 300.0
        # 几何默认开启（include_geometry 经 options 可关）——geojson 提升
        # 到编排层结果（外层 dispatch 缝的挂层通道）
        assert res.get("result_geojson_hoisted") is True
        assert isinstance(res.get("geojson"), dict)
        res2 = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "viewshed",
            "raster_path": dem_path,
            "observer_x": 500100.0, "observer_y": 3_999_900.0,
            "options": '{"include_geometry": false}'})
        assert res2["success"] is True
        assert "geojson" not in res2 and "geojson" not in res2["result"]


# ── 2. 失效裁决生产闭环 ────────────────────────────────────────────


class TestStalenessProductionWiring:
    @pytest.mark.asyncio
    async def test_observer_moved_invalidates_via_scene_ack(
        self, registry, session_id, dem_path
    ):
        """拖点失效：run viewshed（绑定 feature obs-1）→ 用户改选 obs-2 →
        apply_scene_representation 的 stale_analyses 判旧。"""
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "viewshed",
            "raster_path": dem_path,
            "observer_x": 500100.0, "observer_y": 3_999_900.0,
            "observer_feature_id": "obs-1", "dem_source_id": "dem",
            "options": '{"max_distance": 300}'})
        assert res["success"] is True
        # 用户点选另一个要素（Interaction IR feature_selected）
        await _ledger_write(session_id, [{
            "kind": "feature_selected",
            "payload": {"feature_id": "obs-2", "layer_id": "observers"},
            "server_seq": 1,
        }])
        scene = await registry.dispatch("apply_scene_representation", {
            "session_id": session_id, "user_dimension": "3d"})
        stale = scene.get("stale_analyses") or []
        assert any(
            e["verdict"]["reason_code"] == "SCENE_STALE_OBSERVER_MOVED"
            for e in stale
        ), stale
        assert any(
            e["binding"]["analysis_goal"] == "viewshed" for e in stale)

    @pytest.mark.asyncio
    async def test_camera_only_change_keeps_fresh(
        self, registry, session_id, dem_path
    ):
        """camera-only 不判旧：视角变化（viewport_settled + fly_camera
        SetViewIntent）后裁决仍 fresh。"""
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "viewshed",
            "raster_path": dem_path,
            "observer_x": 500100.0, "observer_y": 3_999_900.0,
            "observer_feature_id": "obs-1", "dem_source_id": "dem",
            "options": '{"max_distance": 300}'})
        assert res["success"] is True
        cam = await registry.dispatch("fly_camera", {
            "session_id": session_id, "pitch": 65, "bearing": 120})
        assert cam.get("success") is True, cam
        await _ledger_write(session_id, [{
            "kind": "viewport_settled",
            "payload": {"zoom": 14, "center": [500000.0, 3999000.0],
                        "pitch": 70, "bearing": 130},
            "server_seq": 1,
        }])
        scene = await registry.dispatch("apply_scene_representation", {
            "session_id": session_id})
        # 相机/viewport 不进入失效判定：无 stale 上行
        assert scene.get("stale_analyses") == []
        from app.services.gis_harness.scene3d.analysis_binding_store import (
            evaluate_session_staleness,
        )

        evaluations = await evaluate_session_staleness(session_id)
        assert evaluations and all(
            e["verdict"]["state"] == "fresh" for e in evaluations
        )

    @pytest.mark.asyncio
    async def test_dem_source_removed_invalidates(
        self, registry, session_id, dem_path
    ):
        """DEM 源消失失效：绑定 dem 源不在当前 spec → SCENE_STALE_DEM_SOURCE_GONE。

        （mapspec intents 无 RemoveSourceIntent——用「当前 spec 无该源」的
        权威面对账，生产 evaluate 路径同一入口。）
        """
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "viewshed",
            "raster_path": dem_path,
            "observer_x": 500100.0, "observer_y": 3_999_900.0,
            "dem_source_id": "dem",
            "options": '{"max_distance": 300}'})
        assert res["success"] is True
        from app.services.gis_harness.scene3d.analysis_binding_store import (
            evaluate_session_staleness,
        )

        evaluations = await evaluate_session_staleness(
            session_id, mapspec={"sources": {}}
        )
        assert any(
            e["verdict"]["reason_code"] == "SCENE_STALE_DEM_SOURCE_GONE"
            for e in evaluations
        )

    @pytest.mark.asyncio
    async def test_prior_stale_reported_on_next_run(
        self, registry, session_id, dem_path
    ):
        """run_3d_analysis 执行前对账：旧绑定判旧随新分析 ACK 上行。"""
        await _seed(session_id)
        first = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "viewshed",
            "raster_path": dem_path,
            "observer_x": 500100.0, "observer_y": 3_999_900.0,
            "observer_feature_id": "obs-1", "dem_source_id": "dem",
            "options": '{"max_distance": 300}'})
        assert first["success"] is True
        assert first["prior_stale_analyses"] == []
        await _ledger_write(session_id, [{
            "kind": "feature_selected",
            "payload": {"feature_id": "obs-9", "layer_id": "observers"},
            "server_seq": 1,
        }])
        second = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "viewshed",
            "raster_path": dem_path,
            "observer_x": 500150.0, "observer_y": 3_999_950.0,
            "observer_feature_id": "obs-2", "dem_source_id": "dem",
            "options": '{"max_distance": 300}'})
        assert second["success"] is True
        assert any(
            e["verdict"]["reason_code"] == "SCENE_STALE_OBSERVER_MOVED"
            for e in second["prior_stale_analyses"]
        )


# ── 3. 绑定存储纪律 ────────────────────────────────────────────────


class TestBindingStoreDiscipline:
    @pytest.mark.asyncio
    async def test_bounded_fifo_and_idempotent_upsert(self, session_id):
        from app.services.gis_harness.scene3d.analysis_binding_store import (
            MAX_SCENE3D_BINDINGS,
            load_analysis_bindings,
            register_analysis_binding,
        )
        from app.services.gis_harness.scene3d.staleness import AnalysisBinding

        for i in range(MAX_SCENE3D_BINDINGS + 3):
            ack = await register_analysis_binding(
                session_id,
                AnalysisBinding(
                    analysis_goal="viewshed",
                    result_layer_id=f"result-{i}",
                    dem_source_id="dem",
                    observer_x=float(i), observer_y=float(i),
                ),
            )
            assert ack["success"] is True
        bindings = await load_analysis_bindings(session_id)
        assert len(bindings) == MAX_SCENE3D_BINDINGS
        # FIFO：最旧的 result-0..2 被逐出
        ids = {b.result_layer_id for b in bindings}
        assert "result-0" not in ids and f"result-{MAX_SCENE3D_BINDINGS + 2}" in ids
        # 幂等覆盖：同 goal+layer+observer 刷新不重复
        target = bindings[-1]
        ack = await register_analysis_binding(session_id, target)
        assert ack["binding_count"] == MAX_SCENE3D_BINDINGS

    @pytest.mark.asyncio
    async def test_corrupt_entries_dropped_not_fatal(self, session_id):
        from app.services.gis_harness.scene3d.analysis_binding_store import (
            BINDINGS_KEY,
            load_analysis_bindings,
        )
        from app.services.session_data import session_data_manager

        await session_data_manager.set_map_state_fields(session_id, {
            BINDINGS_KEY: [{"analysis_goal": "viewshed", "bogus": True}, "junk"]
        })
        bindings = await load_analysis_bindings(session_id)
        assert bindings == []


class TestStalenessSemanticsReviewFixes:
    """review B1/E1 修正的回归锚。"""

    @pytest.mark.asyncio
    async def test_non_observer_analysis_not_stamped_and_stays_fresh(
        self, registry, session_id, dem_path
    ):
        """review B1：非观察者分析（cut_fill）不盖选择戳——用户换选
        别的要素不得把线路/区域语义分析误判旧。"""
        await _seed(session_id)
        await _ledger_write(session_id, [{
            "kind": "feature_selected",
            "payload": {"feature_id": "peak-a", "layer_id": "peaks"},
            "server_seq": 1,
        }])
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "cut_fill_volume",
            "raster_path": dem_path,
            "polygon": [[500100.0, 3999900.0], [500400.0, 3999900.0],
                        [500400.0, 3999600.0], [500100.0, 3999600.0],
                        [500100.0, 3999900.0]],
            "reference": "mean"})
        assert res["success"] is True
        assert res["staleness_binding"]["observer_feature_id"] == ""
        await _ledger_write(session_id, [{
            "kind": "feature_selected",
            "payload": {"feature_id": "peak-b", "layer_id": "peaks"},
            "server_seq": 2,
        }])
        from app.services.gis_harness.scene3d.analysis_binding_store import (
            evaluate_session_staleness,
        )

        evaluations = await evaluate_session_staleness(session_id)
        assert evaluations, "binding must be registered"
        assert all(e["verdict"]["state"] == "fresh" for e in evaluations), (
            "换选要素不得判旧非观察者语义分析（review B1 假阳性反证）"
        )

    @pytest.mark.asyncio
    async def test_observer_analysis_stamped_selection_stays_fresh(
        self, registry, session_id, dem_path
    ):
        """观察者分析（LOS 无显式 feature id）盖当前选择戳——同选择
        不自判旧，换选才失效。"""
        await _seed(session_id)
        await _ledger_write(session_id, [{
            "kind": "feature_selected",
            "payload": {"feature_id": "peak-a", "layer_id": "peaks"},
            "server_seq": 1,
        }])
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "line_of_sight",
            "raster_path": dem_path,
            "observer_x": 500100.0, "observer_y": 3_999_900.0,
            "target_x": 500500.0, "target_y": 3_999_500.0})
        assert res["success"] is True
        assert res["staleness_binding"]["observer_feature_id"] == "peak-a"
        from app.services.gis_harness.scene3d.analysis_binding_store import (
            evaluate_session_staleness,
        )

        # 同选择：fresh
        evaluations = await evaluate_session_staleness(session_id)
        assert all(e["verdict"]["state"] == "fresh" for e in evaluations)
        # 换选：LOS（观察者语义）判旧
        await _ledger_write(session_id, [{
            "kind": "feature_selected",
            "payload": {"feature_id": "peak-c", "layer_id": "peaks"},
            "server_seq": 2,
        }])
        evaluations = await evaluate_session_staleness(session_id)
        assert any(
            e["verdict"]["reason_code"] == "SCENE_STALE_OBSERVER_MOVED"
            for e in evaluations
        )

    @pytest.mark.asyncio
    async def test_cross_family_params_filtered_and_echoed(
        self, registry, session_id, dem_path
    ):
        """review G4/G3：跨族参数被过滤并回显；未知 options 键回显。"""
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "cut_fill_volume",
            "raster_path": dem_path,
            "polygon": [[500100.0, 3999900.0], [500400.0, 3999900.0],
                        [500400.0, 3999600.0], [500100.0, 3999600.0],
                        [500100.0, 3999900.0]],
            "reference": "mean",
            "observer_x": 999.0,  # 跨族污染参数
            "options": '{"typo_key": 1}'})
        assert res["success"] is True
        assert res["ignored_top_level_params"] == ["observer_x"]
        assert res["ignored_options_keys"] == ["typo_key"]
