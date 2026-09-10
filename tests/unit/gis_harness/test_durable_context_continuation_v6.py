"""Durable Context + Long-Horizon Continuation 测试（ADR-0119 D4/D7）。

覆盖：三分层词表分类、recovery_state 读写/有界/预算、loop 计数与重置、
reasoning digest、锚点采集（recovery_state + reasoning_digest 入锚）、
resume 重注入、continuation 裁决全分支（含预算耗尽 abort 与诚实披露）。
"""

from app.services.gis_harness.continuation import (
    decide_continuation,
    loops_remaining,
)
from app.services.gis_harness.durable_context import (
    LOOP_BUDGETS,
    MAX_LOOP_HISTORY,
    classify_context_key,
    new_recovery_state,
    reasoning_digest,
    recovery_state_for_anchor,
)


# --------------------------------------------------------------- 分层词表

def test_context_key_classification():
    assert classify_context_key("user_goal") == "durable"
    assert classify_context_key("recovery_state") == "durable"
    assert classify_context_key("trace_last_seq") == "durable"
    assert classify_context_key("tool_surface") == "rebuildable"
    assert classify_context_key("observation") == "rebuildable"
    assert classify_context_key("llm_raw_context") == "forbidden"
    assert classify_context_key("api_key") == "forbidden"
    assert classify_context_key("mystery_blob") == "forbidden"  # 白名单外保守


def test_recovery_state_loop_accounting(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))

    from app.services.gis_harness.durable_context import (
        load_recovery_state,
        update_recovery_state,
    )
    import asyncio

    async def _run():
        sid = "sess-dc"
        await update_recovery_state(sid, position={"step": "analysis"},
                                    loop="deepen", detail="q missing")
        state = await load_recovery_state(sid)
        assert state["loops"]["deepen"] == 1
        assert state["position"]["step"] == "analysis"
        assert state["history"][-1]["loop"] == "deepen"
        # 重置
        await update_recovery_state(sid, reset_loop="deepen")
        state = await load_recovery_state(sid)
        assert state["loops"]["deepen"] == 0

    asyncio.run(_run())


def test_recovery_state_history_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("MAPSPEC_STORAGE_DIR", str(tmp_path))

    from app.services.gis_harness.durable_context import (
        load_recovery_state,
        update_recovery_state,
    )
    import asyncio

    async def _run():
        sid = "sess-dc-bounded"
        for i in range(MAX_LOOP_HISTORY + 10):
            await update_recovery_state(sid, loop="repair", detail=f"r{i}")
        state = await load_recovery_state(sid)
        assert len(state["history"]) == MAX_LOOP_HISTORY
        assert state["loops"]["repair"] == MAX_LOOP_HISTORY + 10

    asyncio.run(_run())


def test_reasoning_digest_bounded_and_clean():
    chapter = {"workflow_instance": {"status": "running", "huge": "x" * 9999}}
    recovery = {"loops": {"deepen": 1, "repair": 0}}
    d = reasoning_digest(chapter, recovery)
    assert "wf=running" in d and "deepen=1" in d
    assert len(d) <= 512
    assert "x" * 200 not in d  # 不携带大载荷（非 LLM raw context）


def test_recovery_state_for_anchor_schema():
    snap = recovery_state_for_anchor(
        {"loops": {"deepen": 3, "bogus": 9}, "position": {"a": "b"},
         "history": [{"loop": "deepen"}]})
    assert snap["loops"]["deepen"] == 3
    assert "bogus" not in snap["loops"]
    assert set(snap["loops"]) == set(LOOP_BUDGETS)
    assert recovery_state_for_anchor("garbage") == {}


# --------------------------------------------------------------- continuation

def test_loops_remaining_clamps():
    rem = loops_remaining({"loops": {"deepen": 99}})
    assert rem["deepen"] == 0
    rem_full = loops_remaining(
        {"loops": {"repair": LOOP_BUDGETS["repair"]}})
    assert rem_full["repair"] == 0  # used == budget → 余量 0
    rem2 = loops_remaining(new_recovery_state())
    assert rem2 == LOOP_BUDGETS


def test_continuation_budget_exhaustion_aborts():
    drained = {"loops": {k: LOOP_BUDGETS[k] for k in LOOP_BUDGETS}}
    d = decide_continuation(recovery_state=drained)
    assert d.verdict == "abort_with_disclosure"
    assert d.disclosure, "预算耗尽必须披露"


def test_continuation_unrecoverable_failure_aborts():
    d = decide_continuation(
        recovery_state=new_recovery_state(),
        failure={"class": "cancelled"})
    assert d.verdict == "abort_with_disclosure"


def test_continuation_render_failure_repair_then_reobserve():
    fresh = new_recovery_state()
    d = decide_continuation(
        recovery_state=fresh, failure={"class": "renderer_failure"})
    assert d.verdict == "remediate_and_retry" and d.loop == "repair"
    # repair 预算耗尽 → reobserve（以真实观测裁决）
    drained = {"loops": {**{k: 0 for k in LOOP_BUDGETS},
                         "repair": LOOP_BUDGETS["repair"]}}
    d2 = decide_continuation(
        recovery_state=drained, failure={"class": "renderer_failure"})
    assert d2.verdict == "reobserve"
    assert d2.disclosure


def test_continuation_data_qualification_loop():
    fresh = new_recovery_state()
    qual = {"status": "insufficient"}
    d = decide_continuation(recovery_state=fresh, qualification=qual)
    assert d.verdict == "deepen_profile" and d.loop == "deepen"
    # deepen 用尽 → requalify（按事实降级）
    deep_used = {"loops": {**{k: 0 for k in LOOP_BUDGETS},
                           "deepen": LOOP_BUDGETS["deepen"]}}
    d2 = decide_continuation(recovery_state=deep_used, qualification=qual)
    assert d2.verdict == "requalify" and d2.loop == "requalify"
    # 全部用尽 → abort（拒绝带病执行）
    both_used = {"loops": {**{k: 0 for k in LOOP_BUDGETS},
                           "deepen": LOOP_BUDGETS["deepen"],
                           "requalify": LOOP_BUDGETS["requalify"]}}
    d3 = decide_continuation(recovery_state=both_used, qualification=qual)
    assert d3.verdict == "abort_with_disclosure"


def test_continuation_honest_observation_pending():
    d = decide_continuation(
        recovery_state=new_recovery_state(),
        observation={"state": "pending"})
    assert d.verdict == "reobserve"
    assert any("pending" in x for x in d.disclosure)


def test_continuation_default_continue():
    d = decide_continuation(
        recovery_state=new_recovery_state(),
        observation={"state": "semantically_correct"})
    assert d.verdict == "continue"


def test_ledger_budget_exhaustion_aborts():
    d = decide_continuation(
        recovery_state=new_recovery_state(),
        failure={"class": "tool_error", "tool": "clip_layer"},
        ledger_attempts=3)
    assert d.verdict == "abort_with_disclosure"
    assert "durable" in d.disclosure[0]


def test_decision_payload_bounded():
    d = decide_continuation(
        recovery_state=new_recovery_state(),
        failure={"class": "renderer_failure"},
        qualification={"status": "insufficient"})
    payload = d.to_payload()
    assert set(payload) == {"verdict", "loop", "reason", "disclosure"}
    assert len(payload["reason"]) <= 160
