"""后端 i18n 测试（ADR-0144 / P6）：Accept-Language 协商、回退链、信封本地化。

红线对齐：结构化字段（category/retryable/code）永不本地化；分类学默认短语
是最终回退；i18n 面绝不抛。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.errors import (
    CATEGORY_DEFAULTS,
    ErrorCategory,
    PlatformError,
    localized_user_message,
)
from app.core.i18n import (
    DEFAULT_LOCALE,
    AcceptLanguageMiddleware,
    current_locale,
    normalize_locale,
    parse_accept_language,
    user_message_for,
)
from app.core.exception import global_exception_handler


# ── Accept-Language 解析 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "zh_CN"),
        ("", "zh_CN"),
        ("*", "zh_CN"),
        ("en", "en_US"),
        ("en-US,en;q=0.9", "en_US"),
        ("zh-CN,zh;q=0.9,en;q=0.8", "zh_CN"),
        ("fr-FR,fr;q=0.9,en-US;q=0.8", "en_US"),
        ("ja-JP,ja;q=0.9,en;q=0.3", "en_US"),
        ("zh-TW", "zh_CN"),
        ("garbage;;;,=", "zh_CN"),
        ("en;q=0.1,zh;q=0.9", "zh_CN"),
    ],
)
def test_parse_accept_language(header, expected):
    assert parse_accept_language(header) == expected


def test_normalize_locale():
    assert normalize_locale("en-GB") == "en_US"
    assert normalize_locale("zh") == "zh_CN"
    assert normalize_locale("fr") == DEFAULT_LOCALE


# ── 回退链：locale → zh_CN → 分类学默认 ────────────────────────────────────


def test_user_message_zh_matches_category_defaults():
    """zh catalog 与 CATEGORY_DEFAULTS 一一相等（单一事实源镜像）。"""
    for category, spec in CATEGORY_DEFAULTS.items():
        assert user_message_for(category, "zh_CN") == spec.user_message


def test_user_message_fallback_chain(monkeypatch):
    locale_msg = user_message_for(ErrorCategory.TIMEOUT, "en_US")
    assert locale_msg and "timed out" in locale_msg.lower()

    # 模拟 en catalog 缺键 → 回落 zh_CN
    import app.core.i18n as i18n_mod

    monkeypatch.setitem(i18n_mod._catalog_cache, "en_US", {})
    assert user_message_for(ErrorCategory.TIMEOUT, "en_US") == CATEGORY_DEFAULTS[
        ErrorCategory.TIMEOUT
    ].user_message

    # zh catalog 也缺 → 分类学默认（category_defaults，仍是同一短语）
    monkeypatch.setitem(i18n_mod._catalog_cache, "zh_CN", {})
    assert user_message_for(ErrorCategory.TIMEOUT, "en_US") == CATEGORY_DEFAULTS[
        ErrorCategory.TIMEOUT
    ].user_message


def test_localized_user_message_accepts_classification():
    exc = PlatformError("boom", category=ErrorCategory.CRS)
    cls = type(exc).__mro__  # noqa: F841 — 仅确保导入面
    from app.core.errors import classify_exception

    classification = classify_exception(exc)
    assert localized_user_message(classification, "en_US") == user_message_for(
        ErrorCategory.CRS, "en_US"
    )
    assert localized_user_message(classification, "zh_CN") == CATEGORY_DEFAULTS[
        ErrorCategory.CRS
    ].user_message


def test_contextvar_scoping():
    token = current_locale.set("en_US")
    try:
        assert current_locale.get() == "en_US"
    finally:
        current_locale.reset(token)
    assert current_locale.get() == DEFAULT_LOCALE


# ── 中间件：信封本地化（结构化字段零触碰）─────────────────────────────────


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AcceptLanguageMiddleware)

    @app.exception_handler(Exception)
    async def handler(request, exc):
        return await global_exception_handler(request, exc)

    @app.get("/boom")
    async def boom():
        raise PlatformError("secret internals", category=ErrorCategory.TIMEOUT)

    @app.get("/ok")
    async def ok():
        return {"success": True, "data": 1}

    return app


def test_middleware_localizes_error_envelope(monkeypatch):
    # 生产面（dev 的 detailed message 按既有设计覆盖 message —— 内部诊断优先）
    from app.core.config import settings as app_settings

    monkeypatch.setattr(type(app_settings), "is_production", lambda self: True)
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/boom", headers={"Accept-Language": "en-US"})
    # 既有 handler 语义：PlatformError 不带 status_code → 信封状态 500
    # （http_status_for 的映射属恢复面，不在信封路径）；此处只断言信封字段。
    assert resp.status_code == 500
    data = resp.json()
    assert data["success"] is False
    assert data["category"] == "timeout"  # 结构化字段原样
    assert data["retryable"] is True
    assert "timed out" in data["message"].lower()


def test_middleware_default_locale_keeps_envelope(monkeypatch):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(type(app_settings), "is_production", lambda self: True)
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/boom")
    data = resp.json()
    assert data["message"] == CATEGORY_DEFAULTS[ErrorCategory.TIMEOUT].user_message


def test_middleware_success_response_untouched():
    client = TestClient(_make_app())
    resp = client.get("/ok", headers={"Accept-Language": "en-US"})
    assert resp.json() == {"success": True, "data": 1}


# ── 大错误体缓冲有界（#1641）──────────────────────────────────────────────


def test_middleware_bounds_large_error_body():
    """非默认 Locale 的 4xx 大错误体（>1MB）不整体缓冲：体完整、状态不变。"""
    import app.core.i18n as i18n_mod

    assert i18n_mod.MAX_ERROR_BODY_BYTES >= 1024 * 1024
    app = FastAPI()
    app.add_middleware(AcceptLanguageMiddleware)
    big = {"blob": "x" * (2 * 1024 * 1024)}  # 远超错误信封体积的大 4xx 体

    @app.get("/big-error")
    async def big_error():
        return JSONResponse(big, status_code=400)

    client = TestClient(app)
    resp = client.get("/big-error", headers={"Accept-Language": "en-US"})
    assert resp.status_code == 400
    assert len(resp.content) > i18n_mod.MAX_ERROR_BODY_BYTES
    assert resp.json() == big, "超限错误体必须完整下发，不得截断"


def test_middleware_overflow_path_roundtrips_body(monkeypatch):
    """帽调至 64B 后 >64B 错误体仍完整 round-trip（证伪「截断」式实现）。"""
    import app.core.i18n as i18n_mod

    monkeypatch.setattr(i18n_mod, "MAX_ERROR_BODY_BYTES", 64)
    app = FastAPI()
    app.add_middleware(AcceptLanguageMiddleware)
    body = {"errors": [f"e{i}" for i in range(500)]}

    @app.get("/wide-error")
    async def wide_error():
        return JSONResponse(body, status_code=422)

    client = TestClient(app)
    resp = client.get("/wide-error", headers={"Accept-Language": "en-US"})
    assert resp.status_code == 422
    assert resp.json() == body, "前缀 + 原流剩余重组必须保持体完整"
