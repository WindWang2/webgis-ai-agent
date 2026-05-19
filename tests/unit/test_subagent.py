"""Subagent dispatcher + spawn_subagent 工具的单元测试。

避免真打 LLM —— 我们把 ChatEngine.chat 整体 mock 掉，只断言 dispatcher 的契约：
- 工具子集筛选行为（tier1 always、tier2 域命中、tier3 拦截、元工具黑名单）
- 共享 session_id 导致 refs 在父侧立即可见
- 工具入口校验（空 task、非法 max_rounds）
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.tools.registry import ToolRegistry
from app.services.session_data import session_data_manager
from app.services.subagent import (
    SubagentDispatcher,
    SubagentResult,
    select_tools_for_subagent,
)
from app.tools.subagent import register_subagent_tools


# ─── 测试用 registry：覆盖各 tier 与一个元工具 ──────────────


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register("buffer_analysis", "buf", func=lambda **_: {}, tier=1)
    r.register("layer_alias", "al", func=lambda **_: {}, tier=1)
    r.register("compute_ndvi", "ndvi", func=lambda **_: {}, tier=2, domains=["raster"])
    r.register("plan_route", "route", func=lambda **_: {}, tier=2, domains=["network"])
    r.register("hotspot_analysis", "ho", func=lambda **_: {}, tier=2, domains=["statistics"])
    r.register("what_if_simulate", "whatif", func=lambda **_: {}, tier=3, domains=["what_if"])
    r.register("create_new_skill", "skill", func=lambda **_: {}, tier=3, domains=["meta"])
    # 元工具：永远不应给 subagent
    r.register("propose_plan", "pp", func=lambda **_: {}, tier=1)
    r.register("execute_plan", "ep", func=lambda **_: {}, tier=1)
    r.register("spawn_subagent", "sa", func=lambda **_: {}, tier=2, domains=["meta"])
    register_subagent_tools(r)  # 注册真实的 spawn_subagent（会覆盖 stub）
    return r


def _names(schemas):
    return {s["function"]["name"] for s in schemas}


# ─── 工具子集筛选 ────────────────────────────────────────────────


def test_subset_includes_tier1_only_when_no_domains(registry):
    schemas = select_tools_for_subagent(registry)
    names = _names(schemas)
    assert "buffer_analysis" in names
    assert "layer_alias" in names
    assert "compute_ndvi" not in names  # tier 2，无域命中
    assert "spawn_subagent" not in names  # 元工具黑名单
    assert "propose_plan" not in names
    assert "execute_plan" not in names


def test_subset_adds_tier2_when_domain_matches(registry):
    schemas = select_tools_for_subagent(registry, domains=["raster"])
    names = _names(schemas)
    assert "compute_ndvi" in names
    assert "plan_route" not in names  # network 未在 domains


def test_subset_excludes_tier3_by_default(registry):
    schemas = select_tools_for_subagent(registry, domains=["what_if", "meta"])
    names = _names(schemas)
    assert "what_if_simulate" not in names
    assert "create_new_skill" not in names


def test_subset_can_include_tier3_explicitly(registry):
    schemas = select_tools_for_subagent(
        registry, domains=["what_if"], exclude_tier3=False
    )
    assert "what_if_simulate" in _names(schemas)


def test_subset_extra_tools_force_in(registry):
    schemas = select_tools_for_subagent(
        registry, domains=[], extra_tools=["compute_ndvi"]
    )
    assert "compute_ndvi" in _names(schemas)


def test_subset_never_includes_meta_tools(registry):
    """即便用户显式 extra_tools=['spawn_subagent']，也要被黑名单拦截。"""
    schemas = select_tools_for_subagent(
        registry,
        extra_tools=["spawn_subagent", "propose_plan", "execute_plan"],
    )
    names = _names(schemas)
    assert "spawn_subagent" not in names
    assert "propose_plan" not in names
    assert "execute_plan" not in names


# ─── 派遣器：mock chat 不打真实 LLM ──────────────────────────────


@pytest.mark.asyncio
async def test_dispatcher_invokes_chat_and_returns_summary(registry):
    parent_sid = "test-parent-session-1"
    mock_chat = AsyncMock(return_value={
        "session_id": parent_sid,
        "content": "子任务完成。生成 ref:data-abc。",
        "reasoning": "",
    })
    with patch("app.services.chat_engine.ChatEngine.chat", mock_chat):
        dispatcher = SubagentDispatcher(registry, parent_session_id=parent_sid)
        result = await dispatcher.run(
            task="测试任务",
            domains=["raster"],
        )
    assert result.success is True
    assert "子任务完成" in result.summary
    # 子代理使用父 session_id（设计：refs 自动在父可见）
    mock_chat.assert_called_once()
    call_args = mock_chat.call_args
    assert call_args.kwargs.get("session_id") == parent_sid


@pytest.mark.asyncio
async def test_dispatcher_captures_new_refs(registry):
    """子任务产生的 ref 在父 session 立即可见。"""
    parent_sid = "test-parent-session-2"
    # 模拟：chat 内部假装做了一次 session_data_manager.store
    async def fake_chat(self, message, session_id=None, **kwargs):
        ref = session_data_manager.store(session_id, {"fake": "data"}, prefix="data")
        return {"session_id": session_id, "content": f"已生成 {ref}", "reasoning": ""}

    with patch("app.services.chat_engine.ChatEngine.chat", fake_chat):
        dispatcher = SubagentDispatcher(registry, parent_session_id=parent_sid)
        result = await dispatcher.run(task="生成数据")

    assert result.success is True
    assert len(result.refs) == 1
    assert result.refs[0].startswith("ref:data-")
    # 父侧也能查到
    fetched = session_data_manager.get(parent_sid, result.refs[0])
    assert fetched == {"fake": "data"}


@pytest.mark.asyncio
async def test_dispatcher_propagates_failure(registry):
    parent_sid = "test-parent-session-3"
    mock_chat = AsyncMock(side_effect=RuntimeError("LLM 崩溃"))
    with patch("app.services.chat_engine.ChatEngine.chat", mock_chat):
        dispatcher = SubagentDispatcher(registry, parent_session_id=parent_sid)
        result = await dispatcher.run(task="x")
    assert result.success is False
    assert "LLM 崩溃" in (result.error or "")


def test_dispatcher_rejects_empty_parent_session(registry):
    with pytest.raises(ValueError):
        SubagentDispatcher(registry, parent_session_id="")


# ─── 工具入口：通过 registry.dispatch ─────────────────────────


@pytest.mark.asyncio
async def test_spawn_subagent_requires_session(registry):
    result = await registry.dispatch(
        "spawn_subagent",
        {"task": "x"},
        session_id=None,
    )
    assert result["success"] is False
    assert "session_id" in result["message"]


@pytest.mark.asyncio
async def test_spawn_subagent_rejects_empty_task(registry):
    result = await registry.dispatch(
        "spawn_subagent",
        {"task": "   "},
        session_id="some-session",
    )
    assert result["success"] is False
    assert "不能为空" in result["message"]


@pytest.mark.asyncio
async def test_spawn_subagent_rejects_out_of_range_rounds(registry):
    for bad in [0, 31, -1]:
        result = await registry.dispatch(
            "spawn_subagent",
            {"task": "x", "max_rounds": bad},
            session_id="some-session",
        )
        assert result["success"] is False
        assert "max_rounds" in result["message"]


@pytest.mark.asyncio
async def test_spawn_subagent_happy_path(registry):
    parent_sid = "test-spawn-happy-1"
    mock_chat = AsyncMock(return_value={
        "session_id": parent_sid,
        "content": "完工。",
        "reasoning": "",
    })
    with patch("app.services.chat_engine.ChatEngine.chat", mock_chat):
        result = await registry.dispatch(
            "spawn_subagent",
            {"task": "做点事", "domains": ["raster"], "max_rounds": 5},
            session_id=parent_sid,
        )
    assert result["success"] is True
    assert result["summary"] == "完工。"
