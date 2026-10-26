"""G3D04 M4 · 场景编排工具面测试（真实 registry dispatch 缝，零 LLM）。

三条端到端链的单元级锚（验收 D1）：

1. 表达链：apply_scene_representation → SetSceneIntent 事务（真实
   mapspec_store），is_compiled/mutation_revision 证据面；
2. 相机链：fly_camera → SetViewIntent（user-wins 抑制为 typed 状态）；
3. 分析链：run_3d_analysis → capability 门 → viewshed_analysis 真实
   dispatch（合成 DEM GeoTIFF）→ MethodUseContract 摘要 + 失效绑定。
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
    return f"test-scene3d-tools-{uuid.uuid4().hex[:12]}"


async def _seed(session_id: str) -> None:
    """最小 spec 骨架（init + dem 源 + 挤出层证据）。"""
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


class TestGet3DCapabilities:
    @pytest.mark.asyncio
    async def test_requires_session(self, registry):
        res = await registry.dispatch("get_3d_capabilities", {})
        assert res["success"] is False

    @pytest.mark.asyncio
    async def test_reports_unavailable_with_alternatives_on_empty_session(
            self, registry, session_id):
        await _seed(session_id)
        # dem 源在 → terrain available；无挤出层时 extrusion unavailable
        res = await registry.dispatch("get_3d_capabilities", {
            "session_id": session_id})
        assert res["success"] is True
        entries = {e["key"]: e for e in res["capabilities"]}
        assert entries["terrain"]["status"] == "available"
        assert entries["extrusion"]["status"] == "available"  # 有挤出层证据
        assert entries["scene_asset"]["status"] == "unavailable"
        assert entries["scene_asset"]["alternatives"]


class TestApplySceneRepresentation:
    @pytest.mark.asyncio
    async def test_dry_run_returns_decision_without_mutation(
            self, registry, session_id):
        await _seed(session_id)
        from app.services.mapspec_store import mapspec_store

        before = await mapspec_store.get_mapspec(session_id)
        res = await registry.dispatch("apply_scene_representation", {
            "session_id": session_id, "user_dimension": "3d",
            "terrain_requested": True, "dry_run": True})
        assert res["success"] is True
        assert res["decision"]["mode"] == "3d"
        assert res["suggested_scene"]["terrain"]["source"] == "dem"
        assert res.get("applied") is None
        after = await mapspec_store.get_mapspec(session_id)
        assert (after or {}).get("scene") == (before or {}).get("scene")

    @pytest.mark.asyncio
    async def test_applies_scene_transactionally_with_evidence(
            self, registry, session_id):
        await _seed(session_id)
        res = await registry.dispatch("apply_scene_representation", {
            "session_id": session_id, "user_dimension": "3d",
            "terrain_requested": True})
        assert res["success"] is True, res
        assert res["applied"] is True
        assert res["scene_applied"]["mode"] == "3d"
        assert isinstance(res.get("mutation_revision"), int)
        assert res.get("is_compiled") is not None

    @pytest.mark.asyncio
    async def test_honest_degradation_without_evidence(
            self, registry, session_id):
        """无证据会话：用户 3D → 降级执行 + degradations 可见（不伪造 3D）。"""
        from app.services.gis_harness.requirement_ir.service import (
            ensure_document,
        )
        from app.services.gis_harness.intent import MapRequestIntent
        from app.services.mapspec_store import mapspec_store
        from app.services.session_data import session_data_manager

        # IR 披露闭环需要需求文档在场（无 intent 的会话记 skipped）
        await ensure_document(session_data_manager, session_id,
                              "把地图切换成 3D 视图", turn=1,
                              core=MapRequestIntent(task="distribution_overview"))
        await mapspec_store.init_project(session_id)
        res = await registry.dispatch("apply_scene_representation", {
            "session_id": session_id, "user_dimension": "3d",
            "terrain_requested": True})
        assert res["success"] is True
        assert res["decision"]["mode"] != "3d"
        codes = {d["code"] for d in res["decision"]["degradations"]}
        assert "SCENE_USER_3D_NO_HEIGHT_EVIDENCE" in codes
        assert res["decision"]["user_dimension"] == "3d"
        # D4 披露闭环（review P1-4）：降级注记落 IR 跨轮可见
        assert res.get("degrade_reason_recorded") == "recorded"
        from app.services.gis_harness.requirement_ir.service import (
            get_requirement_view,
        )
        from app.services.session_data import session_data_manager

        view = await get_requirement_view(session_data_manager, session_id)
        scene_view = view.get("scene") or {}
        assert scene_view.get("degrade_reason") == "SCENE_USER_3D_NO_HEIGHT_EVIDENCE"


class TestFlyCamera:
    @pytest.mark.asyncio
    async def test_applies_view_transactionally(self, registry, session_id):
        await _seed(session_id)
        res = await registry.dispatch("fly_camera", {
            "session_id": session_id, "pitch": 45.0, "bearing": -15.0})
        assert res["success"] is True, res
        assert res["status"] == "apply" and res["applied"] is True
        from app.services.mapspec_store import mapspec_store

        spec = await mapspec_store.get_mapspec(session_id)
        assert spec["view"]["pitch"] == 45.0
        assert spec["view"]["bearing"] == -15.0

    @pytest.mark.asyncio
    async def test_user_wins_suppressed_when_viewport_diverges(
            self, registry, session_id):
        """user-wins 反证：用户镜头偏离 spec 时 fly_camera 必须 typed 抑制。"""
        await _seed(session_id)
        # 注入用户 viewport（与 spec view 偏离）
        from app.services.gis_harness.interaction_ir.ingest import (
            ingest_interaction_batch,
        )
        from app.services.session_data import session_data_manager

        await ingest_interaction_batch(session_data_manager, session_id, [{
            "event_id": "evt-viewport-1",
            "kind": "viewport_settled",
            "client_instance_id": "c1",
            "client_seq": 1,
            "actor": "user",
            "payload": {"center": [104.0, 30.6], "zoom": 11.0,
                        "bearing": 88.0, "pitch": 65.0},
        }])
        res = await registry.dispatch("fly_camera", {
            "session_id": session_id, "pitch": 30.0})
        assert res["success"] is True
        assert res["status"] == "user_wins_suppressed"
        assert res["applied"] is False
        assert res["reason_code"] == "SCENE_CAMERA_USER_VIEWPORT_ACTIVE"
        from app.services.mapspec_store import mapspec_store

        spec = await mapspec_store.get_mapspec(session_id)
        assert spec["view"].get("pitch") != 30.0


class TestRun3DAnalysis:
    @pytest.mark.asyncio
    async def test_declines_when_dem_capability_missing(
            self, registry, session_id):
        from app.services.mapspec_store import mapspec_store

        await mapspec_store.init_project(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "viewshed",
            "raster_path": "dem.tif", "observer_x": 1.0, "observer_y": 1.0})
        assert res["success"] is False
        assert res["declined"] is True
        assert res["reason_code"] == "SCENE_CAP_VIEWSHED_NO_DEM"
        assert res["alternatives"], "typed decline 必须带 alternatives"

    @pytest.mark.asyncio
    async def test_declines_pending_analysis_kinds(self, registry, session_id):
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "skyline"})
        assert res["success"] is False and res["declined"] is True
        assert res["reason_code"] == "SCENE_CAP_ALGORITHM_PENDING_G3D03"

    @pytest.mark.asyncio
    async def test_viewshed_real_dispatch_with_synthetic_dem(
            self, registry, session_id, monkeypatch):
        """分析链端到端：合成 DEM GeoTIFF → 真实 viewshed dispatch →
        证据 + MethodUseContract 摘要 + 失效绑定。"""
        await _seed(session_id)
        rasterio = pytest.importorskip("rasterio")
        # 合成锥形山体 DEM（data_dir 内，validate_data_path 通过）
        repo_data = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                         "data"))
        os.makedirs(repo_data, exist_ok=True)
        path = os.path.join(repo_data, f"test_scene3d_dem_{uuid.uuid4().hex[:8]}.tif")
        n = 61
        r, c = np.mgrid[0:n, 0:n].astype(float)
        cone = np.clip(100.0 - 2.0 * np.hypot(r - 30, c - 30), 0.0, None)
        try:
            with rasterio.open(
                    path, "w", driver="GTiff", height=n, width=n, count=1,
                    dtype="float32",
                    transform=rasterio.transform.from_origin(
                        500000.0, 4_000_000.0, 10.0, 10.0),
                    crs="EPSG:32648") as dst:
                dst.write(cone.astype("float32"), 1)
            res = await registry.dispatch("run_3d_analysis", {
                "session_id": session_id, "analysis": "viewshed",
                "raster_path": path,
                "observer_x": 500100.0, "observer_y": 3_999_900.0,
                "max_distance": 400.0})
            assert res["success"] is True, res
            assert res["result"]["success"] is True
            assert 0.0 <= res["result"]["visible_fraction"] <= 1.0
            assert res["method_use_contract"]["algorithm_id"] == "terrain.viewshed"
            assert res["staleness_binding"]["analysis_goal"] == "viewshed"
            # P2-4：未显式提供源 id 时不回填猜测
            assert res["staleness_binding"]["dem_source_id"] == ""
            assert res["staleness_binding"]["dem_source_binding"] == "none"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @pytest.mark.asyncio
    async def test_missing_raster_path_is_typed_decline(
            self, registry, session_id):
        await _seed(session_id)
        res = await registry.dispatch("run_3d_analysis", {
            "session_id": session_id, "analysis": "viewshed",
            "observer_x": 0, "observer_y": 0})
        assert res["declined"] is True
        assert res["reason_code"] == "SCENE_ANALYSIS_MISSING_RASTER_PATH"
