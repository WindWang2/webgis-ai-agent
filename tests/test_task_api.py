"""Task API 测试

Run with: python -m pytest tests/test_task_api.py -v
"""
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI, Depends

from app.services.chat_engine import ChatEngine
from app.tools.registry import ToolRegistry
from app.api.routes import chat as chat_mod
from app.api.routes.task import router as task_router
from app.api.routes.layer import _verify_session_owner
from app.core.auth import get_current_user

# Create a real ChatEngine instance for tests
_engine = ChatEngine(ToolRegistry())
router = chat_mod.router

_mock_user = {"user_id": "test-user"}


@pytest.fixture(autouse=True)
def _inject_engine():
    """Ensure chat module engine is set for every test in this module."""
    original = chat_mod.engine
    chat_mod.engine = _engine
    yield
    chat_mod.engine = original


@pytest.fixture
def app(monkeypatch):
    """跨租户守卫 _verify_session_owner 依赖 Conversation.user_id 校验。
    单测不连真 DB，stub 成 always-pass 即可（隔离由 test_cross_tenant_isolation
    单独覆盖）。"""
    async def _noop_verify(session_id, user_id):
        return None
    monkeypatch.setattr("app.api.routes.layer._verify_session_owner", _noop_verify)
    # task.py 通过 from app.api.routes.layer import _verify_session_owner 拷贝名字
    monkeypatch.setattr("app.api.routes.task._verify_session_owner", _noop_verify)

    _app = FastAPI()
    _app.dependency_overrides[get_current_user] = lambda: _mock_user
    _app.include_router(router, prefix="/api/v1")
    _app.include_router(task_router, prefix="/api/v1")
    return _app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _seed_tracker():
    """通过获取的 engine 的 tracker 并注入测试数据"""
    task = _engine.tracker.create("test-session", "查询北京大学")
    step = _engine.tracker.start_step(task.id, "query_osm_poi", {"area": "北京"})
    _engine.tracker.complete_step(task.id, step.id, {"count": 10})
    _engine.tracker.complete_task(task.id)
    return task


@pytest.mark.asyncio
async def test_get_task(client):
    """测试获取任务状态"""
    task = _seed_tracker()
    resp = await client.get(f"/api/v1/tasks/{task.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == task.id
    assert data["session_id"] == "test-session"
    assert data["original_request"] == "查询北京大学"
    assert data["status"] == "completed"
    assert len(data["steps"]) == 1
    assert data["steps"][0]["tool"] == "query_osm_poi"
    assert data["steps"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_get_task_not_found(client):
    """测试获取不存在的任务"""
    resp = await client.get("/api/v1/tasks/task-nonexistent")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_tasks(client):
    """测试列出任务列表 —— 必须带 session_id（审计 S33：防止跨租户泄漏）"""
    _seed_tracker()
    # 缺 session_id：422（必填）
    resp = await client.get("/api/v1/tasks")
    assert resp.status_code == 422
    # 带 session_id：返回该 session 下的任务
    resp = await client.get("/api/v1/tasks?session_id=test-session")
    assert resp.status_code == 200
    data = resp.json()
    assert "tasks" in data
    assert len(data["tasks"]) >= 1


@pytest.mark.asyncio
async def test_list_tasks_filtered(client):
    """测试按 session 过滤任务"""
    task = _engine.tracker.create("filter-session", "过滤测试")
    _engine.tracker.complete_task(task.id)

    resp = await client.get("/api/v1/tasks?session_id=filter-session")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["session_id"] == "filter-session"


@pytest.mark.asyncio
async def test_cancel_task(client):
    """测试取消任务"""
    task = _engine.tracker.create("cancel-session", "取消测试")
    resp = await client.delete(f"/api/v1/tasks/{task.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cancelled"] is True

    # 验证任务状态已更新
    task_info = _engine.tracker.get(task.id)
    assert task_info.status.value == "cancelled"


@pytest.mark.asyncio
async def test_cancel_task_not_found(client):
    """测试取消不存在的任务（跨租户守卫先于 cancel 命中）"""
    resp = await client.delete("/api/v1/tasks/task-nonexistent")
    assert resp.status_code == 404
