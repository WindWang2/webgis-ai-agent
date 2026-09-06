"""E2SFCA (Enhanced Two-Step Floating Catchment Area) tests — Foundation V2 A4.

Covers the new ``method="e2sfca"`` branch of NetworkAccessibilityService
(Luo & Qi 2009 Gaussian band decay) plus the additive tool plumbing:

- hand-computed 3-node golden: cutoff split into decay_zones equal bands,
  band-midpoint Gaussian weights w_r = exp(-0.5*(r+0.5)^2), step1
  R_j = S_j / Σ w·P, step2 A_i = Σ w·R_j — closed form on an equator chain
  where 0.001° = R·π/180·0.001 m exactly;
- nearer-demand ranking property and determinism;
- unreachable demand stays explicit (score 0, is_served False);
- decay_zones bounds (1-10) and the parameter contract convergence;
- tool result carries the e2sfca zone-weight diagnostic in evidence.

The pre-existing 15min_circle / 2sfca paths are asserted unchanged
(backward compatibility guard for the refactor).
"""
import asyncio
import math

import pytest

from app.lib.gis.parameter_contracts import apply_contract
from app.services.network.accessibility import NetworkAccessibilityService
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import DemandPoint, Facility

pytestmark = pytest.mark.unit

# Haversine radius used by graph_builder (m); 1° of longitude on the equator
# is exactly R·π/180 metres, so per-edge travel times are closed-form.
_EARTH_R = 6371000.0
_SPEED_40_MS = 40.0 / 3.6
_T1_S = _EARTH_R * math.pi / 180.0 * 0.001 / _SPEED_40_MS  # seconds per 0.001°


def _chain_fc(n_nodes: int = 6) -> dict:
    features = []
    for i in range(n_nodes - 1):
        features.append({
            "type": "Feature",
            "properties": {"id": f"seg{i}", "speed_kmh": 40.0, "one_way": False},
            "geometry": {
                "type": "LineString",
                "coordinates": [[116.0 + i * 0.001, 0.0], [116.0 + (i + 1) * 0.001, 0.0]],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _demand(demand_id: str, lng: float, weight: float = 1.0) -> DemandPoint:
    return DemandPoint(
        demand_id=demand_id, weight=weight,
        geometry={"type": "Point", "coordinates": [lng, 0.0]},
    )


def _facility(facility_id: str, lng: float, capacity: float = 1.0) -> Facility:
    return Facility(
        facility_id=facility_id, capacity=capacity,
        geometry={"type": "Point", "coordinates": [lng, 0.0]},
    )


def _service() -> NetworkAccessibilityService:
    return NetworkAccessibilityService()


def _build_graph():
    return NetworkGraphBuilder().build_graph(_chain_fc())


class TestE2SFCAHandComputed:
    def test_e2sfca_three_node_hand_computed_golden(self):
        """f1(116.000, S=10) -- d1(116.001, w=2) -- d2(116.002, w=2), cutoff 30 s,
        decay_zones=3 (band width 10 s). d1 sits 1 hop away (t≈10.0075 s →
        band 1), d2 sits 2 hops away (t≈20.015 s → band 2):
        w1 = exp(-0.5*1.5²), w2 = exp(-0.5*2.5²)
        R  = 10/(2·w1 + 2·w2);  A_d1 = w1·R;  A_d2 = w2·R."""
        graph, dataset = _build_graph()
        svc = _service()

        res = svc.network_accessibility(
            demand_points=[_demand("d1", 116.001, weight=2.0),
                           _demand("d2", 116.002, weight=2.0)],
            facilities=[_facility("f1", 116.000, capacity=10.0)],
            graph=graph, network_dataset=dataset,
            cutoff_minutes=0.5, method="e2sfca", decay_zones=3,
        )

        t1_min = _T1_S / 60.0
        assert t1_min * 60 > 10.0 and t1_min * 60 < 20.0  # band 1 sanity
        w1 = math.exp(-0.5 * 1.5 ** 2)
        w2 = math.exp(-0.5 * 2.5 ** 2)
        expected_r = 10.0 / (2.0 * w1 + 2.0 * w2)
        scores = {m["demand_id"]: m["accessibility_score"] for m in res.per_zone_metrics}
        assert scores["d1"] == pytest.approx(w1 * expected_r, abs=1e-4)
        assert scores["d2"] == pytest.approx(w2 * expected_r, abs=1e-4)
        assert res.total_demand == 4.0
        assert res.served_demand == 4.0
        assert res.coverage_percentage == 100.0
        assert res.analysis_id.startswith("acc_e2sfca_")

    def test_e2sfca_nearer_demand_scores_higher(self):
        """Ranking property: with identical supply, the demand in the nearer
        Gaussian band must score strictly higher (monotone decay)."""
        graph, dataset = _build_graph()
        svc = _service()
        res = svc.network_accessibility(
            demand_points=[_demand("d1", 116.001), _demand("d2", 116.002)],
            facilities=[_facility("f1", 116.000, capacity=10.0)],
            graph=graph, network_dataset=dataset,
            cutoff_minutes=0.5, method="e2sfca", decay_zones=3,
        )
        scores = {m["demand_id"]: m["accessibility_score"] for m in res.per_zone_metrics}
        assert scores["d1"] > scores["d2"] > 0.0

    def test_e2sfca_single_band_matches_2sfca(self):
        """decay_zones=1 ⇒ the only band midpoint weight is exp(-0.25) on both
        steps and cancels: A_i must equal the equal-weight 2SFCA score."""
        graph, dataset = _build_graph()
        svc = _service()
        kwargs = dict(
            demand_points=[_demand("d1", 116.000, weight=2.0),
                           _demand("d2", 116.002, weight=2.0)],
            facilities=[_facility("f1", 116.001, capacity=10.0)],
            graph=graph, network_dataset=dataset, cutoff_minutes=15.0,
        )
        e2 = svc.network_accessibility(method="e2sfca", decay_zones=1, **kwargs)
        s2 = svc.network_accessibility(method="2sfca", **kwargs)
        e2_scores = {m["demand_id"]: m["accessibility_score"] for m in e2.per_zone_metrics}
        s2_scores = {m["demand_id"]: m["accessibility_score"] for m in s2.per_zone_metrics}
        assert e2_scores == s2_scores == {"d1": 2.5, "d2": 2.5}

    def test_e2sfca_unreachable_demand_explicit_zero_score(self):
        graph, dataset = NetworkGraphBuilder().build_graph({
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"id": "main", "speed_kmh": 40.0, "one_way": False},
                 "geometry": {"type": "LineString", "coordinates": [[116.0, 0.0], [116.01, 0.0]]}},
                {"type": "Feature", "properties": {"id": "island", "speed_kmh": 40.0, "one_way": False},
                 "geometry": {"type": "LineString", "coordinates": [[116.5, 0.0], [116.51, 0.0]]}},
            ],
        })
        svc = _service()
        res = svc.network_accessibility(
            demand_points=[_demand("d_main", 116.002), _demand("d_island", 116.505)],
            facilities=[_facility("f_main", 116.001, capacity=5.0)],
            graph=graph, network_dataset=dataset,
            cutoff_minutes=0.5, method="e2sfca", decay_zones=3,
        )
        by_id = {m["demand_id"]: m for m in res.per_zone_metrics}
        assert by_id["d_island"]["is_served"] is False
        assert by_id["d_island"]["accessibility_score"] == 0.0
        assert by_id["d_main"]["is_served"] is True
        assert res.unserved_demand == 1.0
        assert res.served_demand == 1.0

    def test_e2sfca_deterministic_and_2sfca_path_unchanged(self):
        graph, dataset = _build_graph()
        svc = _service()
        kwargs = dict(
            demand_points=[_demand("d1", 116.000), _demand("d2", 116.002)],
            facilities=[_facility("f1", 116.001, capacity=10.0)],
            graph=graph, network_dataset=dataset,
            cutoff_minutes=0.5, method="e2sfca", decay_zones=3,
        )
        run1 = svc.network_accessibility(**kwargs)
        run2 = svc.network_accessibility(**kwargs)
        assert run1.per_zone_metrics == run2.per_zone_metrics
        # Refactor guard: the pre-existing 2SFCA golden (vnext) still holds.
        s2 = svc.network_accessibility(
            demand_points=[_demand("d1", 116.002, weight=2.0),
                           _demand("d2", 116.006, weight=2.0)],
            facilities=[_facility("f1", 116.004, capacity=10.0)],
            graph=graph, network_dataset=dataset, cutoff_minutes=15.0, method="2sfca",
        )
        scores = {m["demand_id"]: m["accessibility_score"] for m in s2.per_zone_metrics}
        assert scores == {"d1": 2.5, "d2": 2.5}

    def test_decay_zones_bounds_rejected(self):
        graph, dataset = _build_graph()
        svc = _service()
        for bad in (0, 11, -2):
            with pytest.raises(ValueError, match="decay_zones"):
                svc.network_accessibility(
                    demand_points=[_demand("d1", 116.000)],
                    facilities=[_facility("f1", 116.001)],
                    graph=graph, network_dataset=dataset,
                    cutoff_minutes=0.5, method="e2sfca", decay_zones=bad,
                )


class TestE2SFCAContractAndTool:
    def test_accessibility_contract_e2sfca_convergence(self):
        out = apply_contract("network_accessibility_analysis", {
            "method": "e2sfca", "cutoff_minutes": 15.0, "decay_zones": 5,
        })
        assert out["method"] == "e2sfca"
        assert out["decay_zones"] == 5
        assert out["cutoff_minutes"] == 15.0
        # defaults filled
        defaulted = apply_contract("network_accessibility_analysis", {})
        assert defaulted["method"] == "15min_circle"
        assert defaulted["decay_zones"] == 3
        assert defaulted["cutoff_minutes"] == 15.0
        # out-of-range zones rejected at the parameter layer
        with pytest.raises(ValueError, match="parameter_contract_violation"):
            apply_contract("network_accessibility_analysis", {"decay_zones": 12})
        with pytest.raises(ValueError, match="parameter_contract_violation"):
            apply_contract("network_accessibility_analysis", {"method": "3sfca"})

    def test_accessibility_tool_e2sfca_evidence(self):
        from app.tools import network_tools as nt
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        nt.register_network_tools(reg)
        tool_fn = reg._tools["network_accessibility"]

        out = asyncio.run(tool_fn(
            network=_chain_fc(),
            demand_layer=[
                {"id": "d1", "weight": 2.0, "coordinates": [116.0, 0.0]},
                {"id": "d2", "weight": 2.0, "coordinates": [116.002, 0.0]},
            ],
            facilities=[{"id": "f1", "capacity": 10.0, "coordinates": [116.001, 0.0]}],
            cutoff_minutes=0.5,
            profile="walking",
            method="e2sfca",
            decay_zones=3,
        ))
        assert out.get("type") != "error", out
        ev = out.get("scientific_evidence")
        assert ev, "e2sfca run must carry scientific_evidence"
        assert ev["parameters_applied"]["method"] == "e2sfca"
        assert ev["parameters_applied"]["decay_zones"] == 3
        diag = {d["name"] for d in ev["diagnostics"]}
        assert "e2sfca_zone_weights" in diag
        assert "catchment_radius_min" in diag  # pre-existing diagnostics kept

    def test_new_contract_params_exist_in_tool_schema(self):
        """Registry parity gate semantics: contract parameter names must exist
        in the tool args model (schema properties)."""
        from app.tools import network_tools as nt

        contract = apply_contract  # noqa: F841 (documentation anchor)
        from app.lib.gis.parameter_contracts import get_parameter_contract_registry
        reg = get_parameter_contract_registry()
        c = reg.get("network_accessibility_analysis")
        assert c is not None
        for spec in c.parameters:
            assert spec.name in nt.NetworkAccessibilityArgs.model_fields, spec.name
