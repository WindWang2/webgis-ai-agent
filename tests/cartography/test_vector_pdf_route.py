"""V6（ADR-0120 W8）/export/vector-pdf 路由测试。

最小 FastAPI app + auth 覆写；验证鉴权、结构化错误、真实 PDF 产出。
"""
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")
pytest.importorskip("weasyprint", reason="WeasyPrint not installed")

pytestmark = pytest.mark.cartography

from fastapi import FastAPI  # noqa: E402

from app.api.routes.map import router as map_router  # noqa: E402
from app.core.auth import get_current_user  # noqa: E402


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(map_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "vec-pdf-user"}
    return fastapi_testclient.TestClient(app)


def _payload():
    return {
        "mapspec": {
            "version": "1.1",
            "sources": {
                "g": {
                    "type": "geojson",
                    "inlineData": {
                        "type": "FeatureCollection",
                        "features": [
                            {
                                "type": "Feature",
                                "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
                                "properties": {"name": "站点A"},
                            },
                        ],
                    },
                },
            },
            "layers": [
                {"id": "l", "source": "g", "type": "circle", "paint": {"circle-radius": 5}},
            ],
            "layout": {"components": [{"id": "t", "type": "title", "options": {"text": "路由级矢量 PDF"}}]},
        },
        "title": "路由测试",
    }


def test_vector_pdf_route_renders_pdf(client, tmp_path, monkeypatch):
    import os

    monkeypatch.setattr("app.api.routes.map.EXPORT_DIR", str(tmp_path))
    resp = client.post("/api/v1/export/vector-pdf", json=_payload())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["vector"] is True
    assert data["pages"] >= 1
    assert os.path.isfile(os.path.join(str(tmp_path), data["filename"]))
    with open(os.path.join(str(tmp_path), data["filename"]), "rb") as f:
        assert f.read(5) == b"%PDF-"


def test_vector_pdf_route_requires_auth(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.routes.map.EXPORT_DIR", str(tmp_path))
    from fastapi import FastAPI as _F

    bare = _F()
    bare.include_router(map_router, prefix="/api/v1")
    bare_client = fastapi_testclient.TestClient(bare)
    resp = bare_client.post("/api/v1/export/vector-pdf", json=_payload())
    assert resp.status_code in (401, 403)


def test_vector_pdf_route_rejects_unhydrated_ref_sources(client, tmp_path, monkeypatch):
    """R1-M5：ref 载体矢量源未水合 → 400 typed 拒绝（不渲染空白出版页）。"""
    monkeypatch.setattr("app.api.routes.map.EXPORT_DIR", str(tmp_path))
    payload = _payload()
    payload["mapspec"]["sources"]["g"] = {"type": "geojson", "ref": "ref:session/abc"}
    resp = client.post("/api/v1/export/vector-pdf", json=payload)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "mapspec_ref_sources_unhydrated"
    assert "g" in resp.json()["detail"]["message"]


def test_vector_pdf_route_rejects_forward_version(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.routes.map.EXPORT_DIR", str(tmp_path))
    payload = _payload()
    payload["mapspec"]["version"] = "9.9"
    resp = client.post("/api/v1/export/vector-pdf", json=payload)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "mapspec_forward_version"
