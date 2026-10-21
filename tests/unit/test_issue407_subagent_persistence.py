"""Issue #407 回归测试：子代理内部 transcript 绝不持久化进父会话 DB 历史。

修复前：SubagentDispatcher 用 parent_session_id 跑子引擎，子代理每条内部
消息（任务 prompt、assistant 消息、tool_call/tool 响应）都经 _save_msg_async
写进父会话 DB 行 —— 重载 / LRU 逐出后父上下文包含完整子代理 transcript。

修复（两层）：
- 子代理引擎（is_subagent_engine=True）的 _save_msg_async 整体抑制持久化；
- 「clearing」标记提升为注册表级（class 级共享）：任一引擎实例 clear_session
  后，其他实例（含 in-flight 子引擎）对该会话的 DB 写同样被抑制。
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register("buffer_analysis", "buf", func=lambda **_: {}, tier=1)
    return r


def _recording_history(saved: list):
    """记录 save_message 的 AsyncHistoryService 替身（load_context 返回空历史）。"""
    from app.services.history_store_protocol import HistoryContext

    class _RecordingHistory:
        def __init__(self, db=None):
            self.db = db

        async def load_context(self, **kwargs):
            return HistoryContext(
                session_id=kwargs.get("session_id", ""),
                owner_token=None,
                user_id=None,
                llm_messages=[],
                raw_conversation=None,
            )

        async def save_message(
            self,
            session_id,
            role,
            content,
            tool_calls=None,
            tool_result=None,
            tool_call_id=None,
            reasoning_content=None,
        ):
            saved.append((session_id, role, content))

    return _RecordingHistory


# ─── 子代理不写父会话 DB ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_subagent_turn_does_not_persist_to_parent_db_history(registry):
    """真实子引擎跑一轮完整工具回合：父会话 DB 历史零写入。"""
    from app.services.subagent import SubagentDispatcher, select_tools_for_subagent

    parent_sid = "parent-sess-407-sub"
    saved: list = []

    dispatcher = SubagentDispatcher(registry, parent_session_id=parent_sid)
    sub_engine = dispatcher._build_sub_engine(
        select_tools_for_subagent(registry), max_rounds=5,
    )
    assert sub_engine.is_subagent_engine is True

    resp1 = {"choices": [{"message": {"content": "调用工具", "tool_calls": [
        {"id": "call_1", "function": {"name": "buffer_analysis", "arguments": '{"radius": 10}'}},
    ]}}]}
    resp2 = {"choices": [{"message": {"content": "子任务完成，生成 ref:data-abc。"}}]}

    with patch.object(sub_engine, "_call_llm", new_callable=AsyncMock, side_effect=[resp1, resp2]):
        with patch(
            "app.services.chat.execution_engine.AsyncHistoryService",
            _recording_history(saved),
        ):
            result = await sub_engine.chat("子任务：批量统计", session_id=parent_sid)

    assert "子任务完成" in result["content"]  # 子代理正常跑完
    assert saved == []  # 子代理内部消息（user/assistant/tool）从不写进父会话 DB


@pytest.mark.asyncio
async def test_subagent_repair_orphaned_tool_calls_does_not_persist(registry):
    """取消/断连路径的孤儿 repair（_repair_orphaned_tool_calls 的 DB 写）同样被抑制。

    内存 repair 照常（消息配对补齐，子引擎自己的上下文保持合法），但绝不写库。
    """

    from app.services.subagent import SubagentDispatcher, select_tools_for_subagent

    parent_sid = "parent-sess-407-repair"
    saved: list = []

    dispatcher = SubagentDispatcher(registry, parent_session_id=parent_sid)
    sub_engine = dispatcher._build_sub_engine(
        select_tools_for_subagent(registry), max_rounds=5,
    )

    messages = [
        {"role": "user", "content": "任务"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_orphan_1", "function": {"name": "buffer_analysis", "arguments": "{}"}},
        ]},
    ]
    with patch(
        "app.services.chat.execution_engine.AsyncHistoryService",
        _recording_history(saved),
    ):
        await sub_engine._repair_orphaned_tool_calls(parent_sid, messages)

    # 内存 repair 照常补齐（子引擎上下文合法），DB 零写入
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == "call_orphan_1"
    assert saved == []


# ─── 子代理重入父会话锁（deep-review 并发/运行时）────────────────


@pytest.mark.asyncio
async def test_subagent_chat_under_held_parent_session_lock(registry, monkeypatch):
    """父 turn 持有 session 锁并绑定后，子引擎 chat() 按 F2 门控跳过争锁。

    装配复刻真实父 turn（app/services/chat/execution_engine.py:1368-1372）：
    父引擎进入 ``async with session_lock(sid)`` 后立刻
    ``legacy_bind_engine_lock(sid, lock)`` 把已持有的锁透传给 kernel
    adapter；子引擎（is_subagent_engine=True）经 _parent_holds_session_lock
    探测到该绑定 → chat() 以 nullcontext 取代自取锁
    （execution_engine.py:1305-1311），不再自阻塞 / 抛 LockContentionError。

    #1669：旧版只持锁不绑定（父锁从未经 legacy_bind_engine_lock 透传），
    F2 门控判 False，子引擎诚实重取同一把非重入锁 → 自锁失败。该「门控
    不生效」装配的断言见
    test_subagent_chat_without_parent_lock_binding_contends。
    """
    import app.services.chat.execution_engine as ee_mod
    from app.services import distributed_lock as dl
    from app.services.distributed_lock import session_lock as real_session_lock
    from app.services.harness_kernel.legacy_adapter import (
        bind_engine_lock,
        unbind_engine_lock,
    )

    from app.services.subagent import SubagentDispatcher, select_tools_for_subagent

    parent_sid = "parent-sess-lock-reentry"
    saved: list = []
    acquire_calls: list = []

    dispatcher = SubagentDispatcher(registry, parent_session_id=parent_sid)
    sub_engine = dispatcher._build_sub_engine(
        select_tools_for_subagent(registry), max_rounds=5,
    )
    assert sub_engine.is_subagent_engine is True

    # 缩短 acquire 预算：门控若失效，子引擎会在真实锁上争用，快速暴露而非等 30s。
    def _fast_session_lock(sid, **kwargs):
        kwargs.setdefault("acquire_timeout_s", 0.5)
        return real_session_lock(sid, **kwargs)

    # 结构性断言（#1647 同纪律）：度量锁获取次数而非墙钟 —— 无 Redis 开发机
    # 上 session_data_manager 的连接超时会拖慢场景，与「子引擎是否等锁」无关。
    _orig_acquire = dl._InProcessLock.acquire

    async def _counting_acquire(self, timeout_s=None):
        acquire_calls.append(timeout_s)
        return await _orig_acquire(self, timeout_s=timeout_s)

    monkeypatch.setattr(dl._InProcessLock, "acquire", _counting_acquire)

    resp = {"choices": [{"message": {"content": "子任务完成，生成 ref:data-1。"}}]}

    with patch.object(ee_mod, "session_lock", _fast_session_lock), \
         patch.object(
             sub_engine, "_call_llm", new_callable=AsyncMock, return_value=resp,
         ), patch(
            "app.services.chat.execution_engine.AsyncHistoryService",
            _recording_history(saved),
        ):
        # 父 turn 语义：工具波内全程持有同一 session 的真实锁，并把该锁绑进
        # 环境（legacy_bind_engine_lock），子引擎据此跳过争锁。
        async with real_session_lock(parent_sid) as parent_lock:
            assert len(acquire_calls) == 1, "父锁自身应经 fallback acquire 获取"
            tok = bind_engine_lock(parent_sid, parent_lock)
            try:
                result = await sub_engine.chat("子任务：批量统计", session_id=parent_sid)
            finally:
                unbind_engine_lock(tok)

    assert "子任务完成" in result["content"]
    assert saved == []  # #407 写抑制语义不变
    # 整个场景只有父锁那一次 fallback acquire —— 子引擎跑在父已持有的锁下，
    # 没有（也不必）再取会话锁（F2 门控生效）。
    assert len(acquire_calls) == 1, (
        f"sub-engine acquired the session lock itself ({len(acquire_calls)} calls) "
        "— it must run under the parent's held lock (F2 gate)"
    )


@pytest.mark.asyncio
async def test_subagent_chat_stream_under_held_parent_session_lock(registry, monkeypatch):
    """父 turn 持有 session 锁并绑定后，子引擎 chat_stream() 按 F2 门控跳过争锁。

    流式对照（chat() 路径见上一用例）：装配同样经 legacy_bind_engine_lock
    透传父锁，子引擎用 NoOpSessionLock 取代 keepalive 轮询
    （execution_engine.py:1993-1998），事件照常产出、不抛
    LockContentionError，#407 写抑制语义不变。

    #1669：旧版只持锁不绑定 → 门控判 False → 子引擎每次 acquire 预算耗尽后
    chat_stream 的 keepalive 循环无条件重试，流永久挂起（被 180s timeout
    中断整轮）。绑定父锁后有界完成；「门控不生效」装配见上一用例。
    """
    import app.services.chat.execution_engine as ee_mod
    from app.services import distributed_lock as dl
    from app.services.distributed_lock import session_lock as real_session_lock
    from app.services.harness_kernel.legacy_adapter import (
        bind_engine_lock,
        unbind_engine_lock,
    )

    from app.services.subagent import SubagentDispatcher, select_tools_for_subagent

    parent_sid = "parent-sess-lock-reentry-stream"
    saved: list = []
    acquire_calls: list = []

    dispatcher = SubagentDispatcher(registry, parent_session_id=parent_sid)
    sub_engine = dispatcher._build_sub_engine(
        select_tools_for_subagent(registry), max_rounds=5,
    )
    assert sub_engine.is_subagent_engine is True

    # 缩短 acquire 预算：门控若失效，子引擎会在真实锁上争用，快速暴露而非等 30s。
    def _fast_session_lock(sid, **kwargs):
        kwargs.setdefault("acquire_timeout_s", 0.5)
        return real_session_lock(sid, **kwargs)

    # 结构性断言（#1647 同纪律）：整个场景只有父锁一次 fallback acquire。
    _orig_acquire = dl._InProcessLock.acquire

    async def _counting_acquire(self, timeout_s=None):
        acquire_calls.append(timeout_s)
        return await _orig_acquire(self, timeout_s=timeout_s)

    monkeypatch.setattr(dl._InProcessLock, "acquire", _counting_acquire)

    async def _sub_stream(*args, **kwargs):
        yield ("done", {"message": {"content": "子任务完成，生成 ref:data-1。"}})

    with patch.object(ee_mod, "session_lock", _fast_session_lock), \
         patch.object(sub_engine, "_call_llm_stream",
                      return_value=_sub_stream()), patch(
            "app.services.chat.execution_engine.AsyncHistoryService",
            _recording_history(saved),
        ):
        events: list[str] = []
        # 父 turn 语义：工具波内全程持有同一 session 的真实锁并绑定透传，
        # 子引擎据此用 NoOpSessionLock 跳过 keepalive 轮询（有界完成）。
        async with real_session_lock(parent_sid) as parent_lock:
            assert len(acquire_calls) == 1, "父锁自身应经 fallback acquire 获取"
            tok = bind_engine_lock(parent_sid, parent_lock)
            try:
                async for ev in sub_engine.chat_stream(
                    "子任务：批量统计", session_id=parent_sid,
                ):
                    events.append(ev)
            finally:
                unbind_engine_lock(tok)

    assert events, "子引擎流式回合必须产出事件"
    assert not any(e.startswith("keep_alive") for e in events), (
        "F2 门控生效时子引擎不该进 keepalive 轮询（父已持锁透传）"
    )
    assert saved == []  # #407 写抑制语义不变（流式路径同样不写父会话 DB）
    # 与 chat 版同一纪律：子引擎零次自取会话锁。
    assert len(acquire_calls) == 1, (
        f"sub-engine acquired the session lock itself ({len(acquire_calls)} calls) "
        "— it must run under the parent's held lock (F2 gate)"
    )


@pytest.mark.asyncio
async def test_subagent_chat_without_parent_lock_binding_contends(registry):
    """反向装配（#1669 验收标准 3）：父持锁但未经 legacy_bind_engine_lock 绑定。

    F2 门控判 False（_parent_holds_session_lock 探测不到绑定），子引擎诚实
    重取同一把非重入锁 → acquire 预算耗尽 → LockContentionError 上抛。
    锁死该语义，防止有人把门控改成「无条件跳过争锁」（那会放行父未持锁
    时的并发互斥缺失）。
    """
    import app.services.chat.execution_engine as ee_mod
    from app.services.distributed_lock import (
        LockContentionError,
        session_lock as real_session_lock,
    )

    from app.services.subagent import SubagentDispatcher, select_tools_for_subagent

    parent_sid = "parent-sess-lock-reentry-unbound"
    saved: list = []

    dispatcher = SubagentDispatcher(registry, parent_session_id=parent_sid)
    sub_engine = dispatcher._build_sub_engine(
        select_tools_for_subagent(registry), max_rounds=5,
    )
    assert sub_engine.is_subagent_engine is True

    # 缩短 acquire 预算：0.5s 内诚实暴露争用，而非等 30s。
    def _fast_session_lock(sid, **kwargs):
        kwargs.setdefault("acquire_timeout_s", 0.5)
        return real_session_lock(sid, **kwargs)

    resp = {"choices": [{"message": {"content": "子任务完成，生成 ref:data-1。"}}]}

    with patch.object(ee_mod, "session_lock", _fast_session_lock), \
         patch.object(
             sub_engine, "_call_llm", new_callable=AsyncMock, return_value=resp,
         ), patch(
            "app.services.chat.execution_engine.AsyncHistoryService",
            _recording_history(saved),
        ):
        # 父 turn 只持锁、不绑定（无 legacy_bind_engine_lock）→ 门控不生效
        async with real_session_lock(parent_sid):
            with pytest.raises(LockContentionError):
                await sub_engine.chat("子任务：批量统计", session_id=parent_sid)

    assert saved == []  # 失败路径同样不写父会话 DB（#407 语义不变）


# ─── clearing 标记注册表级共享 ────────────────────────────────────


@pytest.mark.asyncio
async def test_clearing_marker_is_registry_wide(registry):
    """任一实例标记 clearing 后，其他实例（如 in-flight 子引擎）写库被抑制。"""
    from app.services.chat.execution_engine import SessionClearingError
    from app.services.chat_engine import ChatEngine

    e1 = ChatEngine(registry)
    e2 = ChatEngine(registry)
    sid = "sess-407-clear"
    saved: list = []

    try:
        e1._clearing_sessions.add(sid)
        assert sid in e2._clearing_sessions  # 注册表级共享（同一集合）

        with patch(
            "app.services.chat.execution_engine.AsyncHistoryService",
            _recording_history(saved),
        ):
            await e2._save_msg_async(sid, "user", "hello")  # 其他实例的写被抑制
            await e2._save_msg_async("sess-407-other", "user", "hi")  # 非 clearing 会话照常

        assert saved == [("sess-407-other", "user", "hi")]

        with pytest.raises(SessionClearingError):
            e2._reject_if_clearing(sid)  # 新 turn 拒绝（跨实例生效）
    finally:
        e1._clearing_sessions.discard(sid)  # 清掉共享标记，避免泄漏到其他测试
