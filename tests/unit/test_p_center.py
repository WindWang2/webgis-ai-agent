"""P-center location-allocation tests — Foundation V2 A4.

Covers the new ``problem_type="p_center"`` branch of
NetworkLocationAllocationService (Hakimi 1964 max-min objective):

- exact enumeration golden on a tiny equator chain (minimize the maximum
  service cost over reachable demand; ties broken by total weighted cost);
- heuristic (greedy + vertex-substitution, <=10 passes) within 1.1x of the
  exact optimum on a seeded medium instance (combination budget monkeypatched
  small to force the heuristic path deterministically);
- unreachable demand excluded from the objective and disclosed via
  summary.unassigned_ids / unassigned_count;
- solver disclosure (exact|heuristic) and determinism;
- engine objective mapping (minimize_max_cost -> p_center) + bad objective.
"""
import asyncio
import math

import pytest

from app.services.network import allocation as allocation_mod
from app.services.network.allocation import NetworkLocationAllocationService
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.engine import NetworkGraphEngine
from app.services.network.models import DemandPoint, Facility

pytestmark = pytest.mark.unit

_EARTH_R = 6371000.0
_SPEED_60_MS = 60.0 / 3.6
_T1_S = _EARTH_R * math.pi / 180.0 * 0.001 / _SPEED_60_MS  # seconds per 0.001°


def _chain_fc(n_nodes: int = 13, speed_kmh: float = 60.0) -> dict:
    features = []
    for i in range(n_nodes - 1):
        features.append({
            "type": "Feature",
            "properties": {"id": f"seg{i}", "speed_kmh": speed_kmh, "one_way": False},
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


def _facility(facility_id: str, lng: float) -> Facility:
    return Facility(
        facility_id=facility_id,
        geometry={"type": "Point", "coordinates": [lng, 0.0]},
    )


def _split_world_fc() -> dict:
    """Mainland spine (116.000-116.004) + island spine (116.5-116.504)."""
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": "main", "speed_kmh": 60.0, "one_way": False},
             "geometry": {"type": "LineString", "coordinates": [[116.0, 0.0], [116.004, 0.0]]}},
            {"type": "Feature", "properties": {"id": "island", "speed_kmh": 60.0, "one_way": False},
             "geometry": {"type": "LineString", "coordinates": [[116.5, 0.0], [116.504, 0.0]]}},
        ],
    }


class TestPCenterExactGoldens:
    def test_p_center_exact_one_facility_golden(self):
        """Demands at 116.000 / 116.002 / 116.004, candidates at the same
        nodes, p=1: f@116.002 gives max cost 2·t1 — strictly better than the
        end candidates (4·t1). Max-min optimum, hand-computable."""
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        svc = NetworkLocationAllocationService()
        res = svc.location_allocation(
            candidate_facilities=[_facility("fA", 116.000), _facility("fB", 116.002),
                                  _facility("fC", 116.004)],
            demand_points=[_demand("d1", 116.000), _demand("d2", 116.002),
                           _demand("d3", 116.004)],
            p_count=1, problem_type="p_center",
            graph=graph, network_dataset=dataset,
        )
        assert res.summary["problem_type"] == "p_center"
        assert res.summary["solver"] == "exact"
        assert res.summary["unassigned_count"] == 0
        assert res.summary["max_service_cost"] == pytest.approx(2.0 * _T1_S, abs=0.01)
        chosen = {f["facility_id"] for f in res.allocated_facilities}
        assert chosen == {"fB"}
        # every reachable demand is assigned to its nearest selected facility
        assigned_ids = [d for f in res.allocated_facilities for d in f["assigned_demand_ids"]]
        assert sorted(assigned_ids) == ["d1", "d2", "d3"]

    def test_p_center_exact_two_facilities_max_cost(self):
        """p=2 on a 5-node chain with candidates at every node: the pair
        {1,3} covers every node within 1 hop, and 1 hop is a trivial lower
        bound with only 2 of 5 covered — max-min optimum = t1 (hand-proven:
        max cost is unique even though the optimal pair is not)."""
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc(5))
        svc = NetworkLocationAllocationService()
        res = svc.location_allocation(
            candidate_facilities=[_facility(f"f{i}", 116.0 + i * 0.001) for i in range(5)],
            demand_points=[_demand(f"d{i}", 116.0 + i * 0.001) for i in range(5)],
            p_count=2, problem_type="p_center",
            graph=graph, network_dataset=dataset,
        )
        assert res.summary["solver"] == "exact"
        assert res.summary["max_service_cost"] == pytest.approx(_T1_S, abs=0.01)
        assert res.summary["unassigned_count"] == 0

    def test_p_center_deterministic(self):
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        svc = NetworkLocationAllocationService()
        kwargs = dict(
            candidate_facilities=[_facility(f"f{i}", 116.0 + i * 0.002) for i in range(7)],
            demand_points=[_demand(f"d{i}", 116.0 + i * 0.001) for i in range(13)],
            p_count=3, problem_type="p_center",
            graph=graph, network_dataset=dataset,
        )
        r1 = svc.location_allocation(**kwargs)
        r2 = svc.location_allocation(**kwargs)
        assert r1.summary == r2.summary
        assert r1.allocated_facilities == r2.allocated_facilities


class TestPCenterHeuristic:
    def test_p_center_heuristic_within_11pct_of_exact(self, monkeypatch):
        """Same instance solved twice: exact (default budget) vs heuristic
        (budget monkeypatched below C(m,p) so the solver genuinely switches).
        The heuristic's max service cost must be within 1.1x of exact."""
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        facilities = [_facility(f"f{i}", 116.0 + i * 0.001) for i in range(9)]
        demands = [_demand(f"d{i}", 116.0 + i * 0.0005) for i in range(12)]
        svc = NetworkLocationAllocationService()
        kwargs = dict(
            candidate_facilities=facilities, demand_points=demands,
            p_count=3, problem_type="p_center",
            graph=graph, network_dataset=dataset,
        )
        exact = svc.location_allocation(**kwargs)
        assert exact.summary["solver"] == "exact"

        monkeypatch.setattr(allocation_mod, "_MAX_EXACT_COMBINATIONS", 10)
        heuristic = svc.location_allocation(**kwargs)
        assert heuristic.summary["solver"] == "heuristic"
        assert heuristic.summary["max_service_cost"] <= (
            1.1 * exact.summary["max_service_cost"] + 1e-6
        )

    def test_heuristic_passes_bounded(self, monkeypatch):
        """Vertex-substitution must terminate within the declared <=10 passes
        even on a degenerate all-equal-cost instance (no infinite loop)."""
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        svc = NetworkLocationAllocationService()
        monkeypatch.setattr(allocation_mod, "_MAX_EXACT_COMBINATIONS", 1)
        res = svc.location_allocation(
            candidate_facilities=[_facility(f"f{i}", 116.0 + i * 0.001) for i in range(6)],
            demand_points=[_demand(f"d{i}", 116.0 + i * 0.001) for i in range(6)],
            p_count=2, problem_type="p_center",
            graph=graph, network_dataset=dataset,
        )
        assert res.summary["solver"] == "heuristic"
        assert res.summary["max_service_cost"] >= 0.0


class TestPCenterUnreachable:
    def test_p_center_unreachable_demand_excluded_and_disclosed(self):
        """Island demand has inf cost to every candidate: it must be excluded
        from the max-min objective (inf is not a service cost) and named in
        summary.unassigned_ids — never silently dropped, never faked."""
        graph, dataset = NetworkGraphBuilder().build_graph(_split_world_fc())
        svc = NetworkLocationAllocationService()
        res = svc.location_allocation(
            candidate_facilities=[_facility("f1", 116.001), _facility("f2", 116.003)],
            demand_points=[_demand("d_main_1", 116.000), _demand("d_main_2", 116.002),
                           _demand("d_island", 116.502)],
            p_count=1, problem_type="p_center",
            graph=graph, network_dataset=dataset,
        )
        assert res.summary["problem_type"] == "p_center"
        assert res.summary["unassigned_ids"] == ["d_island"]
        assert res.summary["unassigned_count"] == 1
        # objective covers only the mainland demands: best max = t1 (f1 covers
        # d_main_1 at t1 and d_main_2 at t1)
        assert res.summary["max_service_cost"] == pytest.approx(_T1_S, abs=0.01)


class TestPCenterEngineSurface:
    def test_engine_objective_mapping_p_center(self):
        engine = NetworkGraphEngine()
        res = asyncio.run(engine.solve_location_allocation(
            network=_chain_fc(),
            candidate_facilities=[{"id": f"f{i}", "coordinates": [116.0 + i * 0.002, 0.0]}
                                  for i in range(4)],
            demand_points=[{"id": f"d{i}", "weight": 1.0,
                            "coordinates": [116.0 + i * 0.001, 0.0]} for i in range(9)],
            n_to_choose=2,
            objective="minimize_max_cost",
        ))
        assert res.summary["problem_type"] == "p_center"
        assert res.summary["max_service_cost"] > 0.0

    def test_engine_unknown_objective_rejected(self):
        engine = NetworkGraphEngine()
        with pytest.raises(ValueError, match="minimize_max_cost"):
            asyncio.run(engine.solve_location_allocation(
                network=_chain_fc(),
                candidate_facilities=[{"id": "f0", "coordinates": [116.0, 0.0]}],
                demand_points=[{"id": "d0", "coordinates": [116.001, 0.0]}],
                n_to_choose=1,
                objective="maximize_profit",
            ))

    def test_p_center_tool_evidence_backend_diagnostic(self):
        """Tool path: p-center via location_allocation carries scientific
        evidence with an honest default-path backend diagnostic."""
        from app.tools import network_tools as nt
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        nt.register_network_tools(reg)
        tool_fn = reg._tools["location_allocation"]

        out = asyncio.run(tool_fn(
            network=_chain_fc(),
            candidate_facilities=[{"id": f"f{i}", "coordinates": [116.0 + i * 0.002, 0.0]}
                                  for i in range(3)],
            demand_points=[{"id": f"d{i}", "coordinates": [116.0 + i * 0.001, 0.0]}
                           for i in range(7)],
            number_to_choose=1,
            objective="minimize_max_cost",
        ))
        assert out.get("type") != "error", out
        ev = out.get("scientific_evidence")
        assert ev, "location_allocation must carry scientific_evidence"
        assert ev["algorithm"] == "network.location_allocation"
        assert "hakimi1964" in ev["method_references"]
        names = [d["name"] for d in ev["diagnostics"]]
        assert "backend_selection" in names
        backend = next(d for d in ev["diagnostics"] if d["name"] == "backend_selection")
        assert "variant=" in backend["text"]
        assert ev["parameters_applied"]["objective"] == "minimize_max_cost"
