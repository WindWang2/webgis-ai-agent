"""PR B - 内存泄漏修复的回归测试。

覆盖：
- M9: clear_session 清 layer_schema_cache
- M1: _session_locks 上限保护
- M10: cache_hit_var miss 时重置为 False
- S46: _periodic_session_cleanup 后台任务存在
"""
import asyncio
import inspect
import os
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-medium-memory-leaks-32")
os.environ.setdefault("ENV", "development")


# ── M9: clear_session 清 layer_schema_cache ─────────────────────────────


def test_m9_clear_session_calls_clear_layer_schema_cache():
    """M9：ChatEngine.clear_session 必须调 clear_layer_schema_cache。

    用源码 inspect 验证（直接测需要完整 mock 链路，过于脆弱）。
    """
    from app.services.chat_engine import ChatEngine

    source = inspect.getsource(ChatEngine.clear_session)
    assert "clear_layer_schema_cache" in source, (
        "clear_session 未调用 clear_layer_schema_cache -> 旧 schema 跨 session 泄漏"
    )


# ── M1: _session_locks 上限保护 ─────────────────────────────────────────


def test_m1_session_locks_has_max_bound():
    """M1：ChatEngine 必须有 _MAX_LOCKS 上限保护。"""
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    engine = ChatEngine(ToolRegistry())
    assert hasattr(engine, "_MAX_LOCKS"), "ChatEngine 缺 _MAX_LOCKS 属性"
    assert engine._MAX_LOCKS > 0
    # _session_locks 是 dict
    assert isinstance(engine._session_locks, dict)


def test_m1_session_locks_eviction_logic_present():
    """M1：_get_or_create_session 必须有 evict 逻辑。"""
    from app.services.chat_engine import ChatEngine

    source = inspect.getsource(ChatEngine._get_or_create_session)
    assert "_MAX_LOCKS" in source, "缺 _session_locks 上限保护逻辑"
    assert "evict" in source.lower() or "len(self._session_locks)" in source


# ── M10: cache_hit_var miss 重置 ─────────────────────────────────────────


def test_m10_cache_hit_var_resets_on_miss():
    """M10：cached_tool 的 wrapper 在 cache miss 时必须 set(False)。

    源码 inspect 验证（行为测试需要 Redis，CI 环境不稳定；源码检查更可靠）。
    """
    from app.lib import tool_cache

    source = inspect.getsource(tool_cache)
    # async_wrapper 和 sync_wrapper 都应有 cache_hit_var.set(False)
    assert source.count("cache_hit_var.set(False)") >= 2, (
        "cached_tool 的 async/sync wrapper 都应在 miss 路径 set(False)"
    )


# ── S46: _periodic_session_cleanup 后台任务 ──────────────────────────────


def test_s46_periodic_cleanup_function_exists():
    """S46：main.py 必须定义 _periodic_session_cleanup 函数。"""
    from app import main as main_module

    assert hasattr(main_module, "_periodic_session_cleanup"), (
        "main.py 缺 _periodic_session_cleanup 函数 -> cleanup_idle_sessions 仍是死代码"
    )


def test_s46_lifespan_starts_cleanup_task():
    """S46：lifespan 必须启动 cleanup 后台任务。"""
    from app import main as main_module

    source = inspect.getsource(main_module.lifespan)
    assert "_periodic_session_cleanup" in source, "lifespan 未启动 cleanup 任务"
    assert "create_task" in source, "lifespan 未用 create_task 启动后台任务"
    assert "cleanup_task.cancel()" in source, "lifespan 未在退出时 cancel cleanup 任务"
