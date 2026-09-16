"""合成离线 E2E（ADR-0197 Oracle）：网络 deny 下核心本地 GIS 任务可完成。

双重 deny 证明：
- 应用层：NETWORK_EGRESS_MODE=allowlist（真实守卫决策路径）；
- 套接字层：``offline_socket_guard()``（tests/data/offline_guard.py，
  外部 connect 直接 raise）——管线全程不触外网是**证明**而非假设。

管线：合成 GeoJSON → SessionStore ref → buffer/statistics 分析 →
MapSpec 图层 → GeoJSON 导出文件。同进程内反向证明：远程源请求 typed 拒绝。
"""
import json

import pytest

from app.core.config import settings
from app.core.egress import AirGappedEgressError, reset_policy_cache
from tests.data.offline_guard import offline_socket_guard


@pytest.fixture()
def air_gapped_env(monkeypatch, tmp_path):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "DEPLOYMENT_PROFILE", "air_gapped")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_MODE", "allowlist")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_ALLOW", "")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_ALLOW_PRIVATE", "true")
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "TMP_DIR", str(tmp_path / "tmp"))
    reset_policy_cache()
    yield tmp_path
    reset_policy_cache()


def _synthetic_points(n: int = 10) -> dict:
    """合成 POI 点集（确定性；EPSG:4326，北京一带）。"""
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [116.30 + 0.01 * i, 39.90 + 0.005 * i]},
                "properties": {"name": f"poi-{i}", "value": i * 10},
            }
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_offline_pipeline_data_analysis_map_export(air_gapped_env):
    from app.services.mapspec_store import MapSpecStore
    from app.services.session_data import session_data_manager
    from app.services.spatial_analyzer import SpatialAnalyzer

    points = _synthetic_points(10)

    with offline_socket_guard():
        # ── 1. 数据面：入库拿 ref（LLM/前端按 ref 取数，不塞 context）────
        session_id = "offline-e2e-session"
        ref_id = await session_data_manager.store(
            session_id, points, prefix="geojson")

        # ── 2. 分析面：纯本地 buffer + statistics（shapely，无网络）─────
        buffered = SpatialAnalyzer.buffer(points, distance=500, unit="m")
        assert buffered.success, buffered.summary
        buffered_fc = buffered.data
        assert buffered_fc["type"] == "FeatureCollection"
        assert len(buffered_fc["features"]) == 10

        stats = SpatialAnalyzer.statistics(points)
        assert stats.success

        # ── 3. 地图面：MapSpec 图层组装（本地 profile/view 推导）────────
        mapspec_store = MapSpecStore()
        await mapspec_store.init_project(session_id)
        layer = {
            "id": "poi_buffer_layer",
            "source": "poi_buffer_source",
            "type": "fill",
            "paint": {"color": "#3b82f6", "fill-opacity": 0.4},
        }
        res = await mapspec_store.layer_upsert(
            session_id, layer, source_data=buffered_fc)
        assert res["success"], res
        mapspec = res["mapspec"]
        assert any(ly["id"] == "poi_buffer_layer" for ly in mapspec["layers"])
        assert mapspec["sources"]["poi_buffer_source"]["profile"][
            "featureCount"] == 10

        # ── 4. 导出面：GeoJSON 落盘（离线部署的交付物形态）──────────────
        export_dir = air_gapped_env / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / "poi_buffer.geojson"
        export_path.write_text(
            json.dumps(buffered_fc, ensure_ascii=False), encoding="utf-8")
        reloaded = json.loads(export_path.read_text(encoding="utf-8"))
        assert len(reloaded["features"]) == 10
        assert reloaded["features"][0]["properties"]["name"] == "poi-0"

    # ref 可解析（SessionStore 本地实现，不经网络）
    assert ref_id.startswith("ref:")


@pytest.mark.asyncio
async def test_offline_pipeline_remote_capability_typed_denied(air_gapped_env):
    """同进程反向证明：守卫激活时远程数据源 typed unavailable。"""
    from app.services.data_fabric.errors import SecurityBlockedError
    from app.services.data_fabric.security import DataFabricSecurity

    with offline_socket_guard():
        with pytest.raises(SecurityBlockedError):
            DataFabricSecurity.validate_url("https://public.example/wfs")
        session = None
        from app.services.data_fabric.security import make_safe_session
        session = make_safe_session(allow_private=True)
        try:
            with pytest.raises((AirGappedEgressError, SecurityBlockedError)):
                session.get("https://api.stepfun.com/v1/models")
        finally:
            session.close()
