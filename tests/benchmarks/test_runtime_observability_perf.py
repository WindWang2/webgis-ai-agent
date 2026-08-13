"""Performance-budget + correlation-chain evidence benchmark.

Deterministic work-count assertions (NOT wall-clock ms) that double as the
"before/after evidence" for the observability PR. Run with ``-s`` to capture the
printed evidence chain.
"""
from __future__ import annotations

import json

import pytest

from app.lib.runtime import context as rt_ctx
from app.lib.runtime.evidence import (
    Outcome,
    TurnEvidence,
    TURN_EVIDENCE,
    bind_turn_evidence,
)


def _print(title: str, obj):
    print(f"\n=== {title} ===\n{json.dumps(obj, ensure_ascii=False, indent=2)}")


@pytest.mark.asyncio
async def test_evidence_full_correlation_chain(capsys):
    """Simulate one turn end-to-end and prove the diagnostic summary carries
    identity + timing + outcome + work — the answer to "slow where / failed
    where / how much wasted work"."""
    with rt_ctx.bind_runtime_context(request_id="req-demo", session_id="sess-demo"):
        ev = TurnEvidence(
            request_id="req-demo", session_id="sess-demo",
            turn_id="turn-demo", run_id="run-demo",
        )
        TURN_EVIDENCE.register(ev)
        with bind_turn_evidence(ev):
            ev.add_context_ms(18.4)
            ev.add_llm_round(total_ms=320.0, ttft_ms=210.0)
            ev.add_tool_call(duration_ms=140.0)
            ev.add_tool_call(duration_ms=90.0)
            ev.add_deduped_tool_call()  # wasted work, now visible
            ev.inc_sse_event(42)
            ev.record_map_action_issued("ma-1")
            ev.record_map_action_issued("ma-2")
            record_ack("turn-demo", "ma-1", "succeeded")
            ev.settle(Outcome.SUCCEEDED)
            ev.mark_ended()
            summary = ev.to_summary()
        TURN_EVIDENCE.remove("turn-demo")

    # Evidence-chain invariants (the system can now answer the 3 questions):
    assert summary["correlation"]["turn_id"] == "turn-demo"
    assert summary["correlation"]["run_id"] == "run-demo"   # real run id, not session
    assert summary["outcome"]["outcome"] == "succeeded"
    assert summary["timing_ms"]["llm_ttft"] == 210.0
    assert summary["work"]["tool_calls"] == 2
    assert summary["work"]["deduped_tool_calls"] == 1        # wasted work visible
    assert summary["work"]["map_actions_acked"] == 1
    _print("turn diagnostic summary", summary)


def test_evidence_tool_metrics_correlation_now_populated(monkeypatch):
    """Before: tool_call_id/run_id/turn_id were always NULL in the metrics row.
    After: resolved from context → joinable to the turn."""
    from app.services import tool_metrics
    captured = []
    monkeypatch.setattr(tool_metrics, "_enqueue", lambda line: captured.append(line))
    from app.services.jobs.context import JobOrigin, use_origin
    with rt_ctx.bind_runtime_context(session_id="s", turn_id="t", run_id="r"), \
         use_origin(JobOrigin(session_id="s", turn_id="t", run_id="r", tool_call_id="tc")):
        tool_metrics.record_tool_call(
            tool="x", arg_bytes=1, result_bytes=1, duration_ms=1,
            cache_hit=False, error=None, session_id="s",
        )
    row = json.loads(captured[0])
    _print("tool_metrics row correlation (was all-NULL before)",
           {k: row[k] for k in ("tool_call_id", "run_id", "turn_id", "session_id")})
    assert row["tool_call_id"] == "tc" and row["run_id"] == "r" and row["turn_id"] == "t"


def test_evidence_false_success_eliminated():
    """Before: ErrorRecoveryRate defaulted to 100.0 with no evidence (false
    success). After: null + evaluated=False."""
    from app.lib.harness.pi_agent_harness import PiAgentHarness
    h = PiAgentHarness(session_id="empty")
    s = h.get_telemetry_summary()
    _print("telemetry with no evidence (was 100.0 before)", s["rates"])
    assert s["rates"]["ErrorRecoveryRate"] is None
    assert s["evaluated"]["ErrorRecoveryRate"] is False


@pytest.mark.asyncio
async def test_budget_llm_pooling_one_client_per_host():
    """Regression guard: the LLM HTTP client is pooled (the prior perf bug was
    per-call client creation)."""
    from app.services.chat.llm_client import LLMHttpClientRegistry
    reg = LLMHttpClientRegistry()
    try:
        for _ in range(5):
            await reg.acquire("https://pool.example.com")
        assert reg.created_count == 1
        _print("LLM HTTP clients created for 5 same-host calls", {"created": reg.created_count})
    finally:
        await reg.aclose_all()


# helper
def record_ack(turn_id, action_id, status):
    from app.lib.runtime.evidence import record_map_action_acked_by_turn
    record_map_action_acked_by_turn(turn_id, action_id, status)
