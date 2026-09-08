"""Network science-v3 batch tests — MCLP exact MILP + heuristic-family OD scale guards.

Covers (audit 04-network §8 R2 + R3):

- MCLP exact (scipy.optimize.milp / HiGHS, Church & ReVelle 1974):
  ``max Σ w_i·z_i; z_i ≤ Σ_{j∈N_i} y_j; Σ y = p`` with N_i = {j: c_ij ≤ cutoff};
- enumeration equivalence: MILP covered weight equals the brute-force
  C(m,p) optimum on small instances (incl. inf/unreachable cells and the
  cutoff boundary semantics min_c ≤ cutoff);
- coverage semantics: demand weights steer the optimum (unweighted count
  would pick a different site); demands with an empty coverage set are
  excluded from the model and disclosed via unassigned_demand_indices;
- scale guards (typed, honest refusal — never a silent fallback):
  - model guards: candidates ≤ 500, n_demand×n_candidates ≤ 25000;
  - R3 uniform n×m OD matrix guard on the heuristic-family tool surface
    (location_allocation heuristic path, accessibility, gravity, huff,
    closest_facility) — monkeypatched cap + typed assertion;
- determinism: two identical runs are bit-identical;
- greedy heuristic stays within the classic (1−1/e) submodular guarantee
  and never exceeds the exact optimum (disclosed as heuristic, not exact);
- tool evidence anchors the exact MCLP descriptor (church_revelle1974).
"""
import asyncio
import itertools
import math

import numpy as np
import pytest

from app.lib.gis.algorithm_registry import get_algorithm_registry
from app.lib.gis.scientific_errors import ResourceScaleMismatch
from app.services.network import scale_guard as scale_guard_mod
from app.services.network import allocation as allocation_mod
from app.services.network.allocation import (
    NetworkLocationAllocationService,
    solve_max_coverage_milp,
)
from app.services.network.accessibility import NetworkAccessibilityService
from app.services.network.facility import NetworkClosestFacilityService
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.interaction import NetworkInteractionService
from app.services.network.models import DemandPoint, Facility

pytestmark = pytest.mark.unit


# ── fixtures / helpers ───────────────────────────────────────────────

def _cost_matrix(n_dem: int, m_fac: int, seed: int = 42, hi: int = 20) -> list:
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


def _enum_mclp_optimum(C: list, w: list, p: int, cutoff: float) -> float:
    """Brute-force C(m,p) gold standard: max covered demand weight."""
    m = len(C[0])
    best = -1.0
    for combo in itertools.combinations(range(m), p):
        cov = 0.0
        for i, wi in enumerate(w):
            min_c = min((C[i][j] for j in combo), default=float("inf"))
            if min_c <= cutoff:
                cov += wi
        best = max(best, cov)
    return best


def _covered_weight(C: list, w: list, subset, cutoff: float) -> float:
    return sum(
        wi for i, wi in enumerate(w)
        if min((C[i][j] for j in subset), default=float("inf")) <= cutoff
    )


# ── 1. exact MCLP MILP ───────────────────────────────────────────────

class TestMCLPExactMILP:
    def test_mclp_exact_matches_enumeration_optimum(self):
        """10 demand × 5 candidates, p=3, with inf cells: the MILP covered
        weight must equal the C(5,3)=10-combination enumeration optimum
        exactly, and the returned subset must achieve it."""
        C = _cost_matrix(10, 5, seed=13)
        C[2][1] = float("inf")   # sprinkle unreachable pairs (honest semantics)
        C[7][0] = float("inf")
        C[7][4] = float("inf")
        w = _weights(10, seed=5)
        cutoff = 12.0
        optimum = _enum_mclp_optimum(C, w, 3, cutoff)

        out = solve_max_coverage_milp(C, w, 3, cutoff)
        assert out["optimality"] == "optimal"
        assert out["objective_value"] == pytest.approx(optimum, abs=1e-9)
        assert len(out["selected"]) == 3
        assert len(set(out["selected"])) == 3
        assert _covered_weight(C, w, out["selected"], cutoff) == pytest.approx(optimum, abs=1e-9)
        assert out["covered_demand_count"] >= 0
        assert out["cutoff"] == pytest.approx(cutoff)

    def test_mclp_exact_respects_demand_weights_and_cutoff_boundary(self):
        """(a) Weights steer the optimum: unweighted coverage count prefers
        {f0} (2 sites-worth) while the weighted MCLP optimum is {f1} (10 > 2)
        — an unweighted Σ z model solves the wrong problem.
        (b) Cutoff boundary is inclusive: cost exactly == cutoff counts as
        covered (min_c ≤ cutoff, same semantics as enumeration/heuristic)."""
        # (a) weighted vs unweighted: unweighted coverage count prefers {f0}
        # (2 demand points vs 1) while the weighted MCLP optimum is {f1}
        # (10 > 2) — an unweighted Σ z model solves the wrong problem.
        C = [[1.0, 5.0],
             [1.0, 5.0],
             [5.0, 1.0]]
        w = [1.0, 1.0, 10.0]
        cutoff = 2.0
        assert _enum_mclp_optimum(C, w, 1, cutoff) == pytest.approx(10.0, abs=1e-9)
        out = solve_max_coverage_milp(C, w, 1, cutoff)
        assert out["optimality"] == "optimal"
        assert out["selected"] == (1,)
        assert out["objective_value"] == pytest.approx(10.0, abs=1e-9)

        # (b) boundary: only reachable candidate sits exactly AT the cutoff
        out_b = solve_max_coverage_milp([[2.0, 100.0]], [5.0], 1, 2.0)
        assert out_b["selected"] == (0,)
        assert out_b["objective_value"] == pytest.approx(5.0, abs=1e-9)
        assert out_b["covered_demand_count"] == 1

    def test_mclp_exact_uncappable_demand_disclosed(self):
        """A demand with no candidate inside the cutoff has an empty coverage
        set: it never enters the model (cannot be covered), is named in
        unassigned_demand_indices, and its weight stays out of the objective
        — no soft-coverage fabrication."""
        C = [[1.0, 2.0],
             [float("inf"), float("inf")]]  # d1 unreachable entirely
        w = [1.0, 7.0]
        out = solve_max_coverage_milp(C, w, 1, 900.0)
        assert out["unassigned_demand_indices"] == [1]
        assert out["objective_value"] == pytest.approx(1.0, abs=1e-9)
        assert out["covered_demand_count"] == 1

        # Beyond-cutoff exclusion: same structural effect as inf.
        out2 = solve_max_coverage_milp([[1.0, 3.0], [50.0, 60.0]], [1.0, 9.0], 1, 10.0)
        assert out2["unassigned_demand_indices"] == [1]
        assert out2["objective_value"] == pytest.approx(1.0, abs=1e-9)

    def test_mclp_scale_guard_refusal_is_typed(self, monkeypatch):
        """Honest refusal at both model-guard limits — never a silent
        heuristic fallback. Candidate cap fires first, then the product cap
        (mirrors test_exact_milp_scale_guard_refusal_is_typed)."""
        w3 = [1.0, 1.0, 1.0]
        with pytest.raises(ResourceScaleMismatch) as ei:
            solve_max_coverage_milp(_cost_matrix(3, 501, seed=1), w3, 2, 10.0)
        assert ei.value.scientific_code == "RESOURCE_SCALE_MISMATCH"
        assert "candidates=501" in (ei.value.estimated or "")
        assert "heuristic" in (ei.value.correction_hint or "")

        # 160 × 157 = 25120 > 25000: product guard (candidates 157 ≤ 500).
        with pytest.raises(ResourceScaleMismatch) as ei2:
            solve_max_coverage_milp(_cost_matrix(160, 157, seed=2), [1.0] * 160, 3, 10.0)
        assert "n_demand*n_candidates=25120" in (ei2.value.estimated or "")

        # Service path: shrunk MILP-scale OD guard ⇒ typed refusal BEFORE
        # any OD work（review R2-1：MILP 服务路径用自身 25_000 刻度，
        # 测试收缩该常量而非启发式面刻度）。
        monkeypatch.setattr(allocation_mod, "_MILP_MAX_PRODUCT", 10)
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        with pytest.raises(ResourceScaleMismatch):
            NetworkLocationAllocationService().max_coverage_exact(
                candidate_facilities=[_facility(f"f{i}", 116.0 + i * 0.002) for i in range(4)],
                demand_points=[_demand(f"d{i}", 116.0 + i * 0.001) for i in range(12)],
                p_count=2, graph=graph, network_dataset=dataset,
            )

    def test_mclp_exact_service_discloses_highs_solver(self):
        """Service path: summary discloses solver=milp_highs / optimality /
        HiGHS status / solve_stats / model_stats + MCLP cutoff disclosure;
        the MILP optimum matches enumeration on the service's own OD cost
        matrix; explicit dispatch (solver=exact_milp, maximize_coverage)
        routes to the same result."""
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        facilities = [_facility(f"f{i}", 116.0 + i * 0.002) for i in range(4)]
        demands = [_demand(f"d{i}", 116.0 + i * 0.001, weight=1.0 + (i % 3)) for i in range(12)]
        svc = NetworkLocationAllocationService()
        # Chain segment = 0.001° ≈ 111.19 m at 60 km/h → t1 ≈ 6.67 s per
        # segment; cutoff = 5 segments of travel time (active impedance, s).
        t1 = 0.001 * 6371000.0 * math.pi / 180.0 / (60.0 / 3.6)
        cutoff_cost = 5.0 * t1

        res = svc.max_coverage_exact(
            candidate_facilities=facilities, demand_points=demands,
            p_count=3, cutoff_cost=cutoff_cost,
            graph=graph, network_dataset=dataset,
        )
        s = res.summary
        assert s["solver"] == "milp_highs"
        assert s["solver_request"] == "exact_milp"
        assert s["problem_type"] == "max_coverage"
        assert s["optimality"] == "optimal"
        assert isinstance(s["highs_status"], str) and s["highs_status"]
        assert "mip_gap" in s["solve_stats"]
        assert s["model_stats"]["n_variables"] > 0
        assert s["selected_facilities_count"] == 3
        assert s["cutoff_cost"] == pytest.approx(cutoff_cost)
        assert s["covered_demand_count"] > 0

        # MILP objective == enumeration optimum on the same OD cost matrix.
        C = svc._od_cost_matrix(facilities, demands, graph=graph, network_dataset=dataset)
        w = [d.weight for d in demands]
        optimum = _enum_mclp_optimum(C, w, 3, cutoff_cost)
        assert s["objective_value"] == pytest.approx(optimum, abs=1e-6)

        # Dispatch parity: solver="exact_milp" + maximize_coverage → MILP.
        via_dispatch = svc.location_allocation(
            candidate_facilities=facilities, demand_points=demands,
            p_count=3, problem_type="max_coverage", cutoff_cost=cutoff_cost,
            graph=graph, network_dataset=dataset, solver="exact_milp",
        )
        assert via_dispatch.summary["solver"] == "milp_highs"
        assert via_dispatch.summary["objective_value"] == pytest.approx(optimum, abs=1e-6)

    def test_mclp_greedy_heuristic_within_submodular_guarantee(self):
        """The greedy-add heuristic is bracketed by the exact optimum from
        above and the classic (1−1/e) submodular guarantee from below —
        disclosed as heuristic, never as exact (audit F8)."""
        C = _cost_matrix(12, 6, seed=21)
        w = _weights(12, seed=9)
        cutoff = 12.0
        exact = _enum_mclp_optimum(C, w, 3, cutoff)
        heur_subset = NetworkLocationAllocationService()._solve_max_coverage_heuristic(
            C, w, 3, cutoff
        )
        heur = _covered_weight(C, w, heur_subset, cutoff)
        assert exact > 0
        assert heur <= exact + 1e-9
        assert heur >= (1.0 - 1.0 / math.e) * exact - 1e-9


# ── 2. determinism ───────────────────────────────────────────────────

class TestMCLPDeterminism:
    def test_mclp_exact_deterministic_same_result_twice(self):
        """HiGHS is deterministic for fixed inputs: two identical service
        runs are bit-identical — no wall clock, no RNG."""
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        facilities = [_facility(f"f{i}", 116.0 + i * 0.002) for i in range(6)]
        demands = [_demand(f"d{i}", 116.0 + i * 0.001, weight=1.0 + (i % 2)) for i in range(11)]
        svc = NetworkLocationAllocationService()

        m1 = svc.max_coverage_exact(candidate_facilities=facilities, demand_points=demands,
                                    p_count=2, cutoff_cost=3600.0,
                                    graph=graph, network_dataset=dataset)
        m2 = svc.max_coverage_exact(candidate_facilities=facilities, demand_points=demands,
                                    p_count=2, cutoff_cost=3600.0,
                                    graph=graph, network_dataset=dataset)
        assert m1.model_dump() == m2.model_dump()
        assert m1.summary["solver"] == "milp_highs"


# ── 3. R3: uniform n×m OD scale guard on the heuristic family ────────

class TestODMatrixScaleGuardFamily:
    """Shrunk guard ⇒ typed ResourceScaleMismatch from every surface that
    materializes the full n×m OD cost matrix — refusal BEFORE allocation,
    no silent truncation, no silent fallback (audit F4/R3)."""

    def test_location_allocation_heuristic_path_refusal_is_typed(self, monkeypatch):
        monkeypatch.setattr(scale_guard_mod, "MAX_OD_MATRIX_PAIRS", 10)
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        facilities = [_facility(f"f{i}", 116.0 + i * 0.002) for i in range(4)]
        demands = [_demand(f"d{i}", 116.0 + i * 0.001) for i in range(12)]  # 48 pairs
        with pytest.raises(ResourceScaleMismatch) as ei:
            NetworkLocationAllocationService().location_allocation(
                candidate_facilities=facilities, demand_points=demands,
                p_count=2, problem_type="p_median",
                graph=graph, network_dataset=dataset,
            )
        assert ei.value.scientific_code == "RESOURCE_SCALE_MISMATCH"
        assert "n_demand*n_facility=48" in (ei.value.estimated or "")
        assert "拆批" in (ei.value.correction_hint or "") or "subnet" in (ei.value.correction_hint or "")

    def test_accessibility_gravity_huff_closest_facility_refusals_are_typed(self, monkeypatch):
        monkeypatch.setattr(scale_guard_mod, "MAX_OD_MATRIX_PAIRS", 10)
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        facilities = [_facility(f"f{i}", 116.0 + i * 0.002) for i in range(4)]
        demands = [_demand(f"d{i}", 116.0 + i * 0.001) for i in range(12)]  # 48 pairs

        with pytest.raises(ResourceScaleMismatch) as ei_acc:
            NetworkAccessibilityService().network_accessibility(
                demand_points=demands, facilities=facilities,
                graph=graph, network_dataset=dataset,
                cutoff_minutes=15.0, method="2sfca",
            )
        assert ei_acc.value.scientific_code == "RESOURCE_SCALE_MISMATCH"
        assert "n_demand*n_facility=48" in (ei_acc.value.estimated or "")

        inter = NetworkInteractionService()
        with pytest.raises(ResourceScaleMismatch) as ei_grav:
            inter.gravity_accessibility(
                demand_points=demands, facilities=facilities,
                graph=graph, network_dataset=dataset,
            )
        assert ei_grav.value.scientific_code == "RESOURCE_SCALE_MISMATCH"

        with pytest.raises(ResourceScaleMismatch) as ei_huff:
            inter.huff_probabilities(
                demand_points=demands, facilities=facilities,
                graph=graph, network_dataset=dataset,
            )
        assert ei_huff.value.scientific_code == "RESOURCE_SCALE_MISMATCH"

        with pytest.raises(ResourceScaleMismatch) as ei_cf:
            NetworkClosestFacilityService().network_closest_facility(
                demand_points=demands, facilities=facilities,
                graph=graph, network_dataset=dataset,
            )
        assert ei_cf.value.scientific_code == "RESOURCE_SCALE_MISMATCH"
        assert ei_cf.value.estimated == "n_demand*n_facility=48"

    def test_exact_service_paths_refuse_before_od_materialization(self, monkeypatch):
        """R3 guard applies to the exact service surfaces too (统一资源包络):
        shrunk OD cap ⇒ typed refusal before the OD matrix is built, while
        the MILP model guard (25000/500) keeps bounding the solver itself."""
        # review R2-1：exact 服务路径用 MILP 自身刻度 —— 收缩该常量。
        monkeypatch.setattr(allocation_mod, "_MILP_MAX_PRODUCT", 10)
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        facilities = [_facility(f"f{i}", 116.0 + i * 0.002) for i in range(4)]
        demands = [_demand(f"d{i}", 116.0 + i * 0.001) for i in range(12)]
        svc = NetworkLocationAllocationService()

        for method, kwargs in (
            ("p_median_exact", {}),
            ("p_center_exact", {}),
            ("max_coverage_exact", {"cutoff_cost": 3600.0}),
        ):
            with pytest.raises(ResourceScaleMismatch) as ei:
                getattr(svc, method)(
                    candidate_facilities=facilities, demand_points=demands,
                    p_count=2, graph=graph, network_dataset=dataset, **kwargs,
                )
            assert ei.value.scientific_code == "RESOURCE_SCALE_MISMATCH"
            assert method in str(ei.value)

    def test_guard_passes_small_requests_unchanged(self):
        """Below the guard, behavior is unchanged: small-instance auto path
        still enumerates exactly (solver=exact) with the default 10k cap."""
        graph, dataset = NetworkGraphBuilder().build_graph(_chain_fc())
        facilities = [_facility(f"f{i}", 116.0 + i * 0.002) for i in range(4)]
        demands = [_demand(f"d{i}", 116.0 + i * 0.001) for i in range(12)]
        assert scale_guard_mod.MAX_OD_MATRIX_PAIRS == 10_000
        res = NetworkLocationAllocationService().location_allocation(
            candidate_facilities=facilities, demand_points=demands,
            p_count=2, problem_type="p_median",
            graph=graph, network_dataset=dataset,
        )
        assert res.summary["solver"] == "exact"


# ── 4. descriptor / tool / registry surface ──────────────────────────

class TestMCLPSurface:
    def test_mclp_descriptor_registration_and_references(self):
        """network.mclp_exact is registered with the MCLP citation, exact
        semantics declared, and the registry validates clean for it."""
        desc = get_algorithm_registry().get("network.mclp_exact")
        assert desc is not None
        assert desc.approximate is False
        assert desc.deterministic is True
        assert desc.method_references == ["church_revelle1974"]
        assert desc.complexity  # complexity declared (audit F5 gap closed here)
        assert desc.uncertainty_outputs == []
        assert desc.fallback_semantics == {
            "network.location_allocation": "approximation"}
        assert any("test_network_mclp.py" in t for t in desc.conformance_tests)

        issues = [i for i in get_algorithm_registry().validate()
                  if "network.mclp_exact" in i]
        assert issues == []

    def test_mclp_tool_evidence_anchors_exact_descriptor(self):
        """Tool path (objective=maximize_coverage, solver=exact_milp): the
        evidence block anchors network.mclp_exact with church_revelle1974
        and the payload discloses the MILP solver."""
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
            number_to_choose=2,
            objective="maximize_coverage",
            solver="exact_milp",
        ))
        assert out.get("type") != "error", out
        assert out["summary"]["solver"] == "milp_highs"
        assert out["summary"]["problem_type"] == "max_coverage"
        ev = out["scientific_evidence"]
        assert ev["algorithm"] == "network.mclp_exact"
        assert "church_revelle1974" in ev["method_references"]
        assert ev["parameters_applied"]["objective"] == "maximize_coverage"

        # Heuristic MCLP (solver omitted) keeps the legacy descriptor anchor.
        out_auto = asyncio.run(reg._tools["location_allocation"](
            network=_chain_fc(),
            candidate_facilities=facilities,
            demand_points=demands,
            number_to_choose=2,
            objective="maximize_coverage",
        ))
        assert out_auto.get("type") != "error", out_auto
        assert out_auto["scientific_evidence"]["algorithm"] == "network.location_allocation"
