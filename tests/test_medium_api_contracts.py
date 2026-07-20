"""PR I - API 契约修复的回归测试。

覆盖：
- A5: /chat/sessions 加 pagination (limit/offset)
- A4: 关键端点显式 response_model 声明（response_model=None 标注可变返回）
"""
import os
import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
import importlib.util

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-api-contracts-32-chars")
os.environ.setdefault("ENV", "development")


def _load_chat_module():
    spec = importlib.util.spec_from_file_location(
        "app.api.routes.chat",
        os.path.join(os.path.dirname(__file__), "..", "app", "api", "routes", "chat.py"),
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def app():
    mod = _load_chat_module()
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── A5: pagination ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a5_list_sessions_accepts_limit_offset(client, monkeypatch):
    """A5：/chat/sessions 必须接受 limit + offset query params。"""
    # mock list_sessions 返回空（测契约不测 DB）
    async def fake_list(self, limit=50, user_id=None):
        return []
    from app.services.history_service_async import AsyncHistoryService
    monkeypatch.setattr(AsyncHistoryService, "list_sessions", fake_list)
    # patch async_db_session
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def fake_db():
        yield None
    import app.api.routes.chat as chat_mod
    monkeypatch.setattr(chat_mod, "async_db_session", fake_db)

    resp = await client.get("/api/v1/chat/sessions?limit=10&offset=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "limit" in data
    assert "offset" in data
    assert data["limit"] == 10
    assert data["offset"] == 5


@pytest.mark.asyncio
async def test_a5_list_sessions_rejects_invalid_limit(client):
    """A5：limit 超范围应 422。"""
    resp = await client.get("/api/v1/chat/sessions?limit=500")
    assert resp.status_code == 422  # > 200 上限

    resp = await client.get("/api/v1/chat/sessions?limit=0")
    assert resp.status_code == 422  # < 1


# ── A4: response_model 声明 ─────────────────────────────────────────────


def test_a4_execute_tool_has_response_model_none():
    """A4：/tools/execute 应显式声明 response_model=None（可变返回形状）。"""
    mod = _load_chat_module()
    # router 有 prefix=/chat，所以 path 是 /chat/tools/execute
    route = next(
        (r for r in mod.router.routes if getattr(r, "path", "") == "/chat/tools/execute"),
        None,
    )
    assert route is not None, "未找到 /chat/tools/execute route"
    # response_model=None 表示"返回原始 dict，不强制 schema"
    assert route.response_model is None


def test_a4_stream_has_response_model_none():
    """A4：/stream 也应声明 response_model=None（SSE text/event-stream）。"""
    mod = _load_chat_module()
    route = next(
        (r for r in mod.router.routes if getattr(r, "path", "") == "/chat/stream"),
        None,
    )
    assert route is not None
    assert route.response_model is None
