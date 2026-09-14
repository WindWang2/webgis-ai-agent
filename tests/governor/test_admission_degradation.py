"""R3/R7/R8/R11/R12 单测：准入决策链、降级计划、计划提示、健康视图、context 协同。"""
from __future__ import annotations

from pathlib import Path


from app.services.governor.admission import AdmissionPolicy
from app.services.governor.config import load_manifest
from app.services.governor.contract import (
    AdmissionDecision,
    Dimension,
    DimValue,
    ResourceClass,
    ResourceDemand,
    ResourceEstimate,
    Subsystem,
)
from app.services.governor.degradation import (
    DegradeAction,
    DegradationSemantics,
    plan_degradation,
)
from app.services.governor.health import HealthView
from app.services.governor.session_budget import BudgetViolation, SessionBudgetLedger
from app.services.governor.context_link import (
    PlanAlternativeView,
    ResourceAwarePlanHint,
    advise_plan,
    context_priority_hints,
    record_context_report,
)

_MANIFEST = Path(__file__).resolve().parents[2] / "config" / "governor_budgets.json"


def _demand(*, rclass=ResourceClass.LIGHT, memory: float = 1e8,
            wall: float = 1.0, subsystem=Subsystem.TOOL_DISPATCH,
            session_id: str = "s1", unknown_memory: bool = False) -> ResourceDemand:
    dims = {Dimension.WALL_TIME_S: DimValue.known(wall)}
    if unknown_memory:
        dims[Dimension.MEMORY_BYTES] = DimValue.unknown("no descriptor")
    elif memory > 0:
        dims[Dimension.MEMORY_BYTES] = DimValue.known(memory)
    est = ResourceEstimate(resource_class=rclass, subsystem=subsystem, dims=dims)
    return ResourceDemand(session_id=session_id, estimate=est)


def _policy(**kw) -> AdmissionPolicy:
    base = dict(global_memory_pressure_bytes=6e9, slo_breach_soft_limit=20,
                defer_when_channel_waiters=8)
    base.update(kw)
    return AdmissionPolicy(SessionBudgetLedger(load_manifest(_MANIFEST)), **base)


class TestAdmissionDecisions:
    def test_clean_demand_accepts(self):
        d = _policy().decide(_demand())
        assert d.decision is AdmissionDecision.ACCEPT
        assert "clean_admission" in d.reasons
        assert d.estimate_snapshot is not None

    def test_unknown_memory_charged_conservatively(self):
        # unknown memory → 保守地板 256MiB（unknown≠0）：用 128MiB 的
        # session 预算让地板值必然触线
        session_budget = load_manifest(_MANIFEST)["session"].model_copy(update={
            "limits": {Dimension.MEMORY_BYTES: 128 * 1024**2},
        })
        pol = AdmissionPolicy(SessionBudgetLedger({"session": session_budget}),
                              global_memory_pressure_bytes=1e12)
        d = pol.decide(_demand(unknown_memory=True))
        assert any(r.startswith("soft_budget:session:memory_bytes") for r in d.reasons)
        # provisional → 仍是 accept（校准前不硬拒）
        assert d.decision is AdmissionDecision.ACCEPT

    def test_hard_violation_rejects(self):
        led = SessionBudgetLedger({
            "session": load_manifest(_MANIFEST)["session"].model_copy(
                update={"provisional": False}),
        })
        pol = AdmissionPolicy(led, global_memory_pressure_bytes=1e12)
        d = pol.decide(_demand(memory=4e9))
        assert d.decision is AdmissionDecision.REJECT
        assert any(r.startswith("hard_budget:session") for r in d.reasons)
        assert d.suggestions

    def test_memory_pressure_defers_light_rejects_remote_heavy(self):
        pol = _policy(global_memory_pressure_bytes=2e8)
        # 已有 1e8 在飞 → light(1.5e8) 触发压力 → defer；heavy(4e8)+remote → reject
        light = pol.decide(_demand(memory=1.5e8), global_live_memory=1e8)
        assert light.decision is AdmissionDecision.DEFER
        assert light.queue_hint == "heavy"

        remote_heavy = pol.decide(
            _demand(rclass=ResourceClass.HEAVY, memory=4e8),
            global_live_memory=1e8, remote_only=True)
        assert remote_heavy.decision is AdmissionDecision.REJECT

    def test_provider_open_degrades_with_local_fallback(self):
        pol = _policy()
        d = pol.decide(_demand(subsystem=Subsystem.DATA_FABRIC),
                       open_providers=["osm.remote"])
        assert d.decision is AdmissionDecision.DEGRADE
        assert any(r.startswith("provider_open:") for r in d.reasons)
        steps = (d.degrade_hint or {}).get("steps", [])
        assert any(s["action"] == "local_fallback" for s in steps)

    def test_remote_only_with_open_provider_rejects(self):
        pol = _policy()
        d = pol.decide(_demand(), open_providers=["osm.remote"], remote_only=True)
        assert d.decision is AdmissionDecision.REJECT

    def test_slo_backlog_tightens_limits(self):
        pol = _policy(slo_breach_soft_limit=5)
        d = pol.decide(_demand(wall=120.0), slo_breach_total=10)
        assert d.decision is AdmissionDecision.ACCEPT_WITH_LIMITS
        assert d.limits[Dimension.WALL_TIME_S] <= 60.0

    def test_channel_backlog_predicts_defer(self):
        pol = _policy(defer_when_channel_waiters=2)
        state = {"raster": (1, 5)}
        d = pol.decide(_demand(rclass=ResourceClass.RASTER), channel_state=state)
        assert d.decision is AdmissionDecision.DEFER
        assert d.queue_hint == "raster"

    def test_decision_is_deterministic(self):
        pol = _policy()
        a = pol.decide(_demand(unknown_memory=True))
        b = pol.decide(_demand(unknown_memory=True))
        assert a.as_dict() == b.as_dict()


class TestDegradation:
    def test_no_violations_empty_plan(self):
        plan = plan_degradation(_demand(), [])
        assert plan.steps == []
        assert plan.best_semantics is None

    def test_feature_violation_maps_to_sample(self):
        v = BudgetViolation(
            scope="session", scope_id="s1", check="dim",
            dimension=Dimension.FEATURE_COUNT, projected=1e6, limit=5e5,
            accounting="live", provisional=True)
        plan = plan_degradation(
            _demand(subsystem=Subsystem.VECTOR_COMPUTE), [v])
        assert plan.steps
        assert plan.steps[0].action is DegradeAction.SAMPLE_FEATURES
        assert plan.best_semantics is not DegradationSemantics.NON_COMPARABLE
        assert plan.steps[0].reason_codes

    def test_pixel_violation_prefers_coarsen_for_raster(self):
        v = BudgetViolation(
            scope="session", scope_id="s1", check="dim",
            dimension=Dimension.PIXEL_COUNT, projected=1e9, limit=5e8,
            accounting="live", provisional=True)
        plan = plan_degradation(
            _demand(subsystem=Subsystem.RASTER_COMPUTE), [v])
        assert plan.steps[0].action is DegradeAction.COARSEN_RESOLUTION

    def test_plan_is_deterministic_and_capped(self):
        vs = [
            BudgetViolation(scope="session", scope_id="s1", check="dim",
                            dimension=dim, projected=1e9, limit=1e8,
                            accounting="live", provisional=True)
            for dim in (Dimension.FEATURE_COUNT, Dimension.PIXEL_COUNT,
                        Dimension.MEMORY_BYTES, Dimension.WALL_TIME_S)
        ]
        p1 = plan_degradation(_demand(), vs)
        p2 = plan_degradation(_demand(), vs)
        assert [s.action for s in p1.steps] == [s.action for s in p2.steps]
        assert len(p1.steps) <= 3


class TestAdvisePlan:
    def test_kde_scenario_picks_cheap_alternative_under_budget(self):
        exact = ResourceEstimate(dims={
            Dimension.MEMORY_BYTES: DimValue.estimated(2e9, 4e9, 6e9),
            Dimension.WALL_TIME_S: DimValue.known(30.0)})
        native = ResourceEstimate(dims={
            Dimension.MEMORY_BYTES: DimValue.estimated(1e8, 3e8, 5e8),
            Dimension.WALL_TIME_S: DimValue.known(5.0)})
        hint = ResourceAwarePlanHint(
            plan_id="kde", session_id="s1",
            alternatives=[PlanAlternativeView("exact_kde", exact),
                          PlanAlternativeView("native_heatmap", native)])
        budget = load_manifest(_MANIFEST)["session"]  # 2GiB
        advice = advise_plan(hint, budget)
        assert advice.preferred == "native_heatmap"
        assert "exact_kde" in " ".join(advice.reasons)

    def test_all_infeasible_honest_none(self):
        huge = ResourceEstimate(dims={
            Dimension.MEMORY_BYTES: DimValue.known(1e12)})
        hint = ResourceAwarePlanHint(
            plan_id="p", alternatives=[PlanAlternativeView("only", huge)])
        advice = advise_plan(hint, load_manifest(_MANIFEST)["session"])
        assert advice.preferred is None
        assert advice.reasons

    def test_no_budget_prefers_smallest_work(self):
        a = ResourceEstimate(dims={Dimension.MEMORY_BYTES: DimValue.known(1e9)})
        b = ResourceEstimate(dims={Dimension.MEMORY_BYTES: DimValue.known(2e6)})
        advice = advise_plan(
            ResourceAwarePlanHint("p", alternatives=[
                PlanAlternativeView("big", a), PlanAlternativeView("small", b)]),
            None)
        assert advice.preferred == "small"


class _FakeBreakerRegistry:
    def __init__(self, states):
        self._states = states

    def state(self, key):
        class _S:
            def __init__(self, v):
                self.value = v
        return _S(self._states[key])


class TestHealthView:
    def test_open_provider_detected(self):
        hv = HealthView(_FakeBreakerRegistry({"osm": "open", "bing": "closed"}))
        assert hv.is_open("osm") is True
        assert hv.provider_state("bing") == "closed"

    def test_unknown_source_is_unknown_not_open(self):
        hv = HealthView(_FakeBreakerRegistry({}))
        assert hv.provider_state("anything") == "unknown"
        assert hv.is_open("anything") is False

    def test_registry_failure_is_fail_open(self):
        class _Boom:
            def state(self, _):
                raise RuntimeError("down")
        hv = HealthView(_Boom())
        assert hv.provider_state("x") == "unknown"


class _FakeReport:
    def __init__(self):
        self.total_est_tokens = 4200
        self.over_budget = False
        self.window_unknown = False
        self.violations = ["over_limit:tool_schemas:9000>8000"]


class _FakeAdvice:
    def __init__(self):
        self.actions = [
            {"item": "h1", "category": "HISTORY", "action": "drop_oldest",
             "est_tokens": 900},
            {"item": "r1", "category": "TOOL_RESULTS", "action": "offload_ref",
             "est_tokens": 5000},
            {"item": "s1", "category": "SESSION_PLAN", "action": "condense",
             "est_tokens": 1200},
        ]


class TestContextLink:
    def test_record_context_report_to_ledger(self):
        led = SessionBudgetLedger(load_manifest(_MANIFEST))
        summary = record_context_report(led, "s1", "t1", _FakeReport())
        assert summary["tokens"] == 4200
        snap = led.snapshot("s1", turn_id="t1")
        assert snap["session"]["cumulative"]["context_tokens"] == 4200

    def test_priority_hints_cost_ordered(self):
        hints = context_priority_hints(_FakeAdvice())
        assert hints[0].startswith("offload_ref:r1")
        assert hints[-1].startswith("condense:s1")

    def test_record_zero_tokens_is_noop(self):
        led = SessionBudgetLedger(load_manifest(_MANIFEST))
        led.record_context_tokens("s1", "t1", 0)
        assert led.snapshot("s1") == {}
