"""
Network Spatial Interaction Service Component (Foundation V2 · A4).

Implements two OD-cost-based interaction models over the road network:

- Gravity accessibility (Hansen 1959 potential / Zipf 1946 gravity):
  A_i = Σ_j S_j^α / d_ij^β with d_ij = network OD cost (active impedance,
  default travel_time_s in seconds — never degrees-as-meters).
- Huff spatial interaction probabilities (Huff 1964):
  P_ij = A_j·d_ij^-β / Σ_k A_k·d_ik^-β with A = facility capacity as
  attractiveness; per-demand top-3 shares + Shannon entropy, per-facility
  market share and captive share.

Honesty axes: unreachable pairs are skipped AND counted (never silently
dropped); zero-cost pairs are clamped to an ε floor (disclosed); typed
errors (DisconnectedNetwork) for structurally disconnected inputs.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx

from app.lib.gis.scientific_errors import DisconnectedNetwork

from app.services.network.models import (
    NetworkDataset,
    TravelProfile,
    Facility,
    DemandPoint,
    GravityAccessResult,
    HuffInteractionResult,
)
from app.services.network.scale_guard import od_matrix_scale_guard
from app.services.network.snapping import PointSnappingService
from app.services.network.od_matrix import NetworkODMatrixService

# 零成本下限：需求点与设施捕捉到同一节点时 OD 成本恰为 0，d^-β 会除零。
# 以 ε 下限截断（截断后在 limitations 披露）——不丢弃最可达的对。
_EPS_COST = 1e-6

# 参数界（与参数契约 gravity_accessibility_analysis / huff_interaction_analysis
# 的 minimum/maximum 一致 —— 工具层 apply_contract 先拦，服务层兜底）。
_MASS_EXPONENT_BOUNDS = (0.0, 3.0)
_DISTANCE_DECAY_BOUNDS = (0.5, 4.0)


def _check_param(name: str, value: float, bounds: Tuple[float, float]) -> float:
    lo, hi = bounds
    if not lo <= value <= hi:
        raise ValueError(
            f"{name} 必须在 {lo}-{hi} 之间（收到 {value}）：界外指数使模型外推无意义"
        )
    return float(value)


def _cost_matrix_from_od(
    od_service: NetworkODMatrixService,
    demand_points: List[DemandPoint],
    facilities: List[Facility],
    graph: nx.DiGraph,
    network_dataset: NetworkDataset,
    profile: Optional[TravelProfile],
) -> List[List[float]]:
    """Raw (unrounded) OD cost matrix via the OD service's Dijkstra core.

    Uses ``_compute_od`` directly: the public ``network_od_matrix`` rounds
    travel times to 2 decimals — fine for allocation, but gravity/Huff raise
    d to a power where 10 ms per edge compounds into visible score error.
    Same single-Dijkstra-per-origin semantics, costs in the active
    impedance's units; inf = unreachable.
    """
    origins = [(d.geometry["coordinates"][0], d.geometry["coordinates"][1]) for d in demand_points]
    destinations = [(f.geometry["coordinates"][0], f.geometry["coordinates"][1]) for f in facilities]
    res = od_service._compute_od(
        origins, destinations, graph, network_dataset,
        profile=profile, need_paths=False,
    )
    n_dem, m_fac = len(origins), len(destinations)
    matrix: List[List[float]] = [[float("inf")] * m_fac for _ in range(n_dem)]
    for i, (o_node, _) in enumerate(res["origin_nodes"]):
        dists = res["results"].get(o_node, {}).get("dists", {})
        for j, (d_node, _) in enumerate(res["dest_nodes"]):
            if d_node in dists:
                matrix[i][j] = max(float(dists[d_node]), _EPS_COST)
    return matrix


def _top_contributions(pairs: List[Tuple[str, float]], k: int = 3) -> List[Dict[str, Any]]:
    """Sorted (id, share) pairs → top-k dicts (deterministic: share desc, id asc)."""
    ordered = sorted(pairs, key=lambda p: (-p[1], p[0]))[:k]
    return [{"facility_id": fid, "share": round(share, 6)} for fid, share in ordered]


class NetworkInteractionService:
    """Gravity accessibility + Huff spatial interaction over network OD costs."""

    def __init__(self, snapper: Optional[PointSnappingService] = None):
        self.snapper = snapper or PointSnappingService()
        self.od_service = NetworkODMatrixService(snapper=self.snapper)

    # ── Gravity accessibility（Hansen 1959 / Zipf 1946）────────────────

    def gravity_accessibility(
        self,
        demand_points: List[DemandPoint],
        facilities: List[Facility],
        graph: nx.DiGraph,
        network_dataset: NetworkDataset,
        mass_exponent: float = 1.0,
        distance_decay: float = 2.0,
        cutoff_cost: Optional[float] = None,
        profile: Optional[TravelProfile] = None,
    ) -> GravityAccessResult:
        """A_i = Σ_j S_j^α / d_ij^β over reachable OD pairs.

        S_j = facility capacity (α ∈ [0,3]); d_ij = OD cost in the active
        impedance's units (default seconds — β is unit-sensitive, disclosed).
        Unreachable pairs are skipped and counted; a demand with no reachable
        facility scores 0. All-pairs-unreachable (no cutoff) raises
        DisconnectedNetwork (structural fact, mirroring solve_od_matrix).
        """
        if not demand_points or not facilities:
            raise ValueError(
                "gravity_accessibility 需要 ≥1 个需求点与 ≥1 个设施"
                f"（收到 {len(demand_points)} 需求 / {len(facilities)} 设施）"
            )
        alpha = _check_param("mass_exponent", mass_exponent, _MASS_EXPONENT_BOUNDS)
        beta = _check_param("distance_decay", distance_decay, _DISTANCE_DECAY_BOUNDS)

        n_dem, m_fac = len(demand_points), len(facilities)
        # science-v3 R3：n×m OD 代价矩阵统一规模闸 —— 物化之前拒绝。
        od_matrix_scale_guard(n_dem, m_fac, context="gravity_accessibility")
        costs = _cost_matrix_from_od(
            self.od_service, demand_points, facilities, graph, network_dataset, profile
        )

        reachable_pairs = sum(1 for i in range(n_dem) for j in range(m_fac)
                              if costs[i][j] < float("inf"))
        unreachable_pairs = n_dem * m_fac - reachable_pairs
        if reachable_pairs == 0 and cutoff_cost is None:
            raise DisconnectedNetwork(
                f"引力可达性的 {n_dem * m_fac} 个需求×设施对全部不可达："
                "图不连通（或供需被方向隔离）——无势能可算"
            )

        capacities = [max(float(getattr(f, "capacity", 1.0) or 1.0), 0.0) for f in facilities]
        mass_terms = [cap ** alpha for cap in capacities]

        per_demand: List[Dict[str, Any]] = []
        scores: List[float] = []
        for i, dem in enumerate(demand_points):
            contributions: List[Tuple[str, float]] = []
            score = 0.0
            reachable_count = 0
            for j, fac in enumerate(facilities):
                d_ij = costs[i][j]
                if d_ij == float("inf"):
                    continue
                if cutoff_cost is not None and d_ij > cutoff_cost:
                    continue
                contrib = mass_terms[j] / (d_ij ** beta)
                score += contrib
                reachable_count += 1
                contributions.append((fac.facility_id, contrib))
            if score > 0:
                shares = [(fid, c / score) for fid, c in contributions]
            else:
                shares = []
            scores.append(score)
            per_demand.append({
                "demand_id": dem.demand_id,
                "weight": dem.weight,
                "score": round(score, 6),
                "reachable_facility_count": reachable_count,
                "top_facility_contributions": _top_contributions(shares),
            })

        finite = [s for s in scores if s > 0]
        summary = {
            "score_min": round(min(scores), 6) if scores else 0.0,
            "score_mean": round(sum(scores) / len(scores), 6) if scores else 0.0,
            "score_max": round(max(scores), 6) if scores else 0.0,
            "demands_with_supply": len(finite),
            "demands_without_supply": n_dem - len(finite),
        }

        return GravityAccessResult(
            mass_exponent=alpha,
            distance_decay=beta,
            impedance_field=profile.impedance_field if profile else "travel_time_s",
            cutoff_cost=cutoff_cost,
            demand_point_count=n_dem,
            facility_count=m_fac,
            reachable_pair_count=reachable_pairs,
            unreachable_pair_count=unreachable_pairs,
            per_demand_metrics=per_demand,
            summary=summary,
        )

    # ── Huff spatial interaction（Huff 1964）───────────────────────────

    def huff_probabilities(
        self,
        demand_points: List[DemandPoint],
        facilities: List[Facility],
        graph: nx.DiGraph,
        network_dataset: NetworkDataset,
        distance_decay: float = 2.0,
        cutoff_cost: Optional[float] = None,
        profile: Optional[TravelProfile] = None,
    ) -> HuffInteractionResult:
        """P_ij = A_j·d_ij^-β / Σ_k A_k·d_ik^-β per demand over its candidate set.

        Candidates = facilities reachable within cutoff_cost (None = all
        reachable). Per-demand top-3 shares + Shannon entropy (nat) over the
        FULL candidate set; per-facility market share Σ_i w_i·P_ij and captive
        share (demands whose candidate set is exactly {j}). Demands with an
        empty candidate set are disclosed as unassigned, never dropped.
        """
        if not demand_points or not facilities:
            raise ValueError(
                "huff_probabilities 需要 ≥1 个需求点与 ≥1 个设施"
                f"（收到 {len(demand_points)} 需求 / {len(facilities)} 设施）"
            )
        beta = _check_param("distance_decay", distance_decay, _DISTANCE_DECAY_BOUNDS)

        n_dem, m_fac = len(demand_points), len(facilities)
        # science-v3 R3：n×m OD 代价矩阵统一规模闸 —— 物化之前拒绝。
        od_matrix_scale_guard(n_dem, m_fac, context="huff_interaction")
        costs = _cost_matrix_from_od(
            self.od_service, demand_points, facilities, graph, network_dataset, profile
        )

        unreachable_pairs = sum(1 for i in range(n_dem) for j in range(m_fac)
                                if costs[i][j] == float("inf"))

        attractiveness = [max(float(getattr(f, "capacity", 1.0) or 1.0), 0.0) for f in facilities]

        per_demand: List[Dict[str, Any]] = []
        market_share = [0.0] * m_fac
        captive_weight = [0.0] * m_fac
        unassigned_ids: List[str] = []
        total_weight = sum(d.weight for d in demand_points)
        assigned_weight = 0.0

        for i, dem in enumerate(demand_points):
            candidates: List[Tuple[int, float]] = []  # (facility_idx, utility)
            for j in range(m_fac):
                d_ij = costs[i][j]
                if d_ij == float("inf"):
                    continue
                if cutoff_cost is not None and d_ij > cutoff_cost:
                    continue
                candidates.append((j, attractiveness[j] / (d_ij ** beta)))

            denom = sum(u for _, u in candidates)
            if not candidates or denom <= 0:
                unassigned_ids.append(dem.demand_id)
                per_demand.append({
                    "demand_id": dem.demand_id,
                    "weight": dem.weight,
                    "candidate_count": len(candidates),
                    "top_facility_shares": [],
                    "share_entropy": None,
                })
                continue

            shares_all = [(facilities[j].facility_id, u / denom) for j, u in candidates]
            entropy = -sum(p * math.log(p) for _, p in shares_all if p > 0)
            assigned_weight += dem.weight
            for j, u in candidates:
                market_share[j] += dem.weight * (u / denom)
            if len(candidates) == 1:
                captive_weight[candidates[0][0]] += dem.weight

            per_demand.append({
                "demand_id": dem.demand_id,
                "weight": dem.weight,
                "candidate_count": len(candidates),
                "top_facility_shares": _top_contributions(shares_all),
                "share_entropy": round(entropy, 6),
            })

        facility_metrics: List[Dict[str, Any]] = []
        for j, fac in enumerate(facilities):
            facility_metrics.append({
                "facility_id": fac.facility_id,
                "attractiveness": attractiveness[j],
                "market_share_weight": round(market_share[j], 6),
                "market_share_ratio": round(market_share[j] / total_weight, 6) if total_weight > 0 else 0.0,
                "captive_demand_weight": round(captive_weight[j], 6),
                "captive_share_ratio": round(captive_weight[j] / total_weight, 6) if total_weight > 0 else 0.0,
            })

        summary = {
            "total_demand_weight": round(total_weight, 6),
            "assigned_demand_weight": round(assigned_weight, 6),
            "unassigned_count": len(unassigned_ids),
            "unassigned_demand_ids": unassigned_ids,
            "unreachable_pair_count": unreachable_pairs,
        }

        return HuffInteractionResult(
            distance_decay=beta,
            impedance_field=profile.impedance_field if profile else "travel_time_s",
            cutoff_cost=cutoff_cost,
            demand_point_count=n_dem,
            facility_count=m_fac,
            unreachable_pair_count=unreachable_pairs,
            per_demand_metrics=per_demand,
            facility_metrics=facility_metrics,
            summary=summary,
        )
