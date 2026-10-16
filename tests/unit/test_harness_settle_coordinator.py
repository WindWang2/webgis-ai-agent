"""ADR-0223 host-neutral settle coordinator 契约与 adversarial 矩阵。

确定性：真实 kernel 台账（内存后端，conftest 钉 USE_REDIS=false），效果
函数在各自 home 模块打桩（与 test_pi_post_dispatch_pipeline 同纪律）。
不触 LLM / Pi 子进程 / 网络。

覆盖（ADR-0223 Acceptance）：
- cause→status→class 单表与 bridge/legacy 归一 parity；
- E1..E6 固定顺序、clean/reduced 差异、逐段隔离（终验抛错不级联）；
- 幂等：同键重复 settle 重放收据、零重跑；并发 settle 串行化；
- stale：turn N 迟到 settle（后继已终态化 / 已异终态）零效果；
- pre_terminated（legacy 锁内预终态化）正规路径与收据诚实性；
- unknown_turn / 快照读失败 fail-closed；
- kill switch：OFF 摘披露效果、authority 腿照常；
- 有界：failure_detail/failure_class/receipt detail 截断；
- legacy 引擎接线（chat/chat_stream 锁外真实触发 coordinator）。
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.harness_kernel.settle as hks
from app.services.harness_kernel import get_runtime
from app.services.harness_kernel.settle import (
    CAUSE_STATUS,
    DISCLOSURE_EFFECTS,
    SETTLE_CAUSES,
    HarnessSettleInput,
    cause_from_host_signals,
    cause_from_legacy_outcome,
    settle_class_for_status,
    settle_turn,
    status_for_cause,
)


def _sid(prefix: str = "s") -> str:
    return f"{prefix}-settle-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _isolated_effects(monkeypatch):
    """效果函数全部打桩（home 模块 attr；记录调用序与注入的失败）。"""
    calls: list[str] = []
    failures: dict[str, Exception] = {}
    results: dict[str, object] = {}

    import app.services.gis_harness.map_completion as mc
    import app.services.gis_harness.workflow_instance as wfi
    import app.services.gis_harness.runtime_state_machine as rsm
    import app.services.gis_harness.context_layers as cl
    import app.lib.harness.replay.recorder  # noqa: F401  (确保不误触真实录制)
    import app.lib.runtime.chain_emitters as ce
    import app.services.gis_harness.trace_store as ts

    async def _finalize(session_id, *, reason="", final_gate=False, force=False):
        calls.append("final_gate")
        if "final_gate" in failures:
            raise failures["final_gate"]
        return results.get("final_gate")

    def _payload(completion, session_id, *, mapspec=None, mutation_revision=None):
        calls.append("payload")
        return {"task_complete": True, "source": "finalized"}

    async def _snap(session_id):
        calls.append("spec_snapshot")
        return (None, 7)

    async def _stored(session_id):
        calls.append("stored_product")
        return {"task_complete": True, "source": "stored"}

    async def _wfi(session_id, *, reason="", event="auto", force=False):
        calls.append("workflow")
        if "workflow" in failures:
            raise failures["workflow"]

    async def _rts(session_id, *, reason="", trigger="", turn_settled=False):
        calls.append("runtime_state")
        if "runtime_state" in failures:
            raise failures["runtime_state"]

    async def _ckpt(session_id):
        calls.append("checkpoint")
        if "checkpoint" in failures:
            raise failures["checkpoint"]

    def _emit(turn_id, stage, **payload):
        calls.append("chain_emit")
        return True

    def _persist(turn_id, session_id=""):
        calls.append("chain_persist")
        if "chain_persist" in failures:
            raise failures["chain_persist"]
        return True

    monkeypatch.setattr(mc, "maybe_finalize_map_product", _finalize)
    monkeypatch.setattr(mc, "finalization_sse_payload", _payload)
    monkeypatch.setattr(mc, "current_mapspec_for_disclosure", _snap)
    monkeypatch.setattr(mc, "read_stored_map_product", _stored)
    monkeypatch.setattr(wfi, "maybe_update_workflow_instance", _wfi)
    monkeypatch.setattr(rsm, "maybe_update_runtime_state", _rts)
    monkeypatch.setattr(rsm, "runtime_state_enabled", lambda: True)
    monkeypatch.setattr(cl, "checkpoint_context_layers", _ckpt)
    monkeypatch.setattr(ce, "emit_chain_for", _emit)
    monkeypatch.setattr(ts, "persist_turn_chain", _persist)

    hks.reset_for_tests()
    yield SimpleNamespace(calls=calls, failures=failures, results=results)
    hks.reset_for_tests()


async def _begin(session_id: str, turn_id: str, *, message: str = "hi") -> None:
    await get_runtime(session_id).begin_turn(turn_id, host="pi", message=message)


async def _status_of(session_id: str, turn_id: str) -> str | None:
    snap = await get_runtime(session_id).lifecycle_parity_snapshot(turn_id)
    return None if snap is None else str(snap.get("status"))


# ── 词表与归一 parity ─────────────────────────────────────────────────────


def test_cause_status_closed_vocabulary_fail_closed():
    assert set(CAUSE_STATUS) == set(SETTLE_CAUSES)
    assert status_for_cause("success") == "completed"
    assert status_for_cause("user_cancel") == "cancelled"
    assert status_for_cause("system_abort") == "aborted"
    assert status_for_cause("policy_abort") == "aborted"
    assert status_for_cause("host_interrupted") == "interrupted"
    for cause in ("tool_error", "model_error", "timeout", "send_error",
                  "host_process_death", "unclassified_error"):
        assert status_for_cause(cause) == "failed"
    # fail-closed：未知/空 cause 一律失败族，绝不伪装 completed。
    assert status_for_cause("bogus") == "failed"
    assert status_for_cause("") == "failed"
    assert settle_class_for_status("completed") == "clean"
    assert settle_class_for_status("refused") == "clean"
    assert settle_class_for_status("cancelled") == "cancelled"
    assert settle_class_for_status("aborted") == "aborted"
    assert settle_class_for_status("failed") == "failed"
    assert settle_class_for_status("interrupted") == "failed"


def test_pi_signal_mapping_parity_with_bridge_table():
    """kernel cause 单表 ≡ F03 ``_hk_turn_status`` 优先级（逐位）。"""
    from app.agent_pi_bridge import _hk_turn_status

    for kw in [
        dict(cancelled=True, timed_out=False, send_failed=False, process_died=False),
        dict(cancelled=False, timed_out=True, send_failed=False, process_died=False),
        dict(cancelled=False, timed_out=False, send_failed=True, process_died=False),
        dict(cancelled=False, timed_out=False, send_failed=False, process_died=True),
        dict(cancelled=False, timed_out=False, send_failed=False, process_died=False, error=True),
        dict(cancelled=False, timed_out=False, send_failed=False, process_died=False),
        dict(cancelled=False, timed_out=False, send_failed=False, process_died=False, abort_source="user"),
        dict(cancelled=False, timed_out=False, send_failed=False, process_died=False, abort_source="system"),
        dict(cancelled=False, timed_out=False, send_failed=False, process_died=False, abort_source="policy"),
        dict(cancelled=False, timed_out=True, send_failed=True, process_died=True),  # 取消/abort 优先
        dict(cancelled=True, timed_out=True, send_failed=True, process_died=True, abort_source="system"),
    ]:
        assert _hk_turn_status(**kw) == status_for_cause(
            cause_from_host_signals(**kw)
        ), kw


def test_legacy_outcome_cause_mapping():
    from enum import Enum

    class _Outcome(Enum):
        SUCCEEDED = 1
        CANCELLED = 2
        FAILED = 3

    def _ev(name, failure_class=None, detail=None):
        outcome = getattr(_Outcome, name) if name else None
        recorder = SimpleNamespace(outcome=outcome,
                                   failure_class=failure_class, detail=detail)
        return SimpleNamespace(outcome=recorder)

    assert cause_from_legacy_outcome(_ev("SUCCEEDED")) == "success"
    assert cause_from_legacy_outcome(_ev("CANCELLED")) == "user_cancel"
    assert cause_from_legacy_outcome(_ev("FAILED", "turn_timeout")) == "timeout"
    assert cause_from_legacy_outcome(_ev("FAILED", "max_rounds")) == "timeout"
    assert cause_from_legacy_outcome(_ev("FAILED", "empty_result")) == "model_error"
    assert cause_from_legacy_outcome(_ev("FAILED", "no_progress")) == "tool_error"
    assert cause_from_legacy_outcome(_ev("FAILED", "chat_stream_exception")) == "send_error"
    assert cause_from_legacy_outcome(_ev("FAILED", "KeyError")) == "unclassified_error"
    # 未结算 → interrupted 语义
    assert cause_from_legacy_outcome(_ev(None)) == "host_interrupted"
    assert cause_from_legacy_outcome(SimpleNamespace(outcome=None)) == "host_interrupted"


# ── clean / reduced 效果序 ────────────────────────────────────────────────


async def test_clean_settle_runs_full_effect_sequence(_isolated_effects):
    sid, turn = _sid(), "t-clean-1"
    await _begin(sid, turn)
    _isolated_effects.results["final_gate"] = SimpleNamespace(
        status="complete", repairs_applied=False,
    )
    out = await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, host="pi", cause="success"))
    assert out.disposition == "settled"
    assert out.kernel_status == "completed"
    assert out.settle_class == "clean"
    assert out.map_product == {"task_complete": True, "source": "finalized"}
    # E1..E6 → K → P，全部 applied（真实写入）
    assert [r.effect for r in out.receipts] == list(DISCLOSURE_EFFECTS) + [
        "kernel_end_turn", "lifecycle_parity",
    ]
    assert all(r.status == "applied" for r in out.receipts)
    # 顺序即权威序（final gate 先于投影，投影先于披露，链最后）
    assert _isolated_effects.calls == [
        "final_gate", "workflow", "runtime_state", "checkpoint",
        "payload", "chain_emit", "chain_persist",
    ]
    assert await _status_of(sid, turn) == "completed"


async def test_reduced_settle_skips_final_gate_and_disclosure(_isolated_effects):
    sid, turn = _sid(), "t-cancel-1"
    await _begin(sid, turn)
    out = await settle_turn(HarnessSettleInput(
        session_id=sid, turn_id=turn, host="pi", cause="user_cancel",
    ))
    assert out.kernel_status == "cancelled"
    assert out.settle_class == "cancelled"
    assert out.map_product is None  # 完成度奖励不属于失败/取消终态
    by_effect = {r.effect: r.status for r in out.receipts}
    assert by_effect["final_gate"] == "skipped"
    assert by_effect["map_product_disclosure"] == "skipped"
    for e in ("workflow_projection", "runtime_state_projection",
              "context_checkpoint", "evidence_chain", "kernel_end_turn"):
        assert by_effect[e] == "applied", e
    assert "final_gate" not in _isolated_effects.calls
    assert "payload" not in _isolated_effects.calls
    assert await _status_of(sid, turn) == "cancelled"


@pytest.mark.parametrize("cause", ["timeout", "tool_error", "unclassified_error",
                                   "system_abort", "policy_abort", "host_process_death"])
async def test_failure_family_reduced_settle_matrix(_isolated_effects, cause):
    sid, turn = _sid(), f"t-{cause}"
    await _begin(sid, turn)
    out = await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause=cause))
    assert out.kernel_status == status_for_cause(cause)
    assert out.settle_class in ("failed", "aborted")
    assert out.map_product is None
    by_effect = {r.effect: r.status for r in out.receipts}
    assert by_effect["map_product_disclosure"] == "skipped"
    assert by_effect["evidence_chain"] == "applied"  # 错误同样有可回放证据


async def test_finalizer_crash_no_longer_cascades(_isolated_effects):
    """recon H1 修正被钉死：终验抛错不再连带跳过 workflow/state/checkpoint。

    review P1-1：终验抛错同时不得触发 E5 的 read_stored 兜底披露——失败的
    终验不能给链上 task_complete=True 的假象（旧实现此时披露恒 None）。
    """
    _isolated_effects.failures["final_gate"] = RuntimeError("finalizer boom")
    sid, turn = _sid(), "t-fx-1"
    await _begin(sid, turn)
    out = await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause="success"))
    by_effect = {r.effect: (r.status, r.detail) for r in out.receipts}
    assert by_effect["final_gate"][0] == "failed"
    assert "finalizer boom" in by_effect["final_gate"][1]
    for e in ("workflow_projection", "runtime_state_projection",
              "context_checkpoint", "evidence_chain"):
        assert by_effect[e][0] == "applied", e
    assert by_effect["kernel_end_turn"][0] == "applied"
    assert out.kernel_status == "completed"
    # P1-1：无兜底披露、无 map_product、链上谓词恒 False
    assert by_effect["map_product_disclosure"][0] == "skipped"
    assert "final gate failed" in by_effect["map_product_disclosure"][1]
    assert out.map_product is None
    assert "stored_product" not in _isolated_effects.calls
    assert "payload" not in _isolated_effects.calls


async def test_pending_completion_discloses_nothing(_isolated_effects):
    """终验 pending（无进展/未完成）→ 不披露 map_product（诚实状态）。"""
    _isolated_effects.results["final_gate"] = SimpleNamespace(
        status="pending", repairs_applied=False,
    )
    sid, turn = _sid(), "t-pending-1"
    await _begin(sid, turn)
    out = await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause="success"))
    assert out.map_product is None
    assert "payload" not in _isolated_effects.calls
    assert "stored_product" not in _isolated_effects.calls


async def test_gate_skip_falls_back_to_stored_product(_isolated_effects):
    """幂等门跳过（completion None）→ 披露已存储完成态（MapSpec revision
    前进后重验属 pipeline 域；coordinator 契约 = 跳过时兜底披露）。"""
    sid, turn = _sid(), "t-idem-1"
    await _begin(sid, turn)
    out = await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause="success"))
    assert out.map_product == {"task_complete": True, "source": "stored"}


# ── 幂等 / 并发 / stale ──────────────────────────────────────────────────


async def test_duplicate_settle_replays_receipt_without_effects(_isolated_effects):
    sid, turn = _sid(), "t-dup-1"
    await _begin(sid, turn)
    first = await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause="success"))
    n_effects = len(_isolated_effects.calls)
    second = await settle_turn(HarnessSettleInput(
        session_id=sid, turn_id=turn, cause="user_cancel",  # 迟到且异语义的重复
    ))
    assert second.disposition == "duplicate"
    assert second.kernel_status == first.kernel_status  # 重放同一收据，不改写
    assert second.receipt_line() == first.receipt_line()
    assert len(_isolated_effects.calls) == n_effects  # 零重跑
    assert await _status_of(sid, turn) == "completed"


async def test_concurrent_settle_serialized_exactly_once(_isolated_effects):
    sid, turn = _sid(), "t-conc-1"
    await _begin(sid, turn)
    outs = await asyncio.gather(*[
        settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause="success"))
        for _ in range(4)
    ])
    dispositions = sorted(o.disposition for o in outs)
    assert dispositions.count("settled") == 1
    assert dispositions.count("duplicate") == 3
    assert _isolated_effects.calls.count("final_gate") == 1
    assert _isolated_effects.calls.count("chain_persist") == 1


async def test_late_settle_after_successor_terminalization_is_stale(_isolated_effects):
    """turn N 的 settle 在 turn N+1 begin_turn（把 N 终态化为 interrupted）
    之后迟到 → stale：零效果、不改写台账、不污染在飞的新 turn。

    review P1-2：stale 的 kernel_status 保持 host 计算值（completed）——
    台账的 interrupted 不回灌 adapter 投影（tracker 不得因此按成功收口）；
    记录值只进收据。
    """
    sid = _sid()
    await _begin(sid, "t-late-n")
    await _begin(sid, "t-late-n1")  # 后继开始 → N 被终态化为 interrupted
    assert await _status_of(sid, "t-late-n") == "interrupted"
    n_effects = len(_isolated_effects.calls)
    out = await settle_turn(HarnessSettleInput(
        session_id=sid, turn_id="t-late-n", cause="success",
    ))
    assert out.disposition == "stale"
    assert out.kernel_status == "completed"  # host 计算值，不回灌 interrupted
    snap_receipt = out.receipts[0]
    assert snap_receipt.detail == "terminal=interrupted"  # 台账真相进收据
    assert len(_isolated_effects.calls) == n_effects  # 零效果（含终验）
    assert await _status_of(sid, "t-late-n") == "interrupted"
    # 在飞的新 turn 不受影响
    assert await _status_of(sid, "t-late-n1") == "running"


async def test_already_settled_same_status_is_duplicate_not_stale(_isolated_effects):
    sid, turn = _sid(), "t-dup2-1"
    await _begin(sid, turn)
    await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause="timeout"))
    out = await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause="timeout"))
    assert out.disposition == "duplicate"
    assert out.kernel_status == "failed"


# ── legacy 预终态化（pre_terminated）──────────────────────────────────────


async def test_pre_terminated_runs_effects_end_turn_receipt_honest(_isolated_effects):
    """legacy：锁内 safe_end_turn 已写终态 → 锁外 settle 效果照跑、
    K 腿按实际写入记 already（预终态化成功 → no-op）。"""
    sid, turn = _sid("s-legacy"), "t-pre-1"
    await _begin(sid, turn)
    # 引擎锁内预终态化（真实 kernel 写入）
    await get_runtime(sid).end_turn(turn, host="chatengine", status="completed")
    out = await settle_turn(HarnessSettleInput(
        session_id=sid, turn_id=turn, host="chatengine", cause="success",
        pre_terminated=True,
    ))
    assert out.disposition == "settled"
    assert out.kernel_status == "completed"
    by_effect = {r.effect: r.status for r in out.receipts}
    assert by_effect["final_gate"] == "applied"      # legacy 此前缺失的效果补齐
    assert by_effect["evidence_chain"] == "applied"
    assert by_effect["kernel_end_turn"] == "already"  # 台账已终态，诚实 no-op
    assert out.map_product is not None


async def test_pre_terminated_recovers_when_adapter_write_failed(_isolated_effects):
    """引擎锁内预终态化失败（turn 仍 running）→ coordinator 的 K 腿是恢复
    写入：收据必须记 applied（不是 already）。"""
    sid, turn = _sid("s-legacy"), "t-pre-fx"
    await _begin(sid, turn)
    # 模拟 safe_end_turn 超时失败：turn 仍 running
    out = await settle_turn(HarnessSettleInput(
        session_id=sid, turn_id=turn, host="chatengine", cause="user_cancel",
        pre_terminated=True,
    ))
    assert out.disposition == "settled"
    by_effect = {r.effect: r.status for r in out.receipts}
    assert by_effect["kernel_end_turn"] == "applied"
    assert await _status_of(sid, turn) == "cancelled"


async def test_pre_terminated_no_refusal_downgrade(_isolated_effects, monkeypatch):
    """pre_terminated 的终态已由引擎写定（first-wins），降级窗口关闭。"""
    async def _refusal(sid, turn_id):
        return {"reason_code": "clarification_required", "question": "q?"}
    monkeypatch.setattr(hks, "resolve_turn_refusal", _refusal)
    s = _sid("s-legacy")
    await _begin(s, "t-pre-ref")
    await get_runtime(s).end_turn("t-pre-ref", host="chatengine", status="completed")
    out = await settle_turn(HarnessSettleInput(
        session_id=s, turn_id="t-pre-ref", host="chatengine",
        cause="success", pre_terminated=True,
    ))
    assert out.kernel_status == "completed"
    assert out.refusal is None


async def test_pre_terminated_successor_in_flight_skips_session_effects(_isolated_effects):
    """锁释放→效果取锁的窗口内后继 turn 已在飞：会话级效果跳过（评估后继
    中间状态即污染），收据如实记录；台账不被改写。"""
    sid = _sid("s-legacy")
    await _begin(sid, "t-n")
    await get_runtime(sid).end_turn("t-n", host="chatengine", status="completed")
    # 后继在 N 的锁外 settle 之前已在飞
    await _begin(sid, "t-n1")
    assert await _status_of(sid, "t-n1") == "running"
    out = await settle_turn(HarnessSettleInput(
        session_id=sid, turn_id="t-n", host="chatengine",
        cause="success", pre_terminated=True,
    ))
    assert out.disposition == "settled"
    by_effect = {r.effect: r.status for r in out.receipts}
    for e in DISCLOSURE_EFFECTS:
        assert by_effect[e] == "skipped", e
    assert _isolated_effects.calls == []  # 零会话级效果
    assert by_effect["kernel_end_turn"] == "already"
    assert await _status_of(sid, "t-n") == "completed"
    assert await _status_of(sid, "t-n1") == "running"


# ── refusal / unknown / fail-closed / kill switch ─────────────────────────


async def test_refusal_downgrade_clean_only(_isolated_effects, monkeypatch):
    asked = []

    async def _refusal(sid, turn_id):
        asked.append(turn_id)
        return {"reason_code": "clarification_required", "question": "which city?"}
    monkeypatch.setattr(hks, "resolve_turn_refusal", _refusal)

    s = _sid()
    await _begin(s, "t-ref-1")
    out = await settle_turn(HarnessSettleInput(session_id=s, turn_id="t-ref-1", cause="success"))
    assert out.kernel_status == "refused"
    assert out.settle_class == "clean"
    assert out.refusal["question"] == "which city?"
    assert out.map_product is not None  # clean 语义的效果照跑
    assert await _status_of(s, "t-ref-1") == "refused"

    # 失败族永不降级
    await _begin(s, "t-ref-2")
    out2 = await settle_turn(HarnessSettleInput(session_id=s, turn_id="t-ref-2", cause="timeout"))
    assert out2.kernel_status == "failed"
    assert out2.refusal is None
    assert asked == ["t-ref-1"]


async def test_unknown_turn_zero_effects_not_cached(_isolated_effects):
    sid = _sid()
    out = await settle_turn(HarnessSettleInput(
        session_id=sid, turn_id="t-never-began", cause="success",
    ))
    assert out.disposition == "unknown_turn"
    assert _isolated_effects.calls == []
    # 未缓存：turn 稍后真实 begin 后仍可正规结算
    await _begin(sid, "t-never-began")
    out2 = await settle_turn(HarnessSettleInput(
        session_id=sid, turn_id="t-never-began", cause="success",
    ))
    assert out2.disposition == "settled"


async def test_snapshot_read_failure_fail_closed_and_retryable(_isolated_effects, monkeypatch):
    async def _boom(sid, turn_id):
        raise RuntimeError("store down")
    monkeypatch.setattr(hks, "read_settle_snapshot", _boom)
    sid, turn = _sid(), "t-snapfx"
    await _begin(sid, turn)
    out = await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause="success"))
    assert out.disposition == "unknown_turn"
    assert _isolated_effects.calls == []  # fail-closed：零效果
    # 恢复后重试可正规结算（未缓存坏结局）
    async def _real(session_id, turn_id):
        from app.services.harness_kernel.runtime import get_runtime
        return await get_runtime(session_id).lifecycle_parity_snapshot(turn_id)
    monkeypatch.setattr(hks, "read_settle_snapshot", _real)
    out2 = await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause="success"))
    assert out2.disposition == "settled"


async def test_kill_switch_off_still_terminalizes(_isolated_effects, monkeypatch):
    monkeypatch.setenv("GIS_HOST_NEUTRAL_SETTLE", "0")
    try:
        sid, turn = _sid(), "t-kill-1"
        await _begin(sid, turn)
        out = await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause="timeout"))
        by_effect = {r.effect: r.status for r in out.receipts}
        for e in DISCLOSURE_EFFECTS:
            assert by_effect[e] == "skipped", e
        assert _isolated_effects.calls == []
        # authority 腿不受开关影响
        assert by_effect["kernel_end_turn"] == "applied"
        assert await _status_of(sid, turn) == "failed"
    finally:
        monkeypatch.delenv("GIS_HOST_NEUTRAL_SETTLE", raising=False)


async def test_effect_failure_receipts_are_honest(_isolated_effects):
    _isolated_effects.failures["workflow"] = RuntimeError("wf down")
    _isolated_effects.failures["chain_persist"] = RuntimeError("trace down")
    sid, turn = _sid(), "t-legfx-1"
    await _begin(sid, turn)
    out = await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, cause="success"))
    by_effect = {r.effect: r.status for r in out.receipts}
    assert by_effect["workflow_projection"] == "failed"
    assert by_effect["evidence_chain"] == "failed"
    assert by_effect["runtime_state_projection"] == "applied"
    assert by_effect["kernel_end_turn"] == "applied"
    assert out.disposition == "settled"  # 单段失败不推翻结算
    assert await _status_of(sid, turn) == "completed"


def test_input_bounds_truncate():
    inp = HarnessSettleInput(
        session_id="s", turn_id="t",
        failure_class="x" * 300, failure_detail="y" * 1000,
    )
    assert len(inp.failure_class) == 64
    assert len(inp.failure_detail) == 200


def test_turn_lock_registry_bounded():
    """_TURN_LOCKS 有界性（review P1-3）：空闲锁逐出，注册表不超过上限。"""
    hks.reset_for_tests()
    try:
        for i in range(hks._TURN_LOCKS_MAX + 50):
            hks._lock_for(f"s-bounded-{i}:t")
        assert len(hks._TURN_LOCKS) <= hks._TURN_LOCKS_MAX
    finally:
        hks.reset_for_tests()


async def test_receipt_and_disposition_vocab_closed(_isolated_effects):
    """收据状态/处置词表封闭性（ADR-0223 Acceptance；review P2-3）——
    跑一组矩阵后所有收据/处置值都必须落在封闭词表内。"""
    sid = _sid()
    for cause in ("success", "user_cancel", "timeout", "unclassified_error"):
        turn = f"t-vocab-{cause}"
        await _begin(sid, turn)
        out = await settle_turn(HarnessSettleInput(
            session_id=sid, turn_id=turn, cause=cause,
        ))
        assert out.disposition in hks._DISPOSITIONS
        for r in out.receipts:
            assert r.status in hks._RECEIPT_OK, (cause, r)
    # duplicate 重放同样在词表内且计入观测（review P2-2）
    replay = await settle_turn(HarnessSettleInput(
        session_id=sid, turn_id="t-vocab-success", cause="success",
    ))
    assert replay.disposition == "duplicate"


async def test_metrics_dimensions(_isolated_effects):
    from app.services.harness_kernel import metrics as hk_metrics
    hk_metrics.reset_for_tests()
    sid, turn = _sid(), "t-metric-1"
    await _begin(sid, turn)
    await settle_turn(HarnessSettleInput(session_id=sid, turn_id=turn, host="pi", cause="success"))
    assert hk_metrics.get_counter(
        "turn_settled|disposition=settled|host=pi|status=completed"
    ) == 1
    assert hk_metrics.get_counter("turn_ended|host=pi|status=completed") == 1


# ── legacy 引擎接线（chat / chat_stream 真实触发锁外 settle）────────────


def _make_engine(monkeypatch):
    from app.services.chat_engine import ChatEngine
    from app.tools.registry import ToolRegistry
    from app.services.subagent import SubagentDispatcher  # noqa: F401

    registry = ToolRegistry()

    @registry.tool(name="echo_tool", description="echo", tier=1)
    def echo_tool(text: str = "") -> dict:
        return {"echo": text, "success": True}

    engine = ChatEngine(registry)

    async def _fake_get_or_create_session(session_id, user_id=None):
        return [{"role": "system", "content": "sys"}]

    monkeypatch.setattr(engine, "_get_or_create_session", _fake_get_or_create_session)
    monkeypatch.setattr(engine, "_save_msg_async", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "_maybe_plan", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "_generate_title", AsyncMock(return_value=None))
    monkeypatch.setattr(engine, "_persist_map_state", AsyncMock())
    return engine


async def test_legacy_chat_triggers_post_lock_settle(_isolated_effects, monkeypatch):
    """非流式 legacy turn：锁内 kernel 预终态化 + 锁外同一 coordinator 效果
    （ADR-0223 D4 —— legacy 与 Pi 共享单一 settle 编排）。"""
    engine = _make_engine(monkeypatch)
    resp = {"choices": [{"message": {"content": "done", "tool_calls": None}}]}
    monkeypatch.setattr(engine, "_call_llm", AsyncMock(return_value=resp))

    settle_classes: list[str] = []
    real_effects = hks.run_settle_effects

    async def _spy(sid, turn_id, settle_class, **kw):
        settle_classes.append((sid, turn_id, settle_class))
        return await real_effects(sid, turn_id, settle_class, **kw)
    monkeypatch.setattr(hks, "run_settle_effects", _spy)

    sid = _sid("s-engine")
    result = await engine.chat("hi", session_id=sid)
    assert result
    # 恰好一次 clean 效果序（引擎 turn 成功 → success cause）
    assert settle_classes and settle_classes[-1][2] == "clean"
    # kernel 台账终态为 completed（引擎锁内预终态化）
    turn_ids = [t for s, t, c in settle_classes]
    assert await _status_of(sid, turn_ids[-1]) == "completed"


async def test_legacy_stream_triggers_post_lock_settle(_isolated_effects, monkeypatch):
    engine = _make_engine(monkeypatch)

    async def fake_stream(*a, **k):
        yield ("done", {"message": {"content": "done", "tool_calls": None}})
    monkeypatch.setattr(engine, "_call_llm_stream", fake_stream)

    settle_classes: list[str] = []
    real_effects = hks.run_settle_effects

    async def _spy(sid, turn_id, settle_class, **kw):
        settle_classes.append((sid, turn_id, settle_class))
        return await real_effects(sid, turn_id, settle_class, **kw)
    monkeypatch.setattr(hks, "run_settle_effects", _spy)

    sid = _sid("s-engine-st")
    events = [e async for e in engine.chat_stream("hi", session_id=sid)]
    assert any("task_complete" in str(e) or "done" in str(e) for e in events)
    assert settle_classes and settle_classes[-1][2] == "clean"
    turn_ids = [t for s, t, c in settle_classes]
    assert await _status_of(sid, turn_ids[-1]) == "completed"


async def test_legacy_failed_turn_settles_reduced(_isolated_effects, monkeypatch):
    """legacy 失败 turn（空补全 → empty_result）→ 锁外 reduced settle。"""
    engine = _make_engine(monkeypatch)
    empty = {"choices": [{"message": {"content": "", "tool_calls": None}}]}
    monkeypatch.setattr(engine, "_call_llm", AsyncMock(return_value=empty))

    settle_classes: list[str] = []
    real_effects = hks.run_settle_effects

    async def _spy(sid, turn_id, settle_class, **kw):
        settle_classes.append((sid, turn_id, settle_class))
        return await real_effects(sid, turn_id, settle_class, **kw)
    monkeypatch.setattr(hks, "run_settle_effects", _spy)

    sid = _sid("s-engine-fx")
    with pytest.raises(RuntimeError, match="empty completion"):
        await engine.chat("hi", session_id=sid)
    assert settle_classes and settle_classes[-1][2] == "failed"
    assert "final_gate" not in _isolated_effects.calls  # reduced：无终验
    assert "workflow" in _isolated_effects.calls        # 但投影/链照常收口
    turn_ids = [t for s, t, c in settle_classes]
    assert await _status_of(sid, turn_ids[-1]) == "failed"
