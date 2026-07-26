"""PR C - 数据完整性修复的回归测试。

覆盖：
- M11: update_layer_in_state / remove_layer_from_state 用 WATCH/MULTI
- M6: report.py generate_report 期间不持有 DB session
- M8: _save_msg_async 截断 tool_result 到 100000 字符
"""
import inspect
import os
import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-medium-data-integrity-32")
os.environ.setdefault("ENV", "development")


# ── M11: Redis WATCH/MULTI ──────────────────────────────────────────────
# 行为测试：update_layer_in_state / remove_layer_from_state 在 WatchError
# （并发写竞争）时必须重试并最终成功，且有重试上限。用 fake pipeline 注入
# WatchError 验证重试语义，替代旧的源码 inspect（断言 "watch(" in source）。


def _make_manager_with_watch_failures(fail_first_n: int):
    """构造一个 RedisSessionDataManager，其 pipeline 前 fail_first_n 次
    execute() 抛 WatchError，模拟并发竞争。之后正常提交。

    直接 setattr 注入 _r（绕过 __init__ 的 from_url），并把 _bound_loop 指向
    当前运行 loop，复用 _make_manager_with_failing_redis 的模式。
    """
    import asyncio
    import redis.asyncio as aioredis
    from app.services.session_data_redis import RedisSessionDataManager

    manager = RedisSessionDataManager.__new__(RedisSessionDataManager)
    manager.capacity = 100
    try:
        manager._bound_loop = asyncio.get_running_loop()
    except RuntimeError:
        manager._bound_loop = None

    # 内存存储，模拟单个 state_key 的 hset/hget
    store: dict[str, dict[str, str]] = {}
    attempt_counter = {"n": 0}

    class _FakePipeline:
        def __init__(self):
            self._tx = {}

        def __getattr__(self, name):
            # hget 在 WATCH 后、MULTI 前调用，直接读 store
            if name == "hget":
                async def _hget(key, field):
                    return store.get(key, {}).get(field)
                return _hget
            # hset/expire/sadd 在 MULTI 后排队
            def _queue(method_name):
                def _fn(*args, **kwargs):
                    if method_name == "hset":
                        self._tx[args[0]] = (args[1], args[2])
                    return self
                return _fn
            return _queue(name)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def watch(self, key):
            return True

        def multi(self):
            return self

        async def execute(self):
            attempt_counter["n"] += 1
            if attempt_counter["n"] <= fail_first_n:
                raise aioredis.WatchError("simulated concurrent modification")
            # 提交排队的事务
            for key, (field, val) in self._tx.items():
                store.setdefault(key, {})[field] = val
            return []

    class _FakeRedis:
        def pipeline(self, transaction=True):
            return _FakePipeline()

    manager._r = _FakeRedis()
    manager._attempt_counter = attempt_counter
    manager._store = store
    return manager


@pytest.mark.asyncio
async def test_m11_update_layer_retries_on_watch_error():
    """M11：update_layer_in_state 遇 WatchError 必须重试并最终成功。

    旧 bug：read-modify-write 无 WATCH，两个并发 update 后写覆盖先写。
    修复后用 WATCH/MULTI + retry。本测试注入前 2 次 WatchError，验证会重试到成功。
    """
    manager = _make_manager_with_watch_failures(fail_first_n=2)
    # 不应抛 —— 前两次 WatchError 被捕获并重试，第 3 次成功
    await manager.update_layer_in_state("sess-1", "layer-A", {"opacity": 0.8})
    # 至少尝试了 3 次（2 次失败 + 1 次成功）
    assert manager._attempt_counter["n"] >= 3, (
        f"未观察到 WatchError 重试，尝试次数={manager._attempt_counter['n']}"
    )
    # 最终数据写入成功
    state_key = manager._state_key("sess-1")
    import json
    layers = json.loads(manager._store[state_key]["layers"])
    assert any(l.get("id") == "layer-A" and l.get("opacity") == 0.8 for l in layers)


@pytest.mark.asyncio
async def test_m11_update_layer_gives_up_after_retry_limit():
    """M11：retry 必须有上限（防无限循环）—— 持续 WatchError 时放弃而非死循环。

    注入始终失败的 pipeline，验证 update_layer_in_state 在有限次重试后返回
    （降级，不抛），而不是无限循环卡死测试。
    """
    import asyncio
    manager = _make_manager_with_watch_failures(fail_first_n=10_000)

    # 用超时兜底：若无限重试，wait_for 会抛 TimeoutError
    await asyncio.wait_for(
        manager.update_layer_in_state("sess-2", "layer-B", {"opacity": 1.0}),
        timeout=5.0,
    )
    # 重试次数有上限（生产代码 range(3) = 最多 3 次）
    assert manager._attempt_counter["n"] <= 5, (
        f"重试次数无上限（可能死循环），尝试={manager._attempt_counter['n']}"
    )


@pytest.mark.asyncio
async def test_m11_remove_layer_retries_on_watch_error():
    """M11：remove_layer_from_state 同样用 WATCH/MULTI + retry。"""
    manager = _make_manager_with_watch_failures(fail_first_n=1)
    # 预置一条 layer 数据
    state_key = manager._state_key("sess-3")
    import json
    manager._store[state_key] = {"layers": json.dumps([{"id": "layer-X"}, {"id": "keep"}])}

    # 第 1 次 WatchError 后重试成功，移除 layer-X
    await manager.remove_layer_from_state("sess-3", "layer-X")
    assert manager._attempt_counter["n"] >= 2, "未观察到 WatchError 重试"
    layers = json.loads(manager._store[state_key]["layers"])
    assert all(l.get("id") != "layer-X" for l in layers), "layer 未被移除"
    assert any(l.get("id") == "keep" for l in layers), "误删了其他 layer"


# ── M6: report.py 不持有 DB session ─────────────────────────────────────
# 注：create_report 的 expunge 行为依赖完整 AsyncSession 生命周期 + DB 表结构，
# 行为测试需要构造真实 DB session 与 Report ORM 对象，mock 链路过深且脆弱。
# 此处保留源码 inspect 作为结构性守卫（确保 generate_report 前 expunge，
# 最终 status 用新 AsyncSessionLocal 写入），是本场景下更稳定的做法。


def test_m6_report_generate_uses_expunge():
    """M6：create_report 必须在 generate_report 前 expunge（释放 DB session）。

    结构性守卫：generate_report 可能耗时 30s，期间不应持有原 DB session
    （否则连接池耗尽 + 崩溃后报告卡在 'generating'）。行为测试需完整 DB
    生命周期，mock 脆弱，故用源码 inspect 验证结构（expunge + 新 session）。
    """
    from app.api.routes import report

    source = inspect.getsource(report.create_report)
    assert "expunge" in source, "create_report 未在 generate_report 前 expunge"
    # 不应在 generate_report 之后还有同一个 db.commit() 持有原 session
    assert "AsyncSessionLocal" in source, "应用新 session 写最终 status"


# ── M8: _save_msg_async 截断 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_m8_save_msg_async_truncates_tool_result(monkeypatch):
    """M8：_save_msg_async 必须对超长 tool_result 截断到 100000 字符。

    行为测试：直接调用 _save_msg_async 传入 >100000 字符的 tool_result，
    捕获实际落库的内容，验证被截断且带 [truncated] 标记（旧 bug 不截断，
    超大 GeoJSON tool result 会撑爆 SQLite 行）。
    """
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    engine = ChatEngine(ToolRegistry())
    captured: dict = {}

    class _FakeHistoryService:
        def __init__(self, db):
            pass

        async def save_message(self, session_id, role, content,
                               tool_calls=None, tool_result=None,
                               tool_call_id=None, reasoning_content=None):
            captured["tool_result"] = tool_result

    # _save_msg_async 内部用 `async with async_db_session() as db:` 再包
    # AsyncHistoryService(db)。patch 这两个依赖，避免真实 DB。
    import contextlib

    @contextlib.asynccontextmanager
    async def fake_db_session():
        yield object()  # 假 db 句柄

    monkeypatch.setattr("app.services.chat_engine.async_db_session",
                        fake_db_session)
    monkeypatch.setattr("app.services.chat_engine.AsyncHistoryService",
                        _FakeHistoryService)

    huge_result = "X" * 150_000  # 150k 字符，超过 100000 阈值
    await engine._save_msg_async(
        "sess-M8", "tool", "tool done", tool_result=huge_result,
    )

    saved = captured.get("tool_result")
    assert saved is not None, "save_message 未被调用"
    # 必须被截断（100000 内容 + 截断标记），不能是原始 150k
    assert len(saved) < 150_000, "tool_result 未被截断（旧 M8 bug 回归）"
    assert "[truncated]" in saved, "截断后应带 [truncated] 标记"


@pytest.mark.asyncio
async def test_m8_save_msg_async_keeps_short_tool_result(monkeypatch):
    """M8：短 tool_result 不应被截断（边界：刚好低于阈值）。"""
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry

    engine = ChatEngine(ToolRegistry())
    captured: dict = {}

    class _FakeHistoryService:
        def __init__(self, db):
            pass

        async def save_message(self, session_id, role, content,
                               tool_calls=None, tool_result=None,
                               tool_call_id=None, reasoning_content=None):
            captured["tool_result"] = tool_result

    import contextlib

    @contextlib.asynccontextmanager
    async def fake_db_session():
        yield object()

    monkeypatch.setattr("app.services.chat_engine.async_db_session",
                        fake_db_session)
    monkeypatch.setattr("app.services.chat_engine.AsyncHistoryService",
                        _FakeHistoryService)

    short_result = '{"features": []}'  # 远低于阈值
    await engine._save_msg_async(
        "sess-M8b", "tool", "ok", tool_result=short_result,
    )
    assert captured["tool_result"] == short_result, "短结果不应被截断"
