"""per-tool governor estimate/actual 入链契约（ADR-0214 D4，WP3）。

钉死的行为：
- dispatch 适配器 finally 把 estimate/actual 有界投影进 TurnEvidence
  （turn_id 键控；缺席诚实丢弃；观测面绝不抛）；
- TurnEvidence 资源投影有界（≤16 FIFO）；
- build_trace 填充预留 trace.governor（entries + plan_cost_delta）；
- metrics 投影 tolerant 行（replay.resource_entries / plan_cost_ratio）。
"""
from __future__ import annotations

import pytest

from app.lib.harness.replay.metrics import project_metrics
from app.lib.harness.replay.schema import build_trace
from app.lib.runtime.evidence import TurnEvidence
from app.services.governor.config import GovernorMode
from app.services.governor.dispatch_adapter import GovernorDispatchAdapter

pytestmark = pytest.mark.cartography


class _Decision:
    allowed = True
    reasons: list = []
    suggestions: list = []
    degrade_hint = {}


class _Config:
    mode = GovernorMode.ENFORCE


class _FakeGovernor:
    def __init__(self):
        self.config = _Config()

    async def admit_and_reserve(self, demand):
        return _Decision(), object(), object()

    async def complete(self, reservation, ticket, *, usage=None, actual=None,
                       estimate=None):
        return None


@pytest.mark.asyncio
async def test_adapter_projects_resource_usage_into_turn_evidence():
    turn_id = "turn-res01"
    ev = TurnEvidence(request_id="r", session_id="s-res", turn_id=turn_id,
                      run_id="run")
    from app.lib.runtime.evidence import TURN_EVIDENCE

    TURN_EVIDENCE.register(ev)
    try:
        adapter = GovernorDispatchAdapter(
            _FakeGovernor(), metadata_fn=lambda _n: {"cost": "light"})

        from types import SimpleNamespace

        async def inner():
            # 生产契约：dispatch 返回 ToolDispatchResult 形态（raw_result
            # 是工具原始返回，_cheap_actuals 只读它的 O(1) 计数键）。
            return SimpleNamespace(
                raw_result={"success": True, "feature_count": 42})

        result = await adapter.run(
            tool_name="spatial_aggregate",
            tool_args={"geometry": "x"},
            session_id="s-res",
            turn_id=turn_id,
            dispatch_inner=inner,
        )
        assert result.raw_result["feature_count"] == 42
        entries = ev.resource_usages()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["tool"] == "spatial_aggregate"
        assert entry["attempt"] == 1
        assert entry["status"] == "completed"
        assert isinstance(entry["estimate_wall_s"], float)
        assert entry["actual_wall_s"] >= 0.0
        assert entry["actual_dims"].get("feature_count") == 42.0
    finally:
        TURN_EVIDENCE.remove(turn_id)


@pytest.mark.asyncio
async def test_adapter_without_turn_id_drops_projection_honestly():
    adapter = GovernorDispatchAdapter(
        _FakeGovernor(), metadata_fn=lambda _n: {"cost": "light"})

    async def inner():
        return {"success": True}

    # turn_id 缺席 → 无证据落点，绝不抛（诚实丢弃）。
    result = await adapter.run(
        tool_name="t", tool_args={}, session_id="s", turn_id="",
        dispatch_inner=inner)
    assert result["success"] is True


def test_turn_evidence_resource_usage_bounded_fifo():
    ev = TurnEvidence(request_id="r", session_id="s", turn_id="t-cap",
                      run_id="run")
    for i in range(20):
        ev.add_resource_usage({"tool": f"t{i}", "actual_wall_s": float(i)})
    entries = ev.resource_usages()
    assert len(entries) == 16
    assert entries[0]["tool"] == "t4"
    assert entries[-1]["tool"] == "t19"


def test_build_trace_governor_payload_with_plan_cost_delta():
    chain_dict = {"stages": [], "total_records": 0}
    trace = build_trace(
        session_id="s-gov", turn_id="turn-gov01",
        chain_dict=chain_dict,
        turn_summary={
            "outcome": {"outcome": "succeeded"},
            "resource_usage": [
                {"tool": "a", "estimate_wall_s": 1.0, "actual_wall_s": 2.0,
                 "attempt": 1, "status": "completed"},
                {"tool": "b", "estimate_wall_s": 3.0, "actual_wall_s": 1.0,
                 "attempt": 1, "status": "completed"},
            ],
        },
    )
    governor = trace.governor
    assert governor is not None
    assert governor["schema_version"] == 1
    assert len(governor["entries"]) == 2
    delta = governor["plan_cost_delta"]
    assert delta["estimated_wall_s"] == 4.0
    assert delta["actual_wall_s"] == 3.0
    assert delta["ratio"] == 0.75


def test_build_trace_governor_absent_honestly():
    trace = build_trace(
        session_id="s", turn_id="turn-gov02",
        chain_dict={"stages": []},
        turn_summary={"outcome": {"outcome": "succeeded"}},
    )
    assert trace.governor is None


def test_metrics_resource_rows():
    rows = project_metrics(
        {
            "verdict": {"map_product": {}},
            "governor": {
                "schema_version": 1,
                "entries": [{"tool": "a"}, {"tool": "b"}],
                "plan_cost_delta": {"estimated_wall_s": 2.0,
                                    "actual_wall_s": 8.0, "ratio": 4.0},
            },
        },
        scene_id="scn-res",
    )
    by_id = {r["check_id"]: r["value"] for r in rows}
    assert by_id.get("replay.resource_entries") == 2.0
    assert by_id.get("replay.plan_cost_ratio") == 4.0


def test_plan_cost_delta_detects_cost_drift_through_build_trace():
    """资源策略漂移定位：actual 倍增于 estimate 经 build_trace → ratio 可见
    （review P2-6：产品代码参与断言，非测试内自算）。"""
    trace = build_trace(
        session_id="s", turn_id="turn-drift01",
        chain_dict={"stages": []},
        turn_summary={
            "outcome": {"outcome": "succeeded"},
            "resource_usage": [
                {"tool": "heavy_export", "estimate_wall_s": 5.0,
                 "actual_wall_s": 30.0, "attempt": 1, "status": "completed"},
            ],
        },
    )
    delta = trace.governor["plan_cost_delta"]
    assert delta["ratio"] == 6.0, "6× 超估算的资源策略漂移必须可见"
