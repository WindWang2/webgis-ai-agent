"""#518 regression — deep_explore owner chain + explorer progress reachability.

The `deep_explore` tool used to call `start_exploration(query, context)` with
no session/user, so `register_owner` never ran (S42) and every owner-verified
explorer endpoint (`/explorer/stream|status|abort/{task_id}`) 404'd even for
the initiating user. These tests pin:
  1. the tool forwards the dispatch-injected session_id + resolved owner
     user_id to the orchestrator (trusted context, never LLM args),
  2. `start_exploration(..., user_id=...)` registers ownership and
     `verify_owner` gates correctly,
  3. the HTTP stream endpoint rejects non-owners (404) and serves owners.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.task_queue import TaskQueueService


@pytest.fixture(autouse=True)
def _clean_task_owners():
    TaskQueueService._task_owners.clear()
    yield
    TaskQueueService._task_owners.clear()


@pytest.mark.asyncio
async def test_deep_explore_forwards_session_and_owner_user_id():
    """#518: deep_explore 把 registry 注入的 session_id 与解析出的归属
    user_id 传给 start_exploration（可信上下文 —— session_id 由调度入口
    注入、覆盖 LLM 参数，user_id 由 DB 按该 session 解析，LLM 无法伪造）。"""
    from app.tools.explorer_tools import register_explorer_tools
    from app.tools.registry import ToolRegistry

    fake_orch = MagicMock()
    fake_orch.start_exploration = AsyncMock(return_value="exp-sess-1-123")
    registry = ToolRegistry()
    with patch("app.tools.explorer_tools.ExplorerOrchestrator", return_value=fake_orch):
        register_explorer_tools(registry)
    with patch(
        "app.tools.explorer_tools._resolve_session_owner_user_id",
        AsyncMock(return_value="owner-u1"),
    ):
        result = await registry.dispatch(
            "deep_explore", {"query": "海淀区学校"}, session_id="sess-1"
        )

    assert result["type"] == "explorer_task"
    assert result["task_id"] == "exp-sess-1-123"
    fake_orch.start_exploration.assert_awaited_once()
    kwargs = fake_orch.start_exploration.await_args.kwargs
    assert kwargs["session_id"] == "sess-1"
    assert kwargs["user_id"] == "owner-u1"


@pytest.mark.asyncio
async def test_start_exploration_registers_owner_and_verify_owner_gates():
    """#518/S42: start_exploration 带 user_id 时 register_owner 生效，
    verify_owner 对归属用户放行、对他人拒绝。"""
    from app.services.explorer.orchestrator import ExplorerOrchestrator
    from app.services.explorer.models import SearchContext

    orchestrator = ExplorerOrchestrator()

    mock_result = MagicMock()
    mock_result.id = "final_task_id"
    node = mock_result
    for _ in range(4):
        parent = MagicMock()
        parent.parent = None
        node.parent = parent
        node = parent
    node.id = "first_task_id"

    with patch("app.services.explorer.orchestrator.chain") as mock_chain:
        mock_chain.return_value.apply_async.return_value = mock_result
        task_id = await orchestrator.start_exploration(
            query="海淀区学校",
            context=SearchContext(query="海淀区学校"),
            user_id="u1",
        )

    assert task_id == "final_task_id"
    assert TaskQueueService.verify_owner(task_id, "u1") is True
    assert TaskQueueService.verify_owner(task_id, "u2") is False
    assert TaskQueueService.verify_owner("other-task", "u1") is False


@pytest.mark.asyncio
async def test_start_exploration_without_user_skips_owner_registration():
    """#518: 匿名会话（user_id ""）不注册归属 —— verify_owner 恒 False，
    与 S42「未注册即 404」语义一致。"""
    from app.services.explorer.orchestrator import ExplorerOrchestrator
    from app.services.explorer.models import SearchContext

    orchestrator = ExplorerOrchestrator()
    mock_result = MagicMock()
    mock_result.id = "anon_final"
    node = mock_result
    for _ in range(4):
        parent = MagicMock()
        parent.parent = None
        node.parent = parent
        node = parent
    node.id = "anon_first"

    with patch("app.services.explorer.orchestrator.chain") as mock_chain:
        mock_chain.return_value.apply_async.return_value = mock_result
        task_id = await orchestrator.start_exploration(
            query="q",
            context=SearchContext(query="q"),
        )
    assert TaskQueueService.verify_owner(task_id, "u1") is False


@pytest.mark.asyncio
async def test_explorer_stream_owner_gating_http():
    """#518/S42: /explorer/stream/{task_id} 对非归属用户 404，对归属用户 200
    （verify_owner 门在生成器启动前执行）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes import explorer as _explorer_mod
    from app.core.auth import get_current_user

    TaskQueueService.register_owner("exp-task-1", "owner-u1")

    app = FastAPI()
    app.include_router(_explorer_mod.router, prefix="/api/v1")
    # 让 stream_progress 立即收敛到终态（SUCCESS → 发一条 final 事件后退出）
    with patch.object(
        _explorer_mod.orchestrator,
        "get_task_status",
        AsyncMock(
            return_value={
                "task_id": "exp-task-1",
                "status": "SUCCESS",
                "stage": "validate",
                "progress": 100,
                "result": {"meta": {}},
            }
        ),
    ):
        app.dependency_overrides[get_current_user] = lambda: {"user_id": "owner-u1"}
        with TestClient(app) as client:
            resp = client.get("/api/v1/explorer/stream/exp-task-1")
            assert resp.status_code == 200
            assert "explorer_progress" in resp.text

            # 他人会话/无归属 → 404（不泄漏任务存在性）
            app.dependency_overrides[get_current_user] = lambda: {"user_id": "intruder"}
            resp2 = client.get("/api/v1/explorer/stream/exp-task-1")
            assert resp2.status_code == 404
