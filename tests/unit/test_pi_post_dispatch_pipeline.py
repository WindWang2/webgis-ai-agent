"""方向 09（ADR-0210）：Pi 后置披露管线 typed 单元测试。

验证 ``app/services/chat/pi_post_dispatch.py`` 的阶段顺序 / never-raise 纪律 /
stale 世代诚实短路 / 迟到回调跳过语义 / turn 结算管线幂等披露。

Patch 面契约：harness 访问器经 ``app.agent_pi_bridge`` 命名空间惰性解析
（既有测试的 patch 面，刻意保持）；其余依赖按各自 canonical 模块 patch。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.agent_pi_bridge as bridge_mod
from app.services.chat.pi_post_dispatch import (
    DispatchDisclosure,
    apply_post_dispatch_disclosure,
    settle_turn_projections,
)


def _disclosure(**over) -> DispatchDisclosure:
    base = dict(
        session_id="sess-pd",
        tool_call_id="tc-1",
        tool_name="webgis_execute",
        arguments={"a": 1},
        status="ok",
        raw_result={"summary": "done"},
        llm_payload="done",
        geojson_ref="",
        map_actions=(),
        turn_id="turn-cb",
        active_turn_id="turn-cb",
        late_for_plan=False,
        duration_ms=12,
    )
    base.update(over)
    return DispatchDisclosure(**base)


class FakeRuntime:
    """kernel runtime替身：记录 apply_tool_evidence 调用。"""

    def __init__(self, fail_first_with=None, order=None):
        self.calls = []
        self._fail_first_with = fail_first_with
        self._order = order if order is not None else []

    async def apply_tool_evidence(self, tool_name, raw_result, **kw):
        self.calls.append({"tool": tool_name, "raw": raw_result, **kw})
        self._order.append("plan_evidence")
        if self._fail_first_with is not None and len(self.calls) == 1:
            raise self._fail_first_with
        return [{"type": "plan_row"}] if kw.get("success") else [{"type": "failed_row"}]


class FakeHarness:
    def __init__(self, order=None):
        self.events = []
        self.map_actions = []
        self._order = order if order is not None else []

    def record_event(self, ev):
        self.events.append(ev)
        self._order.append("cartography_event")

    def record_map_action_issued(self, **kw):
        self.map_actions.append(kw)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """统一 patch：kernel/plan/投影/终验/链 全部替身化（隔离 + 观测）。"""
    state = SimpleNamespace(
        runtime=None,
        harness=None,
        projections=[],
        state_updates=[],
        workflow_updates=[],
        finalize_calls=[],
        finalize_result=None,
        stored_product=None,
        chain_emits=[],
        persists=[],
        checkpoints=[],
        stages=[],
        persist_context_results={True},
        order=[],
    )
    state.runtime = FakeRuntime(order=state.order)
    state.harness = FakeHarness(order=state.order)

    import app.services.harness_kernel as hk
    monkeypatch.setattr(hk, "get_runtime", lambda sid: state.runtime)

    import app.services.session_plan as sp
    monkeypatch.setattr(sp, "events_to_sse", lambda events, sid: f"SSE:{len(events)}")

    import app.services.gis_harness.workflow_instance as wfi
    async def _wfi(sid, *, reason, event):
        state.order.append("workflow")
        state.workflow_updates.append((sid, reason, event))
    monkeypatch.setattr(wfi, "maybe_update_workflow_instance", _wfi)

    import app.services.gis_harness.runtime_state_machine as rsm
    async def _rts(sid, *, reason, trigger, turn_settled=False):
        state.order.append("state")
        state.state_updates.append((sid, reason, trigger, turn_settled))
    monkeypatch.setattr(rsm, "maybe_update_runtime_state", _rts)
    monkeypatch.setattr(rsm, "runtime_state_enabled", lambda: True)

    import app.services.gis_harness.runtime_bridge as rb
    async def _rbp(sid, *, reason):
        state.order.append("projection")
        state.projections.append((sid, reason))
    monkeypatch.setattr(rb, "maybe_update_runtime_projection", _rbp)

    import app.services.gis_harness.map_completion as mc
    async def _finalize(sid, *, reason, final_gate=False):
        state.order.append("finalize")
        state.finalize_calls.append((sid, reason, final_gate))
        return state.finalize_result
    monkeypatch.setattr(mc, "maybe_finalize_map_product", _finalize)
    monkeypatch.setattr(mc, "finalization_sse_payload",
                        lambda completion, sid, **kw: {"task_complete": True, "sid": sid})
    async def _snap(sid):
        return ({"spec": 1}, 7)
    monkeypatch.setattr(mc, "current_mapspec_for_disclosure", _snap)
    async def _stored(sid):
        return state.stored_product
    monkeypatch.setattr(mc, "read_stored_map_product", _stored)

    import app.services.gis_harness.context_layers as cl
    async def _ckpt(sid):
        state.checkpoints.append(sid)
    monkeypatch.setattr(cl, "checkpoint_context_layers", _ckpt)

    import app.lib.runtime.chain_emitters as ce
    def _emit(turn_id, stage, **kw):
        state.chain_emits.append((turn_id, kw))
    monkeypatch.setattr(ce, "emit_chain_for", _emit)

    import app.services.gis_harness.trace_store as ts
    def _persist(turn_id, *, session_id=""):
        state.persists.append((turn_id, session_id))
    monkeypatch.setattr(ts, "persist_turn_chain", _persist)

    import app.lib.runtime.gis_trace as gt
    def _stage(turn_id, stage, **kw):
        state.order.append("trace")
        state.stages.append((turn_id, stage, kw))
    monkeypatch.setattr(gt, "record_stage", _stage)

    # cartography harness 访问器：经 bridge 命名空间（rendezvous patch 面）。
    async def _persist_ctx_simple(sid, event, map_actions):
        return next(iter(state.persist_context_results))
    monkeypatch.setattr(bridge_mod, "_persist_cartographic_harness_context", _persist_ctx_simple)
    monkeypatch.setattr(bridge_mod, "_get_session_harness", lambda sid, create=False: state.harness)
    async def _evaluate(sid):
        return None
    monkeypatch.setattr(bridge_mod, "evaluate_cartographic_session", _evaluate)

    import app.services.cartography_runtime as cr
    monkeypatch.setattr(cr, "result_indicates_map_change", lambda raw: False)

    return state


# ── dispatch 后置披露 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ok_status_runs_full_disclosure_in_order(_env):
    _env.finalize_result = SimpleNamespace(status="completed", repairs_applied=False)
    out = await apply_post_dispatch_disclosure(_disclosure())

    assert out.cache_plan_sse_unconditionally is True
    assert out.plan_sse == "SSE:1"
    assert out.finalization_payload == {"task_complete": True, "sid": "sess-pd"}
    assert out.stale_generation is False

    # kernel 证据：verifiedTurnId 优先 + success=True
    assert _env.runtime.calls[0]["success"] is True
    assert _env.runtime.calls[0]["turn_id"] == "turn-cb"
    # 投影：workflow(auto) + state(execution_progressed) + projection
    assert _env.workflow_updates[0][2] == "auto"
    assert any(t == "execution_progressed" for _, _, t, _ in _env.state_updates)
    assert _env.projections[0][0] == "sess-pd"
    # cartography 证据 + 评估
    assert len(_env.harness.events) == 1
    # 证据链三阶段（无 map_actions → 无 MAP_MUTATIONS）
    assert len(_env.stages) == 3


@pytest.mark.asyncio
async def test_error_status_marks_failure_without_state_machine(_env):
    out = await apply_post_dispatch_disclosure(_disclosure(status="error", llm_payload="boom"))

    assert _env.runtime.calls[0]["success"] is False
    # 既有不对称（显式化）：error 不推进 runtime_state_machine、不做终验
    assert _env.state_updates == []
    assert _env.finalize_calls == []
    assert _env.workflow_updates[0][2] == "tool_failure"
    assert _env.projections  # 投影仍推进
    assert out.finalization_payload is None
    # error 时不发 issued map 动作
    assert _env.harness.map_actions == []


@pytest.mark.asyncio
async def test_lock_contention_retries_once(_env):
    _env.runtime._fail_first_with = asyncio.TimeoutError()
    out = await apply_post_dispatch_disclosure(_disclosure())
    assert len(_env.runtime.calls) == 2  # 首次超时 + 重试一次
    assert out.plan_sse == "SSE:1"


@pytest.mark.asyncio
async def test_apply_failure_never_raises(_env):
    _env.runtime._fail_first_with = RuntimeError("kernel down")
    out = await apply_post_dispatch_disclosure(_disclosure())
    assert len(_env.runtime.calls) == 1
    assert out.plan_sse == ""


@pytest.mark.asyncio
async def test_stale_generation_short_circuits_trace(_env):
    _env.persist_context_results = {False}  # 旧 MapSpec 世代
    _env.finalize_result = SimpleNamespace(status="completed", repairs_applied=False)
    out = await apply_post_dispatch_disclosure(
        _disclosure(raw_result={"mapspec_fingerprint": "fp-1"})
    )
    assert out.stale_generation is True
    # 诚实短路：证据链不再记录（既有行为）
    assert _env.stages == []
    # cartography 事件未入 harness（跳过本帧上下文）
    assert _env.harness.events == []


@pytest.mark.asyncio
async def test_lock_degraded_maps_to_stale_not_500(_env, monkeypatch):
    async def _lock_broken(sid, event, map_actions):
        raise bridge_mod.LockDegradedError("degraded")
    monkeypatch.setattr(bridge_mod, "_persist_cartographic_harness_context", _lock_broken)
    out = await apply_post_dispatch_disclosure(
        _disclosure(raw_result={"mapspec_fingerprint": "fp-1"})
    )
    assert out.stale_generation is True


@pytest.mark.asyncio
async def test_late_callback_skips_plan_and_projections_but_keeps_cartography(_env):
    out = await apply_post_dispatch_disclosure(
        _disclosure(late_for_plan=True, turn_id="turn-old", active_turn_id="turn-new")
    )
    assert _env.runtime.calls == []
    assert _env.workflow_updates == []
    assert _env.finalize_calls == []
    # cartography 证据照常（在 ok/error if 之外的既有行为）
    assert len(_env.harness.events) == 1
    assert _env.stages  # 证据链照常
    assert out.finalization_payload is None


@pytest.mark.asyncio
async def test_no_harness_still_records_trace(_env, monkeypatch):
    monkeypatch.setattr(bridge_mod, "_get_session_harness", lambda sid, create=False: None)
    out = await apply_post_dispatch_disclosure(_disclosure())
    assert out.stale_generation is False
    assert _env.stages


@pytest.mark.asyncio
async def test_ok_with_map_actions_records_issued_side(_env):
    _env.finalize_result = SimpleNamespace(status="pending", repairs_applied=False)
    ma = {"action_id": "a1", "command": "add_layer", "requested": {"x": 1}}
    out = await apply_post_dispatch_disclosure(_disclosure(map_actions=(ma,)))
    assert _env.harness.map_actions[0]["action_id"] == "a1"
    # pending 不披露
    assert out.finalization_payload is None
    # MAP_MUTATIONS 阶段入链
    assert len(_env.stages) == 4


@pytest.mark.asyncio
async def test_ok_disclosure_stage_order_is_pinned(_env):
    """review P1 #2 回归钉：终验先落 map_product，投影随后读取（基线顺序）。"""
    _env.finalize_result = SimpleNamespace(status="completed", repairs_applied=True)
    ma = {"action_id": "a1", "command": "add_layer", "requested": {}}
    await apply_post_dispatch_disclosure(_disclosure(map_actions=(ma,)))
    assert _env.order == [
        "plan_evidence",
        "finalize",
        "workflow",
        "state",
        "projection",
        "cartography_event",
        "trace", "trace", "trace", "trace",
    ]


@pytest.mark.asyncio
async def test_error_disclosure_stage_order_is_pinned(_env):
    _env.finalize_result = SimpleNamespace(status="completed", repairs_applied=False)
    await apply_post_dispatch_disclosure(_disclosure(status="error"))
    # error：无终验、无 state 推进（既有不对称显式化）
    assert _env.order == [
        "plan_evidence",
        "workflow",
        "projection",
        "cartography_event",
        "trace", "trace", "trace",
    ]


# ── turn 结算管线（stream / non-stream 共用）────────────────────────────


@pytest.mark.asyncio
async def test_settle_runs_full_pipeline_and_returns_payload(_env):
    _env.finalize_result = SimpleNamespace(status="completed", repairs_applied=True)
    payload = await settle_turn_projections("sess-s", "turn-1")

    assert _env.finalize_calls == [("sess-s", "turn_settled", True)]  # final gate
    assert payload == {"task_complete": True, "sid": "sess-s"}
    assert _env.state_updates[0][3] is True  # turn_settled 旗标
    assert _env.checkpoints == ["sess-s"]
    # 链：repair 改写 → 快照携带 spec+revision；task_complete=True 入链
    assert _env.chain_emits[0][0] == "turn-1"
    assert _env.chain_emits[0][1]["final_gate"] is True
    assert _env.chain_emits[0][1]["task_complete"] is True
    assert _env.persists == [("turn-1", "sess-s")]


@pytest.mark.asyncio
async def test_settle_idempotent_gate_discloses_stored_product(_env):
    # 幂等门跳过（finalize 返回 None）→ 披露已存储完成态
    _env.finalize_result = None
    _env.stored_product = {"task_complete": True}
    payload = await settle_turn_projections("sess-s", "turn-1")
    assert payload == {"task_complete": True}


@pytest.mark.asyncio
async def test_settle_pending_yields_no_payload_but_persists_chain(_env):
    _env.finalize_result = SimpleNamespace(status="pending", repairs_applied=False)
    payload = await settle_turn_projections("sess-s", "turn-1")
    assert payload is None
    assert _env.chain_emits[0][1]["task_complete"] is False
    assert _env.persists == [("turn-1", "sess-s")]


@pytest.mark.asyncio
async def test_settle_finalization_failure_does_not_block_chain(_env, monkeypatch):
    import app.services.gis_harness.map_completion as mc

    async def _boom(sid, *, reason, final_gate=False):
        raise RuntimeError("finalize down")
    monkeypatch.setattr(mc, "maybe_finalize_map_product", _boom)
    payload = await settle_turn_projections("sess-s", "turn-1")
    assert payload is None
    # 链持久化不受 finalization 失败影响
    assert _env.chain_emits and _env.persists
