"""PR B - 内存泄漏修复的回归测试。

覆盖：
- M9: clear_session 清 layer_schema_cache
- M1: _session_locks 上限保护
- M10: cache_hit_var miss 时重置为 False
- S46: _periodic_session_cleanup 后台任务存在
"""
import inspect
import os
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-medium-memory-leaks-32")
os.environ.setdefault("ENV", "development")


# ── M9: clear_session 清 layer_schema_cache ─────────────────────────────


@pytest.mark.asyncio
async def test_m9_clear_session_clears_layer_schema_cache(monkeypatch):
    """M9：ChatEngine.clear_session 必须清掉该 session 的 layer_schema_cache。

    行为测试：往模块级 _layer_schema_cache 塞入两条记录（目标 session + 另一
    session），调用 clear_session（DB 删除返回 True），验证目标 session 的缓存
    被清掉、另一 session 的缓存保留。旧 bug：clear_session 不清缓存，清空后重建
    同 session_id 会读到旧 schema，跨 session 泄漏。
    """
    from app.services.chat_engine import ChatEngine
    from app.services.chat.context import layer_schema
    from app.tools.registry import ToolRegistry

    # 预置缓存：sess-M9a（待清除）+ sess-other（应保留）
    layer_schema._layer_schema_cache[("sess-M9a", "ref-1")] = {"geom": "Point"}
    layer_schema._layer_schema_cache[("sess-other", "ref-2")] = {"geom": "Polygon"}
    assert ("sess-M9a", "ref-1") in layer_schema._layer_schema_cache

    engine = ChatEngine(ToolRegistry())

    # mock DB 删除成功（clear_session 只在 deleted=True 时清缓存）
    from unittest.mock import AsyncMock, MagicMock
    fake_db = MagicMock()
    fake_history = MagicMock()
    fake_history.delete_session = AsyncMock(return_value=True)
    import contextlib

    @contextlib.asynccontextmanager
    async def fake_db_session():
        yield fake_db

    monkeypatch.setattr("app.services.chat.execution_engine.async_db_session", fake_db_session)
    monkeypatch.setattr("app.services.chat.execution_engine.AsyncHistoryService", lambda db: fake_history)
    # clear_session 还会调 session_data_manager.clear_session / planner.clear_plan
    monkeypatch.setattr("app.services.chat.execution_engine.session_data_manager",
                        MagicMock(clear_session=AsyncMock()))

    deleted = await engine.clear_session("sess-M9a")
    assert deleted is True

    # 核心：目标 session 的 schema 缓存被清掉
    assert ("sess-M9a", "ref-1") not in layer_schema._layer_schema_cache, (
        "clear_session 未清 layer_schema_cache -> 旧 schema 跨 session 泄漏"
    )
    # 其他 session 的缓存保留（不能误清）
    assert ("sess-other", "ref-2") in layer_schema._layer_schema_cache
    # 清理测试残留
    layer_schema._layer_schema_cache.clear()


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


@pytest.mark.asyncio
async def test_m1_session_locks_bounded_under_many_sessions(monkeypatch):
    """M1：_session_locks 超过 _MAX_LOCKS 时必须有 evict，不能无界增长。

    行为测试：把 _MAX_LOCKS 调小，创建超过上限数量的 session，验证 _session_locks
    的容量被限制在 _MAX_LOCKS 附近（旧 bug：被遗弃 session 的 Lock 永久泄漏，
    _session_locks 无界增长）。
    """
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    engine = ChatEngine(ToolRegistry())
    # 用一个小的上限加速测试（生产默认 200）
    monkeypatch.setattr(engine, "_MAX_LOCKS", 8, raising=False)
    # _get_or_create_session 慢路径会 _load_session_from_db；mock 成空消息列表
    from unittest.mock import AsyncMock
    monkeypatch.setattr(engine, "_load_session_from_db", AsyncMock(return_value=[]))

    # 创建远超 _MAX_LOCKS 的 session（每个都会在 _session_locks 建一个 Lock）
    for i in range(40):
        await engine._get_or_create_session(f"sess-leak-{i}")

    # 核心断言：_session_locks 有上限保护，不能线性涨到 40
    assert len(engine._session_locks) <= engine._MAX_LOCKS + engine._MAX_LOCKS // 4 + 1, (
        f"_session_locks 无界增长：{len(engine._session_locks)} 个 lock（上限 "
        f"{engine._MAX_LOCKS}）-> 被遗弃 session 的 Lock 永久泄漏"
    )


# ── M10: cache_hit_var miss 重置 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_m10_cache_hit_var_set_false_on_async_cache_miss(monkeypatch):
    """M10：cached_tool 的 async wrapper 在 cache miss 时必须 set(False)。

    行为测试：装饰一个 async 工具，先 set(True) 模拟"上一轮命中残留"，再调用
    工具触发 miss 路径，验证 cache_hit_var 在外层读到的值是 False。旧 bug：miss
    时未显式 set(False)，ContextVar 保留前一次的 True -> registry timing 误报。
    """
    from app.lib import tool_cache

    @tool_cache.cached_tool(ttl=60)
    async def probe_tool(x: int) -> int:
        return x * 2

    # 让 make_cache_key 返回稳定 key，get_cached 返回 None（强制 miss）
    monkeypatch.setattr(tool_cache, "make_cache_key", lambda name, kw: f"key:{name}")
    monkeypatch.setattr(tool_cache, "get_cached", lambda key: None)
    monkeypatch.setattr(tool_cache, "set_cached", lambda key, val, ttl: None)
    monkeypatch.setattr(tool_cache, "_acquire_lock", lambda key, token, ttl: True)
    monkeypatch.setattr(tool_cache, "_release_lock", lambda key, token: None)

    # 先污染 ContextVar：模拟上一轮命中残留的 True
    token = tool_cache.cache_hit_var.set(True)
    try:
        await probe_tool(x=5)
        # 外层（depth==0）miss 路径 set(False)，且不 reset（留给 registry 读）
        assert tool_cache.cache_hit_var.get() is False, (
            "cache miss 时 cache_hit_var 未被 set(False) -> 残留上一轮 True，timing 误报"
        )
    finally:
        tool_cache.cache_hit_var.reset(token)


def test_m10_cache_hit_var_set_true_on_sync_cache_hit(monkeypatch):
    """M10：cached_tool 的 sync wrapper 在 cache hit 时必须 set(True)（对照测试）。

    验证缓存包装器的 ContextVar 契约：命中=set(True)，未命中=set(False)。
    """
    from app.lib import tool_cache

    @tool_cache.cached_tool(ttl=60)
    def sync_probe(x: int) -> int:
        return x + 1

    monkeypatch.setattr(tool_cache, "make_cache_key", lambda name, kw: f"key:{name}")
    monkeypatch.setattr(tool_cache, "get_cached", lambda key: {"cached": True})
    monkeypatch.setattr(tool_cache, "set_cached", lambda key, val, ttl: None)

    token = tool_cache.cache_hit_var.set(False)
    try:
        sync_probe(x=1)
        assert tool_cache.cache_hit_var.get() is True, "cache hit 时 cache_hit_var 应为 True"
    finally:
        tool_cache.cache_hit_var.reset(token)


# ── S46: _periodic_session_cleanup 后台任务 ──────────────────────────────


def test_s46_periodic_cleanup_function_exists():
    """S46：main.py 必须定义 _periodic_session_cleanup 函数。"""
    from app import main as main_module

    assert hasattr(main_module, "_periodic_session_cleanup"), (
        "main.py 缺 _periodic_session_cleanup 函数 -> cleanup_idle_sessions 仍是死代码"
    )


@pytest.mark.asyncio
async def test_s46_periodic_cleanup_invokes_cleanup_idle_sessions(monkeypatch):
    """S46：_periodic_session_cleanup 必须实际调用 cleanup_idle_sessions。

    行为测试：跑一次 cleanup（用极短 sleep），验证它真的调了
    session_data_manager.cleanup_idle_sessions —— 这是"死代码变活"的核心行为。
    """
    from app import main as main_module
    from unittest.mock import MagicMock

    called = {"n": 0}

    async def fake_cleanup():
        called["n"] += 1

    fake_manager = MagicMock()
    fake_manager.cleanup_idle_sessions = fake_cleanup
    # _periodic_session_cleanup 内部 from ... import session_data_manager
    monkeypatch.setattr("app.services.session_data.session_data_manager", fake_manager)

    # 让 sleep 只跑一次就退出：第一次 sleep 立即返回，第二次抛 CancelledError 终止循环
    import asyncio
    call_count = {"n": 0}

    async def fake_sleep(seconds):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await main_module._periodic_session_cleanup(interval_seconds=0)

    assert called["n"] >= 1, "_periodic_session_cleanup 未调用 cleanup_idle_sessions"


def test_s46_lifespan_starts_cleanup_task():
    """S46：lifespan 必须启动 cleanup 后台任务。

    结构性守卫：完整驱动 lifespan 需要真实 init_db / ToolRegistry / DB schema，
    mock 链路过深且与 S46 的"后台任务启停"无关。此处用源码 inspect 验证 lifespan
    用 create_task 启动 _periodic_session_cleanup，并在退出时 cancel —— 这是验证
    后台任务生命周期的最直接方式。
    """
    from app import main as main_module

    source = inspect.getsource(main_module.lifespan)
    assert "_periodic_session_cleanup" in source, "lifespan 未启动 cleanup 任务"
    assert "create_task" in source, "lifespan 未用 create_task 启动后台任务"
    assert "cleanup_task.cancel()" in source, "lifespan 未在退出时 cancel cleanup 任务"
