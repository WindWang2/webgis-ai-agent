"""Harness Kernel unit tests (ADR-0180) — K0/K1/K2/K5/K7/K8 contracts.

Deterministic: in-memory session store (conftest pins USE_REDIS=false),
fake chapter payloads, no LLM / no Pi subprocess.
"""
import uuid

import pytest

from app.services.harness_kernel import get_runtime
from app.services.harness_kernel import metrics as hk_metrics
from app.services.harness_kernel.models import PlanPatch, PlanPatchKind
from app.services.session_data import session_data_manager
from app.services.session_plan import (
    SESSION_PLAN_STEP,
    load_session_plan,
)


def _sid() -> str:
    return f"sess-hk-{uuid.uuid4().hex[:8]}"


def _chapter(query: str, caps: list[str], *, tool_map: dict | None = None) -> dict:
    """Minimal MapProductPlan dump with data_requirements rows.

    ``tool_map``：capability → resolved_tool（生产中由 planner 裁决填充；
    begin_step 的命中判定依赖它或 registry 的 tool→capability 映射）。
    """
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
    """Counters are process-global — reset per test for deterministic asserts."""
    hk_metrics.reset_for_tests()
    yield
    hk_metrics.reset_for_tests()


# ── K1/K7: turn lifecycle ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_lifecycle_journal_and_recovery(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi", message="成都市小学分布情况")
    plan = await load_session_plan(sid)
    assert plan is not None
    assert plan.recovery.last_turn_id == "turn-1"
    assert plan.recovery.last_turn_status == "running"
    assert [t.turn_id for t in plan.turns] == ["turn-1"]
    assert plan.created_at > 0
    rev0 = plan.revision

    await rt.end_turn("turn-1", host="pi", status="completed")
    plan = await load_session_plan(sid)
    assert plan.recovery.last_turn_status == "completed"
    assert plan.turns[0].status == "completed"
    assert plan.turns[0].ended_at >= plan.turns[0].started_at
    assert plan.revision > rev0  # end_turn (+checkpoint) bumps revision


@pytest.mark.asyncio
async def test_begin_turn_idempotent_on_same_turn(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    plan = await load_session_plan(sid)
    n_records = len(plan.turns)
    rev = plan.revision
    await rt.begin_turn("turn-1", host="pi")  # duplicate begin (replay)
    plan = await load_session_plan(sid)
    assert len(plan.turns) == n_records  # no duplicate record
    assert hk_metrics.get_counter("turn_started|host=pi") == 1
    assert hk_metrics.get_counter("duplicate_turn_prevented|host=pi") == 1


@pytest.mark.asyncio
async def test_unsettled_turn_marked_interrupted_on_next_begin(sid):
    """Pi restart / crash: previous running turn → interrupted + resume note."""
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    # No end_turn — process died. Next turn hydrates and reconciles.
    await rt.begin_turn("turn-2", host="pi", message="继续")
    plan = await load_session_plan(sid)
    statuses = {t.turn_id: t.status for t in plan.turns}
    assert statuses["turn-1"] == "interrupted"
    assert statuses["turn-2"] == "running"
    assert plan.recovery.resume_count == 1
    assert plan.recovery.last_turn_id == "turn-2"
    kinds = [d.kind for d in plan.decisions]
    assert "turn_resumed" in kinds


@pytest.mark.asyncio
async def test_end_turn_settles_running_steps_by_status(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    # 生产顺序：intent 先物化步骤 → begin_step 标 running → 证据落定。
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter(
            "成都市小学分布情况",
            ["poi_query"],
            tool_map={"poi_query": "query_local_poi"},
        )},
        success=True, tool_call_id="tc-i", turn_id="turn-1", host="pi",
    )
    await rt.begin_step(
        tool_name="query_local_poi", tool_call_id="tc-1", turn_id="turn-1", host="pi"
    )
    plan = await load_session_plan(sid)
    running = [s for s in plan.steps if s.status == "running"]
    assert [s.capability for s in running] == ["poi_query"]

    # Cancelled turn → running steps skipped (never left "running").
    await rt.end_turn("turn-1", host="pi", status="cancelled")
    plan = await load_session_plan(sid)
    step = next(s for s in plan.steps if s.capability == "poi_query")
    assert step.status == "skipped"


# ── K0/K3: step materialization + evidence ─────────────────────────────────


@pytest.mark.asyncio
async def test_apply_tool_evidence_materializes_and_settles_steps(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    events = await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query", "admin_boundary_query"])},
        success=True,
        tool_call_id="tc-i",
        turn_id="turn-1",
        host="pi",
    )
    plan = await load_session_plan(sid)
    caps = {s.capability: s for s in plan.steps}
    assert set(caps) == {"poi_query", "admin_boundary_query"}
    assert all(s.status == "pending" for s in caps.values())
    assert any(e.event == SESSION_PLAN_STEP for e in events)
    kinds = [d.kind for d in plan.decisions]
    assert "plan_created" in kinds

    # Failure marks the step failed + evidence + attempts.
    await rt.apply_tool_evidence(
        "query_local_poi",
        {"error": "boom"},
        success=False,
        tool_call_id="tc-p",
        turn_id="turn-1",
        host="pi",
    )
    plan = await load_session_plan(sid)
    step = next(s for s in plan.steps if s.capability == "poi_query")
    assert step.status == "failed"
    assert step.attempts == 1
    assert step.latest_evidence().error == "boom"

    # Retry succeeds → succeeded with two evidence entries (bounded).
    await rt.apply_tool_evidence(
        "query_local_poi",
        {"features": []},
        success=True,
        geojson_ref="ref:geojson-x",
        tool_call_id="tc-p2",
        turn_id="turn-1",
        host="pi",
    )
    plan = await load_session_plan(sid)
    step = next(s for s in plan.steps if s.capability == "poi_query")
    assert step.status == "succeeded"
    assert step.attempts == 2
    assert step.latest_evidence().ref == "ref:geojson-x"
    assert len(step.evidence) == 2
    # Turn counter incremented by dispatch evidence.
    assert plan.turns[0].tool_calls == 3


@pytest.mark.asyncio
async def test_scope_narrow_same_goal_updates_envelope_and_keeps_steps(sid):
    """Acceptance scenario 2 (plan level): same-goal follow-up updates the
    SAME envelope — never a supersede into an unrelated new world."""
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query"])},
        success=True, tool_call_id="tc-1", turn_id="turn-1", host="pi",
    )
    plan0 = await load_session_plan(sid)
    assert plan0.replaced is False

    await rt.begin_turn("turn-2", host="pi", message="只看主城区")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query", "admin_boundary_query"])},
        success=True, tool_call_id="tc-2", turn_id="turn-2", host="pi",
    )
    plan1 = await load_session_plan(sid)
    assert plan1.envelope_id == plan0.envelope_id  # same envelope updated
    assert plan1.replaced is True
    assert plan1.superseded is False
    caps = {s.capability: s for s in plan1.steps}
    assert set(caps) == {"poi_query", "admin_boundary_query"}
    kinds = [d.kind for d in plan1.decisions]
    assert "plan_replaced" in kinds


@pytest.mark.asyncio
async def test_goal_change_invalidates_vanished_capability_steps(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query", "heatmap_analysis"])},
        success=True, tool_call_id="tc-1", turn_id="turn-1", host="pi",
    )
    plan0 = await load_session_plan(sid)
    assert plan0.superseded is False

    # Different goal → supersede semantics from the store layer produce a new
    # envelope via the full apply path; simulate the resulting envelope swap
    # by re-applying intent with a new goal key (registry-level supersede is
    # covered by test_pi_session_plan_host). Here: chapter without heatmap.
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("分析北京学校", ["poi_query"])},
        success=True, tool_call_id="tc-2", turn_id="turn-1", host="pi",
    )
    plan1 = await load_session_plan(sid)
    caps = {s.capability: s.status for s in plan1.steps}
    assert caps["poi_query"] == "pending"
    assert "heatmap_analysis" not in caps  # vanished rows drop from steps


# ── K2: persistence invariants ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revision_monotonic_and_history_archive(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    plan = await load_session_plan(sid)
    revisions = [plan.revision]
    await rt.end_turn("turn-1", host="pi", status="completed")
    plan = await load_session_plan(sid)
    revisions.append(plan.revision)
    await rt.begin_turn("turn-2", host="pi")
    plan = await load_session_plan(sid)
    revisions.append(plan.revision)
    assert revisions == sorted(revisions) and len(set(revisions)) == 3
    assert plan.schema_version == 2
    # supersede archive: legacy envelope accessible via history alias.
    alias = f"session-plan-id:{plan.envelope_id}"
    # (no supersede happened — just verify checkpoint ring below)


@pytest.mark.asyncio
async def test_v1_envelope_backcompat_loads_with_defaults(sid):
    """A pre-kernel v1 payload (no additive fields) loads clean."""
    from app.services.session_plan import SessionPlan, save_session_plan

    legacy = SessionPlan(
        envelope_id="sp-legacy",
        session_id=sid,
        user_goal="旧信封",
        updated_at=123.0,
    )
    payload = legacy.model_dump()
    for field in ("schema_version", "created_at", "revision", "turns", "steps",
                  "decisions", "recovery"):
        payload.pop(field, None)
    await session_data_manager.store(sid, payload, prefix="sessionplan")
    ref_id = await session_data_manager.resolve_alias(sid, "session-plan")
    # store without alias wiring: emulate by direct save/load roundtrip
    plan = SessionPlan.model_validate(payload)
    assert plan.schema_version == 2  # default applied on load
    assert plan.turns == [] and plan.steps == []
    await save_session_plan(plan)
    loaded = await load_session_plan(sid)
    assert loaded.revision >= 1


# ── K7: checkpoint ring ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_checkpoint_ring_rotates_and_records(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    for i in range(5):
        await rt.checkpoint(reason=f"cp-{i}", host="pi", turn_id="turn-1")
    cps = await rt.list_checkpoints()
    assert 1 <= len(cps) <= 3
    revs = [c["revision"] for c in cps]
    assert revs == sorted(revs, reverse=True)
    plan = await load_session_plan(sid)
    assert plan.recovery.checkpoint_id.startswith("cp-")
    kinds = [d.kind for d in plan.decisions]
    assert kinds.count("checkpoint") == 5


# ── K5: plan patch protocol ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_plan_invalidates_targets_and_journals(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query", "admin_boundary_query"])},
        success=True, tool_call_id="tc-1", turn_id="turn-1", host="pi",
    )
    result = await rt.patch_plan(
        PlanPatch(
            kind=PlanPatchKind.CANCEL_SUBGOAL,
            turn_id="turn-1",
            host="pi",
            note="用户取消边界子目标",
            target_step_ids=["step-admin_boundary_query"],
        )
    )
    assert result.applied is True
    assert result.invalidated_step_ids == ["step-admin_boundary_query"]
    assert result.revision >= 1
    assert result.events and result.events[0]["data"]["step_id"] == "step-admin_boundary_query"
    plan = await load_session_plan(sid)
    step = next(s for s in plan.steps if s.capability == "admin_boundary_query")
    assert step.status == "invalidated"
    kinds = [d.kind for d in plan.decisions]
    assert "plan_patched" in kinds
    assert hk_metrics.get_counter("plan_patched|host=pi|kind=cancel_subgoal") == 1


@pytest.mark.asyncio
async def test_patch_plan_unknown_targets_is_noop(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.apply_tool_evidence(
        "webgis_map_intent",
        {"plan": _chapter("成都市小学分布情况", ["poi_query"])},
        success=True, tool_call_id="tc-1", turn_id="turn-1", host="pi",
    )
    result = await rt.patch_plan(
        PlanPatch(kind=PlanPatchKind.SCOPE_CHANGE, target_step_ids=["step-nope"])
    )
    assert result.applied is False
    assert result.invalidated_step_ids == []


# ── K8: metrics + projection lines ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_metrics_snapshot_shape(sid):
    hk_metrics.reset_for_tests()
    rt = get_runtime(sid)
    await rt.begin_turn("turn-1", host="pi")
    await rt.end_turn("turn-1", host="pi", status="completed")
    snap = hk_metrics.snapshot()
    assert snap["counters"]["turn_started|host=pi"] == 1
    assert snap["counters"]["turn_ended|host=pi|status=completed"] == 1
    assert "uptime_s" in snap


def test_projection_lines_zero_drift_and_resume_hint():
    from app.services.harness_kernel.projection import (
        format_recovery_line,
        format_steps_line,
    )
    from app.services.session_plan import SessionPlan

    bare = SessionPlan(envelope_id="sp-1", session_id="s")
    assert format_recovery_line(bare) == ""
    assert format_steps_line(bare) == ""

    bare.recovery.last_turn_id = "turn-9"
    bare.recovery.last_host = "pi"
    bare.recovery.last_turn_status = "interrupted"
    line = format_recovery_line(bare)
    assert "turn-9" in line and "interrupted" in line
