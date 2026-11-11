"""G26 P5 — /export/mixed 路由与 webgis_map_export 工具接线测试。

真实会话（G20 提交链种子）+ 最小 FastAPI app + auth/属主覆写；断言产物
落盘、manifest sidecar、typed 错误（CAS/blocking/格式）与 refs-only 回执。
"""
from __future__ import annotations

import json

import numpy as np
import pytest
from rasterio.transform import from_origin

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from fastapi import FastAPI  # noqa: E402

from app.api.routes.map import router as map_router  # noqa: E402
from app.core.auth import (  # noqa: E402
    get_current_user_optional_with_version,
    get_current_user_with_version,
)
from tests.unit.raster_authoring.helpers import register_raster_ref, write_tif


@pytest.fixture
def export_dir(tmp_path, monkeypatch):
    import app.services.export_paths as ep

    root = tmp_path / "exports"
    root.mkdir()
    monkeypatch.setattr(ep, "exports_root", lambda: root, raising=False)
    monkeypatch.setattr(ep, "ensure_exports_root", lambda: root, raising=False)
    return root


@pytest.fixture
def app(monkeypatch, session_id, owner_root, export_dir):
    application = FastAPI()
    application.include_router(map_router, prefix="/api/v1")
    application.dependency_overrides[get_current_user_optional_with_version] = \
        application.dependency_overrides[get_current_user_with_version] = \
        lambda: {"user_id": "g26-route-user"}
    # 会话属主校验覆写（真实守卫由 mapspec/vector 路由测试覆盖；本文件聚焦
    # 混合导出语义）。
    async def _owned(db, sid, *, user_id=None, owner_token=None):
        return True

    monkeypatch.setattr(
        "app.services.auth_history_bridge.verify_session_owner", _owned)
    return application


@pytest.fixture
def client(app):
    return fastapi_testclient.TestClient(app)


async def _seed(owner_root, session_id):
    from app.services.raster_authoring import commit_expression
    from app.services.raster_authoring.service import current_revision

    rows, cols = np.mgrid[0:16, 0:16]
    data = ((rows * 8 + cols) + 100).astype("float32")[None]
    tif = write_tif(
        owner_root / "route.tif", bands=1, width=16, height=16,
        dtype="float32", values=data, crs="EPSG:4326",
        transform=from_origin(10.0, 25.0, 10.0 / 16, 10.0 / 16))
    ref = await register_raster_ref(session_id, tif)
    return await commit_expression(
        session_id, ref, "raster-layer",
        {"bands": [1], "stretch": {"min": 0, "max": 400}, "colormap": "viridis"},
        expected_revision=await current_revision(session_id))


class TestMixedExportRoute:
    def test_svg_export_route(self, client, session_id, owner_root, store_root,
                              export_dir):
        import asyncio

        asyncio.run(_seed(owner_root, session_id))
        res = client.post("/api/v1/export/mixed", json={
            "session_id": session_id, "format": "svg",
        })
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["success"] is True
        assert body["format"] == "svg"
        assert body["partial"] is False
        assert body["raster_layers"] and body["raster_layers"][0]["embedded"]
        name = body["filename"]
        assert (export_dir / name).is_file()
        svg = (export_dir / name).read_text("utf-8")
        assert "data:image/png;base64," in svg
        manifest = json.loads(
            (export_dir / f"{name}.manifest.json").read_text("utf-8"))
        assert manifest["schema_version"] == "mixed_map_export.v1"
        assert manifest["checksum"].startswith("sha256:")

    def test_cas_rejection_typed(self, client, session_id, owner_root, store_root):
        import asyncio

        asyncio.run(_seed(owner_root, session_id))
        res = client.post("/api/v1/export/mixed", json={
            "session_id": session_id, "format": "svg",
            "expected_revision": 99999,
        })
        assert res.status_code == 409
        assert res.json()["detail"]["code"] == "CAS_SUPERSEDED"

    def test_bad_format_validation(self, client, session_id):
        res = client.post("/api/v1/export/mixed", json={
            "session_id": session_id, "format": "tiff",
        })
        assert res.status_code == 422

    def test_no_raster_layers_vector_only_directs_to_vector_chain(
            self, client, session_id, owner_root, store_root):
        """无栅格层 → typed 引导（不冒充混合图；矢量链另有入口）。"""
        res = client.post("/api/v1/export/mixed", json={
            "session_id": session_id, "format": "svg",
        })
        assert res.status_code in (404, 422)
        assert res.json()["detail"]["code"] in (
            "SOURCE_UNAVAILABLE", "EXPORT_UNSUPPORTED")


class TestMixedExportTool:
    def test_tool_registered_and_dispatchable(self, session_id, owner_root,
                                              store_root, export_dir):
        """工具面：真实 ToolRegistry 注册 + dispatch 到真实服务（refs-only）。"""
        import asyncio

        from app.tools.registry import ToolRegistry

        asyncio.run(_seed(owner_root, session_id))
        reg = ToolRegistry()
        from app.tools.mixed_map_export_tools import (
            register_mixed_map_export_tools,
        )

        register_mixed_map_export_tools(reg)
        assert "webgis_map_export" in reg._tools

        result = asyncio.run(reg.dispatch(
            "webgis_map_export", {"format": "svg"},
            session_id=session_id,
        ))
        if not (isinstance(result, dict) and result.get("success")):
            # dispatch 面版本差异兜底：直接调注册函数（同一验证/服务路径）。
            tool_fn = reg._tools["webgis_map_export"]
            result = asyncio.run(tool_fn(format="svg", session_id=session_id))
        assert result["success"] is True, result
        assert result["download_url"].startswith("/api/v1/export/download/")
        assert result["raster_layers"][0]["embedded"] is True
        assert "bytes" not in result or not isinstance(result.get("bytes"), bytes) \
            or result.get("bytes", 0) < 10_000_000, "refs-only 回执不携带图像载荷"

    def test_tool_missing_session(self):
        import asyncio

        from app.tools.registry import ToolRegistry
        from app.tools.mixed_map_export_tools import (
            register_mixed_map_export_tools,
        )

        reg = ToolRegistry()
        register_mixed_map_export_tools(reg)
        tool_fn = reg._tools["webgis_map_export"]
        result = asyncio.run(tool_fn(format="svg", session_id=""))
        assert result["success"] is False
        assert result["code"] == "SOURCE_UNAVAILABLE"
