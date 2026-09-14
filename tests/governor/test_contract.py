"""R1 契约单测：certainty 语义、unknown≠0、range 诚实性、六类核心类型。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.governor.contract import (
    CONSERVATIVE_FLOORS,
    SCHEMA_VERSION,
    AdmissionDecision,
    Certainty,
    Dimension,
    DimValue,
    RasterWindow,
    ResourceBudget,
    ResourceDecision,
    ResourceDemand,
    ResourceEstimate,
    ResourceReservation,
    ResourceUsage,
    Subsystem,
)


class TestDimValue:
    def test_known_value_adjudges_to_itself(self):
        v = DimValue.known(1024.0, source="test")
        assert v.certainty is Certainty.KNOWN
        assert v.adjudged(dim=Dimension.MEMORY_BYTES) == 1024.0

    def test_estimated_range_is_normalized_monotonic(self):
        v = DimValue.estimated(500, 100, 300, source="t")
        # max < expected → 折回 expected；expected < min → 折回 min
        assert v.min <= v.expected <= v.max

    def test_unknown_adjudges_to_conservative_floor_never_zero(self):
        v = DimValue.unknown("no descriptor")
        for dim in Dimension:
            floor = CONSERVATIVE_FLOORS[dim]
            if dim is Dimension.ESTIMATED_LLM_COST:
                continue  # 成本维地板允许 0
            assert v.adjudged(dim=dim) == floor, dim
            assert v.adjudged(dim=dim) > 0 or floor == 0

    def test_unavailable_adjudges_zero_and_is_not_meaningful(self):
        v = DimValue.unavailable("n/a")
        assert v.adjudged(dim=Dimension.MEMORY_BYTES) == 0.0
        assert not v.is_meaningful()

    def test_estimated_without_numbers_falls_back_conservatively(self):
        v = DimValue(certainty=Certainty.ESTIMATED, reason="degenerate")
        assert v.adjudged(dim=Dimension.WALL_TIME_S) == CONSERVATIVE_FLOORS[Dimension.WALL_TIME_S]

    def test_confidence_is_clamped(self):
        assert DimValue.estimated(1, 2, 3, confidence=5.0).confidence == 1.0
        assert DimValue.estimated(1, 2, 3, confidence=-1.0).confidence == 0.0


class TestResourceEstimate:
    def test_schema_version_is_rg_v1(self):
        est = ResourceEstimate()
        assert est.schema_version == SCHEMA_VERSION

    def test_missing_dim_is_unavailable_not_zero(self):
        est = ResourceEstimate()
        assert est.dim(Dimension.MEMORY_BYTES).certainty is Certainty.UNAVAILABLE

    def test_adjudged_uses_expected(self):
        est = ResourceEstimate(dims={
            Dimension.MEMORY_BYTES: DimValue.estimated(2e8, 5.5e8, 1.4e9, source="t"),
        })
        assert est.adjudged(Dimension.MEMORY_BYTES) == pytest.approx(5.5e8)

    def test_overall_confidence_shortboard_semantics(self):
        est = ResourceEstimate(
            dims={
                Dimension.MEMORY_BYTES: DimValue.estimated(1, 2, 3, confidence=0.9),
                Dimension.WALL_TIME_S: DimValue.unknown("no data"),
            },
            confidence=0.8,
        )
        assert est.overall_confidence() == pytest.approx(0.8)
        tight = est.with_dim(
            Dimension.WALL_TIME_S,
            DimValue.estimated(1, 2, 3, confidence=0.3),
        )
        assert tight.overall_confidence() == pytest.approx(0.3)

    def test_raster_window_pixel_math(self):
        w = RasterWindow(width=1000, height=500, bands=3)
        assert w.pixels == 1_500_000

    def test_as_dict_round_trip_keys(self):
        d = ResourceEstimate(
            subsystem=Subsystem.RASTER_COMPUTE,
            dims={Dimension.PIXEL_COUNT: DimValue.known(9.0)},
        ).as_dict()
        assert d["subsystem"] == "raster_compute"
        assert d["dims"]["pixel_count"]["certainty"] == "known"


class TestCoreTypes:
    def test_decision_allowed_semantics(self):
        for dec in (AdmissionDecision.ACCEPT, AdmissionDecision.ACCEPT_WITH_LIMITS,
                    AdmissionDecision.DEFER):
            assert ResourceDecision(decision=dec).allowed
        for dec in (AdmissionDecision.DEGRADE, AdmissionDecision.REJECT):
            assert not ResourceDecision(decision=dec).allowed

    def test_decision_observe_mode_recorded(self):
        d = ResourceDecision(decision=AdmissionDecision.REJECT, mode="observe")
        assert d.mode == "observe"
        assert d.as_dict()["mode"] == "observe"

    def test_budget_scope_limit_lookup(self):
        b = ResourceBudget(scope="session", scope_id="s1",
                           limits={Dimension.MEMORY_BYTES: 1e9})
        assert b.limit_for(Dimension.MEMORY_BYTES) == 1e9
        assert b.limit_for(Dimension.WALL_TIME_S) is None
        assert b.provisional is True

    def test_demand_scope_key_fallback(self):
        assert ResourceDemand(session_id="abc").scope_key() == "abc"
        assert ResourceDemand().scope_key() == "session:anonymous"

    def test_reservation_defaults_distinct_ids(self):
        a, b = ResourceReservation(), ResourceReservation()
        assert a.reservation_id != b.reservation_id
        assert not a.released and not a.cancelled

    def test_usage_defaults_completed(self):
        u = ResourceUsage(session_id="s", tool_name="t")
        assert u.status == "completed"
        assert u.degraded is False

    def test_demand_monotonic_created_at(self):
        import time as _t
        d1 = ResourceDemand()
        _t.sleep(0.001)
        d2 = ResourceDemand()
        assert d2.created_at >= d1.created_at


class TestValidation:
    def test_invalid_confidence_rejected(self):
        with pytest.raises(ValidationError):
            DimValue(confidence=2.0)

    def test_negative_raster_window_rejected(self):
        with pytest.raises(ValidationError):
            RasterWindow(width=-1, height=10)
