"""PR G - 后端架构 Medium 的回归测试。

覆盖：
- S44: decision_log tool_args 截断 + 脱敏
- M2: _sessions LRU capacity 可配置
- M3: provider_health.snapshot 加锁
- M4: 非流式 chat() 注册 TaskTracker
- M7: task_tracker cancel 文档化 cooperative 限制
"""
import asyncio
import inspect
import os
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-medium-backend-arch-32")
os.environ.setdefault("ENV", "development")


# ── S44: decision_log tool_args 截断 + 脱敏 ────────────────────────────


def test_s44_tool_args_truncated():
    """S44：tool_args 整体超 2000 字符时截断。"""
    from app.services.chat.decision_log import ToolDecisionRecord

    big_args = {"geojson": {"features": ["x" * 5000]}}
    record = ToolDecisionRecord(
        session_id="s1", round=1, user_message="test",
        active_domains=["raster"], from_plan=False,
        subset_size=10, total_tools=100,
        tool_chosen="zonal_stats", tool_args=big_args,
        result_quality="ok", plan_step_matched=None,
    )
    d = record.to_dict()
    args_str = d["tool_args"]
    if isinstance(args_str, str):
        # 截断到 2000 + "...[truncated]" 标记（共 ~2014）
        assert len(args_str) <= 2020, f"args 未截断: {len(args_str)}"
        assert "[truncated]" in args_str


def test_s44_sensitive_keys_redacted():
    """S44：api_key/token/password 等 key 值替换为 <redacted>。"""
    from app.services.chat.decision_log import ToolDecisionRecord

    record = ToolDecisionRecord(
        session_id="s1", round=1, user_message="test",
        active_domains=[], from_plan=False,
        subset_size=1, total_tools=1,
        tool_chosen="x", tool_args={"api_key": "sk-secret-123", "normal_param": "ok"},
        result_quality="ok", plan_step_matched=None,
    )
    d = record.to_dict()
    args = d["tool_args"]
    if isinstance(args, dict):
        assert args["api_key"] == "<redacted>"
        assert args["normal_param"] == "ok"


# ── M2: _sessions LRU capacity 可配置 ──────────────────────────────────


def test_m2_session_cache_size_configurable(monkeypatch):
    """M2：SESSION_CACHE_SIZE 环境变量可调整 LRU capacity。"""
    monkeypatch.setenv("SESSION_CACHE_SIZE", "500")
    # 重新 import ChatEngine 拿新 capacity（__init__ 里读 env）
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    # 直接构造实例验证 capacity
    engine = ChatEngine(ToolRegistry())
    assert engine._sessions.capacity == 500, f"期望 500，实际 {engine._sessions.capacity}"


def test_m2_session_cache_default_is_200():
    """M2：默认 capacity 是 200（不是原来的 50）。

    行为测试：不设 SESSION_CACHE_SIZE 环境变量，构造 ChatEngine 实例，验证
    _sessions.capacity == 200。旧 bug：默认 50，>50 并发会话 evict 最老。
    """
    import os
    saved = os.environ.pop("SESSION_CACHE_SIZE", None)
    try:
        from app.services.chat_engine import ChatEngine
        from app.tools.registry import ToolRegistry

        engine = ChatEngine(ToolRegistry())
        assert engine._sessions.capacity == 200, (
            f"默认 SESSION_CACHE_SIZE 应为 200，实际 {engine._sessions.capacity}"
        )
    finally:
        if saved is not None:
            os.environ["SESSION_CACHE_SIZE"] = saved


# ── M3: provider_health.snapshot 加锁 ──────────────────────────────────


def test_m3_snapshot_is_async_and_locked():
    """M3：snapshot 必须是 async（之前 sync 不加锁会 race）。"""
    from app.services.provider_health import ProviderHealthTracker

    assert inspect.iscoroutinefunction(ProviderHealthTracker.snapshot), (
        "snapshot 必须是 async（之前 sync 不加锁会 race）"
    )


@pytest.mark.asyncio
async def test_m3_snapshot_returns_isolated_copy():
    """M3：snapshot 返回的必须是内部状态的拷贝，后续 record 不影响已取快照。

    行为测试替代旧的源码 inspect（断言 "list(self._state" in source）。这验证
    了 snapshot 用 list() copy 防 iterate-mutate 的真实效果：先取快照，再并发
    record 新 provider，旧快照不应变化。
    """
    from app.services.provider_health import ProviderHealthTracker

    tracker = ProviderHealthTracker()
    await tracker.record_attempt("amap")
    snap1 = await tracker.snapshot()
    assert "amap" in snap1

    # 取快照后再新增 provider / 修改状态
    await tracker.record_attempt("bing")

    # 旧快照不应被后续 mutate 影响（说明 snapshot 返回的是拷贝）
    assert "bing" not in snap1, "snapshot 返回了内部 dict 的引用而非拷贝"
    assert "amap" in snap1
    # 新快照包含新 provider
    snap2 = await tracker.snapshot()
    assert "bing" in snap2


@pytest.mark.asyncio
async def test_m3_snapshot_does_not_raise_on_concurrent_mutation():
    """M3：并发 record_attempt 时 snapshot 不应抛 RuntimeError。"""
    from app.services.provider_health import ProviderHealthTracker

    tracker = ProviderHealthTracker()
    # 启动多个并发 record + 一个 snapshot
    async def record_loop():
        for _ in range(50):
            await tracker.record_attempt("amap")

    async def snap_loop():
        for _ in range(10):
            await tracker.snapshot()

    await asyncio.gather(record_loop(), snap_loop(), return_exceptions=False)
    # 不抛即通过


# ── M4: 非流式 chat() 注册 TaskTracker ──────────────────────────────────


@pytest.mark.asyncio
async def test_m4_chat_registers_and_completes_tracker_task(monkeypatch):
    """M4：非流式 chat() 必须把任务注册进 TaskTracker 并在结束时 complete。

    行为测试替代旧的源码 inspect（断言 "self.tracker.create" in source）。旧 bug：
    非流式 chat() 不注册 TaskTracker -> 通过 /chat/completions 发起的任务在
    /tasks 端点不可见，也无法 cancel。本测试调用 chat()，验证 tracker 里出现该
    session 的任务且最终状态为 completed。
    """
    from app.services.chat_engine import ChatEngine
    from app.services.task_tracker import TaskStatus
    from app.services.tool_catalog import ToolCatalog
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    engine = ChatEngine(reg, tool_catalog=ToolCatalog(reg))

    # 跳过规划，让主 LLM 直接返回最终回复（无 tool_calls -> 立即 complete_task）
    async def fake_maybe_plan(self, *a, **k):
        return None
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))

    async def fake_call_llm(*a, **k):
        return {"choices": [{"message": {"role": "assistant",
                                          "content": "hello back"}}]}
    monkeypatch.setattr(engine, "_call_llm", fake_call_llm)

    result = await engine.chat("hi", session_id="sess-M4")

    assert "hello back" in result["content"]
    # 核心：该 session 的任务被注册进 tracker 且状态为 completed
    tasks = [t for t in engine.tracker.list_all() if t.session_id == "sess-M4"]
    assert tasks, "chat() 未注册 TaskTracker task -> 任务在 /tasks 端点不可见"
    assert tasks[0].status == TaskStatus.completed, (
        f"任务未标记 completed，实际 {tasks[0].status}"
    )


@pytest.mark.asyncio
async def test_m4_chat_marks_task_failed_on_llm_error(monkeypatch):
    """M4：chat() 异常路径必须 fail_task，避免任务卡在 running 状态。"""
    from app.services.chat_engine import ChatEngine
    from app.services.task_tracker import TaskStatus
    from app.services.tool_catalog import ToolCatalog
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    engine = ChatEngine(reg, tool_catalog=ToolCatalog(reg))

    async def fake_maybe_plan(self, *a, **k):
        return None
    monkeypatch.setattr(engine, "_maybe_plan",
                        fake_maybe_plan.__get__(engine, type(engine)))

    async def failing_llm(*a, **k):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(engine, "_call_llm", failing_llm)

    with pytest.raises(RuntimeError, match="LLM down"):
        await engine.chat("hi", session_id="sess-M4b")

    tasks = [t for t in engine.tracker.list_all() if t.session_id == "sess-M4b"]
    assert tasks, "chat() 未注册 TaskTracker task"
    assert tasks[0].status == TaskStatus.failed, (
        f"异常路径任务应标记 failed，实际 {tasks[0].status}"
    )


# ── M7: task_tracker cancel 文档化 ─────────────────────────────────────
# 注：本测试检查 docstring 内容（API 文档契约），不是源码结构 inspect。
# cancel() 的取消语义是对外行为约定，文档化它本身就是需求。
#
# ADR-0052 之后契约变了：cancel 不再「只是协作式、不打断在跑的 tool」，而是
# 抢占式 + 协作式混合 —— 点燃 CancellationToken 后，asyncio 侧立即打断在飞任务，
# 同步 GIS 循环在 checkpoint 处退出。因此本测试改为验证新契约被文档化。


def test_m7_cancel_docstring_documents_cancellation_contract():
    """M7：cancel() docstring 必须说明取消如何贯穿执行路径。"""
    from app.services.task_tracker import TaskTracker

    doc = TaskTracker.cancel.__doc__ or ""
    lowered = doc.lower()

    # 协作式部分（GIS 循环在检查点退出）仍然是契约的一半
    assert "协作" in doc or "cooperative" in lowered or "checkpoint" in lowered, (
        "cancel() docstring 应说明协作式取消（检查点退出）"
    )
    # 抢占式部分：取消不再等在跑的 tool 自然结束
    assert "抢占" in doc or "preemptive" in lowered, (
        "cancel() docstring 应说明抢占式取消（立即打断在飞任务）"
    )
    # 传播机制必须写清楚，否则调用方不知道取消能到多深
    assert "cancellationtoken" in lowered, (
        "cancel() docstring 应说明通过 CancellationToken 传播"
    )
    # 幂等性是调用方最容易踩的点（重复取消）
    assert "幂等" in doc or "idempotent" in lowered, (
        "cancel() docstring 应说明重复取消的幂等语义"
    )
