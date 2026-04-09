"""Task API 测试

Run with: python -m pytest tests/test_task_api.py -v
"""
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
import importlib.util
import sys
import os

# 使用项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Load chat module directly to get engine instance
chat_path = os.path.join(PROJECT_ROOT, "app", "api", "routes", "chat.py")
_spec = importlib.util.spec_from_file_location(
    "app.api.routes.chat",
    chat_path,
    submodule_search_locations=[]
)
_chat_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_chat_mod)
router = _chat_mod.router

# 获取 engine 实例
_engine = _chat_mod.engine

# 加载 task 模块
task_path = os.path.join(PROJECT_ROOT, "app", "api", "routes", "task.py")
_task_spec = importlib.util.spec_from_file_location(
    "app.api.routes.task",
    task_path,
    submodule_search_locations=[]
)
_task_mod = importlib.util.module_from_spec(_task_spec)
_task_spec.loader.exec_module(_task_mod)

# 关键：用测试的 engine 替换 task 模块中的 engine
_task_mod.engine = _engine

task_router = _task_mod.router


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(task_router, prefix="/api/v1")
    return app


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
    """测试列出任务列表"""
    _seed_tracker()
    resp = await client.get("/api/v1/tasks")
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
    """测试取消不存在的任务"""
    resp = await client.delete("/api/v1/tasks/task-nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cancelled"] is False