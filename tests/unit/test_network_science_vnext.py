"""Network Analysis scientific-honesty pack tests (VNext, ADR-0099).

Covers the four honesty axes of the network domain pack:

B. Unreachable / disconnected semantics
   - OD rows are ALWAYS complete: an island (disconnected component) target is
     listed as an explicit ``reachable=False`` + inf row, never silently missing.
   - ``DisconnectedNetwork`` is raised ONLY when every origin→destination pair
     is unreachable (whole graph disconnected from all sources).
   - Closest facility: an unreachable facility is never chosen; demands with no
     reachable facility are named in ``summary.unmatched_demand_ids``.
   - Directed one-way ring: OD costs are asymmetric (A→B ≠ B→A).

C. Snapping tolerance contract / disclosure
   - ``snap_evidence`` (per-endpoint meters + confidence) is surfaced in tool
     results; over-tolerance snaps produce explicit warnings.
   - Parameter contracts only declare parameters that genuinely exist in the
     tool signatures (parity gate semantics).

D. Accessibility methodology evidence
   - Two hand-computable 2SFCA fixtures (these are the descriptor's cited
     conformance anchors): ratio R_j = capacity / catchment demand, score
     A_i = Σ R_j within cutoff; island supply never contaminates ratios.
   - Tool result carries ``scientific_evidence`` with methodology diagnostics.

F. Determinism + hand-verifiable exact-cost shortest path + descriptor honesty
   (service_area.simple is a PROXY, accessibility references, GEODESIC class).
"""
import asyncio
import math

import pytest

from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.parameter_contracts import (
    apply_contract,
    get_parameter_contract_registry,
)
from app.lib.gis.scientific_errors import DisconnectedNetwork
from app.services.network.engine import NetworkGraphEngine
from app.services.network.facility import NetworkClosestFacilityService
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import DemandPoint, Facility, TravelProfile
from app.services.network.od_matrix import NetworkODMatrixService

pytestmark = pytest.mark.unit

# Haversine radius used by graph_builder (m); 1 degree of longitude on the
# equator is exactly R·π/180 metres.
_EARTH_R = 6371000.0


# ── fixtures ─────────────────────────────────────────────────────────

def _mainland_and_island_fc() -> dict:
    """Mainland spine (116.000–116.010 at lat 39) + a disjoint island spine
    (116.500–116.510). ~54 km apart: graph-disconnected AND far beyond a
    15-minute catchment at the builder's default 40 km/h."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"id": "main", "speed_kmh": 40.0, "one_way": False},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[116.0, 39.0], [116.01, 39.0]],
                },
            },
            {
                "type": "Feature",
                "properties": {"id": "island", "speed_kmh": 40.0, "one_way": False},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[116.5, 39.0], [116.51, 39.0]],
                },
            },
        ],
    }


def _equator_chain_fc(n_nodes: int = 6) -> dict:
    """n_nodes nodes on the equator, 0.001° apart, as 2-point segments sharing
    endpoints. On the equator haversine(east-west) = R·Δλ EXACTLY, so the
    5-edge path cost is hand-computable in closed form."""
    features = []
    for i in range(n_nodes - 1):
        features.append({
            "type": "Feature",
            "properties": {"id": f"seg{i}", "speed_kmh": 40.0, "one_way": False},
            "geometry": {
                "type": "LineString",
                "coordinates": [
                    [116.0 + i * 0.001, 0.0],
                    [116.0 + (i + 1) * 0.001, 0.0],
                ],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _one_way_ring_fc() -> dict:
    """A clockwise one-way square ring: A(116.000,39.000) → B(116.001,39.000)
    → C(116.001,39.001) → D(116.000,39.001) → A. Every segment one_way=True,
    so B→A is only reachable the long way round (3 edges)."""
    corners = [
        [116.0, 39.0], [116.001, 39.0], [116.001, 39.001], [116.0, 39.001],
    ]
    features = []
    for i in range(4):
        features.append({
            "type": "Feature",
            "properties": {"id": f"ring{i}", "speed_kmh": 40.0, "one_way": True},
            "geometry": {
                "type": "LineString",
                "coordinates": [corners[i], corners[(i + 1) % 4]],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _demand(demand_id: str, lng: float, lat: float = 39.0, weight: float = 1.0) -> DemandPoint:
    return DemandPoint(
        demand_id=demand_id, weight=weight,
        geometry={"type": "Point", "coordinates": [lng, lat]},
    )


def _facility(facility_id: str, lng: float, lat: float = 39.0, capacity: float = 1.0) -> Facility:
    return Facility(
        facility_id=facility_id, capacity=capacity,
        geometry={"type": "Point", "coordinates": [lng, lat]},
    )


# ── B1: OD unreachable targets are explicit rows, never silently missing ──

class TestODUnreachableSemantics:
    def test_od_matrix_island_target_is_explicit_unreachable_row(self):
        graph, dataset = NetworkGraphBuilder().build_graph(_mainland_and_island_fc())
        svc = NetworkODMatrixService()

        pairs = svc.network_od_matrix(
            origins=[(116.002, 39.0)],
            destinations=[(116.008, 39.0), (116.5025, 39.0)],
            graph=graph, network_dataset=dataset,
        )

        # Row count is ALWAYS origins × destinations — no silent row loss.
        assert len(pairs) == 2
        reachable = [p for p in pairs if p.reachable]
        unreachable = [p for p in pairs if not p.reachable]
        assert len(reachable) == 1 and len(unreachable) == 1

        u = unreachable[0]
        assert math.isinf(u.distance_m) and math.isinf(u.travel_time_s)
        assert "116.5025" in u.destination_id  # the island destination
        r = reachable[0]
        assert r.distance_m > 0 and r.travel_time_s > 0

    def test_solve_od_matrix_partial_island_reports_unreachable_counts(self):
        engine = NetworkGraphEngine()
        res = asyncio.run(engine.solve_od_matrix(
            network=_mainland_and_island_fc(),
            origins=[(116.002, 39.0)],
            destinations=[(116.008, 39.0), (116.5025, 39.0)],
        ))
        assert res.status == "success"
        assert len(res.od_matrix) == 2
        assert res.summary["pair_count"] == 2
        assert res.summary["unreachable_pair_count"] == 1
        assert res.summary["unreachable_ratio"] == 0.5

    def test_solve_od_matrix_all_unreachable_raises_disconnected_network(self):
        """DisconnectedNetwork ONLY for the degenerate case: every pair
        unreachable (whole graph disconnected from all sources)."""
        engine = NetworkGraphEngine()
        with pytest.raises(DisconnectedNetwork):
            asyncio.run(engine.solve_od_matrix(
                network=_mainland_and_island_fc(),
                origins=[(116.002, 39.0)],
                destinations=[(116.5025, 39.0), (116.504, 39.0)],
            ))

    def test_one_way_ring_od_costs_are_asymmetric(self):
        graph, dataset = NetworkGraphBuilder().build_graph(_one_way_ring_fc())
        svc = NetworkODMatrixService()

        a, b = (116.0, 39.0), (116.001, 39.0)
        pairs = svc.network_od_matrix(
            origins=[a, b], destinations=[b, a],
            graph=graph, network_dataset=dataset,
        )
        by_pair = {(p.origin_id, p.destination_id): p for p in pairs}
        assert len(by_pair) == 4

        # Both directions must be reachable (one-way ≠ unreachable) ...
        finite = [p for p in pairs if p.reachable and p.origin_id != p.destination_id]
        assert len(finite) >= 2
        costs = sorted(p.travel_time_s for p in finite)
        short, long = costs[0], costs[-1]
        # ... but NOT symmetric: A→B is 1 edge, B→A must go round the ring.
        assert long > short * 2.0


# ── B2: closest facility unreachable facility / unmatched demand ─────

class TestClosestFacilityUnreachableSemantics:
    def test_island_facility_not_chosen_when_reachable_competitor_exists(self):
        graph, dataset = NetworkGraphBuilder().build_graph(_mainland_and_island_fc())
        svc = NetworkClosestFacilityService()

        res = svc.network_closest_facility(
            demand_points=[_demand("d1", 116.002)],
            facilities=[_facility("f_island", 116.5025), _facility("f_near", 116.008)],
            graph=graph, network_dataset=dataset,
            target_facility_count=1,
        )

        assert len(res.routes) == 1
        assert res.routes[0].destination_id == "f_near"
        assert res.summary["unmatched_demand_count"] == 0
        assert res.summary["unmatched_demand_ids"] == []

    def test_only_island_facility_leaves_demand_explicitly_unmatched(self):
        graph, dataset = NetworkGraphBuilder().build_graph(_mainland_and_island_fc())
        svc = NetworkClosestFacilityService()

        res = svc.network_closest_facility(
            demand_points=[_demand("d1", 116.002)],
            facilities=[_facility("f_island", 116.5025)],
            graph=graph, network_dataset=dataset,
            target_facility_count=1,
        )

        assert res.routes == []
        assert res.summary["unmatched_demand_count"] == 1
        assert res.summary["unmatched_demand_ids"] == ["d1"]

    def test_engine_surface_attaches_snap_evidence_to_closest_facility(self):
        engine = NetworkGraphEngine()
        res = asyncio.run(engine.solve_closest_facility(
            network=_mainland_and_island_fc(),
            incidents=[(116.002, 39.0)],
            facilities=[(116.008, 39.0)],
        ))
        assert len(res.routes) == 1
        ev = res.summary["snap_evidence"]
        assert "incident_0" in ev and "facility_0" in ev
        assert ev["incident_0"]["distance_m"] < 500.0


# ── B3: service area — facilities that produce no isochrone are disclosed ──

class TestServiceAreaUnreachableDisclosure:
    def test_facility_without_service_area_is_listed_not_silent(self):
        engine = NetworkGraphEngine()
        res = asyncio.run(engine.solve_service_area(
            network={"type": "FeatureCollection", "features": []},
            facilities=[(116.0, 39.0)],
            breaks_minutes=[5.0, 10.0],
        ))
        assert res.service_area_breaks == []
        assert res.summary["unreachable_facility_count"] == 1
        assert res.summary["unreachable_facility_ids"] == ["fac_0"]

    def test_reachable_facility_not_flagged(self):
        engine = NetworkGraphEngine()
        res = asyncio.run(engine.solve_service_area(
            network=_mainland_and_island_fc(),
            facilities=[(116.004, 39.0)],
            breaks_minutes=[5.0],
        ))
        assert len(res.service_area_breaks) >= 1
        assert res.summary["unreachable_facility_count"] == 0
        assert res.summary["unreachable_facility_ids"] == []


# ── C: snapping disclosure + parameter contract honesty ──────────────

class TestSnappingDisclosureAndContracts:
    def test_snap_evidence_surfaced_on_shortest_path(self):
        engine = NetworkGraphEngine()
        res = asyncio.run(engine.solve_shortest_path(
            network=_equator_chain_fc(),
            origin=(116.0, 0.0),
            destination=(116.005, 0.0),
        ))
        ev = res.summary["snap_evidence"]
        assert set(ev.keys()) == {"origin", "destination"}
        for endpoint in ev.values():
            assert isinstance(endpoint["distance_m"], float)
            assert 0.0 <= endpoint["confidence"] <= 1.0
        assert res.summary["reachable"] is True
        assert res.warnings == []

    def test_snap_evidence_over_tolerance_produces_warning(self):
        engine = NetworkGraphEngine()
        # 0.006° north of the mainland spine ≈ 667 m > the 500 m snapping
        # tolerance: the snap still happens (nearest-edge吸附) but must be
        # disclosed as a warning with confidence 0.
        res = asyncio.run(engine.solve_shortest_path(
            network=_mainland_and_island_fc(),
            origin=(116.004, 39.006),
            destination=(116.008, 39.0),
        ))
        ev = res.summary["snap_evidence"]
        assert ev["origin"]["distance_m"] > 500.0
        assert ev["origin"]["confidence"] == 0.0
        assert any("snap_evidence" in w for w in res.warnings)

    def test_contracts_only_declare_params_that_exist_in_tool_signatures(self):
        from app.tools import network_tools as nt

        reg = get_parameter_contract_registry()
        cases = [
            ("network_shortest_path", nt.NetworkShortestPathArgs),
            ("network_od_matrix", nt.NetworkODMatrixArgs),
            ("network_service_area", nt.NetworkServiceAreaArgs),
        ]
        for contract_id, args_model in cases:
            contract = reg.get(contract_id)
            assert contract is not None, contract_id
            assert contract.parameters, contract_id
            for spec in contract.parameters:
                if spec.required:
                    # parity gate semantics: required names must be in the
                    # tool's args model (schema properties).
                    assert spec.name in args_model.model_fields, (
                        contract_id, spec.name)

    def test_od_cutoff_contract_rejects_negative(self):
        with pytest.raises(ValueError, match="parameter_contract_violation"):
            apply_contract("network_od_matrix", {"cutoff_s": -5.0})

    def test_shortest_path_contract_fills_defaults(self):
        out = apply_contract("network_shortest_path", {
            "origin": "[116.0, 0.0]", "destination": "[116.005, 0.0]",
        })
        assert out["impedance"] == "travel_time_s"
        assert out["profile"] == "driving"


# ── D: hand-computable 2SFCA (descriptor conformance anchors) ────────

class TestTwoSFCAHandComputed:
    def test_2sfca_single_facility_hand_computed_ratio(self):
        """1 facility (capacity 10), 2 demands (weight 2 each) within cutoff.
        R = 10/(2+2) = 2.5; A_i = 2.5 for both; coverage 100%."""
        graph, dataset = NetworkGraphBuilder().build_graph(_mainland_and_island_fc())
        svc = NetworkGraphEngine().acc_service

        res = svc.network_accessibility(
            demand_points=[_demand("d1", 116.002, weight=2.0),
                           _demand("d2", 116.006, weight=2.0)],
            facilities=[_facility("f1", 116.004, capacity=10.0)],
            graph=graph, network_dataset=dataset,
            cutoff_minutes=15.0, method="2sfca",
        )

        scores = {m["demand_id"]: m["accessibility_score"] for m in res.per_zone_metrics}
        assert scores == {"d1": 2.5, "d2": 2.5}
        assert res.total_demand == 4.0
        assert res.served_demand == 4.0
        assert res.unserved_demand == 0.0
        assert res.coverage_percentage == 100.0
        assert all(m["is_served"] for m in res.per_zone_metrics)

    def test_2sfca_island_facility_ratio_and_unreachable_demand(self):
        """Split supply: mainland f1 (capacity 6) serves mainland demand d1
        (weight 3); island f2 (capacity 10) serves island demand d2 (weight 1).
        Cross-pairs are unreachable (inf) and must NOT contaminate ratios:
        R1 = 6/3 = 2, R2 = 10/1 = 10, A(d1)=2, A(d2)=10."""
        graph, dataset = NetworkGraphBuilder().build_graph(_mainland_and_island_fc())
        svc = NetworkGraphEngine().acc_service

        res = svc.network_accessibility(
            demand_points=[_demand("d1", 116.002, weight=3.0),
                           _demand("d2", 116.502, weight=1.0)],
            facilities=[_facility("f1", 116.004, capacity=6.0),
                        _facility("f2", 116.504, capacity=10.0)],
            graph=graph, network_dataset=dataset,
            cutoff_minutes=15.0, method="2sfca",
        )

        scores = {m["demand_id"]: m["accessibility_score"] for m in res.per_zone_metrics}
        assert scores == {"d1": 2.0, "d2": 10.0}
        assert res.served_demand == 4.0
        assert res.coverage_percentage == 100.0
        # min_travel_time is only reported for reachable (d1→f1, d2→f2) legs.
        mins = {m["demand_id"]: m["min_travel_time_min"] for m in res.per_zone_metrics}
        assert mins["d1"] is not None and mins["d1"] < 15.0
        assert mins["d2"] is not None and mins["d2"] < 15.0

    def test_engine_accessibility_summary_carries_methodology_diagnostics(self):
        engine = NetworkGraphEngine()
        res = asyncio.run(engine.solve_accessibility(
            network=_mainland_and_island_fc(),
            demand_layer=[
                {"id": "d1", "weight": 2.0, "coordinates": [116.002, 39.0]},
                {"id": "d2", "weight": 2.0, "coordinates": [116.006, 39.0]},
            ],
            facilities=[{"id": "f1", "capacity": 10.0, "coordinates": [116.004, 39.0]}],
            cutoff_minutes=15.0,
        ))
        assert res.summary["catchment_radius_min"] == 15.0
        assert res.summary["demand_total"] == 4.0
        assert res.summary["supply_total"] == 10.0
        assert res.summary["facility_count"] == 1

    def test_accessibility_tool_attaches_scientific_evidence(self):
        from app.tools import network_tools as nt
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        nt.register_network_tools(reg)
        tool_fn = reg._tools["network_accessibility"]

        out = asyncio.run(tool_fn(
            network=_mainland_and_island_fc(),
            demand_layer=[
                {"id": "d1", "weight": 2.0, "coordinates": [116.002, 39.0]},
                {"id": "d2", "weight": 2.0, "coordinates": [116.006, 39.0]},
            ],
            facilities=[{"id": "f1", "capacity": 10.0, "coordinates": [116.004, 39.0]}],
            cutoff_minutes=15.0,
            profile="walking",
        ))

        ev = out.get("scientific_evidence")
        assert ev, "accessibility tool result must carry scientific_evidence"
        assert ev["algorithm"] == "network.accessibility"
        assert "luo_qi2009" in ev["method_references"]
        diag = {d["name"]: d for d in ev["diagnostics"]}
        assert diag["catchment_radius_min"]["value"] == 15.0
        assert diag["demand_total"]["value"] == 4.0
        assert diag["supply_total"]["value"] == 10.0
        assert ev["reproducibility"]["deterministic"] is True


# ── F: exact-cost shortest path, determinism, descriptor honesty ─────

class TestExactCostShortestPath:
    def test_shortest_path_six_node_chain_exact_cost(self):
        """5 edges × 0.001° on the equator: haversine east-west = R·Δλ exactly,
        so the route cost is hand-computable in closed form (conformance
        anchor cited by the network.shortest_path descriptor)."""
        engine = NetworkGraphEngine()
        graph, dataset = engine.build_network(_equator_chain_fc())

        route = engine.shortest_path(
            (116.0, 0.0), (116.005, 0.0), dataset, graph=graph,
            profile=TravelProfile(name="driving", impedance_field="length_m"),
            impedance=None,
        )

        expected = 5 * _EARTH_R * math.radians(0.001)
        assert len(route.path_node_ids) == 6
        assert abs(route.total_distance_m - expected) < 1e-3
        assert abs(route.total_cost - expected) < 1e-3
        assert route.total_distance_m > 0


class TestDeterminism:
    @staticmethod
    def _scrub_synthetic_ids(route_dict: dict) -> dict:
        """Virtual-node handles (vt_<uuid>, split edge ids) are per-run
        synthetic identifiers — the semantic content (totals, geometry,
        directions) is deterministic. Scrub them before comparing."""
        route_dict["path_node_ids"] = [
            "<vt>" if str(n).startswith("vt_") else n for n in route_dict["path_node_ids"]
        ]
        route_dict["path_edge_ids"] = [
            "<vt>" if "_sp" in str(e) else e for e in route_dict["path_edge_ids"]
        ]
        return route_dict

    def test_same_inputs_produce_identical_results(self):
        fc = _mainland_and_island_fc()

        def _run():
            engine = NetworkGraphEngine()
            graph, dataset = engine.build_network(
                {"type": "FeatureCollection", "features": fc["features"]},
            )
            route = engine.shortest_path(
                (116.0, 39.0), (116.008, 39.0), dataset, graph=graph,
                profile=TravelProfile(name="driving", impedance_field="length_m"),
            )
            od = engine.od_matrix(
                [(116.002, 39.0)], [(116.008, 39.0), (116.5025, 39.0)],
                dataset, graph=graph,
            )
            sa = engine.service_area(
                facilities=[(116.004, 39.0)], breaks=[5.0],
                network_dataset=dataset, graph=graph,
            )
            return route, od, sa

        route1, od1, sa1 = _run()
        route2, od2, sa2 = _run()

        assert self._scrub_synthetic_ids(route1.model_dump()) == \
            self._scrub_synthetic_ids(route2.model_dump())
        assert [p.model_dump() for p in od1] == [p.model_dump() for p in od2]
        assert len(sa1) == len(sa2) == 1
        for b1, b2 in zip(sa1[0].breaks, sa2[0].breaks):
            assert b1.reachable_edge_count == b2.reachable_edge_count
            assert b1.geometry["type"] == b2.geometry["type"]


class TestDescriptorHonesty:
    def test_service_area_simple_is_declared_proxy_with_limitations(self):
        algo = get_algorithm_registry().get("network.service_area.simple")
        assert algo is not None
        assert algo.approximate is True
        assert algo.fallback_semantics.get("network.isochrone") == "proxy", (
            "speed-table buffer vs real network isochrone is a proximity "
            "proxy, not an equivalence"
        )
        assert algo.limitations
        assert any("直线" in lim or "欧氏" in lim for lim in algo.limitations)

    def test_accessibility_descriptor_references_2sfca(self):
        algo = get_algorithm_registry().get("network.accessibility")
        assert "luo_qi2009" in algo.method_references
        assert algo.algorithm_family == "accessibility"
        assert any("2SFCA" in a for a in algo.assumptions)
        assert any("E2SFCA" in lim for lim in algo.limitations)
        assert algo.uncertainty_outputs == []
        assert algo.scientific_status == "VALIDATED"
        assert algo.conformance_tests  # the two hand-computed fixtures above
        for node in algo.conformance_tests:
            assert node.startswith("tests/unit/test_network_science_vnext.py::")

    def test_shortest_path_descriptor_geodesic_and_validated(self):
        algo = get_algorithm_registry().get("network.shortest_path")
        assert algo.crs_class == "GEODESIC"
        assert "dijkstra1959" in algo.method_references
        assert algo.scientific_status == "VALIDATED"
        assert algo.parameter_contract_ref == "network_shortest_path"

    def test_network_pack_fallback_semantics_complete(self):
        """Every fallback edge in the network pack must carry a scientific
        equivalence class (backbone rule, verified after all changes)."""
        for aid in get_algorithm_registry().all_ids:
            if not aid.startswith("network.") and not aid.startswith("flow."):
                continue
            algo = get_algorithm_registry().get(aid)
            for fb in algo.fallback_algorithms:
                assert fb in algo.fallback_semantics, (aid, fb)

    def test_external_route_tools_are_bound_and_honestly_flagged(self):
        """The formerly orphan external-API tools are bound to capabilities
        with honest non-determinism + external-dependency disclosure."""
        reg = get_algorithm_registry()
        for algo_id, tool_name in [
            ("network.route_external_api", "plan_route"),
            ("network.transit_route_external", "search_transit_route"),
            ("network.traffic_status_external", "get_traffic_status"),
        ]:
            algo = reg.get(algo_id)
            assert algo is not None, algo_id
            assert tool_name in algo.tool_candidates
            assert algo.deterministic is False, algo_id
            assert algo.scientific_status == "EXPERIMENTAL"
            assert any("外部依赖" in lim or "API_KEY" in lim for lim in algo.limitations)
        # isochrone_network (real network isochrone) gets its own descriptor —
        # the two existing service_area algorithms' tool_candidates are legacy
        # exactness contracts and must not grow.
        assert reg.get("network.service_area.multi").tool_candidates == ["network_service_area"]
        assert "isochrone_network" in reg.get("network.isochrone.local").tool_candidates
        assert reg.get("network.isochrone").tool_candidates == ["isochrone_analysis"]
