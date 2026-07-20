"""审计 T5: /config/* 路由的集成测试。

config.py 之前没有任何路由级测试（只有 Settings 模块测试）。
这些端点是 admin-only（PR #93 的 require_admin），是 RCE 等价入口
（skills/upload 写盘 + importlib.exec_module），必须有测试覆盖。
"""
import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-config-routes-32-chars")
os.environ.setdefault("ENV", "development")


@pytest_asyncio.fixture
async def app_and_client():
    """加载 config 路由 + chat 路由（config 依赖 chat.get_engine/get_registry）。"""
    from app.api.routes import config as config_routes
    from app.api.routes import chat as chat_routes
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    # 给 chat 模块注入 engine + registry（config 路由会用）
    registry = ToolRegistry()
    chat_routes.registry = registry
    chat_routes.engine = ChatEngine(registry)

    app = FastAPI()
    app.include_router(config_routes.router, prefix="/api/v1")
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield app, c
    finally:
        chat_routes.engine = None
        chat_routes.registry = None


@pytest.mark.asyncio
async def test_config_llm_requires_admin_token(app_and_client):
    """S29: /config/llm 必须 admin token，无 token -> 401。"""
    _, client = app_and_client
    resp = await client.get("/api/v1/config/llm")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_config_llm_rejects_viewer(app_and_client):
    """S29: viewer token -> 403。"""
    from app.core.auth import create_access_token
    viewer_token = create_access_token({"sub": "v1", "username": "v", "role": "viewer"})
    _, client = app_and_client
    resp = await client.get(
        "/api/v1/config/llm",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_config_llm_accepts_admin(app_and_client):
    """S29: admin token -> 200。"""
    from app.core.auth import create_access_token
    admin_token = create_access_token({"sub": "a1", "username": "a", "role": "admin"})
    _, client = app_and_client
    resp = await client.get(
        "/api/v1/config/llm",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_config_skills_list_requires_admin(app_and_client):
    """S29: /config/skills 也需要 admin。"""
    _, client = app_and_client
    resp = await client.get("/api/v1/config/skills")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_config_skills_upload_rejects_non_admin(app_and_client):
    """S29: skills/upload 是 RCE 等价，必须 admin。"""
    from app.core.auth import create_access_token
    viewer_token = create_access_token({"sub": "v1", "username": "v", "role": "viewer"})
    _, client = app_and_client

    # 即使带了文件，viewer 也应被 403 拒绝
    resp = await client.post(
        "/api/v1/config/skills/upload",
        headers={"Authorization": f"Bearer {viewer_token}"},
        files={"file": ("test.py", b"print('hello')", "text/python")},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_config_skills_refresh_requires_admin(app_and_client):
    """S29: /config/skills/refresh 也需要 admin。"""
    _, client = app_and_client
    resp = await client.post("/api/v1/config/skills/refresh")
    assert resp.status_code == 401
