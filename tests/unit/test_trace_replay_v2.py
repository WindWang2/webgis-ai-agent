"""ADR-0101 Wave 8: trace 事件模式 + replay harness 测试。"""
import pytest

from app.evaluation.replay import (
    ReplayEntry,
    ScriptedCall,
    check_trace_invariants,
    replay_tools,
    simulate_agent_loop,
)
from app.lib.runtime.trace import (
    EVENT_DISPATCH_COMPLETED,
    EVENT_DISPATCH_STARTED,
    EVENT_FALLBACK,
    EVENT_MODEL_SELECTED,
    EVENT_NO_PROGRESS,
    EVENT_TURN_SETTLED,
    TraceRegistry,
    bound_meta,
)
from app.tools.registry import ToolRegistry


@pytest.fixture()
def reg():
    registry = ToolRegistry()

    def deterministic_double(x: int) -> dict:
        return {"success": True, "value": x * 2}

    def read_only_scan(q: str) -> dict:
        return {"success": True, "hits": 3}

    def destructive_wipe(confirm: bool = False) -> dict:
        return {"success": True, "wiped": True}

    registry.register(name="double", description="翻倍", func=deterministic_double,
                      side_effect="deterministic_compute")
    registry.register(name="scan", description="扫描", func=read_only_scan,
                      side_effect="cacheable_read")
    registry.register(name="wipe", description="清空", func=destructive_wipe,
                      tier=3, side_effect="destructive")
    return registry


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

def test_trace_event_lifecycle_bounded():
    reg = TraceRegistry(max_turns=2, max_events_per_turn=8)
    reg.start_turn("t1", "s1")
    for i in range(12):
        reg.emit("t1", EVENT_DISPATCH_STARTED, tool=f"tool{i}", tool_call_id=f"c{i}")
        reg.emit("t1", EVENT_DISPATCH_COMPLETED, tool=f"tool{i}", tool_call_id=f"c{i}", status="ok")
    trace = reg.get("t1")
    assert len(trace.events) <= 8
    assert trace.dropped_count > 0


def test_trace_registry_bounded_lru():
    reg = TraceRegistry(max_turns=3)
    for i in range(6):
        reg.start_turn(f"t{i}", "s")
    assert reg.stats()["turns"] <= 3
    assert reg.get("t0") is None
    assert reg.get("t5") is not None


def test_trace_never_records_secrets():
    meta = bound_meta({"api_key": "sk-live-xyz", "Authorization": "Bearer x",
                       "model": "m", "detail": "x" * 2000})
    assert meta["api_key"] == "[REDACTED]"
    assert meta["Authorization"] == "[REDACTED]"
    assert meta["model"] == "m"
    assert len(meta["detail"]) < 600


def test_trace_unknown_kind_rejected():
    reg = TraceRegistry()
    reg.emit("t1", "freeform_event_kind", data="x")
    assert reg.get("t1") is None or "freeform_event_kind" not in (reg.get("t1").kinds())


def test_trace_settled_flag_once():
    reg = TraceRegistry()
    reg.start_turn("t1")
    reg.emit("t1", EVENT_TURN_SETTLED)
    assert reg.get("t1").settled is True


# ---------------------------------------------------------------------------
# Trace invariants
# ---------------------------------------------------------------------------

def _make_registry():
    return TraceRegistry()


def test_trace_invariants_detect_orphan_completed():
    reg = _make_registry()
    reg.start_turn("t1")
    reg.emit("t1", EVENT_DISPATCH_COMPLETED, tool="x", tool_call_id="c1", status="ok")
    reg.emit("t1", EVENT_TURN_SETTLED)
    violations = check_trace_invariants(reg.get("t1"))
    assert any("without dispatch_started" in v for v in violations)


def test_trace_invariants_clean_turn():
    reg = _make_registry()
    reg.start_turn("t1")
    reg.emit("t1", EVENT_MODEL_SELECTED, model="m")
    reg.emit("t1", EVENT_DISPATCH_STARTED, tool="x", tool_call_id="c1")
    reg.emit("t1", EVENT_DISPATCH_COMPLETED, tool="x", tool_call_id="c1", status="ok")
    reg.emit("t1", EVENT_TURN_SETTLED)
    assert check_trace_invariants(reg.get("t1")) == []


def test_trace_invariants_double_settle():
    reg = _make_registry()
    reg.start_turn("t1")
    reg.emit("t1", EVENT_TURN_SETTLED)
    reg.emit("t1", EVENT_TURN_SETTLED)
    assert any("2 times" in v for v in check_trace_invariants(reg.get("t1")))


def test_trace_invariants_fallback_before_model_selected():
    reg = _make_registry()
    reg.start_turn("t1")
    reg.emit("t1", EVENT_FALLBACK, model="m2", failure="timeout")
    assert any("fallback before" in v for v in check_trace_invariants(reg.get("t1")))


# ---------------------------------------------------------------------------
# Tool replay
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replay_tools_safe_and_contract_compare(reg):
    entries = [
        ReplayEntry(tool="double", arguments={"x": 3}),
        ReplayEntry(tool="double", arguments={"x": 3},
                    recorded_contract={"ok": True, "semantic_type": "unknown",
                                       "error_code": None, "ref_count": 0,
                                       "artifact_count": 0}),
    ]
    outcomes = await replay_tools(reg, "sess", entries)
    assert outcomes[0].action == "replayed"
    assert outcomes[1].action == "replayed"
    assert outcomes[1].contract_match is True


@pytest.mark.asyncio
async def test_replay_never_runs_destructive(reg):
    """§38 红线：replay 绝不自动执行破坏性工具 —— 即使传 confirm_tier3=True。"""
    entries = [ReplayEntry(tool="wipe", arguments={"confirm": True})]
    outcomes = await replay_tools(reg, "sess", entries, confirm_tier3=True)
    assert outcomes[0].action == "skipped_unsafe"


@pytest.mark.asyncio
async def test_replay_unknown_tool_honest_error(reg):
    outcomes = await replay_tools(reg, "sess", [ReplayEntry(tool="ghost", arguments={})])
    assert outcomes[0].action == "error"
    assert "unknown" in outcomes[0].detail


# ---------------------------------------------------------------------------
# Agent-loop simulation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_simulation_script_with_normalization(reg):
    script = [
        # 别名 + 声明字段保护：应成功执行并产出修复证据
        ScriptedCall(tool="double", arguments={"x": 2}, expect_outcome="ok"),
    ]
    report = await simulate_agent_loop(reg, "sess", script)
    assert report.invariants_held, report.violations
    assert report.steps[0]["ok"] is True
    assert report.steps[0]["signature"].startswith("double:")


@pytest.mark.asyncio
async def test_simulation_destructive_blocked_invariant(reg):
    script = [
        ScriptedCall(tool="wipe", arguments={"confirm": True},
                     expect_error_code="TIER3_CONFIRMATION_REQUIRED"),
    ]
    report = await simulate_agent_loop(reg, "sess", script)
    assert report.invariants_held, report.violations


@pytest.mark.asyncio
async def test_simulation_repeated_failure_detected(reg):
    registry = reg
    registry.register(
        name="flaky", description="总失败",
        func=lambda p: {"success": False, "error": "boom"}, tier=1,
    )
    script = [
        ScriptedCall(tool="flaky", arguments={"p": 1}, expect_outcome="error"),
        ScriptedCall(tool="flaky", arguments={"p": 1}, expect_outcome="error",
                     expect_no_progress_reasons=["exact_repeat_failure"]),
    ]
    report = await simulate_agent_loop(registry, "sess", script)
    assert report.invariants_held, report.violations


@pytest.mark.asyncio
async def test_simulation_script_expectation_mismatch_is_violation(reg):
    script = [
        ScriptedCall(tool="double", arguments={"x": 2}, expect_outcome="ok",
                     expect_error_code="VALIDATION_ERROR"),  # 自相矛盾的期望 → 违规
    ]
    report = await simulate_agent_loop(reg, "sess", script)
    assert not report.invariants_held
