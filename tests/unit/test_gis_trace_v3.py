"""GIS 证据链 V3 与 replay A/B 测试（ADR-0103 §十）。"""
import pytest

from app.lib.runtime.gis_trace import (
    ALL_STAGES,
    STAGE_IDS,
    GisTraceChain,
    GisTraceRegistry,
    Stage,
    get_gis_trace_registry,
    record_stage,
)


def test_stage_vocabulary_is_canonical_18():
    assert len(ALL_STAGES) == 18
    assert STAGE_IDS == set(range(1, 19))
    assert Stage.USER_INTENT == 1
    assert Stage.USER_OUTPUT == 18
    # 关键顺序不变式
    assert Stage.PARSED_INTENT > Stage.USER_INTENT
    assert Stage.TOOL_SURFACE > Stage.SELECTED_WORKFLOW
    assert Stage.MODEL_ROUTING > Stage.TOOL_SURFACE
    assert Stage.TOOL_RESULTS > Stage.TOOL_CALLS
    assert Stage.VERIFICATION > Stage.MAP_OBSERVATION
    assert Stage.USER_OUTPUT > Stage.FINAL_VERDICT


def test_chain_record_bounded_and_ordered():
    chain = GisTraceChain(turn_id="t1", session_id="s1", max_per_stage=3)
    for i in range(6):
        chain.record(Stage.TOOL_CALLS, tool=f"t{i}")
    bucket = chain.stage_records(Stage.TOOL_CALLS)
    assert len(bucket) == 3  # 保留最近
    assert bucket[-1].payload["tool"] == "t5"


def test_chain_payload_is_sanitized():
    chain = GisTraceChain(turn_id="t2")
    rec = chain.record(Stage.MODEL_ROUTING, api_key="super-secret", model="m1")
    assert rec.payload["api_key"] == "[REDACTED]"
    assert rec.payload["model"] == "m1"


def test_chain_completeness_and_dict_shape():
    chain = GisTraceChain(turn_id="t3")
    chain.record(Stage.USER_INTENT, text="成都市热力图")
    chain.record(Stage.TOOL_CALLS, tool="spatial_aggregate")
    assert chain.completeness() == pytest.approx(2 / 18, abs=1e-3)
    d = chain.as_dict()
    assert d["total_records"] == 2
    assert d["stages"][0]["stage"] == "USER_INTENT"
    assert set(d) == {"turn_id", "session_id", "total_records", "completeness", "stages"}


def test_registry_lazy_create_and_bounded():
    reg = GisTraceRegistry(max_chains=4)
    for i in range(6):
        reg.start(f"turn-{i}")
    assert len(reg._chains) <= 4
    # record 惰性创建
    assert reg.record("late-turn", Stage.USER_INTENT, text="x") is True
    assert reg.get("late-turn") is not None
    # 空 turn 拒绝
    assert reg.record("", Stage.USER_INTENT) is False


def test_record_stage_module_entry_never_raises():
    assert record_stage("t-module", Stage.VERIFICATION, verdict="pass") is True
    assert record_stage("", Stage.VERIFICATION) is False


def test_global_registry_accessible():
    assert get_gis_trace_registry() is not None


def test_routing_bridge_records_model_route_stage(monkeypatch):
    from app.services.chat import model_routing_bridge as bridge

    monkeypatch.setattr(bridge, "_MODEL_ROUTER_ENABLED", True)
    # 伪造 runtime context 提供 turn_id
    class _Ctx:
        turn_id = "trace-route-turn"

    fake_rt = type("M", (), {"current_runtime_context": staticmethod(lambda: _Ctx())})
    import sys
    monkeypatch.setitem(sys.modules, "app.lib.runtime.context", fake_rt)

    cfg, decision = bridge.resolve_routed_config("execution")
    assert decision is not None
    chain = get_gis_trace_registry().get("trace-route-turn")
    assert chain is not None
    rec = chain.first(Stage.MODEL_ROUTING)
    assert rec is not None
    assert rec.payload["role"] == "execution"
    assert "model" in rec.payload
    get_gis_trace_registry().drop("trace-route-turn")


def test_ab_compare_tool_surface(registry=None):
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.evaluation.replay import ab_compare_tool_surface

    reg = ToolRegistry()
    init_tools(reg)
    res = ab_compare_tool_surface(
        reg,
        "成都市小学密度热力图",
        ctx_overrides_a={"k_max": 12},
        ctx_overrides_b={"k_max": 30, "task_type": "analysis"},
    )
    d = res.as_dict()
    assert set(d) == {"only_in_a", "only_in_b", "shared", "retriever_a", "retriever_b", "delta"}
    assert set(res.names_a) <= set(res.names_b) or d["delta"] > 0


def test_compare_chains_workflow_regression():
    from app.evaluation.replay import compare_chains

    a = GisTraceChain(turn_id="ca")
    a.record(Stage.USER_INTENT, text="x")
    a.record(Stage.TOOL_CALLS, tool="t")
    a.record(Stage.VERIFICATION, verdict="pass")
    b = GisTraceChain(turn_id="cb")
    b.record(Stage.USER_INTENT, text="x")
    cmp = compare_chains(a, b)
    assert cmp.missing_in_b == ["TOOL_CALLS", "VERIFICATION"]
    assert cmp.missing_in_a == []
    assert cmp.completeness_a > cmp.completeness_b


def test_route_decision_diff():
    from app.evaluation.replay import route_decision_diff

    da = type("D", (), {"as_dict": staticmethod(lambda: {"role": "execution", "model_id": "m1"})})()
    db = type("D", (), {"as_dict": staticmethod(lambda: {"role": "execution", "model_id": "m2"})})()
    diff = route_decision_diff(da, db)
    assert diff == {"model_id": {"a": "m1", "b": "m2"}}
