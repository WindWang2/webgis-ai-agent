"""F14 — 出版基础契约（WP5）：page profile / CJK 字体内嵌 / canvas DPI 入档。

覆盖：
1. ``FramePageSize.profile`` 纸型预设解析（profile > 裸宽高 > A4 landscape 缺省）；
   非法 profile 经 schema invalid_fields 如实披露；
2. 无系统 CJK 字体时 vendored Noto Sans SC 子集经 @font-face data-URI 内嵌
   （进程缓存；probe=True 时零内嵌）；pdf_cjk_font_embedded 发射器登记；
3. canvas /export 的 dpi Form 字段钳制入 lineage（此前 canvas 链永缺 dpi）。
"""
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import map as _mod
from app.core.auth import get_current_user_with_version
from app.lib.cartography.mapspec_schema import parse_mapspec
from app.services import publication_export as pe

_USER = {"user_id": "wp5-owner"}


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
def _exports_tmp(tmp_path, monkeypatch):
    root = tmp_path / "exports"
    root.mkdir()
    monkeypatch.setattr("app.services.export_paths.exports_root", lambda: root)
    _mod._EXPORT_OWNERS.clear()
    yield
    _mod._EXPORT_OWNERS.clear()


def _auth(client):
    client._transport.app.dependency_overrides[get_current_user_with_version] = (
        lambda: _USER
    )


# ── 1) page profile ──────────────────────────────────────────────────────


def test_frame_geometry_profile_presets():
    assert pe._frame_geometry({"pageSize": {"width": 100, "height": 100,
                                            "profile": "a3_landscape"}}) == (
        420.0, 297.0, None)
    assert pe._frame_geometry({"pageSize": {"width": 100, "height": 100,
                                            "profile": "a4_portrait"}}) == (
        210.0, 297.0, None)
    # 无 profile → 裸宽高
    assert pe._frame_geometry({"pageSize": {"width": 200, "height": 150}}) == (
        200.0, 150.0, None)
    # 无 pageSize → A4 landscape 缺省
    assert pe._frame_geometry(None) == (297.0, 210.0, None)


def test_invalid_page_profile_disclosed():
    payload = {
        "version": "1.1",
        "sources": {"s1": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": []}}},
        "layers": [],
        "layout": {"frames": [{"pageSize": {"width": 200, "height": 150,
                                            "profile": "wallsize_giant"}}]},
    }
    result = parse_mapspec(payload)
    invalid = [d for d in result.invalid_fields if "profile" in str(d)]
    assert invalid, "非法 profile 必须进 invalid_fields 如实披露"


def test_page_profile_within_publication_budget():
    from app.services.publication_export import MAX_PAGE_MM, PAGE_PROFILES

    for name, (w, h) in PAGE_PROFILES.items():
        assert max(w, h) <= MAX_PAGE_MM, name


# ── 2) CJK font embedding ────────────────────────────────────────────────


def test_font_face_embedded_when_no_system_cjk(monkeypatch):
    monkeypatch.setattr(pe, "_probe_cjk_font", lambda: False)
    monkeypatch.setattr(pe, "_FONT_FACE_CACHE", None)
    css = pe._cjk_font_face_css()
    assert css.startswith("@font-face")
    assert "data:font/ttf;base64," in css
    # 进程缓存生效：第二次取同对象
    assert pe._cjk_font_face_css() == css


def test_font_face_empty_when_system_cjk_present(monkeypatch):
    monkeypatch.setattr(pe, "_probe_cjk_font", lambda: True)
    monkeypatch.setattr(pe, "_FONT_FACE_CACHE", None)
    assert pe._cjk_font_face_css() == ""


def test_font_face_empty_when_vendored_font_missing(monkeypatch):
    """vendored 字体读取失败 → 空串（回退 pdf_font_fallback 披露路径）。"""
    monkeypatch.setattr(pe, "_probe_cjk_font", lambda: False)
    monkeypatch.setattr(pe, "_FONT_FACE_CACHE", None)
    from pathlib import Path

    def _boom(self, *a, **kw):
        raise OSError("no font")

    monkeypatch.setattr(Path, "read_bytes", _boom)
    assert pe._cjk_font_face_css() == ""


def test_pdf_cjk_font_embedded_emitter_registered():
    from app.lib.cartography.render_diagnostics import EMITTER_REGISTRY

    assert "app.services.publication_export" in EMITTER_REGISTRY[
        "pdf_cjk_font_embedded"]


def test_publication_pdf_emits_font_embedded_when_no_system_cjk(monkeypatch):
    pytest.importorskip("weasyprint")
    monkeypatch.setattr(pe, "_probe_cjk_font", lambda: False)
    monkeypatch.setattr(pe, "_FONT_FACE_CACHE", None)
    spec = {
        "version": "1.1",
        "sources": {"s1": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection",
            "features": [{"type": "Feature",
                          "geometry": {"type": "Point",
                                       "coordinates": [10.0, 40.0]},
                          "properties": {}}]}}},
        "layers": [{"id": "l1", "source": "s1", "type": "circle",
                    "paint": {"circle-color": "#2563eb", "circle-radius": 4},
                    "visible": True}],
    }
    result = pe.render_publication_pdf(spec, title="font")
    codes = {d["code"] for d in result.diagnostics}
    assert "pdf_cjk_font_embedded" in codes
    assert result.font_cjk is False  # probe 如实披露（未发现系统 CJK）


# ── 3) canvas /export dpi → lineage ─────────────────────────────────────


def test_clamp_canvas_dpi():
    assert _mod._clamp_canvas_dpi(None) == 0
    # review P2-7：显式 0 = 未提供 → 0（未记录），不得记成下限 72
    assert _mod._clamp_canvas_dpi(0) == 0
    assert _mod._clamp_canvas_dpi(96) == 96
    assert _mod._clamp_canvas_dpi(9999) == 600


@pytest.mark.asyncio
async def test_canvas_export_dpi_reaches_lineage(client, monkeypatch):
    _auth(client)
    seen: dict = {}

    async def _fake_record(*args, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(_mod, "_record_lineage", _fake_record)
    resp = await client.post(
        "/api/v1/export",
        files={"file": ("map.png", b"png-bytes", "image/png")},
        data={"dpi": "150"},
    )
    assert resp.status_code == 200
    assert seen["target_dpi"] == 150

    seen.clear()
    resp2 = await client.post(
        "/api/v1/export",
        files={"file": ("map.png", b"png-bytes", "image/png")},
        data={},
    )
    assert resp2.status_code == 200
    assert seen["target_dpi"] == 0  # 未带 dpi = 未记录（诚实缺省）


# ── 矢量链 page profile 路由级（已有 frames 契约回归）────────────────────


def test_multi_frame_pages_use_profiles():
    pytest.importorskip("weasyprint")
    payload = {
        "version": "1.1",
        "sources": {"s1": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection",
            "features": [{"type": "Feature",
                          "geometry": {"type": "Point",
                                       "coordinates": [10.0, 40.0]},
                          "properties": {}}]}}},
        "layers": [{"id": "l1", "source": "s1", "type": "circle",
                    "paint": {"circle-color": "#2563eb", "circle-radius": 4},
                    "visible": True}],
        "layout": {"frames": [
            {"id": "f1", "pageSize": {"width": 1, "height": 1,
                                      "profile": "a4_portrait"}},
            {"id": "f2", "pageSize": {"width": 300, "height": 300}},
        ]},
    }
    # 只验几何解析（不真渲 PDF —— weasyprint 在 CI 缺席时 skip 由上层路由测覆盖）
    from app.services.publication_export import _frame_geometry

    assert _frame_geometry(payload["layout"]["frames"][0]) == (
        210.0, 297.0, None)
    assert _frame_geometry(payload["layout"]["frames"][1]) == (300.0, 300.0, None)
    # JSON 载荷口径（diagnostics sidecar 内容断言同款纪律）
    assert json.dumps(payload["layout"]["frames"][0]["pageSize"]) == (
        '{"width": 1, "height": 1, "profile": "a4_portrait"}')
