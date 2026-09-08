"""V5 渲染诊断 sidecar（ADR-0118 D6）契约测试。

POST /api/v1/export 接受 render_diagnostics JSON Form 字段 → 权威词表校验
→ 持久化 `{filename}.diagnostics.json`；GET /api/v1/export/diagnostics/
{filename} 按 download 同源 fail-closed 所有权语义读取。
"""
import json
import os

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.api.routes import map as _mod
from app.core.auth import get_current_user

_TEST_EXPORT_DIR = "/var/tmp/test_exports_diagnostics_sidecar"
os.makedirs(_TEST_EXPORT_DIR, exist_ok=True)

_owner_user = {"user_id": "diag-owner"}
_intruder_user = {"user_id": "diag-intruder"}


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _clean():
    _mod._EXPORT_OWNERS.clear()
    for fn in os.listdir(_TEST_EXPORT_DIR):
        os.remove(os.path.join(_TEST_EXPORT_DIR, fn))
    yield
    _mod._EXPORT_OWNERS.clear()


def _auth(client, user):
    client._transport.app.dependency_overrides[get_current_user] = lambda: user


async def _upload(client, diagnostics: str | None):
    files = {"file": ("map.png", b"png-bytes", "image/png")}
    data = {"render_diagnostics": diagnostics} if diagnostics is not None else {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "EXPORT_DIR", _TEST_EXPORT_DIR)
        return await client.post("/api/v1/export", files=files, data=data)


@pytest.mark.asyncio
async def test_upload_with_diagnostics_persists_sidecar(client):
    _auth(client, _owner_user)
    resp = await _upload(
        client,
        json.dumps([
            {"code": "label_truncated", "detail": "layer=a len=240", "layer_id": "a"},
            {"code": "chart_ref_unavailable", "component_id": "chart-1"},
        ]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["render_diagnostics"]["accepted"] == 2
    assert body["render_diagnostics"]["rejected"] == []
    sidecar = os.path.join(
        _TEST_EXPORT_DIR, f"{body['filename']}.diagnostics.json"
    )
    assert os.path.exists(sidecar)
    payload = json.loads(open(sidecar, encoding="utf-8").read())
    assert [d["code"] for d in payload["diagnostics"]] == [
        "label_truncated", "chart_ref_unavailable",
    ]
    assert payload["diagnostics"][0]["severity"] == "warning"


@pytest.mark.asyncio
async def test_upload_rejects_unknown_code_with_reason(client):
    _auth(client, _owner_user)
    resp = await _upload(
        client,
        json.dumps([{"code": "made_up_code"}, {"code": "label_truncated"}]),
    )
    assert resp.status_code == 200
    out = resp.json()["render_diagnostics"]
    assert out["accepted"] == 1
    assert len(out["rejected"]) == 1
    assert "unknown code" in out["rejected"][0]


@pytest.mark.asyncio
async def test_upload_invalid_json_400(client):
    _auth(client, _owner_user)
    resp = await _upload(client, "{not json")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_no_diagnostics_field_no_sidecar(client):
    _auth(client, _owner_user)
    resp = await _upload(client, None)
    assert resp.status_code == 200
    assert "render_diagnostics" not in resp.json()
    assert not os.path.exists(
        os.path.join(_TEST_EXPORT_DIR, resp.json()["filename"] + ".diagnostics.json")
    )


@pytest.mark.asyncio
async def test_read_sidecar_owner_ok_intruder_403_missing_404(client):
    _auth(client, _owner_user)
    resp = await _upload(client, json.dumps([{"code": "features_truncated", "detail": "2000"}]))
    filename = resp.json()["filename"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "EXPORT_DIR", _TEST_EXPORT_DIR)
        ok = await client.get(f"/api/v1/export/diagnostics/{filename}")
        assert ok.status_code == 200
        assert ok.json()["diagnostics"][0]["code"] == "features_truncated"

        _auth(client, _intruder_user)
        forbidden = await client.get(f"/api/v1/export/diagnostics/{filename}")
        assert forbidden.status_code == 403

        _auth(client, _owner_user)
        missing = await client.get("/api/v1/export/diagnostics/nope_map.png")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_read_requires_bearer(client):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "EXPORT_DIR", _TEST_EXPORT_DIR)
        resp = await client.get("/api/v1/export/diagnostics/whatever.png")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sidecar_traversal_resists(client):
    """路径注入面：filename 经 basename 归一，派生 sidecar 名不可逃逸。"""
    _auth(client, _owner_user)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "EXPORT_DIR", _TEST_EXPORT_DIR)
        resp = await client.get("/api/v1/export/diagnostics/..%2F..%2Fsecret.png")
    assert resp.status_code in (401, 403, 404)
