"""WP08：Pi 工具真实注册入口（与 UI 同一领域服务、同一错误码、有界回执）。"""
from __future__ import annotations

import pytest

from app.services.raster_authoring.service import _read_mapspec, current_revision
from app.tools.registry import ToolRegistry
from app.tools.raster_expression_tools import register_raster_expression_tools
from tests.unit.raster_authoring.helpers import register_raster_ref, write_tif


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_raster_expression_tools(reg)
    return reg


@pytest.mark.asyncio
async def test_registered_tools_cover_describe_apply_probe_export(
    registry, owner_root, session_id, store_root, tmp_path, monkeypatch
):
    import app.services.export_paths as ep

    export_root = tmp_path / "exports"
    export_root.mkdir()
    monkeypatch.setattr(ep, "exports_root", lambda: export_root, raising=False)
    monkeypatch.setattr(ep, "ensure_exports_root", lambda: export_root, raising=False)

    ref = await register_raster_ref(session_id, write_tif(owner_root / "four.tif", bands=4))
    described = await registry.dispatch(
        "webgis_raster_expression_describe", {"ref_id": ref}, session_id=session_id
    )
    assert described["success"] is True, described
    assert described["band_count"] == 4
    assert len(described["bands"]) <= 6
    assert "histograms" not in described  # 有界投影，不放直方图桶
    assert described["defaults"]["mode"] == "rgb"

    applied = await registry.dispatch(
        "webgis_raster_expression_apply",
        {
            "ref_id": ref, "layer_id": "layer-pi",
            "expected_revision": await current_revision(session_id),
            "expression": {"bands": [4, 3, 2], "stretch": {"min": 0, "max": 500}},
            "mode": "commit",
        },
        session_id=session_id,
    )
    assert applied.get("committed") is True, applied
    doc = await _read_mapspec(session_id)
    assert doc["layers"][0]["raster_style"]["bands"] == [4, 3, 2]

    probed = await registry.dispatch(
        "webgis_raster_expression_probe",
        {"ref_id": ref, "points": [[10.005, 19.995]], "crs": "EPSG:4326",
         "bands": [1, 4]},
        session_id=session_id,
    )
    assert probed["success"] is True
    assert [v["raw"] for v in probed["statuses"][0]["values"]] == [100.0, 400.0]
    assert probed["interpolated"] is False

    exported = await registry.dispatch(
        "webgis_raster_expression_export",
        {"expected_revision": applied["mapspec_revision"], "format": "png"},
        session_id=session_id,
    )
    assert exported["success"] is True, exported
    assert (export_root / exported["filename"]).exists()
    assert exported["frozen_style_ids"] == [applied["style_id"]]


@pytest.mark.asyncio
async def test_tool_and_ui_share_the_same_error_code(registry, owner_root, session_id):
    ref = await register_raster_ref(session_id, write_tif(owner_root / "four.tif", bands=4))
    bad = await registry.dispatch(
        "webgis_raster_expression_apply",
        {
            "ref_id": ref, "layer_id": "layer-pi",
            "expected_revision": 0,
            "expression": {"bands": [1, 2], "stretch": {"min": 0, "max": 100}},
            "mode": "commit",
        },
        session_id=session_id,
    )
    assert bad["success"] is False
    assert bad["code"] == "BAND_COUNT_UNSUPPORTED"
    assert bad["field"] == "bands"
    assert (await _read_mapspec(session_id)).get("layers") is None  # 非法输入零写入


@pytest.mark.asyncio
async def test_tool_rejects_local_paths_and_missing_session(registry):
    missing = await registry.dispatch(
        "webgis_raster_expression_describe",
        {"ref_id": "C:/secret/data.tif"},
        session_id="",
    )
    assert missing["success"] is False
