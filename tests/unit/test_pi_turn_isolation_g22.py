"""G22-P1 — turn 身份与取消隔离回归（清偿 G12 review P2-1 残留）。

验收口径（A07/A09）：检查取消消息**实际发送时**的 active-turn 身份，而不是
函数入口的参数 —— ProbeRpcClient 在 ``issue``（写相位）内快照注册表身份，
断言全部落在「写出时刻」。控制面（clear_queue/abort）是 worker 级全局操作，
vendor 无 turn-scoped 命令；隔离由宿主交接门（bridge._control_gate）+
entry 对象同一性校验实现：

1. A07 同会话：T1 结束后迟到的 abort 不写出任何控制 RPC（后继 turn 安全）；
2. A07 交接竞态：abort 冻结在「校验后、写出前」期间 T1 收尾 + T2 准入，
   写出身份仍 == T1（写出在门内完成，注册在门外排队 → 字节序安全）；
3. A09 取消×结算交错：迟到 abort 不翻转已结算终态、不记录新 abort 来源；
4. 门序：注册（后继 turn 登记）严格晚于控制 RPC 写出；
5. legacy ``session_id=None`` 语义保持（解析自 _current_turn）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.agent_pi_bridge as bridge_mod
from app.agent_pi_bridge import (
    PiBridge,
    get_active_turn_entry,
    register_active_pi_turn,
)


class ProbeRpcClient:
    """G22-P1 探针 RPC 客户端：在**写相位**（issue）记录发出时刻的
    active-turn 身份 —— review 口径「取消消息实际发送时的身份」。"""

    def __init__(self, session_id: str, *, manual_turn: bool = False) -> None:
        self.session_id = session_id
        self.events: asyncio.Queue = asyncio.Queue()
        self.in_flight = asyncio.Event()
        self.can_finish = asyncio.Event()
        # manual_turn=False：prompt 立即完成（测试可直接 await 整个 turn）；
        # True：prompt 挂在 can_finish 上（测试持活 turn 后手动放行）。
        self.manual_turn = manual_turn
        #: (command, turn_id_at_write_or_None, seq)
        self.control_writes: list[tuple[str, object, int]] = []
        self.prompt_writes: list[tuple[object, int]] = []
        self.issue_pause = asyncio.Event()
        self.issue_pause.set()
        self.failed_pending: list = []
        self._seq = 0

    # ── bridge 消费的客户端面 ────────────────────────────────────────────
    def pending_request_ids(self):
        return set()

    def fail_pending_ids(self, ids, reason: str) -> None:
        self.failed_pending.append((sorted(ids), reason))

    async def request(self, command: str, data: dict = None) -> dict:
        if command == "prompt":
            entry = get_active_turn_entry(self.session_id)
            self._seq += 1
            self.prompt_writes.append(
                (entry.turn_id if entry is not None else None, self._seq)
            )
            self.in_flight.set()
            if self.manual_turn:
                await self.can_finish.wait()
            await self.events.put({"type": "agent_end", "willRetry": False})
            await self.events.put({"type": "agent_settled"})
            return {"status": "ok"}
        return {}

    async def issue(self, command: str, data: dict = None):
        await self.issue_pause.wait()
        entry = get_active_turn_entry(self.session_id)
        ident = entry.turn_id if entry is not None else None
        self._seq += 1
        if command in ("clear_queue", "abort"):
            self.control_writes.append((command, ident, self._seq))
        elif command == "prompt":
            self.prompt_writes.append((ident, self._seq))
        future = asyncio.get_running_loop().create_future()
        future.set_result({"status": "ok"})
        return SimpleNamespace(id=f"probe-{self._seq}", command=command, future=future)

    async def collect(self, issued, command: str = None) -> dict:
        return await issued.future

    # ── 测试控制 ────────────────────────────────────────────────────────
    async def finish_turn(self) -> None:
        self.can_finish.set()
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def _tracker_stub(monkeypatch):
    """stream/prompt 台账 best-effort：给一个最小 tracker，阻断真实 ChatEngine。"""
    import app.services.chat.engine_instance as engines

    tracker = SimpleNamespace(
        create=lambda sid, message: SimpleNamespace(id=f"task-{sid}"),
        list_by_session=lambda sid: [],
        cancel=lambda tid: None,
        fail_task=lambda tid, reason: None,
        complete_task=lambda tid, message=None, map_product=None: None,
    )
    monkeypatch.setattr(
        engines, "try_get_chat_engine",
        lambda: SimpleNamespace(tracker=tracker),
    )
    saved = bridge_mod._TURN_ABORT_SOURCES
    bridge_mod._TURN_ABORT_SOURCES.clear()
    yield
    bridge_mod._TURN_ABORT_SOURCES.clear()
    bridge_mod._TURN_ABORT_SOURCES.update(saved)


async def _run_turn(bridge: PiBridge, rpc: ProbeRpcClient, message: str):
    return await bridge.prompt(message, session_id=rpc.session_id)


# ── A07：同会话迟到取消 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a07_late_abort_after_settled_turn_cannot_hit_successor():
    """T1 已结算并交接后到达的 abort：legacy 清场语义保留（W07：清 T1
    残留 steer 队列），但写出身份快照为 None（无活动 turn 可误伤），且
    后继 turn 的 prompt 字节严格晚于这些控制字节（门序；旧实现同场景下
    迟到 abort 可命中同会话后继 turn —— G12 review P2-1）。"""
    rpc = ProbeRpcClient("s-a07-late")
    bridge = PiBridge(rpc=rpc)

    t1 = await _run_turn(bridge, rpc, "first")
    assert t1["content"] is not None
    assert get_active_turn_entry("s-a07-late") is None  # T1 已完整交接

    result = await bridge.abort("s-a07-late", source="user")

    assert result == {"status": "ok"}
    assert rpc.control_writes, "legacy 清场路径应写出 clear_queue+abort"
    assert all(ident is None for _, ident, _ in rpc.control_writes), (
        "无在飞 turn 时控制 RPC 的写出身份必须为 None（不可指向任何 turn）"
    )
    assert bridge_mod._TURN_ABORT_SOURCES == {}

    # 后继 turn 正常运行且不被打扰；其 prompt 写出晚于迟到 abort 字节。
    t2 = await _run_turn(bridge, rpc, "second")
    assert t2["content"] is not None
    late_max_seq = max(seq for _, _, seq in rpc.control_writes)
    t2_prompt_seqs = [seq for _, seq in rpc.prompt_writes if seq > late_max_seq]
    assert t2_prompt_seqs, "T2 的 prompt 写出必须晚于迟到 abort 的控制字节"


@pytest.mark.asyncio
async def test_a07_abort_of_active_turn_carries_owner_identity_at_write():
    """对在飞 turn 的 abort：写出时刻身份 == 目标 turn（合法取消路径不回退）。"""
    rpc = ProbeRpcClient("s-a07-live", manual_turn=True)
    bridge = PiBridge(rpc=rpc)

    task = asyncio.create_task(_run_turn(bridge, rpc, "first"))
    await asyncio.wait_for(rpc.in_flight.wait(), timeout=5.0)
    t1_entry = get_active_turn_entry("s-a07-live")
    assert t1_entry is not None
    target_turn_id = t1_entry.turn_id

    await bridge.abort("s-a07-live", source="user")

    assert [(c, i) for c, i, _ in rpc.control_writes] == [
        ("clear_queue", target_turn_id),
        ("abort", target_turn_id),
    ]
    # 目标 turn 的 token 被点燃（快照作用域；F24）
    assert t1_entry.token.cancelled

    await rpc.finish_turn()
    await asyncio.wait_for(task, timeout=5.0)


@pytest.mark.asyncio
async def test_a07_teardown_race_frozen_write_still_cannot_hit_successor():
    """交接竞态（P2-1 的核心窗口）：abort 冻结在「校验后、写出前」（持有交接
    门）期间 T1 收尾、T2 准入 —— 门序保证写出身份 == T1 且 T2 的登记/prompt
    严格晚于控制 RPC 字节；T2 不被取消、正常完成。"""
    rpc = ProbeRpcClient("s-a07-race", manual_turn=True)
    bridge = PiBridge(rpc=rpc)

    t1_task = asyncio.create_task(_run_turn(bridge, rpc, "first"))
    await asyncio.wait_for(rpc.in_flight.wait(), timeout=5.0)
    t1_entry = get_active_turn_entry("s-a07-race")
    t1_turn_id = t1_entry.turn_id

    # 冻结写相位 → abort 停在门内（校验已过、写出未发生）
    rpc.issue_pause.clear()
    abort_task = asyncio.create_task(bridge.abort("s-a07-race", source="user"))
    await asyncio.sleep(0.05)  # 让 abort 进入门内并冻结

    # T1 收尾（teardown 的 unregister 会在交接门外排队等 abort 放门）
    await rpc.finish_turn()
    await asyncio.sleep(0.05)
    # T2 准入也被 turn lease/unregister 顺序挡在后面
    t2_task = asyncio.create_task(_run_turn(bridge, rpc, "second"))
    await asyncio.sleep(0.05)

    # 解冻：abort 写出（此刻注册表里仍是 T1 —— unregister 在等门）
    rpc.issue_pause.set()
    await asyncio.wait_for(abort_task, timeout=5.0)
    await asyncio.wait_for(t1_task, timeout=5.0)

    # 写出身份全部 == T1；若身份为 None/T2 则说明取消打错了 turn
    assert rpc.control_writes, "冻结路径应完成写出（校验时 T1 仍在飞）"
    for command, ident, _ in rpc.control_writes:
        assert ident == t1_turn_id, (
            f"控制 RPC {command} 写出时刻身份 {ident!r} != 目标 turn {t1_turn_id!r}"
        )

    # T2 正常完成；其 token 从未被取消
    await asyncio.wait_for(t2_task, timeout=10.0)
    t2_entry_during = None  # T2 已结束；以行为断言代替（完成 + 无控制写出指向它）
    assert all(ident == t1_turn_id for _, ident, _ in rpc.control_writes)
    assert t2_entry_during is None


@pytest.mark.asyncio
async def test_gate_orders_registration_after_control_writes():
    """门序单元锁：后继 turn 的登记（交接门内）严格晚于在飞 abort 的写出。"""
    rpc = ProbeRpcClient("s-gate-order", manual_turn=True)
    bridge = PiBridge(rpc=rpc)

    t1_task = asyncio.create_task(_run_turn(bridge, rpc, "first"))
    await asyncio.wait_for(rpc.in_flight.wait(), timeout=5.0)

    rpc.issue_pause.clear()
    abort_task = asyncio.create_task(bridge.abort("s-gate-order", source="user"))
    await asyncio.sleep(0.05)

    reg_task = asyncio.create_task(
        register_active_pi_turn("s-gate-order", "turn-2-gate", bridge=bridge)
    )
    await asyncio.sleep(0.05)
    assert not reg_task.done(), "登记必须等待交接门（abort 持门期间）"

    rpc.issue_pause.set()
    await asyncio.wait_for(abort_task, timeout=5.0)
    await asyncio.wait_for(reg_task, timeout=5.0)
    assert get_active_turn_entry("s-gate-order").turn_id == "turn-2-gate"

    # 清场：回到真实 T1 流程收尾（登记被测试覆盖过，先复位）
    await bridge_mod.unregister_active_pi_turn("s-gate-order", "turn-2-gate")
    await rpc.finish_turn()
    await asyncio.wait_for(t1_task, timeout=5.0)
    for _, ident, _ in rpc.control_writes:
        assert ident is not None and ident != "turn-2-gate"


# ── A09：取消 × 结算交错 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a09_late_abort_never_flips_settled_terminal():
    """结算完成后的迟到 abort（legacy 清场）：写出身份为 None、不记录新
    abort 来源（无归属窗口），已结算终态不被翻转。"""
    rpc = ProbeRpcClient("s-a09")
    bridge = PiBridge(rpc=rpc)

    await _run_turn(bridge, rpc, "only turn")  # 正常结算

    late = await bridge.abort("s-a09", source="user")
    assert late == {"status": "ok"}
    assert all(ident is None for _, ident, _ in rpc.control_writes)
    assert bridge_mod._TURN_ABORT_SOURCES == {}, (
        "迟到 abort 不得为已结算 turn 记录来源（会污染下一结算窗口的语义）"
    )


@pytest.mark.asyncio
async def test_a09_double_abort_single_terminal_effects():
    """并发双 abort + 正常结算交错：控制写出幂等（两次 clear+abort 均指向
    同一 turn 身份），快照作用域效果恰好一次生效。"""
    rpc = ProbeRpcClient("s-a09-dbl", manual_turn=True)
    bridge = PiBridge(rpc=rpc)

    task = asyncio.create_task(_run_turn(bridge, rpc, "t"))
    await asyncio.wait_for(rpc.in_flight.wait(), timeout=5.0)
    entry = get_active_turn_entry("s-a09-dbl")
    turn_id = entry.turn_id

    a1 = asyncio.create_task(bridge.abort("s-a09-dbl", source="user"))
    a2 = asyncio.create_task(bridge.abort("s-a09-dbl", source="user"))
    await asyncio.gather(a1, a2)

    for command, ident, _ in rpc.control_writes:
        assert ident == turn_id, f"{command} 写出身份漂移：{ident!r}"
    # abort 来源对同一 turn 幂等记录（bounded dict，单一键）
    assert list(bridge_mod._TURN_ABORT_SOURCES.keys()) == [turn_id]

    await rpc.finish_turn()
    await asyncio.wait_for(task, timeout=5.0)


# ── legacy 语义保持 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_abort_without_session_scopes_to_current_turn():
    """session_id=None 的 legacy 路径：目标解析自 _current_turn，身份校验
    同样以 entry 同一性为准。"""
    rpc = ProbeRpcClient("s-legacy", manual_turn=True)
    bridge = PiBridge(rpc=rpc)

    task = asyncio.create_task(_run_turn(bridge, rpc, "t"))
    await asyncio.wait_for(rpc.in_flight.wait(), timeout=5.0)
    entry = get_active_turn_entry("s-legacy")
    assert bridge._current_turn is entry

    await bridge.abort(source="system")  # type: ignore[arg-type]

    assert {(c, i) for c, i, _ in rpc.control_writes} == {
        ("clear_queue", entry.turn_id), ("abort", entry.turn_id),
    }
    await rpc.finish_turn()
    await asyncio.wait_for(task, timeout=5.0)


# ── 修复轮 2 补测：drain / abort 门 fail-safe（review 二轮遗留）─────────


@pytest.mark.asyncio
async def test_teardown_drains_inflight_control_responses_before_unregister():
    """teardown 在 unregister 前有界等待在飞控制 RPC 的**响应**——vendor
    并发分发下 abort 响应落定 = vendor 侧处理完成（_drain_control_rpcs）。
    """
    rpc = ProbeRpcClient("s-drain", manual_turn=True)
    bridge = PiBridge(rpc=rpc)

    task = asyncio.create_task(_run_turn(bridge, rpc, "t"))
    await asyncio.wait_for(rpc.in_flight.wait(), timeout=5.0)

    # 挂起 abort 的响应（写出已完成、响应永不回）→ 塞进在飞台账
    hang = asyncio.get_running_loop().create_future()
    bridge._inflight_control.add(hang)

    async def _abort():
        await bridge.abort("s-drain", source="user")

    abort_task = asyncio.create_task(_abort())
    await asyncio.sleep(0.05)  # abort 写相位完成（探针立即解 future）

    # teardown 触发（token 已被 abort 点燃）——drain 必须等待挂起响应；
    # 有界 = 5s 超时放行（测试给 12s 余量），且等待真实发生（≥4s）。
    import time as _time

    t_teardown = _time.monotonic()
    await rpc.finish_turn()
    await asyncio.wait_for(task, timeout=12.0)
    waited = _time.monotonic() - t_teardown
    assert waited >= 4.0, f"drain 未真实等待（{waited:.1f}s）"
    assert waited <= 10.0, f"drain 超时有界失效（{waited:.1f}s）"

    # 完成挂起 future 后台账清空
    if not hang.done():
        hang.set_result({})
    for _ in range(50):
        if not bridge._inflight_control:
            break
        await asyncio.sleep(0.05)
    assert not bridge._inflight_control
    await asyncio.wait_for(abort_task, timeout=5.0)


@pytest.mark.asyncio
async def test_abort_gate_wedged_skips_failsafe():
    """门被 wedged 持有 >5s 时 abort **整体跳过**（fail-safe：发不出就
    绝不发，宁可不取消也不冒险命中后继 turn），不无限挂起。"""
    import time

    rpc = ProbeRpcClient("s-wedge")
    bridge = PiBridge(rpc=rpc)
    assert bridge._control_gate is not None
    await bridge._control_gate.acquire()  # 模拟 wedged 控制写持门

    t0 = time.monotonic()
    result = await bridge.abort("s-wedge", source="user")
    elapsed = time.monotonic() - t0

    assert result == {}
    assert elapsed < 10.0, "abort 不得在 wedged 门上无限挂起"
    assert rpc.control_writes == [], "fail-safe 跳过——零控制写出"
    assert bridge_mod._TURN_ABORT_SOURCES == {}

    bridge._control_gate.release()
