"""planner 决策溯源发射契约测试（方向 8 / ADR-0204 决策一）。

生产发射点回归钉：
- CANDIDATE_WORKFLOWS 记录携带 plan_selection DecisionRecord
  （candidates 稳定序 → alternatives rank；selected；policy_version）；
- capability resolution（situation 提供时）→ SELECTED_WORKFLOW 附加
  记录 ``phase=capability_resolution`` 携带 per-capability DecisionRecord；
- ReplayTrace.build_trace 从真实 planner 链提取决策索引 + situation_revision。
"""
from __future__ import annotations

import contextlib
import uuid

import pytest

from app.lib.harness.replay.schema import build_trace
from app.lib.runtime.context import bind_runtime_context
from app.lib.runtime.gis_trace import get_gis_trace_registry
from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.planner import MapProductPlanner

pytestmark = pytest.mark.cartography


def _bind_turn():
    turn_id = f"dp8-{uuid.uuid4().hex[:10]}"
    stack = contextlib.ExitStack()
    stack.enter_context(
        bind_runtime_context(turn_id=turn_id, session_id=f"dp-sess-{turn_id}")
    )
    return turn_id, stack


def test_plan_selection_decision_lands_in_chain():
    turn_id, stack = _bind_turn()
    try:
        intent = resolve_map_request_intent("成都小学分布密度热力图")
        # 非显式 recipe 路径：candidates 由 select_candidates 产出（排序序
        # 进 alternatives）；显式 recipe_id 路径 candidates 恒空表（无重选）。
        MapProductPlanner().plan_from_intent(intent, use_memo=False)
        chain = get_gis_trace_registry().get(turn_id)
        assert chain is not None
        d = chain.as_dict()
        get_gis_trace_registry().drop(turn_id)
        cand = next(
            s for s in d["stages"] if s.get("stage") == "CANDIDATE_WORKFLOWS")
        # 既有 id 串键保持（向后兼容）
        assert cand.get("selected")
        assert isinstance(cand.get("candidates"), str) or isinstance(
            cand.get("candidates"), list)
        # ADR-0204：决策记录
        decision = cand.get("decision")
        assert isinstance(decision, dict), "plan_selection decision missing"
        assert decision["kind"] == "plan_selection"
        assert decision["selected"] == cand.get("selected")
        assert decision["policy_version"] == "recipe_select.v1"
        assert decision["decision_id"].startswith("dec_")
        # 排序序 → alternatives rank（非显式路径必有候选）
        assert decision["alternatives"], (
            "select_candidates must produce ranked alternatives here")
        assert decision["alternatives"][0]["rank"] == 0
        assert decision["alternatives"][0]["id"] == decision["selected"]
    finally:
        stack.__exit__(None, None, None)


def test_capability_resolution_decision_lands_in_chain():
    from app.services.gis_harness.capability_resolution import build_situation

    turn_id, stack = _bind_turn()
    try:
        intent = resolve_map_request_intent("成都小学分布密度热力图")
        situation = build_situation(task_hint=str(intent.task or ""))
        plan = MapProductPlanner().plan_from_intent(
            intent, use_memo=False, situation=situation)
        assert plan.capability_evidence, (
            "situation provided → resolution evidence must exist")
        chain = get_gis_trace_registry().get(turn_id)
        assert chain is not None
        d = chain.as_dict()
        get_gis_trace_registry().drop(turn_id)
        resolution_records = [
            s for s in d["stages"]
            if s.get("stage") == "SELECTED_WORKFLOW"
            and s.get("phase") == "capability_resolution"
        ]
        assert resolution_records, "capability_resolution emission missing"
        decisions = resolution_records[0].get("decisions")
        assert isinstance(decisions, list) and decisions
        first = decisions[0]
        assert first["kind"] == "capability_resolution"
        assert first["decision_id"].startswith("dec_")
        assert first["inputs"].get("capability")
        assert first["policy_version"] == "capability_resolution.v1"
    finally:
        stack.__exit__(None, None, None)


def test_build_trace_extracts_decisions_and_situation_from_real_chain():
    turn_id, stack = _bind_turn()
    try:
        intent = resolve_map_request_intent("成都小学分布密度热力图")
        MapProductPlanner().plan_from_intent(
            intent, recipe_id="poi_distribution_overview", use_memo=False)
        chain = get_gis_trace_registry().get(turn_id)
        assert chain is not None
        chain_dict = chain.as_dict()
        chain_dict["session_id"] = f"dp-sess-{turn_id}"
        get_gis_trace_registry().drop(turn_id)
        trace = build_trace(
            session_id=chain_dict["session_id"],
            turn_id=turn_id,
            chain_dict=chain_dict,
            turn_summary={},
        )
        kinds = {d["kind"] for d in trace.decisions}
        assert "plan_selection" in kinds
        assert all(d["decision_id"].startswith("dec_")
                   for d in trace.decisions)
        # situation_revision：plan_selection 决策的情境投影（#1275 预留位实接）。
        selection = next(
            d for d in trace.decisions if d["kind"] == "plan_selection")
        assert selection["inputs_digest"]
        # replay 决策面对齐：索引条目可直接进 decisions_digest / diff。
        from app.lib.harness.replay.drift import decisions_digest

        assert decisions_digest(trace.decisions)
    finally:
        stack.__exit__(None, None, None)
