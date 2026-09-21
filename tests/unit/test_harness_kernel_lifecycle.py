"""Canonical Harness turn lifecycle (ADR-0204) — K1/K2/K4 contracts.

Deterministic: in-memory session store (conftest pins USE_REDIS=false),
fake chapter payloads, no LLM / no Pi subprocess. Mirrors the fixture
discipline of ``test_harness_kernel_runtime.py``.
"""
import uuid

import pytest

from app.services.harness_kernel import get_runtime
from app.services.harness_kernel import metrics as hk_metrics
from app.services.harness_kernel.models import (
    EVENT_KINDS,
    PHASE_TRANSITIONS,
    TERMINAL_PHASES,
    PlanPatch,
    PlanPatchKind,
    next_phase,
)
from app.services.session_data import session_data_manager
from app.services.session_plan import load_session_plan


def _sid() -> str:
    return f"sess-hkc-{uuid.uuid4().hex[:8]}"


def _chapter(query: str, caps: list[str], *, tool_map: dict | None = None) -> dict:
    return {
        "query": query,
        "recipe_id": "poi_distribution_overview",
        "plan_id": "plan-test",
        "intent": {
            "scope": {"name": "成都市"},
            "subject": {"category": "小学"},
            "task": "分布情况",
        },
        "data_requirements": [
            {
                "capability": cap,
                "purpose": f"获取{cap}",
                "status": "pending",
                "resolved_tool": (tool_map or {}).get(cap, ""),
                "depends_on": [],
            }
            for cap in caps
        ],
        "analysis_steps": [],
    }


@pytest.fixture
async def sid():
    session_id = _sid()
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


@pytest.fixture(autouse=True)
def _reset_hk_metrics():
    hk_metrics.reset_for_tests()
    yield
    hk_metrics.reset_for_tests()


def _turn(plan, turn_id: str):
    return next((t for t in plan.turns if t.turn_id == turn_id), None)


def _kinds(plan):
    return [d.kind for d in plan.decisions]


# ── K1: transition table (the test oracle) ─────────────────────────────────


def test_phase_table_is_closed_terminal_frozen_and_reachable():
    from app.services.harness_kernel.models import USER_MESSAGE_TRIGGER

    all_phases = set(TERMINAL_PHASES) | {
        "created", "understanding", "planning", "qualifying", "executing",
        "observing", "verifying", "repairing", "replanning",
    }
    for (src, trigger), dst in PHASE_TRANSITIONS.items():
        assert src in all_phases and dst in all_phases, (src, trigger, dst)
        assert trigger and isinstance(trigger, str)
    # terminal phases have no outgoing edges
    for (src, _t) in PHASE_TRANSITIONS:
        assert src not in TERMINAL_PHASES, src
    # every terminal phase is reachable from verifying
    for term in TERMINAL_PHASES:
        assert ("verifying", next(
            t for (s, t), d in PHASE_TRANSITIONS.items()
            if s == "verifying" and d == term
        )) in PHASE_TRANSITIONS
    # every running phase reachable from created (BFS over the table)
    seen = {"created"}
    frontier = ["created"]
    while frontier:
        nxt = []
        for node in frontier:
            for (src, _t), dst in PHASE_TRANSITIONS.items():
                if src == node and dst not in seen:
                    seen.add(dst)
                    nxt.append(dst)
        frontier = nxt
    assert seen == all_phases, all_phases - seen
    # settled turns must exist for every terminal status (1:1 mapping)
    from app.services.harness_kernel.models import STATUS_TO_TRIGGER

    assert set(STATUS_TO_TRIGGER) == set(TERMINAL_PHASES) - {"running"} | {
        "completed", "failed", "cancelled", "aborted", "refused", "interrupted",
    }
    assert next_phase("created", USER_MESSAGE_TRIGGER) == "understanding"


def test_next_phase_unknown_pairs_refused():
    assert next_phase("created", "verdict_ready") is None
    assert next_phase("completed", "user_message") is None
    assert next_phase("bogus", "user_message") is None


# ── K1: production mainline drives the full lifecycle ──────────────────────


async def test_production_mainline_sequence(sid):
    """intent → dispatch → evidence → settle: created→…→completed."""
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi", message="成都市小学分布情况")
    plan = await load_session_plan(sid)
    assert _turn(plan, "turn-1").phase == "understanding"

    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query"],
                          tool_map={"poi_query": "query_local_poi"})},
        success=True, tool_call_id="tc-i", turn_id="turn-1", host="pi",
    )
    plan = await load_session_plan(sid)
    assert _turn(plan, "turn-1").phase == "planning"

    await rt.begin_step(
        tool_name="query_local_poi", tool_call_id="tc-1",
        turn_id="turn-1", host="pi",
    )
    plan = await load_session_plan(sid)
    rec = _turn(plan, "turn-1")
    assert rec.phase == "executing"
    # qualifying → executing both recorded on the ring
    assert [h.to_phase for h in rec.phase_history][-2:] == [
        "qualifying", "executing",
    ]

    await rt.apply_tool_evidence(
        "query_local_poi", {"features": []}, success=True,
        geojson_ref="ref:geojson-1", tool_call_id="tc-1",
        turn_id="turn-1", host="pi",
    )
    plan = await load_session_plan(sid)
    assert _turn(plan, "turn-1").phase == "observing"

    # product milestone (the production completion step) → goal evaluated
    await rt.apply_tool_evidence(
        "webgis_map_product", {"map_product": {"verdict": "READY"}},
        success=True, geojson_ref="ref:product-1", tool_call_id="tc-p",
        turn_id="turn-1", host="pi",
    )
    await rt.end_turn("turn-1", host="pi", status="completed")
    plan = await load_session_plan(sid)
    rec = _turn(plan, "turn-1")
    assert rec.phase == "completed"
    assert rec.status == "completed"
    triggers = [h.trigger for h in rec.phase_history]
    assert "settle_started" in triggers and "verdict_ready" in triggers
    # canonical events landed in the journal
    kinds = _kinds(plan)
    for kind in ("turn_started", "phase_changed", "intent_resolved",
                 "plan_created", "tool_started", "tool_succeeded",
                 "observation_received", "goal_evaluated", "turn_ended"):
        assert kind in kinds, kind
    assert "step_marked" not in kinds  # legacy kind no longer emitted


async def test_illegal_transition_refused_and_journaled(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    result = await rt.advance_turn_phase("turn-1", "verdict_ready", host="pi")
    assert result == ""  # created -/verdict_ready-> ? is outside the table
    plan = await load_session_plan(sid)
    rec = _turn(plan, "turn-1")
    assert rec.phase == "understanding"  # unchanged by the refusal
    refused_rows = [h for h in rec.phase_history if h.reason_code != "ok"]
    assert refused_rows and refused_rows[-1].trigger == "verdict_ready"
    assert "phase_refused" in _kinds(plan)
    assert hk_metrics.get_counter("phase_refused|host=pi|trigger=verdict_ready") == 1


# ── terminalization fencing ────────────────────────────────────────────────


async def test_no_duplicate_terminalization_and_phase_frozen_after_settle(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query"],
                          tool_map={"poi_query": "query_local_poi"})},
        success=True, tool_call_id="tc-i", turn_id="turn-1", host="pi",
    )
    await rt.end_turn("turn-1", host="pi", status="completed")
    plan0 = await load_session_plan(sid)
    rev0 = plan0.revision
    ended_rows = [d for d in plan0.decisions if d.kind == "turn_ended"]
    assert len(ended_rows) == 1

    # double settle is a no-op (no second terminal row, no revision bump)
    await rt.end_turn("turn-1", host="pi", status="failed")
    plan1 = await load_session_plan(sid)
    assert len([d for d in plan1.decisions if d.kind == "turn_ended"]) == 1
    assert _turn(plan1, "turn-1").status == "completed"
    assert _turn(plan1, "turn-1").phase == "completed"

    # phase advance after terminal is refused (record not running)
    assert await rt.advance_turn_phase("turn-1", "user_message") == ""
    # late tool evidence for the settled turn must not reopen it, must not
    # advance a phase, and must not append tool events
    await rt.apply_tool_evidence(
        "query_local_poi", {"features": []}, success=True,
        geojson_ref="ref:late", tool_call_id="tc-late",
        turn_id="turn-1", host="pi",
    )
    plan2 = await load_session_plan(sid)
    rec = _turn(plan2, "turn-1")
    assert rec.status == "completed" and rec.phase == "completed"
    assert rev0 == plan1.revision  # idempotent paths wrote nothing
    assert not [d for d in plan2.decisions
                if d.causal_id == "tc-late" and d.kind != "tool_late"]


async def test_late_callback_after_next_turn(sid):
    """#1407/#1441 discipline: late callback attributed to ORIGINAL turn,
    successor turn untouched, settled turn never reopens."""
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.end_turn("turn-1", host="pi", status="completed")
    await rt.begin_turn("turn-2", host="pi", message="换个目标")

    await rt.record_late_callback(
        tool_name="query_local_poi",
        tool_call_id="tc-late-1",
        callback_turn_id="turn-1",
        active_turn_id="turn-2",
        host="pi",
    )
    plan = await load_session_plan(sid)
    t1, t2 = _turn(plan, "turn-1"), _turn(plan, "turn-2")
    assert t1.status == "completed" and t1.phase == "completed"
    assert t2.status == "running" and t2.phase == "understanding"
    late = [d for d in plan.decisions if d.kind == "tool_late"]
    assert len(late) == 1
    assert late[0].turn_id == "turn-1"
    assert late[0].causal_id == "tc-late-1"
    assert hk_metrics.get_counter("late_callback|host=pi") == 1

    # duplicate late callback (same tool_call_id) appends nothing
    await rt.record_late_callback(
        tool_name="query_local_poi",
        tool_call_id="tc-late-1",
        callback_turn_id="turn-1",
        active_turn_id="turn-2",
        host="pi",
    )
    plan = await load_session_plan(sid)
    assert len([d for d in plan.decisions if d.kind == "tool_late"]) == 1


# ── cancel / refused / failure families ────────────────────────────────────


async def test_cancel_during_execution(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query"],
                          tool_map={"poi_query": "query_local_poi"})},
        success=True, tool_call_id="tc-i", turn_id="turn-1", host="pi",
    )
    await rt.begin_step(
        tool_name="query_local_poi", tool_call_id="tc-1",
        turn_id="turn-1", host="pi",
    )
    await rt.end_turn("turn-1", host="pi", status="cancelled")
    plan = await load_session_plan(sid)
    rec = _turn(plan, "turn-1")
    assert rec.phase == "cancelled" and rec.status == "cancelled"
    step = next(s for s in plan.steps if s.capability == "poi_query")
    assert step.status == "skipped"  # never left running


async def test_refused_status_settles_without_reopening(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi", message="把地图删掉")
    await rt.end_turn("turn-1", host="pi", status="refused")
    plan = await load_session_plan(sid)
    rec = _turn(plan, "turn-1")
    assert rec.phase == "refused" and rec.status == "refused"
    assert plan.recovery.last_turn_status == "refused"


async def test_replan_after_recoverable_failure(sid):
    """step failure keeps the turn executing; repair patch → repairing;
    re-intent (replan compiled) → planning → executing again."""
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query", "heatmap_analysis"],
                          tool_map={"poi_query": "query_local_poi"})},
        success=True, tool_call_id="tc-i", turn_id="turn-1", host="pi",
    )
    await rt.begin_step(
        tool_name="query_local_poi", tool_call_id="tc-1",
        turn_id="turn-1", host="pi",
    )
    await rt.apply_tool_evidence(
        "query_local_poi", {"error": "boom"}, success=False,
        tool_call_id="tc-1", turn_id="turn-1", host="pi",
    )
    plan = await load_session_plan(sid)
    assert _turn(plan, "turn-1").phase == "executing"  # loop lives on steps
    step = next(s for s in plan.steps if s.capability == "poi_query")
    assert step.status == "failed"

    result = await rt.patch_plan(
        PlanPatch(
            kind=PlanPatchKind.SCOPE_CHANGE,
            turn_id="turn-1", host="pi",
            note="修复数据面",
        )
    )
    assert result.applied is True
    plan = await load_session_plan(sid)
    assert _turn(plan, "turn-1").phase == "repairing"
    assert "repair_applied" in _kinds(plan)

    # replan lands (user refines → new intent) → planning → executing
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query"],
                          tool_map={"poi_query": "query_local_poi"})},
        success=True, tool_call_id="tc-i2", turn_id="turn-1", host="pi",
    )
    plan = await load_session_plan(sid)
    assert _turn(plan, "turn-1").phase == "planning"
    await rt.begin_step(
        tool_name="query_local_poi", tool_call_id="tc-2",
        turn_id="turn-1", host="pi",
    )
    plan = await load_session_plan(sid)
    assert _turn(plan, "turn-1").phase == "executing"


# ── idempotency ────────────────────────────────────────────────────────────


async def test_duplicate_tool_callback_no_double_events(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query"],
                          tool_map={"poi_query": "query_local_poi"})},
        success=True, tool_call_id="tc-i", turn_id="turn-1", host="pi",
    )
    for _ in range(2):  # duplicate callback (e.g. lock-contention retry)
        await rt.apply_tool_evidence(
            "query_local_poi", {"features": []}, success=True,
            geojson_ref="ref:g", tool_call_id="tc-1",
            turn_id="turn-1", host="pi",
        )
    plan = await load_session_plan(sid)
    ok_events = [d for d in plan.decisions if d.kind == "tool_succeeded"]
    assert len(ok_events) == 1
    assert ok_events[0].event_id == "tool_succeeded:turn-1:tc-1"
    assert ok_events[0].seq > 0
    obs = [d for d in plan.decisions if d.kind == "observation_received"]
    assert len(obs) == 1
    step = next(s for s in plan.steps if s.capability == "poi_query")
    assert step.attempts == 1  # duplicate success inflates nothing
    # causal events carry monotonic seq (replay order)
    seqs = [d.seq for d in plan.decisions if d.seq]
    assert seqs == sorted(seqs)


# ── resume / replay consistency ────────────────────────────────────────────


async def test_resume_replay_consistency(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query"],
                          tool_map={"poi_query": "query_local_poi"})},
        success=True, tool_call_id="tc-i", turn_id="turn-1", host="pi",
    )
    # process death: no end_turn
    await rt.begin_turn("turn-2", host="pi", message="继续")
    plan = await load_session_plan(sid)
    t1, t2 = _turn(plan, "turn-1"), _turn(plan, "turn-2")
    assert t1.status == "interrupted"
    # ADR-0204 D4b: the interrupted turn's PHASE is terminal too — no
    # terminal-status + running-phase mixed truth
    assert t1.phase == "interrupted"
    assert t1.phase_history[-1].trigger == "host_interrupted"
    assert t2.phase == "understanding"
    assert plan.recovery.resume_count == 1
    assert plan.recovery.resumed_from_turn_id == "turn-1"
    # replay: phase timeline reconstructable from the journal + history
    replay_phase = "created"
    for h in t1.phase_history:
        if h.reason_code == "ok":
            replay_phase = h.to_phase
    assert replay_phase == t1.phase
    ev = [d for d in plan.decisions
          if d.kind == "phase_changed" and d.turn_id == "turn-1"]
    # mainline + the restart walk (settle → host_interrupted)
    assert [e.detail["to"] for e in ev] == [
        "understanding", "planning", "verifying", "interrupted",
    ]
    # seq strictly monotonic across the whole journal
    seqs = [d.seq for d in plan.decisions if d.seq]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


async def test_second_dispatch_persists_event_only_audit(sid):
    """review P1#2: a repeat dispatch (step already running, turn already
    executing) appends NO step change but MUST land its tool_started event
    + self-loop audit row — event-only mutations still save."""
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query"],
                          tool_map={"poi_query": "query_local_poi"})},
        success=True, tool_call_id="tc-i", turn_id="turn-1", host="pi",
    )
    await rt.begin_step(
        tool_name="query_local_poi", tool_call_id="tc-1",
        turn_id="turn-1", host="pi",
    )
    plan1 = await load_session_plan(sid)
    rev1 = plan1.revision
    assert any(d.kind == "tool_started" for d in plan1.decisions)

    await rt.begin_step(
        tool_name="query_local_poi", tool_call_id="tc-2",
        turn_id="turn-1", host="pi",
    )
    plan2 = await load_session_plan(sid)
    assert plan2.revision > rev1  # event-only append still persisted
    started2 = [d for d in plan2.decisions if d.kind == "tool_started"]
    assert [d.causal_id for d in started2] == ["tc-1", "tc-2"]
    # self-loop audit row recorded on the ring (no phase churn)
    rec = _turn(plan2, "turn-1")
    assert rec.phase == "executing"
    assert any(
        h.trigger == "dispatch_started" and h.to_phase == "executing"
        and h.from_phase == "executing"
        for h in rec.phase_history
    )


async def test_supersede_migrates_event_seq(sid):
    """review P1#1: goal-change supersede rebuilds the envelope — the
    monotonic event counter must migrate with the ledger, or seq collides."""
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query"])},
        success=True, tool_call_id="tc-1", turn_id="turn-1", host="pi",
    )
    plan0 = await load_session_plan(sid)
    seq0 = plan0.event_seq
    assert seq0 > 0

    # different goal → supersede rebuild path (store-level)
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("分析北京学校", ["poi_query"])},
        success=True, tool_call_id="tc-2", turn_id="turn-1", host="pi",
    )
    plan1 = await load_session_plan(sid)
    assert plan1.superseded is False  # current (rebuilt) envelope
    assert plan1.event_seq > seq0  # counter migrated, not reset
    # every persisted seq stays strictly monotonic (replay order intact)
    seqs = [d.seq for d in plan1.decisions if d.seq]
    assert seqs == sorted(seqs)
    carried = [d for d in plan1.decisions if d.seq and d.seq <= seq0]
    assert carried  # old rows really were carried over
    assert all(d.seq <= plan1.event_seq for d in plan1.decisions if d.seq)


# ── projection parity (canonical ↔ V7) ─────────────────────────────────────


def test_projection_parity_canonical_to_v7():
    from app.services.gis_harness.runtime_state_machine import (
        LEGAL_TRANSITIONS,
    )
    from app.services.harness_kernel.phase_adapter import project_runtime_phase

    mainline = [
        ("created", "understanding"),
        ("understanding", "planning"),
        ("planning", "qualifying"),
        ("qualifying", "executing"),
        ("executing", "observing"),
        ("observing", "verifying"),
        ("verifying", "completed"),
    ]
    mapped = [
        (project_runtime_phase(a), project_runtime_phase(b))
        for a, b in mainline
    ]
    # V7 successor graph (task-level rendering of the turn-level mainline)
    successors: dict[str, set[str]] = {}
    for (src, _t), dst in LEGAL_TRANSITIONS.items():
        successors.setdefault(src, set()).add(dst)
    for (va, vb), (ca, cb) in zip(mapped, mainline):
        if va == vb:
            continue  # self-rendering (e.g. qualifying→planning both plan_ready)
        # reachability (not necessarily a single edge): V7 may decompose a
        # canonical transition into several task-level steps
        # (critiquing→finalizing→committed), and may enter observing only
        # from critiquing/repairing — the rendering stays graph-consistent.
        seen, frontier = {va}, [va]
        while frontier:
            node = frontier.pop()
            for nxt in successors.get(node, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        assert vb in seen, (ca, cb, va, vb)
    # total mapping
    all_phases = set(TERMINAL_PHASES) | {
        "created", "understanding", "planning", "qualifying", "executing",
        "observing", "verifying", "repairing", "replanning",
    }
    for phase in all_phases:
        assert project_runtime_phase(phase)


# ── K2: canonical turn context ─────────────────────────────────────────────


async def test_turn_context_projection_bounded(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi", message="成都市小学分布情况")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query", "heatmap_analysis"],
                          tool_map={"poi_query": "query_local_poi"})},
        success=True, tool_call_id="tc-i", turn_id="turn-1", host="pi",
    )
    await rt.begin_step(
        tool_name="query_local_poi", tool_call_id="tc-1",
        turn_id="turn-1", host="pi",
    )
    ctx = await rt.turn_context(
        "turn-1",
        mission_ref="mission-abc",
        governor_hints={"budget": "low"},
        knowledge_refs=["kb:1"],
    )
    assert ctx is not None
    assert ctx.context_schema == "hk.ctx.v1"
    assert ctx.turn_id == "turn-1" and ctx.host == "pi"
    assert ctx.phase == "executing" and ctx.turn_status == "running"
    assert ctx.mission_ref == "mission-abc"
    assert ctx.steps_total == 2 and ctx.steps_open == 2  # pending+running are open
    assert len(ctx.capabilities) == 2
    assert ctx.summary_line().startswith("[HarnessTurnContext]")
    assert len(ctx.summary_line()) < 400  # bounded disclosure

    await rt.end_turn("turn-1", host="pi", status="completed")
    ctx2 = await rt.turn_context("turn-1")
    assert ctx2.completion == "completed"
    assert ctx2.phase == "completed"


# ── isolation & backcompat ─────────────────────────────────────────────────


async def test_concurrent_session_isolation(sid):
    other = _sid()
    await session_data_manager.clear_session(other)
    try:
        rt_a, rt_b = get_runtime(sid), get_runtime(other)
        await rt_a.begin_turn("turn-a", host="pi")
        await rt_b.begin_turn("turn-b", host="pi")
        await rt_a.apply_tool_evidence(
            "webgis_map_intent",
            {"plan": _chapter("成都小学", ["poi_query"])},
            success=True, tool_call_id="tc-a", turn_id="turn-a", host="pi",
        )
        pa = await load_session_plan(sid)
        pb = await load_session_plan(other)
        assert _turn(pa, "turn-a").phase == "planning"
        assert _turn(pb, "turn-b").phase == "understanding"
        assert _turn(pb, "turn-a") is None
        assert pa.session_id != pb.session_id
        # envelope B only ever saw its own turn
        assert {d.turn_id for d in pb.decisions if d.turn_id} == {"turn-b"}
        assert {t.turn_id for t in pb.turns} == {"turn-b"}
        seqs_a = {d.seq for d in pa.decisions if d.seq}
        seqs_b = {d.seq for d in pb.decisions if d.seq}
        assert seqs_a and seqs_b  # both journals alive, independent counters
    finally:
        await session_data_manager.clear_session(other)


async def test_v1_envelope_loads_new_fields_defaulted(sid):
    from app.services.session_plan import SessionPlan

    legacy = SessionPlan(envelope_id="sp-old", session_id=sid)
    payload = legacy.model_dump()
    for field in ("schema_version", "created_at", "revision", "turns",
                  "steps", "decisions", "recovery", "event_seq"):
        payload.pop(field, None)
    plan = SessionPlan.model_validate(payload)
    assert plan.event_seq == 0
    assert plan.turns == [] and plan.decisions == []
    # ADR-0204 fields are v2-additive: generation does not move
    assert plan.schema_version == 2


# ── vocabulary hygiene ─────────────────────────────────────────────────────


def test_event_vocabulary_superset_and_reserved():
    from app.services.harness_kernel.models import DECISION_KINDS, RESERVED_EVENT_KINDS

    assert set(DECISION_KINDS).issubset(set(EVENT_KINDS))
    for kind in ("phase_changed", "intent_resolved", "tool_started",
                 "tool_succeeded", "tool_failed", "tool_late",
                 "observation_received", "goal_evaluated", "repair_applied"):
        assert kind in EVENT_KINDS
    for kind in RESERVED_EVENT_KINDS:
        assert kind not in DECISION_KINDS  # reserved names are new only


def test_bridge_settle_mapping_single_source():
    """The streaming and non-streaming settle paths share one helper."""
    from app.agent_pi_bridge import _hk_turn_status

    assert _hk_turn_status(cancelled=True, timed_out=False,
                           send_failed=False, process_died=False) == "cancelled"
    assert _hk_turn_status(cancelled=False, timed_out=True,
                           send_failed=False, process_died=False) == "failed"
    assert _hk_turn_status(cancelled=False, timed_out=False,
                           send_failed=True, process_died=False) == "failed"
    assert _hk_turn_status(cancelled=False, timed_out=False,
                           send_failed=False, process_died=True) == "failed"
    assert _hk_turn_status(cancelled=False, timed_out=False,
                           send_failed=False, process_died=False) == "completed"
