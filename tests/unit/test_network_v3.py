"""Network V3 batch tests — exact p-median / p-center MILP + eigenvector centrality.

Covers (Foundation V3):

- p-median exact (scipy.optimize.milp / HiGHS): MILP objective equals the
  brute-force C(m,p) enumeration optimum on small instances; the existing
  Teitz-Bart heuristic lands within [opt, 1.2·opt] on the same instance;
- p-center exact (Big-M MILP): optimum matches enumeration; unreachable
  demand excluded from the max objective and disclosed via
  summary.unassigned_ids (same semantics as the existing p-center paths);
- determinism: two identical runs give bit-identical results (HiGHS is
  deterministic for fixed inputs — no wall-clock or RNG involved);
- scale guards: n_demand×n_candidates ≤ 25000 and n_candidates ≤ 500 —
  typed ResourceScaleMismatch refusal pointing at the heuristic path
  (honest refusal, NEVER a silent fallback);
- solver disclosure: summary.solver == "milp_highs" + optimality + HiGHS
  status + solve_stats (mip_gap / node count / dual bound);
- eigenvector centrality (Bonacich 1972 power iteration): pointwise
  conformance with networkx.eigenvector_centrality (weight param) at
  rtol 1e-6 on directed + undirected weighted graphs;
  determinism + iteration/delta disclosure; negative-weight typed refusal;
  isolated nodes → 0; disconnected graphs disclose dominant-component
  semantics; metrics="eigenvector" opt-in via service/tool/contract.
"""
import asyncio
import itertools
import math

import networkx as nx
import numpy as np
import pytest

from app.lib.gis.parameter_contracts import apply_contract, get_parameter_contract_registry
from app.lib.gis.scientific_errors import ResourceScaleMismatch, UnsupportedMethod
from app.services.network import allocation as allocation_mod
from app.services.network import centrality as centrality_mod
from app.services.network.allocation import (
    NetworkLocationAllocationService,
    solve_p_center_milp,
    solve_p_median_milp,
)
from app.services.network.centrality import NetworkCentralityService, eigenvector_centrality
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import DemandPoint, Facility

pytestmark = pytest.mark.unit

_EARTH_R = 6371000.0
_SPEED_60_MS = 60.0 / 3.6
_T1_S = _EARTH_R * math.pi / 180.0 * 0.001 / _SPEED_60_MS  # seconds per 0.001°


# ── fixtures ─────────────────────────────────────────────────────────

def _cost_matrix(n_dem: int, m_fac: int, seed: int = 42, hi: int = 20) -> list:
    """Deterministic integer cost matrix (fully finite — clean enumeration)."""
    rng = np.random.default_rng(seed)
    return rng.integers(1, hi + 1, size=(n_dem, m_fac)).astype(float).tolist()


def _weights(n: int, seed: int = 7) -> list:
    rng = np.random.default_rng(seed)
    return rng.integers(1, 5, size=n).astype(float).tolist()


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


def _enum_p_median_optimum(C: list, w: list, p: int) -> float:
    m = len(C[0])
    best = float("inf")
    for combo in itertools.combinations(range(m), p):
        obj = sum(w[i] * min(C[i][j] for j in combo) for i in range(len(w)))
        best = min(best, obj)
    return best


def _enum_p_center_optimum(C: list, w: list, p: int) -> float:
    m = len(C[0])
    best = float("inf")
    for combo in itertools.combinations(range(m), p):
        obj = max(min(C[i][j] for j in combo) for i in range(len(w)))
        best = min(best, obj)
    return best


def _objective_of(C: list, w: list, subset) -> float:
    return sum(w[i] * min(C[i][j] for j in subset) for i in range(len(w)))


# ── 1. exact p-median MILP ───────────────────────────────────────────

class TestPMedianExactMILP:
    def test_pmedian_exact_matches_enumeration_optimum(self):
        """12 demand × 4 candidates, p=3: the MILP objective must equal the
        C(4,3)=4-combination enumeration optimum exactly, and the returned
        subset must achieve it."""
        C = _cost_matrix(12, 4, seed=42)
        w = _weights(12, seed=7)
        optimum = _enum_p_median_optimum(C, w, 3)

        out = solve_p_median_milp(C, w, 3)
        assert out["optimality"] == "optimal"
        assert out["objective_value"] == pytest.approx(optimum, abs=1e-9)
        assert len(out["selected"]) == 3
        assert len(set(out["selected"])) == 3
        assert _objective_of(C, w, out["selected"]) == pytest.approx(optimum, abs=1e-9)
        assert out["unassigned_demand_indices"] == []

    def test_pmedian_heuristic_within_20pct_of_exact(self):
        """Same instance through the existing Teitz-Bart heuristic: achieved
        objective must land in [opt, 1.2·opt] (the exact/enumeration and MILP
        paths bracket the heuristic's quality)."""
        C = _cost_matrix(12, 4, seed=42)
        w = _weights(12, seed=7)
        optimum = _enum_p_median_optimum(C, w, 3)

        heur_subset = NetworkLocationAllocationService()._solve_p_median_heuristic(C, w, 3)
        heur_obj = _objective_of(C, w, heur_subset)
        assert optimum <= heur_obj <= 1.2 * optimum

    def test_pmedian_exact_respects_demand_weights(self):
        """Regression: the MILP objective coefficients must carry the demand
        weights — an unweighted Σ c·x model solves the wrong problem. Here the
        unweighted sum strictly prefers {f0} (3 < 6) while the weighted
        p-median optimum is {f1} (105 < 201); the MILP must return the
        weighted one and disclose objective == enumeration optimum."""
        C = [[1.0, 5.0],
             [2.0, 1.0]]  # d0 prefers f0 (1 vs 5); d1 mildly prefers f1 (1 vs 2)
        w = [1.0, 100.0]  # ...but d1 dominates the weighted objective

        assert _enum_p_median_optimum(C, w, 1) == pytest.approx(105.0, abs=1e-9)
        out = solve_p_median_milp(C, w, 1)
        assert out["optimality"] == "optimal"
        assert out["selected"] == (1,)
        assert out["objective_value"] == pytest.approx(105.0, abs=1e-9)

    def test_exact_milp_scale_guard_refusal_is_typed(self, monkeypatch):
        """Honest refusal at both guard limits — never a silent heuristic
        fallback. Candidate cap fires first, then the product cap."""
        w3 = [1.0, 1.0, 1.0]
        with pytest.raises(ResourceScaleMismatch) as ei:
            solve_p_median_milp(_cost_matrix(3, 501, seed=1), w3, 2)
        assert ei.value.scientific_code == "RESOURCE_SCALE_MISMATCH"
        assert "candidates=501" in (ei.value.estimated or "")
        assert "heuristic" in (ei.value.correction_hint or "")

        # 160 × 157 = 25120 > 25000: product guard (candidates 157 ≤ 500).
        with pytest.raises(ResourceScaleMismatch) as ei2:
            solve_p_median_milp(_cost_matrix(160, 157, seed=2), [1.0] * 160, 3)
        assert "n_demand*n_candidates=25120" in (ei2.value.estimated or "")

        # Service path refuses too (shrunk guard ⇒ typed, before any OD work).
        monkeypatch.setattr(allocation_mod, "_MILP_MAX_PRODUCT", 10)
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        with pytest.raises(ResourceScaleMismatch):
            NetworkLocationAllocationService().p_median_exact(
                candidate_facilities=[_facility(f"f{i}", 116.0 + i * 0.002) for i in range(4)],
                demand_points=[_demand(f"d{i}", 116.0 + i * 0.001) for i in range(12)],
                p_count=2, graph=graph, network_dataset=dataset,
            )

    def test_pmedian_exact_service_discloses_highs_solver(self):
        """Service path: summary discloses solver=milp_highs, optimality,
        HiGHS status message, solve_stats and model_stats; the MILP optimum
        matches enumeration on the service's own OD cost matrix; explicit
        solver dispatch works (exact_milp ↔ p_median_exact, forced heuristic
        skips enumeration), and max_coverage has no MILP path."""
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        facilities = [_facility(f"f{i}", 116.0 + i * 0.002) for i in range(4)]
        demands = [_demand(f"d{i}", 116.0 + i * 0.001, weight=1.0 + (i % 3)) for i in range(12)]
        svc = NetworkLocationAllocationService()

        res = svc.p_median_exact(
            candidate_facilities=facilities, demand_points=demands,
            p_count=3, graph=graph, network_dataset=dataset,
        )
        s = res.summary
        assert s["solver"] == "milp_highs"
        assert s["solver_request"] == "exact_milp"
        assert s["optimality"] == "optimal"
        assert isinstance(s["highs_status"], str) and s["highs_status"]
        assert "mip_gap" in s["solve_stats"]
        assert s["model_stats"]["n_variables"] > 0
        assert s["selected_facilities_count"] == 3

        # MILP objective == enumeration optimum on the same OD cost matrix.
        C = svc._od_cost_matrix(facilities, demands, graph=graph, network_dataset=dataset)
        w = [d.weight for d in demands]
        optimum = _enum_p_median_optimum(C, w, 3)
        assert s["objective_value"] == pytest.approx(optimum, abs=1e-6)

        # Dispatch parity: solver="exact_milp" routes to the same result.
        via_dispatch = svc.location_allocation(
            candidate_facilities=facilities, demand_points=demands,
            p_count=3, problem_type="p_median",
            graph=graph, network_dataset=dataset, solver="exact_milp",
        )
        assert via_dispatch.summary["solver"] == "milp_highs"
        assert via_dispatch.summary["selected_facilities_count"] == 3

        # Forced heuristic on a small instance: enumeration branch bypassed.
        # (The legacy heuristic summary discloses solver, not objective_value —
        # recompute its achieved objective from the same OD matrix instead.)
        forced = svc.location_allocation(
            candidate_facilities=facilities, demand_points=demands,
            p_count=3, problem_type="p_median",
            graph=graph, network_dataset=dataset, solver="heuristic",
        )
        assert forced.summary["solver"] == "heuristic"
        assert forced.summary["solver_request"] == "heuristic"
        forced_subset = tuple(
            int(f["facility_id"][1:]) for f in forced.allocated_facilities
        )
        assert _objective_of(C, w, forced_subset) >= optimum - 1e-6

        # max_coverage has no MILP exact path — typed refusal, no fallback.
        with pytest.raises(UnsupportedMethod):
            svc.location_allocation(
                candidate_facilities=facilities, demand_points=demands,
                p_count=3, problem_type="max_coverage",
                graph=graph, network_dataset=dataset, solver="exact_milp",
            )


# ── 2. exact p-center MILP ───────────────────────────────────────────

class TestPCenterExactMILP:
    def test_pcenter_exact_matches_enumeration_optimum(self):
        """10 demand × 4 candidates, p=2: Big-M MILP objective equals the
        enumeration max-min optimum exactly."""
        C = _cost_matrix(10, 4, seed=11)
        w = _weights(10, seed=3)
        optimum = _enum_p_center_optimum(C, w, 2)

        out = solve_p_center_milp(C, w, 2)
        assert out["optimality"] == "optimal"
        assert out["objective_value"] == pytest.approx(optimum, abs=1e-9)
        assert len(out["selected"]) == 2
        achieved = max(min(C[i][j] for j in out["selected"]) for i in range(10))
        assert achieved == pytest.approx(optimum, abs=1e-9)
        assert out["big_m"] == pytest.approx(max(max(row) for row in C), abs=1e-9)

    def test_pcenter_exact_unreachable_demand_disclosed(self):
        """Split world: the island demand has inf cost to every candidate —
        it must be excluded from the max-min objective (inf is not a service
        cost) and named in summary.unassigned_ids, mirroring the existing
        p-center semantics. Mainland optimum is hand-computable: candidates
        sit at 116.001/116.003, so f1@116.001 serves both mainland demands
        (116.000 / 116.002) at t1 each → max = t1 (same golden as
        test_p_center.py::TestPCenterUnreachable on the identical fixture)."""
        graph, dataset = NetworkGraphBuilder().build_graph(_split_world_fc())
        res = NetworkLocationAllocationService().p_center_exact(
            candidate_facilities=[_facility("f1", 116.001), _facility("f2", 116.003)],
            demand_points=[_demand("d_main_1", 116.000), _demand("d_main_2", 116.002),
                           _demand("d_island", 116.502)],
            p_count=1, graph=graph, network_dataset=dataset,
        )
        s = res.summary
        assert s["solver"] == "milp_highs"
        assert s["problem_type"] == "p_center"
        assert s["unassigned_ids"] == ["d_island"]
        assert s["unassigned_count"] == 1
        assert s["max_service_cost"] == pytest.approx(_T1_S, abs=0.01)
        assert s["objective_value"] == pytest.approx(_T1_S, abs=0.01)
        assigned = [d for f in res.allocated_facilities for d in f["assigned_demand_ids"]]
        assert sorted(assigned) == ["d_main_1", "d_main_2"]


# ── 3. determinism (HiGHS, fixed inputs) ─────────────────────────────

class TestExactMILPDeterminism:
    def test_exact_milp_deterministic_same_result_twice(self):
        """HiGHS is deterministic for fixed inputs: two identical service
        runs (p-median and p-center) must be bit-identical — no wall clock,
        no RNG, iteration/count contracts only."""
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        facilities = [_facility(f"f{i}", 116.0 + i * 0.002) for i in range(7)]
        demands = [_demand(f"d{i}", 116.0 + i * 0.001) for i in range(13)]
        svc = NetworkLocationAllocationService()

        m1 = svc.p_median_exact(candidate_facilities=facilities, demand_points=demands,
                                p_count=3, graph=graph, network_dataset=dataset)
        m2 = svc.p_median_exact(candidate_facilities=facilities, demand_points=demands,
                                p_count=3, graph=graph, network_dataset=dataset)
        assert m1.model_dump() == m2.model_dump()
        assert m1.summary["solver"] == "milp_highs"

        c1 = svc.p_center_exact(candidate_facilities=facilities, demand_points=demands,
                                p_count=3, graph=graph, network_dataset=dataset)
        c2 = svc.p_center_exact(candidate_facilities=facilities, demand_points=demands,
                                p_count=3, graph=graph, network_dataset=dataset)
        assert c1.model_dump() == c2.model_dump()
        assert c1.summary["solver"] == "milp_highs"


# ── 4. eigenvector centrality ────────────────────────────────────────

def _weighted_directed_graph() -> nx.DiGraph:
    """Strongly connected + aperiodic (triangles) weighted DiGraph."""
    g = nx.DiGraph()
    edges = [(0, 1, 2.0), (1, 0, 1.5), (1, 2, 1.0), (2, 0, 3.0), (2, 3, 0.5),
             (3, 2, 2.5), (3, 0, 1.0), (0, 3, 4.0), (3, 4, 1.0), (4, 3, 1.0),
             (4, 0, 2.0), (0, 4, 1.0)]
    for u, v, w in edges:
        g.add_edge(u, v, travel_time_s=w)
    return g


def _l2(vec_by_node: dict, nodes) -> np.ndarray:
    a = np.array([vec_by_node[n] for n in nodes], dtype=float)
    return a / np.linalg.norm(a)


class TestEigenvectorCentrality:
    def test_eigenvector_matches_networkx_weighted(self):
        """Conformance: pointwise agreement with networkx.eigenvector_centrality
        (weight param) at rtol 1e-6, directed (left/in-edge semantics) and
        undirected alike."""
        g = _weighted_directed_graph()
        mine, _meta = eigenvector_centrality(g, weight="travel_time_s")
        ref = nx.eigenvector_centrality(g, max_iter=10000, tol=1e-12, weight="travel_time_s")
        np.testing.assert_allclose(_l2(mine, g.nodes), _l2(ref, g.nodes), rtol=1e-6)

        gu = g.to_undirected()
        mine_u, _mu = eigenvector_centrality(gu, weight="travel_time_s")
        ref_u = nx.eigenvector_centrality(gu, max_iter=10000, tol=1e-12, weight="travel_time_s")
        np.testing.assert_allclose(_l2(mine_u, gu.nodes), _l2(ref_u, gu.nodes), rtol=1e-6)

    def test_eigenvector_deterministic_reports_iterations(self):
        """Two runs are bit-identical; meta reports the actual iteration count
        and achieved L1 delta (count contracts, no wall-clock assertions)."""
        g = _weighted_directed_graph()
        s1, m1 = eigenvector_centrality(g, weight="travel_time_s")
        s2, m2 = eigenvector_centrality(g, weight="travel_time_s")
        assert s1 == s2
        assert m1 == m2
        assert m1["converged"] is True
        assert m1["iterations"] >= 1
        assert m1["achieved_l1_delta"] < m1["tol"]
        assert m1["method"] == "power_iteration"
        assert m1["connectivity"] == "connected"

        # Service surface discloses the same convergence facts.
        res = NetworkCentralityService().network_centrality(g, metrics="eigenvector")
        assert res.metrics == ["eigenvector"]
        assert res.summary["eigenvector_converged"] is True
        assert res.summary["eigenvector_iterations"] == m1["iterations"]
        assert res.summary["eigenvector_l1_delta"] == m1["achieved_l1_delta"]

    def test_eigenvector_negative_weight_rejected(self):
        """Negative edge weights break the Perron-Frobenius premise — typed
        refusal (UnsupportedMethod ⊂ ValueError), never a silent shift/abs
        workaround."""
        g = _weighted_directed_graph()
        g[0][1]["travel_time_s"] = -2.0
        with pytest.raises(UnsupportedMethod) as ei:
            eigenvector_centrality(g, weight="travel_time_s")
        assert ei.value.scientific_code == "UNSUPPORTED_METHOD"
        assert isinstance(ei.value, ValueError)
        assert "负" in str(ei.value) or "negative" in str(ei.value).lower()

    def test_eigenvector_isolated_and_disconnected_disclosure(self):
        """Isolated nodes score exactly 0; disconnected graphs iterate anyway
        and disclose the dominant-component semantics in meta."""
        g = nx.DiGraph()
        g.add_edge(0, 1, travel_time_s=1.0)
        g.add_edge(1, 0, travel_time_s=1.0)
        g.add_edge(2, 3, travel_time_s=2.0)
        g.add_edge(3, 2, travel_time_s=2.0)
        g.add_node("isolated")
        scores, meta = eigenvector_centrality(g, weight="travel_time_s")
        assert scores["isolated"] == 0.0
        assert meta["isolated_node_count"] == 1
        assert meta["isolated_node_ids"] == ["isolated"]
        assert meta["connectivity"] == "disconnected"
        assert meta["component_count"] == 3
        assert "主导" in meta["disclosure"]

        # Service surface: isolated node row carries 0, disclosures in summary.
        res = NetworkCentralityService().network_centrality(g, metrics="eigenvector")
        by_node = {r["node_id"]: r for r in res.node_records}
        assert by_node["isolated"]["eigenvector"] == 0.0
        assert res.summary["eigenvector_component_count"] == 3
        assert "eigenvector_disclosure" in res.summary

    def test_eigenvector_node_cap_guard(self, monkeypatch):
        """Scale guard fires before any allocation (shrunk cap ⇒ typed)."""
        monkeypatch.setattr(centrality_mod, "_NODE_CAP", 3)
        g = _weighted_directed_graph()  # 5 nodes > 3
        with pytest.raises(ResourceScaleMismatch) as ei:
            eigenvector_centrality(g)
        assert "nodes=5" in (ei.value.estimated or "")
        assert ei.value.scientific_code == "RESOURCE_SCALE_MISMATCH"


# ── 5. service / tool / contract / registry surface ──────────────────

class TestNetworkV3Surface:
    def test_centrality_eigenvector_is_optin_not_in_all(self):
        """metrics='eigenvector' is an additive enum extension; 'all' keeps
        its historical 4-metric semantics (documented opt-in, not silent)."""
        g = _weighted_directed_graph()
        svc = NetworkCentralityService()
        assert svc.network_centrality(g, metrics="all").metrics == [
            "degree", "closeness", "betweenness", "edge_betweenness",
        ]
        res = svc.network_centrality(g, metrics="eigenvector")
        by_node = {r["node_id"]: r for r in res.node_records}
        assert set(by_node[0].keys()) == {"node_id", "eigenvector"}
        with pytest.raises(UnsupportedMethod):
            svc.network_centrality(g, metrics="harmonic")

    def test_contract_and_tool_accept_eigenvector(self):
        """Contract v2 carries the eigenvector enum; the tool runs it
        end-to-end and attaches evidence with the convergence diagnostic."""
        reg = get_parameter_contract_registry()
        contract = reg.get("network_centrality_analysis")
        assert contract.version == 2
        assert "eigenvector" in contract.spec("metrics").enum_values

        from app.tools import network_tools as nt
        from app.tools.registry import ToolRegistry

        tool_reg = ToolRegistry()
        nt.register_network_tools(tool_reg)
        out = asyncio.run(tool_reg._tools["network_centrality"](
            network=_chain_fc(), metrics="eigenvector", weight="travel_time",
        ))
        assert out.get("type") != "error", out
        assert out["metrics"] == ["eigenvector"]
        ev = out.get("scientific_evidence")
        assert ev and ev["algorithm"] == "network.centrality"
        names = [d["name"] for d in ev["diagnostics"]]
        assert "eigenvector_convergence" in names

    def test_registry_validate_and_parity_clean_for_network_v3(self):
        """Registry validate → [] for the new descriptors; the tool-parity
        gate has no open issues mentioning network; the new allocation
        contract converges with the tool signature (additive solver enum)."""
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.services.gis_harness.registry_validation import (
            validate_algorithm_tool_parameter_parity,
        )

        mine = ("network.pmedian_exact", "network.pcenter_exact",
                "network.eigenvector_centrality")
        issues = [i for i in get_algorithm_registry().validate()
                  if any(m in i for m in mine)]
        assert issues == []

        parity = [i for i in validate_algorithm_tool_parameter_parity()
                  if "network" in i]
        assert parity == []

        # allocation contract: additive solver enum, defaults preserved.
        alloc_contract = get_parameter_contract_registry().get("location_allocation_analysis")
        assert alloc_contract is not None
        assert alloc_contract.spec("solver").enum_values == ["auto", "heuristic", "exact_milp"]
        normalized = apply_contract("location_allocation_analysis", {})
        assert normalized["objective"] == "minimize_cost"
        assert normalized["solver"] == "auto"
        with pytest.raises(ValueError, match="parameter_contract_violation"):
            apply_contract("location_allocation_analysis", {"solver": "genetic"})

    def test_exact_milp_tool_evidence_and_solver_disclosure(self):
        """Tool path with solver='exact_milp': the evidence block anchors the
        EXACT descriptors (with their method references), and the payload
        discloses the MILP solver; default path keeps the legacy anchor."""
        from app.tools import network_tools as nt
        from app.tools.registry import ToolRegistry

        reg = ToolRegistry()
        nt.register_network_tools(reg)
        facilities = [{"id": f"f{i}", "coordinates": [116.0 + i * 0.002, 0.0]} for i in range(4)]
        demands = [{"id": f"d{i}", "weight": 1.0, "coordinates": [116.0 + i * 0.001, 0.0]}
                   for i in range(12)]

        out = asyncio.run(reg._tools["location_allocation"](
            network=_chain_fc(),
            candidate_facilities=facilities,
            demand_points=demands,
            number_to_choose=3,
            objective="minimize_cost",
            solver="exact_milp",
        ))
        assert out.get("type") != "error", out
        assert out["summary"]["solver"] == "milp_highs"
        ev = out["scientific_evidence"]
        assert ev["algorithm"] == "network.pmedian_exact"
        assert "church_revelle1974" in ev["method_references"]
        assert ev["parameters_applied"]["solver"] == "exact_milp"
        names = [d["name"] for d in ev["diagnostics"]]
        assert "backend_selection" in names

        out_pc = asyncio.run(reg._tools["location_allocation"](
            network=_chain_fc(),
            candidate_facilities=facilities,
            demand_points=demands,
            number_to_choose=2,
            objective="minimize_max_cost",
            solver="exact_milp",
        ))
        assert out_pc.get("type") != "error", out_pc
        assert out_pc["summary"]["solver"] == "milp_highs"
        ev_pc = out_pc["scientific_evidence"]
        assert ev_pc["algorithm"] == "network.pcenter_exact"
        assert "hakimi1964" in ev_pc["method_references"]

        # Default path (solver omitted) keeps the legacy descriptor anchor.
        out_auto = asyncio.run(reg._tools["location_allocation"](
            network=_chain_fc(),
            candidate_facilities=facilities,
            demand_points=demands,
            number_to_choose=2,
            objective="minimize_cost",
        ))
        assert out_auto["scientific_evidence"]["algorithm"] == "network.location_allocation"
