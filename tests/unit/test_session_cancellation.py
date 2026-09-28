"""G11 / BUG-18 后续 — clear_session → abort_active_pi_turn 会话级 abort 路径守护。

ADR-0100 统一取消 seam（app/services/chat/session_cancellation.py）此前只有
任务/作业级联（test_pi_cancellation_unified.py）的守护；chat.py 的 BUG-18
修复（DELETE /sessions/{session_id} 在 DB 清理前先对 Pi bridge 发 abort）
没有测试锁，三个回归面处于裸奔状态：

1. 目标会话在途 turn 被中断 —— abort 携带被删会话 id + source="system"，
   且**先于** DB 删除发出（BUG-18 的原始风险：token 泄漏消耗，靠顺序断言
   守护 —— 若有人把 abort 挪回 DB 清理之后，测试立刻红）；
2. 其它会话在途 turn 不被误杀 —— _active_turns 按 session 键解析 owner，
   目标会话无 entry 时连 bridge RPC 都不发（session-blind abort 会把共享
   子进程上别的会话一起杀掉 —— V5-B / F5 教训）；
3. abort RPC 失败仅记日志不阻塞 DB 清理 —— 桥接是 fire-and-forget：RPC
   异常 / 超时 / seam 结构性失败全部被吞 + logger.warning 落日志，清理链
   （锁 → tombstone → DB 行 → mapspec/artifact 清扫）照常走完。

测试策略：seam 层用假 bridge（fake RPC）直测各返回路径；端点层直接调用
chat_routes.clear_session 协程函数，所有协作者（bridge 表 / 锁 / 引擎 /
mapspec / artifact / resume registry）打桩进同一事件序列表 —— abort 调用
本身也记账，事件顺序断言守护「abort 先于一切清理」。
"""
from __future__ import annotations

import asyncio
import functools
import logging

import pytest


# ── 桩件 ─────────────────────────────────────────────────────────────────────


class _FakeBridge:
    """假 worker bridge —— abort RPC 的可编程替身。"""

    def __init__(self, *, hang=False, raise_error=False, result=None, events=None):
        self.calls: list = []  # (session_id, source)
        self.hang = hang
        self.raise_error = raise_error
        self.result = result if result is not None else {"status": "aborted"}
        self._events = events

    async def abort(self, session_id=None, *, source="user"):
        self.calls.append((session_id, source))
        if self._events is not None:
            self._events.append(f"abort:{session_id}")
        if self.raise_error:
            raise RuntimeError("rpc dead")
        if self.hang:
            await asyncio.sleep(30)
        return self.result


class _Entry:
    def __init__(self, bridge):
        self.bridge = bridge


class _FakeLock:
    lost = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeLockRegistry:
    def __init__(self, events):
        self._events = events

    def lock(self, session_id, fail_on_degraded=False):
        self._events.append(f"lock:{session_id}")
        return _FakeLock()


class _FakeEngine:
    def __init__(self, events, ok=True):
        self._events = events
        self._ok = ok

    async def clear_session(self, session_id, user_id=None, owner_token=None):
        self._events.append(f"db_clear:{session_id}")
        return self._ok


def _install_clear_session_env(
    monkeypatch,
    *,
    with_turns=(),
    use_pi=True,
    engine_ok=True,
):
    """给 chat_routes.clear_session 装上全套桩协作者，返回 (events, bridges)。

    with_turns: 视为「有在途 turn」的会话 id 列表 —— 各配一个假 bridge 并
    进 _active_turns 替身表。happy path 事件序列（s1 在途时）：
    abort:s1 → lock:s1 → invalidate:s1 → tombstone:s1 → db_clear:s1 →
    mapspec_purge:s1 → artifact_purge:s1 → tombstone_write:s1 →
    [leave_active:s1，仅真实 manager 具备该方法时] → resume_clear:s1
    """
    import app.agent_pi_bridge as bridge_mod
    import app.api.routes.chat as chat_routes
    import app.services.artifact_lifecycle as artifact_lifecycle_mod
    import app.services.mapspec.store as mapspec_store_mod
    from app.services.session_data import session_data_manager

    events: list = []
    bridges = {sid: _FakeBridge(events=events) for sid in with_turns}
    table = {sid: _Entry(b) for sid, b in bridges.items()}

    monkeypatch.setattr(chat_routes, "_use_pi_bridge", lambda: use_pi)
    monkeypatch.setattr(
        bridge_mod, "get_active_turn_entry", lambda sid: table.get(sid)
    )
    monkeypatch.setattr(
        bridge_mod,
        "clear_cartographic_session_state",
        lambda sid: events.append(f"tombstone:{sid}"),
    )
    monkeypatch.setattr(
        bridge_mod,
        "restore_cartographic_session_state",
        lambda sid: events.append(f"tombstone_restore:{sid}"),
    )
    monkeypatch.setattr(
        chat_routes, "session_lock_registry", _FakeLockRegistry(events)
    )
    monkeypatch.setattr(
        chat_routes, "get_engine", lambda: _FakeEngine(events, ok=engine_ok)
    )
    monkeypatch.setattr(
        session_data_manager,
        "invalidate_local_cache",
        lambda sid: events.append(f"invalidate:{sid}"),
    )

    async def fake_set_map_state(sid, key, value):
        events.append(f"tombstone_write:{sid}")
        return True

    monkeypatch.setattr(session_data_manager, "set_map_state", fake_set_map_state)

    async def fake_leave_active(sid):
        events.append(f"leave_active:{sid}")

    monkeypatch.setattr(
        session_data_manager, "leave_active_set", fake_leave_active, raising=False
    )

    async def fake_clear_files(sid):
        events.append(f"mapspec_purge:{sid}")

    monkeypatch.setattr(
        mapspec_store_mod.mapspec_store_instance, "clear_session_files", fake_clear_files
    )

    async def fake_purge(sid):
        events.append(f"artifact_purge:{sid}")

    monkeypatch.setattr(
        artifact_lifecycle_mod, "purge_session_artifacts", fake_purge
    )
    monkeypatch.setattr(
        chat_routes._turn_resume_registry,
        "clear_session",
        lambda sid: events.append(f"resume_clear:{sid}"),
    )
    return events, bridges


def _call_clear_session(session_id="s1"):
    import app.api.routes.chat as chat_routes

    return chat_routes.clear_session(
        session_id,
        _user={"user_id": "u1"},
        owner_token=None,
        _conv=object(),  # require_owned_session 依赖的替身（直调不触发）
    )


# ── 场景 1：目标会话在途 turn 被中断（seam 层） ──────────────────────────────


def test_seam_abort_routes_to_owning_bridge_with_session_id(monkeypatch):
    """abort 解析 owner-worker 并携带目标会话 id —— 不串别的 bridge。"""
    import app.agent_pi_bridge as bridge_mod
    from app.services.chat import session_cancellation as seam

    b1, b2 = _FakeBridge(), _FakeBridge()
    monkeypatch.setattr(
        bridge_mod,
        "get_active_turn_entry",
        lambda sid: {"s1": _Entry(b1), "s2": _Entry(b2)}.get(sid),
    )
    result = asyncio.run(seam.abort_active_pi_turn("s1"))
    assert result["aborted"] is True
    assert b1.calls == [("s1", "user")]
    assert b2.calls == []


def test_seam_forwards_source_to_bridge(monkeypatch):
    """F03：会话删除是 system 发起的中止 —— source 必须原样转发到 bridge。"""
    import app.agent_pi_bridge as bridge_mod
    from app.services.chat import session_cancellation as seam

    bridge = _FakeBridge()
    monkeypatch.setattr(
        bridge_mod, "get_active_turn_entry", lambda sid: _Entry(bridge)
    )
    result = asyncio.run(
        seam.abort_active_pi_turn("s-del", reason="session deleted", source="system")
    )
    assert result["aborted"] is True
    assert bridge.calls == [("s-del", "system")]


def test_seam_success_reports_bridge_result_truncated(monkeypatch):
    """成功路径返回 aborted=True，detail 携带 bridge 返回值（截断到 200）。"""
    import app.agent_pi_bridge as bridge_mod
    from app.services.chat import session_cancellation as seam

    bridge = _FakeBridge(result={"status": "aborted", "note": "x" * 500})
    monkeypatch.setattr(
        bridge_mod, "get_active_turn_entry", lambda sid: _Entry(bridge)
    )
    result = asyncio.run(seam.abort_active_pi_turn("s1"))
    assert result["aborted"] is True
    assert len(result["detail"]) == 200


# ── 场景 2：其它会话在途 turn 不被误杀 ───────────────────────────────────────


def test_seam_no_active_turn_for_target_is_noop(monkeypatch):
    """目标会话无 _active_turns entry → 不发任何 bridge RPC（no active turn）。"""
    import app.agent_pi_bridge as bridge_mod
    from app.services.chat import session_cancellation as seam

    other = _FakeBridge()
    monkeypatch.setattr(
        bridge_mod, "get_active_turn_entry", lambda sid: {"s2": _Entry(other)}.get(sid)
    )
    result = asyncio.run(seam.abort_active_pi_turn("s1"))
    assert result == {"aborted": False, "detail": "no active turn"}
    assert other.calls == []


def test_seam_missing_session_id_short_circuits_before_table_lookup(monkeypatch):
    """无 session id（None）→ 短路返回，连解析表都不查。"""
    import app.agent_pi_bridge as bridge_mod
    from app.services.chat import session_cancellation as seam

    def _boom(sid):
        raise AssertionError("no session id 时不应触碰解析表")

    monkeypatch.setattr(bridge_mod, "get_active_turn_entry", _boom)
    result = asyncio.run(seam.abort_active_pi_turn(None))
    assert result == {"aborted": False, "detail": "no session id"}


def test_clear_session_does_not_kill_other_sessions_turn(monkeypatch):
    """删 s1 时 s2 的在途 turn 完好无损 —— bridge2 零调用，s1 清理照常完成。"""
    events, bridges = _install_clear_session_env(monkeypatch, with_turns=["s2"])

    response = asyncio.run(_call_clear_session("s1"))
    assert response.status == "ok"
    # 目标会话无在途 turn → 不发 abort；共享子进程上 s2 的 turn 绝不被波及
    assert bridges["s2"].calls == []
    assert not any(e.startswith("abort:") for e in events)
    assert "db_clear:s1" in events


def test_clear_session_hits_only_target_bridge_among_multiple_sessions(monkeypatch):
    """s1/s2 都有在途 turn 时删 s1 —— 只有 s1 的 owner bridge 被打到一次。"""
    events, bridges = _install_clear_session_env(
        monkeypatch, with_turns=["s1", "s2"]
    )

    response = asyncio.run(_call_clear_session("s1"))
    assert response.status == "ok"
    assert bridges["s1"].calls == [("s1", "system")]
    assert bridges["s2"].calls == []
    assert "db_clear:s1" in events


# ── 场景 3：abort RPC 失败仅记日志不阻塞 DB 清理（seam 层） ──────────────────


def test_seam_rpc_error_swallowed_and_logged(monkeypatch, caplog):
    """bridge.abort 抛异常 → 吞掉 + logger.warning 落日志（带会话 id）。"""
    import app.agent_pi_bridge as bridge_mod
    from app.services.chat import session_cancellation as seam

    monkeypatch.setattr(
        bridge_mod,
        "get_active_turn_entry",
        lambda sid: _Entry(_FakeBridge(raise_error=True)),
    )
    with caplog.at_level(
        logging.WARNING, logger="app.services.chat.session_cancellation"
    ):
        result = asyncio.run(seam.abort_active_pi_turn("s-fail"))
    assert result["aborted"] is False
    assert result["detail"].startswith("abort error")
    warning = [r for r in caplog.records if "abort failed" in r.message]
    assert warning and "s-fail" in warning[0].getMessage()


def test_seam_timeout_swallowed_and_logged(monkeypatch, caplog):
    """bridge 挂死 → CONC-F7 预算内超时，吞掉 + 记日志，绝不上抛。"""
    import app.agent_pi_bridge as bridge_mod
    from app.services.chat import session_cancellation as seam

    monkeypatch.setattr(
        bridge_mod,
        "get_active_turn_entry",
        lambda sid: _Entry(_FakeBridge(hang=True)),
    )
    with caplog.at_level(
        logging.WARNING, logger="app.services.chat.session_cancellation"
    ):
        result = asyncio.run(seam.abort_active_pi_turn("s-hang", timeout=0.05))
    assert result["aborted"] is False
    assert result["detail"] == "abort timeout"
    warning = [r for r in caplog.records if "abort timed out" in r.message]
    assert warning and "s-hang" in warning[0].getMessage()


def test_seam_structural_failure_swallowed_and_logged(monkeypatch, caplog):
    """解析表本身炸了（结构性失败）→ 同样吞掉 + 记日志（seam 最后一道网）。"""
    import app.agent_pi_bridge as bridge_mod
    from app.services.chat import session_cancellation as seam

    def _corrupt_table(sid):
        raise RuntimeError("active turn table corrupted")

    monkeypatch.setattr(bridge_mod, "get_active_turn_entry", _corrupt_table)
    with caplog.at_level(
        logging.WARNING, logger="app.services.chat.session_cancellation"
    ):
        result = asyncio.run(seam.abort_active_pi_turn("s-corrupt"))
    assert result["aborted"] is False
    assert result["detail"].startswith("seam error")
    warning = [r for r in caplog.records if "abort_active_pi_turn" in r.message]
    assert warning and "s-corrupt" in warning[0].getMessage()


# ── 场景 1（端点层）：abort 先于一切清理 —— BUG-18 顺序 oracle ───────────────


def test_clear_session_abort_is_first_effectful_step(monkeypatch):
    """事件序列的第一步必须是 abort（携带被删会话 id）——先于锁、tombstone、
    DB 删除。"""
    events, bridges = _install_clear_session_env(monkeypatch, with_turns=["s1"])

    response = asyncio.run(_call_clear_session("s1"))
    assert response.status == "ok"
    assert bridges["s1"].calls == [("s1", "system")]
    assert events[0] == "abort:s1"
    assert events.index("lock:s1") > events.index("abort:s1")


def test_clear_session_abort_precedes_db_deletion_bug18_order(monkeypatch):
    """BUG-18 核心 oracle：abort 必须先于 DB 行删除发出 —— 若 abort 被挪回
    清理之后，在途 prompt 的 token 消耗窗口重新打开，此断言变红。"""
    events, bridges = _install_clear_session_env(monkeypatch, with_turns=["s1"])

    asyncio.run(_call_clear_session("s1"))
    assert bridges["s1"].calls == [("s1", "system")]
    assert events.index("abort:s1") < events.index("db_clear:s1")
    # tombstone（清理链第一步）也必须排在 abort 之后
    assert events.index("abort:s1") < events.index("tombstone:s1")


def test_clear_session_full_cleanup_chain_after_abort(monkeypatch):
    """abort 后清理链完整走完：锁 → invalidate → tombstone → DB →
    mapspec/artifact 清扫 → tombstone 持久化 → active 摘除 → resume 缓冲
    清除。"""
    events, _ = _install_clear_session_env(monkeypatch, with_turns=["s1"])

    response = asyncio.run(_call_clear_session("s1"))
    assert response.status == "ok"
    for expected in (
        "lock:s1",
        "invalidate:s1",
        "tombstone:s1",
        "db_clear:s1",
        "mapspec_purge:s1",
        "artifact_purge:s1",
        "tombstone_write:s1",
        "resume_clear:s1",
    ):
        assert expected in events, f"清理链缺步: {expected}"


def test_clear_session_without_pi_bridge_skips_abort_but_cleans(monkeypatch):
    """legacy 路径（Pi bridge 关闭）→ 不发 abort，清理链照常完整。"""
    events, bridges = _install_clear_session_env(
        monkeypatch, with_turns=["s1"], use_pi=False
    )

    response = asyncio.run(_call_clear_session("s1"))
    assert response.status == "ok"
    assert bridges["s1"].calls == []
    assert not any(e.startswith("abort:") for e in events)
    assert "db_clear:s1" in events
    assert "tombstone:s1" in events


# ── 场景 3（端点层）：RPC 失败降级，DB 清理照常执行 ──────────────────────────


def test_clear_session_completes_when_abort_rpc_raises(monkeypatch, caplog):
    """bridge.abort RPC 抛异常 → 仅记日志，DB 清理 + 全链完成，响应 ok。"""
    events, bridges = _install_clear_session_env(
        monkeypatch, with_turns=["s1"]
    )
    bridges["s1"].raise_error = True

    with caplog.at_level(
        logging.WARNING, logger="app.services.chat.session_cancellation"
    ):
        response = asyncio.run(_call_clear_session("s1"))
    assert response.status == "ok"
    assert bridges["s1"].calls == [("s1", "system")]  # RPC 确实发出去了
    assert "db_clear:s1" in events, "abort 失败不得阻塞 DB 清理"
    assert "mapspec_purge:s1" in events and "artifact_purge:s1" in events
    warning = [r for r in caplog.records if "abort failed" in r.message]
    assert warning and "s1" in warning[0].getMessage()


def test_clear_session_completes_when_abort_rpc_times_out(monkeypatch, caplog):
    """bridge.abort 挂死 → seam 超时（0.05s 预算替代 5s 生产值），
    清理链照常完成、告警落日志。"""
    from app.services.chat import session_cancellation as seam

    events, bridges = _install_clear_session_env(monkeypatch, with_turns=["s1"])
    bridges["s1"].hang = True
    # 端点在调用时才 import seam.abort_active_pi_turn —— 换成小预算的部分
    # 应用仍走完整真实路径（解析 → wait_for → 吞超时 → 记日志），测试不等 5s
    monkeypatch.setattr(
        seam,
        "abort_active_pi_turn",
        functools.partial(seam.abort_active_pi_turn, timeout=0.05),
    )

    with caplog.at_level(
        logging.WARNING, logger="app.services.chat.session_cancellation"
    ):
        response = asyncio.run(_call_clear_session("s1"))
    assert response.status == "ok"
    assert bridges["s1"].calls == [("s1", "system")]
    assert "db_clear:s1" in events
    warning = [r for r in caplog.records if "abort timed out" in r.message]
    assert warning and "s1" in warning[0].getMessage()


def test_clear_session_completes_when_seam_structurally_fails(monkeypatch, caplog):
    """解析表结构性损坏 → seam 吞掉，端点清理链完整走完（最后一道网在端点
    之下依然成立）。"""
    import app.agent_pi_bridge as bridge_mod

    events, _ = _install_clear_session_env(monkeypatch)

    # 后打桩覆盖 env 助手的替身表（monkeypatch 后写者生效）—— 解析表损坏
    def _corrupt_table(sid):
        raise RuntimeError("active turn table corrupted")

    monkeypatch.setattr(bridge_mod, "get_active_turn_entry", _corrupt_table)

    with caplog.at_level(
        logging.WARNING, logger="app.services.chat.session_cancellation"
    ):
        response = asyncio.run(_call_clear_session("s1"))
    assert response.status == "ok"
    assert "db_clear:s1" in events
    warning = [r for r in caplog.records if "abort_active_pi_turn" in r.message]
    assert warning


def test_clear_session_404_still_aborts_first_and_restores_tombstone(monkeypatch):
    """引擎报告会话不存在（404）→ abort 仍是最先发出的一步（顺序守护对失败
    路径同样成立），tombstone 被回滚，resume 清理不执行。"""
    events, bridges = _install_clear_session_env(
        monkeypatch, with_turns=["s1"], engine_ok=False
    )

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_call_clear_session("s1"))
    assert exc_info.value.status_code == 404
    assert bridges["s1"].calls == [("s1", "system")]
    assert events.index("abort:s1") < events.index("db_clear:s1")
    assert "tombstone_restore:s1" in events
    assert "resume_clear:s1" not in events
