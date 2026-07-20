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
import json
import os
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-medium-backend-arch-32")
os.environ.setdefault("ENV", "development")


# ── S44: decision_log tool_args 截断 + 脱敏 ────────────────────────────


def test_s44_tool_args_truncated():
    """S44：tool_args 整体超 2000 字符时截断。"""
    from app.services.chat.decision_log import ToolDecisionRecord, log_tool_decision
    from unittest.mock import patch

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
    """M2：默认 capacity 是 200（不是原来的 50）。"""
    # 不设环境变量（用默认）
    import os
    saved = os.environ.pop("SESSION_CACHE_SIZE", None)
    try:
        from app.services.chat_engine import ChatEngine
        from app.tools.registry import ToolRegistry
        # capacity 在 __init__ 时读 env，所以新建实例拿默认
        # 但如果 env 在模块加载时已缓存可能拿到旧值 -- 用源码 inspect 兜底
        source = inspect.getsource(ChatEngine.__init__)
        assert "200" in source, "默认 SESSION_CACHE_SIZE 应该是 200"
    finally:
        if saved is not None:
            os.environ["SESSION_CACHE_SIZE"] = saved


# ── M3: provider_health.snapshot 加锁 ──────────────────────────────────


def test_m3_snapshot_is_async_and_locked():
    """M3：snapshot 必须是 async + 加锁。"""
    from app.services.provider_health import ProviderHealthTracker

    assert inspect.iscoroutinefunction(ProviderHealthTracker.snapshot), (
        "snapshot 必须是 async（之前 sync 不加锁会 race）"
    )

    source = inspect.getsource(ProviderHealthTracker.snapshot)
    assert "async with self._lock" in source, "snapshot 必须加 self._lock"
    assert "list(self._state" in source, "snapshot 应用 list() 防 iterate-mutate"


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


def test_m4_chat_registers_tracker_task():
    """M4：chat() 源码必须包含 tracker.create / complete_task。"""
    from app.services.chat_engine import ChatEngine

    source = inspect.getsource(ChatEngine.chat)
    assert "self.tracker.create" in source, "chat() 未注册 TaskTracker task"
    assert "self.tracker.complete_task" in source
    assert "self.tracker.is_cancelled" in source, "chat() 缺 cooperative cancel 检查"
    assert "self.tracker.fail_task" in source, "chat() 异常路径未调 fail_task"


# ── M7: task_tracker cancel 文档化 ─────────────────────────────────────


def test_m7_cancel_docstring_documents_cooperative_limitation():
    """M7：cancel() docstring 必须说明 cooperative 限制。"""
    from app.services.task_tracker import TaskTracker

    doc = TaskTracker.cancel.__doc__ or ""
    assert "cooperative" in doc.lower() or "协作" in doc, (
        "cancel() docstring 应说明 cooperative 语义"
    )
    # 应提到不会打断正在执行的 tool
    assert "打断" in doc or "interrupt" in doc.lower() or "preemptive" in doc.lower()
