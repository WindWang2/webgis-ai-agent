"""Gravity accessibility + Huff spatial interaction tests — Foundation V2 A4.

Covers ``NetworkInteractionService`` (app/services/network/interaction.py)
over an equator chain where 0.001° = R·π/180·0.001 m exactly and per-edge
travel time is closed-form:

- gravity (Hansen 1959 / Zipf 1946): A_i = Σ S_j^α / d_ij^β hand-computed
  single-pair golden, α acting on capacity, nearer-demand ranking property,
  unreachable-pair disclosure, DisconnectedNetwork on all-unreachable;
- Huff (1964): P_ij = A_j·d_ij^-β / Σ_k A_k·d_ik^-β — single facility gives
  P=1 (entropy 0), two equidistant equal facilities split 0.5/0.5
  (entropy ln 2), market share = Σ w·P, captive share for single-candidate
  demands, cutoff-driven unassigned disclosure;
- parameter bounds (α∈[0,3], β∈[0.5,4]) rejected at the service layer;
- parameter-contract convergence for both tools and registry parity of the
  contract names against the tool args models;
- tool evidence blocks carry honest backend-selection diagnostics.
"""
import asyncio
import math

import pytest

from app.lib.gis.parameter_contracts import (
    apply_contract,
    get_parameter_contract_registry,
)
from app.lib.gis.scientific_errors import DisconnectedNetwork
from app.services.network.engine import NetworkGraphEngine
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.interaction import NetworkInteractionService
from app.services.network.models import DemandPoint, Facility

pytestmark = pytest.mark.unit

_EARTH_R = 6371000.0
_SPEED_40_MS = 40.0 / 3.6
_T1_S = _EARTH_R * math.pi / 180.0 * 0.001 / _SPEED_40_MS  # seconds per 0.001°


def _chain_fc(n_nodes: int = 8) -> dict:
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


def _split_world_fc() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"id": "main", "speed_kmh": 40.0, "one_way": False},
             "geometry": {"type": "LineString", "coordinates": [[116.0, 0.0], [116.004, 0.0]]}},
            {"type": "Feature", "properties": {"id": "island", "speed_kmh": 40.0, "one_way": False},
             "geometry": {"type": "LineString", "coordinates": [[116.5, 0.0], [116.504, 0.0]]}},
        ],
    }


def _demand(demand_id: str, lng: float, weight: float = 1.0, lat: float = 0.0) -> DemandPoint:
    return DemandPoint(
        demand_id=demand_id, weight=weight,
        geometry={"type": "Point", "coordinates": [lng, lat]},
    )


def _facility(facility_id: str, lng: float, capacity: float = 1.0, lat: float = 0.0) -> Facility:
    return Facility(
        facility_id=facility_id, capacity=capacity,
        geometry={"type": "Point", "coordinates": [lng, lat]},
    )


def _service() -> NetworkInteractionService:
    return NetworkInteractionService()


def _build(chain_fc: dict):
    return NetworkGraphBuilder().build_graph(chain_fc)


class TestGravityAccessibility:
    def test_gravity_single_pair_hand_computed(self):
        """1 demand (116.000) + 1 facility (116.001, S=4), α=1, β=2:
        A = 4 / t1² — closed form, no ambiguity."""
        graph, dataset = _build(_chain_fc())
        res = _service().gravity_accessibility(
            demand_points=[_demand("d1", 116.000)],
            facilities=[_facility("f1", 116.001, capacity=4.0)],
            graph=graph, network_dataset=dataset,
        )
        expected = 4.0 / (_T1_S ** 2)
        row = res.per_demand_metrics[0]
        assert row["score"] == pytest.approx(expected, abs=1e-6)
        assert row["reachable_facility_count"] == 1
        assert row["top_facility_contributions"][0]["share"] == pytest.approx(1.0, abs=1e-9)
        assert res.reachable_pair_count == 1
        assert res.unreachable_pair_count == 0
        assert res.impedance_field == "travel_time_s"

    def test_gravity_mass_exponent_acts_on_capacity(self):
        """α=2 must square the capacity term: A(α=2) = S²/t1² = 4·A(α=1)
        (ratio tolerance widened: per-record scores are rounded to 6 dp)."""
        graph, dataset = _build(_chain_fc())
        svc = _service()
        kwargs = dict(
            demand_points=[_demand("d1", 116.000)],
            facilities=[_facility("f1", 116.001, capacity=4.0)],
            graph=graph, network_dataset=dataset, distance_decay=2.0,
        )
        a1 = svc.gravity_accessibility(mass_exponent=1.0, **kwargs)
        a2 = svc.gravity_accessibility(mass_exponent=2.0, **kwargs)
        expected_a2 = 16.0 / _T1_S ** 2
        assert a2.per_demand_metrics[0]["score"] == pytest.approx(expected_a2, abs=1e-6)
        ratio = a2.per_demand_metrics[0]["score"] / a1.per_demand_metrics[0]["score"]
        assert ratio == pytest.approx(4.0, rel=1e-4)

    def test_gravity_nearer_demand_scores_higher(self):
        """f at 116.000: near (116.001) is 1 hop, far (116.002) is 2 hops —
        β=2 inverse-square decay must rank them strictly."""
        graph, dataset = _build(_chain_fc())
        res = _service().gravity_accessibility(
            demand_points=[_demand("near", 116.001), _demand("far", 116.002)],
            facilities=[_facility("f1", 116.000, capacity=4.0)],
            graph=graph, network_dataset=dataset,
        )
        scores = {m["demand_id"]: m["score"] for m in res.per_demand_metrics}
        assert scores["near"] == pytest.approx(4.0 / _T1_S ** 2, abs=1e-6)
        assert scores["far"] == pytest.approx(4.0 / (2 * _T1_S) ** 2, abs=1e-6)
        assert scores["near"] > scores["far"]

    def test_gravity_unreachable_pairs_disclosed(self):
        """Split world: 3 reachable pairs (mainland d↔mainland f, island
        d↔island f), 3 unreachable — counted, never silently dropped."""
        graph, dataset = _build(_split_world_fc())
        res = _service().gravity_accessibility(
            demand_points=[_demand("d_main_1", 116.001), _demand("d_main_2", 116.003),
                           _demand("d_island", 116.502)],
            facilities=[_facility("f_main", 116.002, capacity=4.0),
                        _facility("f_island", 116.502, capacity=4.0)],
            graph=graph, network_dataset=dataset,
        )
        assert res.reachable_pair_count == 3
        assert res.unreachable_pair_count == 3
        by_id = {m["demand_id"]: m for m in res.per_demand_metrics}
        assert by_id["d_island"]["reachable_facility_count"] == 1
        assert by_id["d_main_1"]["reachable_facility_count"] == 1
        assert res.summary["demands_without_supply"] == 0

    def test_gravity_cutoff_skips_pairs_and_all_unreachable_ok(self):
        """cutoff beyond every pair ⇒ all scores 0 (honest zeros with cutoff
        set — no DisconnectedNetwork, the cutoff is an explicit budget)."""
        graph, dataset = _build(_chain_fc())
        res = _service().gravity_accessibility(
            demand_points=[_demand("d1", 116.000)],
            facilities=[_facility("f1", 116.004, capacity=4.0)],
            graph=graph, network_dataset=dataset,
            cutoff_cost=_T1_S,  # 3·t1 pair exceeds it
        )
        assert res.per_demand_metrics[0]["score"] == 0.0
        # the pair is graph-reachable (finite OD cost) but beyond the budget:
        # reachability counts stay honest, the cutoff filters contributions
        assert res.reachable_pair_count == 1
        assert res.unreachable_pair_count == 0
        assert res.summary["demands_without_supply"] == 1

    def test_gravity_all_unreachable_no_cutoff_raises_disconnected(self):
        graph, dataset = _build(_split_world_fc())
        with pytest.raises(DisconnectedNetwork):
            _service().gravity_accessibility(
                demand_points=[_demand("d_main", 116.001)],
                facilities=[_facility("f_island", 116.502)],
                graph=graph, network_dataset=dataset,
            )

    def test_gravity_parameter_bounds_rejected(self):
        graph, dataset = _build(_chain_fc())
        svc = _service()
        base = dict(
            demand_points=[_demand("d1", 116.000)],
            facilities=[_facility("f1", 116.001)],
            graph=graph, network_dataset=dataset,
        )
        with pytest.raises(ValueError, match="mass_exponent"):
            svc.gravity_accessibility(mass_exponent=3.5, **base)
        with pytest.raises(ValueError, match="mass_exponent"):
            svc.gravity_accessibility(mass_exponent=-0.1, **base)
        with pytest.raises(ValueError, match="distance_decay"):
            svc.gravity_accessibility(distance_decay=0.1, **base)
        with pytest.raises(ValueError, match="distance_decay"):
            svc.gravity_accessibility(distance_decay=4.5, **base)
        with pytest.raises(ValueError, match="需求点"):
            svc.gravity_accessibility(demand_points=[], facilities=[_facility("f1", 116.0)],
                                      graph=graph, network_dataset=dataset)

    def test_gravity_deterministic(self):
        graph, dataset = _build(_chain_fc())
        svc = _service()
        kwargs = dict(
            demand_points=[_demand("d1", 116.000), _demand("d2", 116.002)],
            facilities=[_facility("f1", 116.001, capacity=4.0),
                        _facility("f2", 116.003, capacity=2.0)],
            graph=graph, network_dataset=dataset,
        )
        r1 = svc.gravity_accessibility(**kwargs)
        r2 = svc.gravity_accessibility(**kwargs)
        assert r1.model_dump() == r2.model_dump()


class TestHuffProbabilities:
    def test_huff_single_facility_probability_one_entropy_zero(self):
        """Single candidate ⇒ P=1, entropy 0, market share = demand weight."""
        graph, dataset = _build(_chain_fc())
        res = _service().huff_probabilities(
            demand_points=[_demand("d1", 116.000, weight=3.0)],
            facilities=[_facility("f1", 116.001, capacity=4.0)],
            graph=graph, network_dataset=dataset,
        )
        row = res.per_demand_metrics[0]
        assert row["top_facility_shares"][0]["share"] == pytest.approx(1.0, abs=1e-9)
        assert row["share_entropy"] == pytest.approx(0.0, abs=1e-9)
        assert row["candidate_count"] == 1
        fac = res.facility_metrics[0]
        assert fac["market_share_weight"] == pytest.approx(3.0, abs=1e-6)
        assert fac["market_share_ratio"] == pytest.approx(1.0, abs=1e-6)

    def test_huff_two_equal_facilities_split_half(self):
        """d at 116.002, f1 at 116.001, f2 at 116.003, equal capacity ⇒
        P=0.5/0.5 and Shannon entropy ln 2 (hand-computed)."""
        graph, dataset = _build(_chain_fc())
        res = _service().huff_probabilities(
            demand_points=[_demand("d1", 116.002, weight=2.0)],
            facilities=[_facility("f1", 116.001, capacity=4.0),
                        _facility("f2", 116.003, capacity=4.0)],
            graph=graph, network_dataset=dataset,
        )
        row = res.per_demand_metrics[0]
        shares = {s["facility_id"]: s["share"] for s in row["top_facility_shares"]}
        assert shares["f1"] == pytest.approx(0.5, abs=1e-9)
        assert shares["f2"] == pytest.approx(0.5, abs=1e-9)
        assert row["share_entropy"] == pytest.approx(math.log(2.0), abs=1e-6)
        market = {f["facility_id"]: f["market_share_weight"] for f in res.facility_metrics}
        assert market["f1"] == pytest.approx(1.0, abs=1e-6)
        assert market["f2"] == pytest.approx(1.0, abs=1e-6)

    def test_huff_stronger_nearer_facility_wins_share(self):
        """β and A both active: the nearer, larger facility must take the
        strictly larger share (monotonicity of the Huff ratio)."""
        graph, dataset = _build(_chain_fc())
        res = _service().huff_probabilities(
            demand_points=[_demand("d1", 116.000)],
            facilities=[_facility("near_big", 116.001, capacity=8.0),
                        _facility("far_small", 116.004, capacity=1.0)],
            graph=graph, network_dataset=dataset,
        )
        shares = {s["facility_id"]: s["share"]
                  for s in res.per_demand_metrics[0]["top_facility_shares"]}
        assert shares["near_big"] > shares["far_small"]
        assert shares["near_big"] + shares["far_small"] == pytest.approx(1.0, abs=1e-9)

    def test_huff_market_and_captive_shares(self):
        """Island demand is captive to the island facility; mainland demands
        choose between two mainland facilities (non-captive)."""
        graph, dataset = _build(_split_world_fc())
        res = _service().huff_probabilities(
            demand_points=[_demand("d_main_1", 116.001, weight=2.0),
                           _demand("d_main_2", 116.003, weight=2.0),
                           _demand("d_island", 116.502, weight=1.0)],
            facilities=[_facility("f_main_1", 116.000, capacity=2.0),
                        _facility("f_main_2", 116.004, capacity=2.0),
                        _facility("f_island", 116.502, capacity=2.0)],
            graph=graph, network_dataset=dataset,
        )
        by_fac = {f["facility_id"]: f for f in res.facility_metrics}
        assert by_fac["f_island"]["captive_demand_weight"] == pytest.approx(1.0, abs=1e-6)
        assert by_fac["f_island"]["captive_share_ratio"] == pytest.approx(1.0 / 5.0, abs=1e-6)
        assert by_fac["f_main_1"]["captive_demand_weight"] == 0.0
        # total probability mass: market shares sum to assigned weight (5.0)
        total_market = sum(f["market_share_weight"] for f in res.facility_metrics)
        assert total_market == pytest.approx(5.0, abs=1e-4)
        assert res.summary["unassigned_count"] == 0

    def test_huff_cutoff_unassigned_disclosed(self):
        """cutoff below every reachable cost ⇒ empty candidate sets: demand
        disclosed via unassigned_demand_ids, entropy None, market share 0."""
        graph, dataset = _build(_chain_fc())
        res = _service().huff_probabilities(
            demand_points=[_demand("d1", 116.000, weight=2.0)],
            facilities=[_facility("f1", 116.001, capacity=4.0)],
            graph=graph, network_dataset=dataset,
            cutoff_cost=_T1_S / 2.0,
        )
        assert res.summary["unassigned_demand_ids"] == ["d1"]
        assert res.summary["unassigned_count"] == 1
        assert res.per_demand_metrics[0]["share_entropy"] is None
        assert res.per_demand_metrics[0]["top_facility_shares"] == []
        assert res.facility_metrics[0]["market_share_weight"] == 0.0

    def test_huff_parameter_bounds_and_empty_inputs_rejected(self):
        graph, dataset = _build(_chain_fc())
        svc = _service()
        base = dict(
            demand_points=[_demand("d1", 116.000)],
            facilities=[_facility("f1", 116.001)],
            graph=graph, network_dataset=dataset,
        )
        with pytest.raises(ValueError, match="distance_decay"):
            svc.huff_probabilities(distance_decay=0.2, **base)
        with pytest.raises(ValueError, match="设施"):
            svc.huff_probabilities(demand_points=[_demand("d1", 116.0)], facilities=[],
                                   graph=graph, network_dataset=dataset)


class TestInteractionContractsAndTools:
    def test_huff_and_gravity_contracts_converge(self):
        g = apply_contract("gravity_accessibility_analysis", {
            "mass_exponent": 2.0, "distance_decay": 1.5,
        })
        assert g["mass_exponent"] == 2.0
        assert g["distance_decay"] == 1.5
        assert g.get("cutoff_cost") is None  # optional, absent until given
        h = apply_contract("huff_interaction_analysis", {})
        assert h["distance_decay"] == 2.0
        assert h.get("cutoff_cost") is None
        with pytest.raises(ValueError, match="parameter_contract_violation"):
            apply_contract("gravity_accessibility_analysis", {"mass_exponent": 5.0})
        with pytest.raises(ValueError, match="parameter_contract_violation"):
            apply_contract("huff_interaction_analysis", {"distance_decay": 0.1})

    def test_interaction_contracts_params_exist_in_tool_schema(self):
        """Registry parity gate semantics for the two new analysis contracts."""
        from app.tools import network_tools as nt

        reg = get_parameter_contract_registry()
        cases = [
            ("gravity_accessibility_analysis", nt.NetworkGravityAccessArgs),
            ("huff_interaction_analysis", nt.NetworkHuffInteractionArgs),
            ("network_centrality_analysis", nt.NetworkCentralityArgs),
        ]
        for contract_id, args_model in cases:
            contract = reg.get(contract_id)
            assert contract is not None, contract_id
            for spec in contract.parameters:
                assert spec.name in args_model.model_fields, (contract_id, spec.name)

    def test_interaction_tool_evidence_and_backend_diagnostic(self):
        """Both new tools attach scientific_evidence whose diagnostics include
        the honest (default-path) backend_selection record + methodology."""
        from app.tools import network_tools as nt
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        nt.register_network_tools(reg)

        g_out = asyncio.run(reg._tools["network_gravity_access"](
            network=_chain_fc(),
            demand_points=[{"id": "d1", "weight": 2.0, "coordinates": [116.0, 0.0]},
                           {"id": "d2", "weight": 1.0, "coordinates": [116.002, 0.0]}],
            facilities=[{"id": "f1", "capacity": 4.0, "coordinates": [116.001, 0.0]}],
            mass_exponent=1.0,
            distance_decay=2.0,
            profile="driving",
        ))
        assert g_out.get("type") != "error", g_out
        g_ev = g_out["scientific_evidence"]
        assert g_ev["algorithm"] == "network.gravity_access"
        assert "hansen1959" in g_ev["method_references"]
        g_names = [d["name"] for d in g_ev["diagnostics"]]
        assert "backend_selection" in g_names
        assert "unreachable_pair_count" in g_names

        h_out = asyncio.run(reg._tools["network_huff_interaction"](
            network=_chain_fc(),
            demand_points=[{"id": "d1", "weight": 2.0, "coordinates": [116.0, 0.0]}],
            facilities=[{"id": "f1", "capacity": 4.0, "coordinates": [116.001, 0.0]}],
            distance_decay=2.0,
            profile="driving",
        ))
        assert h_out.get("type") != "error", h_out
        h_ev = h_out["scientific_evidence"]
        assert h_ev["algorithm"] == "network.huff_interaction"
        assert "huff1964" in h_ev["method_references"]
        h_names = [d["name"] for d in h_ev["diagnostics"]]
        assert "backend_selection" in h_names
        assert "unassigned_demand_count" in h_names

    def test_engine_solve_surfaces_deterministic(self):
        engine = NetworkGraphEngine()
        g1 = asyncio.run(engine.solve_gravity_access(
            network=_chain_fc(),
            demand_points=[{"id": "d1", "coordinates": [116.0, 0.0]}],
            facilities=[{"id": "f1", "capacity": 4.0, "coordinates": [116.001, 0.0]}],
        ))
        g2 = asyncio.run(engine.solve_gravity_access(
            network=_chain_fc(),
            demand_points=[{"id": "d1", "coordinates": [116.0, 0.0]}],
            facilities=[{"id": "f1", "capacity": 4.0, "coordinates": [116.001, 0.0]}],
        ))
        assert g1.model_dump() == g2.model_dump()
