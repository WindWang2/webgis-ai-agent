"""vector-pdf 路由 atlas 面（C14）：策略校验/透传、响应与 sidecar 回执。

路由级契约：非法 driver → 400 ``atlas_policy_invalid``；合法 atlas → 响应
携带 ``atlas``/``atlas_pages``/``layout_version``/``spec_fingerprint``，
diagnostics sidecar 同源落盘（GET /export/diagnostics/{filename} 可读）。
"""

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from fastapi import FastAPI  # noqa: E402

from app.api.routes.map import router as map_router  # noqa: E402
from app.core.auth import get_current_user_with_version  # noqa: E402
from app.services import export_paths  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(export_paths, "exports_root", lambda: tmp_path)
    app = FastAPI()
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exceptions import RequestValidationError
    from app.core.exception import (
        unified_http_exception_handler,
        unified_validation_exception_handler,
    )

    app.add_exception_handler(StarletteHTTPException, unified_http_exception_handler)
    app.add_exception_handler(RequestValidationError, unified_validation_exception_handler)
    app.include_router(map_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user_with_version] = lambda: {
        "user_id": "c14-route-user"}
    return fastapi_testclient.TestClient(app)


def _payload(zones: int = 3):
    feats = []
    for i in range(zones * 2):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [116.0 + (i % zones) * 0.3, 39.5 + (i // zones) * 0.3]},
            "properties": {"zone": f"z{i % zones}"},
        })
    return {
        "mapspec": {
            "version": "1.1",
            "sources": {"g": {"type": "geojson",
                              "inlineData": {"type": "FeatureCollection",
                                             "features": feats}}},
            "layers": [{"id": "l", "source": "g", "type": "circle",
                        "paint": {"circle-radius": 5}}],
            "layout": {"components": [
                {"id": "t", "type": "title", "options": {"text": "route atlas"}},
                {"id": "at", "type": "attribution", "options": {"text": "(c) test"}},
            ]},
        },
        "title": "路由 atlas",
    }


def test_route_rejects_invalid_driver(client, tmp_path, monkeypatch):
    monkeypatch.setattr(export_paths, "exports_root", lambda: tmp_path)
    body = _payload()
    body["atlas"] = {"driver": "magic"}
    resp = client.post("/api/v1/export/vector-pdf", json=body)
    assert resp.status_code == 400
    # 统一错误信封（ADR-0138）：结构化 detail 进 data
    data = resp.json().get("data") or {}
    assert data.get("code") == "atlas_policy_invalid"


def test_route_rejects_category_without_property(client, tmp_path, monkeypatch):
    monkeypatch.setattr(export_paths, "exports_root", lambda: tmp_path)
    body = _payload()
    body["atlas"] = {"driver": "category"}
    resp = client.post("/api/v1/export/vector-pdf", json=body)
    assert resp.status_code == 400
    assert (resp.json().get("data") or {}).get("code") == "atlas_category_property_missing"


def test_route_atlas_export_receipt_and_sidecar(client, tmp_path, monkeypatch):
    monkeypatch.setattr(export_paths, "exports_root", lambda: tmp_path)
    body = _payload()
    body["atlas"] = {"driver": "category", "categoryProperty": "zone",
                     "includeCover": True, "atlasTitle": "路由图册"}
    resp = client.post("/api/v1/export/vector-pdf", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["atlas"] is True
    assert data["layout_version"] == "1.0.0"
    assert data["spec_fingerprint"].startswith("pubspec-sha256:")
    pages = data["atlas_pages"]
    assert pages[0]["page_id"] == "cover"
    assert [p["page_id"] for p in pages[1:]] == ["cat_z0", "cat_z1", "cat_z2"]
    assert (tmp_path / data["filename"]).is_file()

    sidecar = client.get(f"/api/v1/export/diagnostics/{data['filename']}")
    assert sidecar.status_code == 200
    payload = sidecar.json()
    # sidecar 顶层回执（**payload 直出）+ 诊断列表
    assert payload.get("layout_version") == "1.0.0"
    assert payload.get("spec_fingerprint", "").startswith("pubspec-sha256:")
    assert [p["page_id"] for p in payload.get("atlas_pages", [])] == [
        "cover", "cat_z0", "cat_z1", "cat_z2"]
    assert isinstance(payload.get("diagnostics"), list)


def test_route_frames_driver_with_budget(client, tmp_path, monkeypatch):
    monkeypatch.setattr(export_paths, "exports_root", lambda: tmp_path)
    body = _payload()
    body["mapspec"]["layout"]["frames"] = [
        {"id": "f1", "title": "页一", "extent": [100.0, 30.0, 110.0, 40.0]},
        {"id": "f2", "title": "页二", "view": {"center": [116.0, 39.9], "zoom": 10}},
    ]
    body["atlas"] = {"driver": "frames", "includeCover": True, "atlasTitle": "帧册"}
    resp = client.post("/api/v1/export/vector-pdf", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["atlas"] is True
    assert [p["page_id"] for p in data["atlas_pages"]] == ["cover", "f1", "f2"]
