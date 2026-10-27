"""G3D-V2-C 剖面 artifact 缝 conformance（profile_chart → ref:profile-*）。

断链修复（#1830 遗留）：elevation_profile 组件的 profileRef 通道此前
**无生产写入方**，且端点白名单硬编码 ref:chart- 直接拒绝——双重断链。
本文件钉住修复后的生产链：

1. terrain_profile 工具产出 profile_chart 种子（{chart: ChartData}）；
2. dispatch 缝铸 ref:profile-*（认可键纪律，沿 #517/uncertainty 先例）
   并剥内联体；
3. _author_display_result upsert elevation_profile 组件（profileRef/
   unit/verticalDatum options），失败 additive 不推翻层挂载；
4. MapSpec 权威面出现组件；端点白名单放行 ref:profile- 且数据可取回；
5. ref:chart- 以外旧白名单拒绝行为不回归（ref:geojson- 仍 400）。
"""

from __future__ import annotations

import os
import uuid

import numpy as np
import pytest

pytestmark = pytest.mark.unit

_REPO_DATA = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data")
)


def _write_cone_dem(path: str, n: int = 61) -> None:
    import rasterio

    r, c = np.mgrid[0:n, 0:n].astype(float)
    cone = np.clip(100.0 - 2.0 * np.hypot(r - 30, c - 30), 0.0, None)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=n,
        width=n,
        count=1,
        dtype="float32",
        transform=rasterio.transform.from_origin(500000.0, 4_000_000.0, 10.0, 10.0),
        crs="EPSG:32648",
    ) as dst:
        dst.write(cone.astype("float32"), 1)


async def _dispatch_profile(session_id: str, raster: str):
    from app.services.tool_dispatch_service import ToolDispatchService
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    svc = ToolDispatchService(registry=reg)
    tc = {
        "id": "tc-profile-1",
        "function": {
            "name": "terrain_profile",
            "arguments": {
                "raster_path": raster,
                "vertices": "[[500050, 3999950], [500450, 3999950]]",
            },
        },
    }
    return await svc.dispatch(tc, session_id, set())


@pytest.mark.asyncio
async def test_profile_ref_minted_and_component_upserted(tmp_path):
    from app.services.mapspec_store import mapspec_store
    from app.services.session_data import session_data_manager

    os.makedirs(_REPO_DATA, exist_ok=True)
    raster = os.path.join(_REPO_DATA, f"test_profile_dem_{uuid.uuid4().hex[:8]}.tif")
    _write_cone_dem(raster)
    session_id = f"profile-seam-{uuid.uuid4().hex[:8]}"
    try:
        result = await _dispatch_profile(session_id, raster)
        assert result.status == "ok", result.error_msg
        raw = result.raw_result
        # 1) ref 铸造 + 内联体剥离
        assert "profile_chart" not in raw
        profile_ref = raw.get("profile_ref")
        assert isinstance(profile_ref, str) and profile_ref.startswith("ref:profile-")
        # 2) 层挂载（geojson 通道不变）
        assert raw.get("layer_id", "").startswith("result-")
        # 3) 组件 upsert 成功
        comp = raw.get("profile_component")
        assert comp and comp.get("success") is True
        assert comp["component_id"] == "elevation-profile"
        # 4) 数据可取回（{chart: ChartData} 形状，≥2 点）
        payload = await session_data_manager.get(session_id, profile_ref)
        assert isinstance(payload, dict) and isinstance(payload.get("chart"), dict)
        chart = payload["chart"]
        assert chart["type"] == "line"
        assert len(chart["data"]) >= 2
        assert all("name" in p and "value" in p for p in chart["data"])
        # 5) MapSpec 权威面：组件带 profileRef options
        spec = await mapspec_store.get_mapspec(session_id)
        comps = [
            c
            for c in ((spec.get("layout") or {}).get("components") or [])
            if isinstance(c, dict) and c.get("type") == "elevation_profile"
        ]
        assert comps, "elevation_profile component must be authored into MapSpec"
        assert comps[0].get("options", {}).get("profileRef") == profile_ref
        # 指纹一致性：result 携带的 mapspec 是组件突变后的最新代
        from app.lib.cartography.quality_loop import cartographic_fingerprint

        assert raw.get("mapspec_fingerprint") == cartographic_fingerprint(
            raw.get("mapspec")
        )
    finally:
        if os.path.exists(raster):
            os.unlink(raster)
        await session_data_manager.clear_session(session_id)


@pytest.mark.asyncio
async def test_chart_artifacts_endpoint_accepts_profile_refs(tmp_path):
    """端点白名单：ref:profile- 放行（{chart:...} 载荷直返）；ref:geojson-
    仍拒绝（旧收紧行为不回归）。"""
    from app.api.routes.chat_session_views import get_session_chart_artifact
    from app.services.session_data import session_data_manager
    from fastapi import HTTPException

    session_id = f"profile-ep-{uuid.uuid4().hex[:8]}"
    ref = await session_data_manager.store(
        session_id,
        {"chart": {"type": "line", "title": "高程剖面",
                    "data": [{"name": "0m", "value": 1.0}, {"name": "10m", "value": 2.0}],
                    "x_label": "线路里程", "y_label": "高程（m）"}},
        prefix="profile",
    )
    try:
        res = await get_session_chart_artifact(session_id, ref, _conv=None)
        assert res["chart"]["type"] == "line"
        # 非 chart/profile 族仍拒绝
        with pytest.raises(HTTPException) as exc_info:
            await get_session_chart_artifact(session_id, "ref:geojson-deadbeef", _conv=None)
        assert exc_info.value.status_code == 400
        with pytest.raises(HTTPException):
            await get_session_chart_artifact(
                session_id, "ref:profile-missing", _conv=None
            )
    finally:
        await session_data_manager.clear_session(session_id)


def test_terrain_profile_tool_seeds_profile_chart(tmp_path, monkeypatch):
    """工具面：profile_chart 种子存在且 ≤512 点（面板可用性预算）。"""
    import rasterio
    from rasterio.transform import Affine

    from app.tools.registry import ToolRegistry
    from app.tools import terrain_analysis as ta

    monkeypatch.setattr(ta, "validate_data_path", lambda p: str(p))
    n = 40
    r, c = np.mgrid[0:n, 0:n].astype(float)
    cone = np.clip(100.0 - 2.0 * np.hypot(r - 20, c - 20), 0.0, None)
    raster = str(tmp_path / "cone.tif")
    with rasterio.open(
        raster, "w", driver="GTiff", height=n, width=n, count=1,
        dtype="float64",
        transform=Affine(10.0, 0.0, 0.0, 0.0, -10.0, float(n * 10)),
        crs="EPSG:32650",
    ) as dst:
        dst.write(cone, 1)
    registry = ToolRegistry()
    ta.register_terrain_tools(registry)
    res = registry._tools["terrain_profile"](
        raster, [[5.0, 395.0], [395.0, 395.0]], sampling_step_m=1.0
    )
    assert res["success"] is True
    seed = res.get("profile_chart")
    assert isinstance(seed, dict) and isinstance(seed.get("chart"), dict)
    assert len(seed["chart"]["data"]) >= 2
    assert len(seed["chart"]["data"]) <= 512
    assert res["meta"]["profile_chart_points"] == len(seed["chart"]["data"])
