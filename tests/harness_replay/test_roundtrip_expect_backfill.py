"""recorded 场景 expect 回填 + 校准契约（ADR-0214 D3，WP2）。

钉死的行为：
- trace 捕获 dispatch bind 双面证据（dispatch_evidence）→ roundtrip 派生
  候选 expect（dispatch / decision_rederive / goal）+ T3 fixture；
- 校准裁掉当前环境不可复现的期望叶并留收据（诚实披露，非静默）；
- 保留下来的期望**可失败**：篡改任一叶 → 重放翻红（非 green-by-construction）；
- 无可回填事实的录制件 → expect_underfilled 诚实标注，绝不伪造期望。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from app.lib.harness.replay.drift import rederive_capability_decision
from app.lib.harness.replay.replayer import OfflineReplayer
from app.lib.harness.replay.roundtrip import (
    calibrate_recorded_scenario,
    traces_to_scenario,
)
from app.lib.harness.replay.schema import build_trace
from app.lib.runtime.decision_record import (
    DECISION_KIND_CAPABILITY_RESOLUTION,
    decision_record,
)
from app.lib.runtime.gis_trace import GisTraceChain, Stage

pytestmark = pytest.mark.cartography

_TOOL = "webgis_layer_upsert"
_CAP = "layer_management"
#: 决策重推导 fixture 用有 ranked 候选的真实 capability
#: （layer_management 无直接 provider，重推导恒 unknown）。
_DECISION_CAP = "analytical_density"


def _recorded_trace_with_dispatch(
    *,
    turn_id: str,
    session_id: str,
    action: str = "allowed",
    with_goal: bool = False,
    with_decision: bool = False,
) -> Dict[str, Any]:
    """合成带 dispatch bind 证据的生产形态录制件（走真实 build_trace）。"""
    chain = GisTraceChain(turn_id=turn_id, session_id=session_id)
    if with_decision:
        chain.record(
            Stage.SELECTED_WORKFLOW, workflow="poi_distribution_overview",
            decision=decision_record(
                DECISION_KIND_CAPABILITY_RESOLUTION,
                selected="tool:spatial_aggregate",
                inputs={"capability": _DECISION_CAP,
                        "situation": {"crs": "EPSG:4326",
                                      "geometry_kinds": ["point"]}},
                policy_version="capability_resolution.v1",
            ),
        )
    chain.record(Stage.TOOL_CALLS, tool=_TOOL, call_id=f"{turn_id}-c1")
    chain.record(Stage.TOOL_RESULTS, tool=_TOOL, status="ok",
                 geojson_ref="ref:geojson:probe")
    entry: Dict[str, Any] = {
        "tool": _TOOL,
        "action": action,
        "capabilities": [_CAP],
        "code": "" if action == "allowed" else "CAPABILITY_INELIGIBLE",
        "reason": "",
    }
    if action == "allowed":
        entry["rank_capability"] = _CAP
        entry["rank"] = 1
        entry["score"] = 0.9
        entry["status"] = "ELIGIBLE"
    else:
        entry["capability"] = _CAP
        entry["status"] = "INELIGIBLE"
    return build_trace(
        session_id=session_id,
        turn_id=turn_id,
        chain_dict=chain.as_dict(),
        turn_summary={
            "outcome": {"outcome": "succeeded"},
            "capability_dispatches": [entry],
        },
        map_product=(
            {"status": "complete", "task_complete": True} if with_goal else None
        ),
    ).to_dict()


class TestCandidateExpect:
    def test_dispatch_evidence_lands_in_trace(self):
        trace = _recorded_trace_with_dispatch(
            turn_id="eb-a", session_id="rs-eb-a")
        assert trace["dispatch_evidence"], "dispatch 证据必须进 trace"
        assert trace["dispatch_evidence"][0]["tool"] == _TOOL
        assert trace["dispatch_evidence"][0]["action"] == "allowed"

    def test_candidate_expect_from_allowed_evidence(self):
        trace = _recorded_trace_with_dispatch(
            turn_id="eb-b", session_id="rs-eb-b")
        scenario = traces_to_scenario([trace])
        turn = scenario.turns[0]
        call_id = turn.ops[0].call_id
        assert turn.expect["dispatch"] == {call_id: {"allowed": True}}
        assert scenario.tool_registry == {_TOOL: [_CAP]}
        assert scenario.dispatch_backed is True
        assert scenario.expect_source == "recorded"
        assert "expect_recorded" in scenario.tags

    def test_candidate_expect_refused_pin(self):
        trace = _recorded_trace_with_dispatch(
            turn_id="eb-c", session_id="rs-eb-c", action="refused")
        scenario = traces_to_scenario([trace])
        expect = scenario.turns[0].expect
        call_id = scenario.turns[0].ops[0].call_id
        assert expect["dispatch"][call_id] == {
            "allowed": False, "capability": _CAP}

    def test_decision_rederive_pin_from_capability_decision(self):
        trace = _recorded_trace_with_dispatch(
            turn_id="eb-d", session_id="rs-eb-d", with_decision=True)
        record = trace["decisions"][0]
        assert record["kind"] == DECISION_KIND_CAPABILITY_RESOLUTION
        # 与重放端同源重推导，取当前可复现的 selected（自洽性锚点）。
        rebuilt = rederive_capability_decision(record)
        assert rebuilt is not None and rebuilt["selected"]
        # 把录制 selected 校正为当前重推导值（模拟录制时它就成立）。
        record["selected"] = rebuilt["selected"]
        record["decision_id"] = __import__(
            "app.lib.runtime.decision_record", fromlist=["decision_id_for"]
        ).decision_id_for(record)
        scenario = traces_to_scenario([trace])
        pin = scenario.turns[0].expect.get("decision_rederive", {}).get(
            record["decision_id"])
        assert pin is not None
        assert pin["selected"] == rebuilt["selected"]


class TestCalibration:
    @pytest.mark.asyncio
    async def test_calibration_keeps_reproducible_pins(self):
        trace = _recorded_trace_with_dispatch(
            turn_id="eb-e", session_id="rs-eb-e", with_decision=True,
            with_goal=True)
        record = trace["decisions"][0]
        rebuilt = rederive_capability_decision(record)
        record["selected"] = rebuilt["selected"]
        record["decision_id"] = __import__(
            "app.lib.runtime.decision_record", fromlist=["decision_id_for"]
        ).decision_id_for(record)
        scenario = traces_to_scenario([trace])
        assert scenario.turns[0].expect, "候选期望应含 dispatch/decision/goal"

        calibrated, receipt = await calibrate_recorded_scenario(scenario)
        kept = calibrated.turns[0].expect
        # dispatch pin（bare context 下 allowed 可复现）保留。
        call_id = calibrated.turns[0].ops[0].call_id
        assert kept["dispatch"][call_id] == {"allowed": True}
        # decision pin（同源重推导一致）保留。
        assert kept["decision_rederive"][record["decision_id"]][
            "selected"] == rebuilt["selected"]
        # goal pin（无 cartography fixture，重放端诚实 not_evaluated）被裁
        # ——收据留痕，不静默。
        assert "goal" not in kept
        assert any(d["path"].startswith("goal") for d in receipt["dropped"])
        assert receipt["residual_diffs"] == []
        assert receipt["underfilled"] is False
        assert calibrated.expect_calibration

    @pytest.mark.asyncio
    async def test_calibration_prunes_irreproducible_refusal(self):
        """录制期 refused 与 bare-context 重放 allowed 不一致 → 裁叶 + 收据。"""
        trace = _recorded_trace_with_dispatch(
            turn_id="eb-f", session_id="rs-eb-f", action="refused")
        scenario = traces_to_scenario([trace])
        calibrated, receipt = await calibrate_recorded_scenario(scenario)
        turn = calibrated.turns[0]
        # 重放端 bare context 判 allowed → 录制的 refused pin 不可复现 → 裁。
        call_id = turn.ops[0].call_id
        assert "dispatch" not in turn.expect or \
            turn.expect["dispatch"].get(call_id, {}).get("allowed") is True
        assert any(d["reason"] == "mismatch" for d in receipt["dropped"])

    @pytest.mark.asyncio
    async def test_no_facts_is_honestly_underfilled(self):
        """无终态调用、无 dispatch/decision/goal 事实 → underfilled 诚实标注。"""
        chain = GisTraceChain(turn_id="eb-g", session_id="rs-eb-g")
        chain.record(Stage.TOOL_CALLS, tool=_TOOL, call_id="c1")
        # 无 TOOL_RESULTS → 调用无终态（issued）→ 无 receipt pin 可派生。
        trace = build_trace(
            session_id="rs-eb-g", turn_id="eb-g",
            chain_dict=chain.as_dict(),
            turn_summary={"outcome": {"outcome": "succeeded"}},
        ).to_dict()
        scenario = traces_to_scenario([trace])
        assert scenario.expect_source == ""
        assert "expect_recorded" not in scenario.tags
        calibrated, receipt = await calibrate_recorded_scenario(scenario)
        assert receipt["underfilled"] is True
        assert "expect_underfilled" in calibrated.tags
        assert not any(turn.expect for turn in calibrated.turns)


class TestFalsifiability:
    @pytest.mark.asyncio
    async def test_calibrated_expect_is_falsifiable(self):
        """DoD：校准后的录制期望必须可失败 —— 篡改任一叶 → 重放翻红。"""
        trace = _recorded_trace_with_dispatch(
            turn_id="eb-h", session_id="rs-eb-h")
        scenario = traces_to_scenario([trace])
        calibrated, receipt = await calibrate_recorded_scenario(scenario)
        assert receipt["residual_diffs"] == []
        call_id = calibrated.turns[0].ops[0].call_id
        assert calibrated.turns[0].expect["dispatch"][call_id] == {
            "allowed": True}
        baseline = await OfflineReplayer(seed=9).replay_scenario(calibrated)
        assert baseline.ok

        import copy

        tampered = copy.deepcopy(calibrated)
        tampered.turns[0].expect["dispatch"][call_id]["allowed"] = False
        red = await OfflineReplayer(seed=9).replay_scenario(tampered)
        assert red.ok is False
        assert any(d["path"].startswith("dispatch.") for d in
                   red.turns[0].exact_diffs)

    @pytest.mark.asyncio
    async def test_decision_pin_catches_rederive_drift(self):
        """决策重推导漂移 → 决策 pin 翻红（decision drift 定位到 decision_id）。"""
        trace = _recorded_trace_with_dispatch(
            turn_id="eb-i", session_id="rs-eb-i", with_decision=True)
        record = trace["decisions"][0]
        rebuilt = rederive_capability_decision(record)
        record["selected"] = rebuilt["selected"]
        record["decision_id"] = __import__(
            "app.lib.runtime.decision_record", fromlist=["decision_id_for"]
        ).decision_id_for(record)
        scenario = traces_to_scenario([trace])
        # 模拟未来策略漂移：图上该 capability 的裁决变了（用篡改 selected
        # 之后的重推导面）—— 直接构造一个与重推导不同的 pin。
        scenario.turns[0].expect["decision_rederive"][record["decision_id"]][
            "selected"] = "tool:some_future_provider"
        red = await OfflineReplayer(seed=11).replay_scenario(scenario)
        assert red.ok is False
        drift_diffs = [
            d for d in red.turns[0].exact_diffs
            if d["path"].startswith("decision_rederive.")
        ]
        assert drift_diffs
        assert record["decision_id"] in drift_diffs[0]["path"]
