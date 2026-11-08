"""V5 渲染诊断 sidecar（ADR-0118 D6）契约测试。

POST /api/v1/export 接受 render_diagnostics JSON Form 字段 → 权威词表校验
→ 持久化 `{filename}.diagnostics.json`；GET /api/v1/export/diagnostics/
{filename} 按 download 同源 fail-closed 所有权语义读取。
"""
import json
import os
import sys

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.api.routes import map as _mod
from app.core.auth import get_current_user_with_version, get_current_user_optional_with_version
from pathlib import Path

# review-fix-rereview-20261108：固定共享 /var/tmp 目录 + autouse 清盘在 xdist
# 并行 worker 间互相删文件（同 test_map_export_download_auth 的既有 flaky
# 模式）。改为每测试独立 tmp_path；_TEST_EXPORT_DIR 由 fixture 指向该目录，
# 用例内 `lambda: Path(_TEST_EXPORT_DIR)` 按全局名在调用时解析。
_TEST_EXPORT_DIR = ""

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
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules[__name__], "_TEST_EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.services.export_paths.exports_root", lambda: Path(_TEST_EXPORT_DIR)
    )
    _mod._EXPORT_OWNERS.clear()
    yield
    _mod._EXPORT_OWNERS.clear()


def _auth(client, user):
    client._transport.app.dependency_overrides[get_current_user_with_version] = lambda: user
    client._transport.app.dependency_overrides[get_current_user_optional_with_version] = lambda: user


async def _upload(client, diagnostics: str | None):
    from io import BytesIO
    from PIL import Image
    png = BytesIO()
    Image.new("RGB", (1, 1)).save(png, format="PNG")
    files = {"file": ("map.png", png.getvalue(), "image/png")}
    data = {"render_diagnostics": diagnostics} if diagnostics is not None else {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.services.export_paths.exports_root", lambda: Path(_TEST_EXPORT_DIR))
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
        mp.setattr("app.services.export_paths.exports_root", lambda: Path(_TEST_EXPORT_DIR))
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
        mp.setattr("app.services.export_paths.exports_root", lambda: Path(_TEST_EXPORT_DIR))
        resp = await client.get("/api/v1/export/diagnostics/whatever.png")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_anonymous_session_owner_reads_own_diagnostics(client, monkeypatch):
    """B-5（review-fix-rereview-20261108）：匿名会话导出主体是 session:<sid>。

    诊断端点此前硬性 Bearer + user_id 比对 —— 匿名导出者（下载可走签名
    URL）永远 401/403，读不到自己的渲染诊断 sidecar。修复后与
    _export_principal 同源：session 主体凭 X-Session-Token 读取。
    """
    from fastapi import HTTPException

    _anon = {"user_id": "anonymous", "role": "anonymous"}
    _auth(client, _anon)

    async def _fake_verify(db, session_id, user_id=None, owner_token=None):
        if session_id != "sess-diag" or owner_token != "tok-diag":
            raise HTTPException(status_code=404, detail="Session not found")
        return object()

    monkeypatch.setattr(
        "app.services.auth_history_bridge.verify_session_owner", _fake_verify
    )

    from io import BytesIO
    from PIL import Image

    png = BytesIO()
    Image.new("RGB", (1, 1)).save(png, format="PNG")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.services.export_paths.exports_root", lambda: Path(_TEST_EXPORT_DIR))
        resp = await client.post(
            "/api/v1/export",
            files={"file": ("map.png", png.getvalue(), "image/png")},
            data={
                "render_diagnostics": json.dumps([{"code": "label_truncated", "detail": "layer=a"}]),
                "session_id": "sess-diag",
            },
            headers={"X-Session-Token": "tok-diag"},
        )
    assert resp.status_code == 200, resp.text
    filename = resp.json()["filename"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.services.export_paths.exports_root", lambda: Path(_TEST_EXPORT_DIR))
        # 匿名导出者携自己的 owner_token → 200
        ok = await client.get(
            f"/api/v1/export/diagnostics/{filename}",
            headers={"X-Session-Token": "tok-diag"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["diagnostics"][0]["code"] == "label_truncated"

        # 匿名 + 错误 token → fail-closed 404（与所有权守卫同款，不泄漏存在性）
        bad = await client.get(
            f"/api/v1/export/diagnostics/{filename}",
            headers={"X-Session-Token": "wrong-token"},
        )
        assert bad.status_code == 404

        # 认证他者 → 403
        _auth(client, _intruder_user)
        other = await client.get(f"/api/v1/export/diagnostics/{filename}")
        assert other.status_code == 403


@pytest.mark.asyncio
async def test_sidecar_traversal_resists(client):
    """路径注入面：filename 经 basename 归一，派生 sidecar 名不可逃逸。"""
    _auth(client, _owner_user)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.services.export_paths.exports_root", lambda: Path(_TEST_EXPORT_DIR))
        resp = await client.get("/api/v1/export/diagnostics/..%2F..%2Fsecret.png")
    assert resp.status_code in (401, 403, 404)


# ────────────────────────────────────────────────────────────────────────
# review-r2：载荷有界性 —— message/detail/标识符全部有界入库（DoS 面）
# ────────────────────────────────────────────────────────────────────────


def test_normalize_caps_detail_and_message():
    """超长 detail：detail 与经模板插值的 message 都必须 ≤ 上界 + 模板本身。

    此前 detail 先插值进 message 再截断，message 无界 —— 10MB detail 会
    原样落 sidecar 并经读取端点回显。
    """
    from app.lib.cartography.render_diagnostics import (
        MAX_DETAIL_CHARS,
        normalize_render_diagnostics,
    )

    huge = "x" * (MAX_DETAIL_CHARS * 100)
    accepted, rejected = normalize_render_diagnostics(
        [{"code": "label_truncated", "detail": huge}]
    )
    assert accepted and not rejected
    entry = accepted[0]
    assert len(entry["detail"]) == MAX_DETAIL_CHARS
    assert len(entry["message"]) <= len(entry["detail"]) + 200


def test_normalize_caps_identifier_fields():
    """layer_id / component_id 同样有界入库（此前无长度上限）。"""
    from app.lib.cartography.render_diagnostics import (
        MAX_ID_CHARS,
        normalize_render_diagnostics,
    )

    accepted, _ = normalize_render_diagnostics(
        [
            {
                "code": "features_truncated",
                "detail": "50000",
                "layer_id": "L" * (MAX_ID_CHARS * 10),
                "component_id": "C" * (MAX_ID_CHARS * 10),
            }
        ]
    )
    assert accepted
    assert len(accepted[0]["layer_id"]) == MAX_ID_CHARS
    assert len(accepted[0]["component_id"]) == MAX_ID_CHARS


def test_diagnostic_factory_caps_detail_in_message():
    """编译器路径（diagnostic()）同一口径：message 与 detail 同界。"""
    from app.lib.cartography.render_diagnostics import (
        MAX_DETAIL_CHARS,
        diagnostic,
    )

    d = diagnostic("label_truncated", detail="长" * (MAX_DETAIL_CHARS * 50))
    assert d is not None
    assert len(d.detail) == MAX_DETAIL_CHARS
    assert len(d.message) <= len(d.detail) + 200


@pytest.mark.asyncio
async def test_upload_oversized_detail_stays_bounded_on_disk(client):
    """端到端：超大（200KB，低于 Starlette 1MB form part 上限）detail 上传
    → sidecar 中 detail/message 均按 200 字符契约有界落盘。"""
    from app.lib.cartography.render_diagnostics import MAX_DETAIL_CHARS

    _auth(client, _owner_user)
    payload = json.dumps(
        [{"code": "label_truncated", "detail": "x" * (200 * 1024)}]
    )
    resp = await _upload(client, payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["render_diagnostics"]["accepted"] == 1
    sidecar = os.path.join(_TEST_EXPORT_DIR, body["filename"] + ".diagnostics.json")
    with open(sidecar, encoding="utf-8") as fh:
        stored = json.load(fh)
    entry = stored["diagnostics"][0]
    assert len(entry["detail"]) == MAX_DETAIL_CHARS
    assert len(entry["message"]) <= len(entry["detail"]) + 200
