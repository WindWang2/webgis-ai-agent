"""RateLimitMiddleware 的 429 必须走统一错误信封（ADR-0138 / #1641）。

路由层 HTTPException 与幂等中间件的 502/503 都经 ``unified_error_envelope``
得 ``{code, success, message, data}``；中间件层限流 429 曾直回 ``{"detail"}``
—— 同一错误面两套形状，且无 ``category`` 字段不被 AcceptLanguageMiddleware
本地化（英文 Locale 用户看到中文原文）。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import RateLimitMiddleware


def _deny_all_limiter(monkeypatch):
    """把限流器换成恒拒的假件（打满限流的确定性手段）。"""
    import app.main as main_mod

    class _DenyAll:
        async def is_allowed(self, key, max_requests, window_seconds):
            return False

    async def _get_rate_limiter():
        return _DenyAll()

    monkeypatch.setattr(main_mod, "get_rate_limiter", _get_rate_limiter)


def _echo_app():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, max_requests=1, window_seconds=60)

    @app.post("/echo")
    async def echo():
        return {"ok": True}

    return TestClient(app)


def test_rate_limit_429_uses_unified_envelope(monkeypatch):
    """打满限流：429 体为 {code:"RATE_LIMITED", success:false, message, data}。"""
    _deny_all_limiter(monkeypatch)
    resp = _echo_app().post("/echo")
    assert resp.status_code == 429
    payload = resp.json()
    assert payload["code"] == "RATE_LIMITED"
    assert payload["success"] is False
    assert payload["message"] == "请求过于频繁，请稍后再试"
    assert payload["data"] is None
    assert "detail" not in payload, "中间件层 429 不得再走旧 detail 体"


def test_rate_limit_429_honors_legacy_envelope_switch(monkeypatch):
    """灰度开关 X-Error-Envelope: detail 对中间件层 429 生效（与路由层一致）。"""
    _deny_all_limiter(monkeypatch)
    resp = _echo_app().post("/echo", headers={"X-Error-Envelope": "detail"})
    assert resp.status_code == 429
    assert resp.json() == {"detail": "请求过于频繁，请稍后再试"}
