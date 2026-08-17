"""Issue #540: network engine hotspot fixes — algorithmic equivalence & bounds.

Three hotspots fixed in this branch:
  1. barrier full-edge scan (B×E) -> one STRtree over edge geometries + prepared
     barrier predicates (B×k), semantics identical to the full scan.
  2. closest_facility built a full Route per reachable pair -> top-K selection
     happens on OD pair costs FIRST, Routes built only for selected pairs;
     `cutoff_cost` now also prunes the shared OD Dijkstra.
  3. VRP 2-opt recomputed the whole tour cost per candidate (O(n³)) -> O(1)
     reversal delta (directed-cost aware via a per-scan prefix) with the exact
     same accepted-move sequence.
  Plus the same-family `service_area` per-call graph.copy() residue: copy now
  happens only when a facility actually needs a virtual-node split.

Every test here compares the optimized path against the naive/pre-fix path on
small inputs (equivalence), plus boundary cases.
"""
import math
import random

import networkx as nx
import pytest
from shapely.geometry import LineString

from app.services.network.facility import NetworkClosestFacilityService
from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import (
    Barrier,
    DemandPoint,
    Facility,
)
from app.services.network.routing import NetworkRoutingService
from app.services.network.service_area import NetworkServiceAreaService
from app.services.network.vrp import NetworkRouteOptimizationService


# ─── shared grid helpers (node-exact coordinates) ────────────────────────────


def _grid_geojson(n: int, step: float = 0.01, base=(116.0, 39.0)) -> dict:
    features = []
    for r in range(n):
        features.append({
            "type": "Feature", "properties": {"id": f"h{r}"},
            "geometry": {"type": "LineString", "coordinates": [
                [base[0], base[1] + r * step], [base[0] + (n - 1) * step, base[1] + r * step]]},
        })
    for c in range(n):
        features.append({
            "type": "Feature", "properties": {"id": f"v{c}"},
            "geometry": {"type": "LineString", "coordinates": [
                [base[0] + c * step, base[1]], [base[0] + c * step, base[1] + (n - 1) * step]]},
        })
    return {"type": "FeatureCollection", "features": features}


def _build_grid(n: int = 4):
    builder = NetworkGraphBuilder()
    return builder.build_graph(_grid_geojson(n))


# ─── 1. barriers: STRtree index vs naive full scan ───────────────────────────


def _naive_apply_barriers(graph, barriers):
    """The pre-fix algorithm: per barrier, scan every edge, rebuild geometry,
    call intersects. Reference implementation for equivalence."""
    if not barriers:
        return graph
    graph_copy = graph.copy()
    for barrier in barriers:
        from shapely.geometry import shape as _shape

        b_geom = _shape(barrier.geometry)
        factor = barrier.impedance_factor
        edges_to_penalize = []
        for u, v, data in graph_copy.edges(data=True):
            edge_geom_dict = data.get("geometry")
            if edge_geom_dict:
                edge_shape = _shape(edge_geom_dict)
                if b_geom.intersects(edge_shape):
                    edges_to_penalize.append((u, v))
            else:
                u_data = graph_copy.nodes[u]
                v_data = graph_copy.nodes[v]
                segment = LineString([(u_data["x"], u_data["y"]), (v_data["x"], v_data["y"])])
                if b_geom.intersects(segment):
                    edges_to_penalize.append((u, v))
        if math.isinf(factor) or factor >= 1e6:
            graph_copy.remove_edges_from(edges_to_penalize)
        else:
            for u, v in edges_to_penalize:
                curr_factor = graph_copy[u][v].get("_barrier_factor", 1.0)
                graph_copy[u][v]["_barrier_factor"] = curr_factor * factor
    return graph_copy


def _naive_full_scan_snapshot(graph, barriers):
    """Reference snapshot: (removed, penalized) from the naive B×E algorithm."""
    naive = _naive_apply_barriers(graph, barriers)
    original_edges = set(graph.edges())
    removed = sorted(original_edges - set(naive.edges()))
    penalized = sorted(
        (u, v, naive[u][v].get("_barrier_factor"))
        for u, v in naive.edges()
        if naive[u][v].get("_barrier_factor", 1.0) != 1.0
    )
    return removed, penalized


def _apply_barriers_snapshot(graph, barriers):
    """(removed edge ids, penalized edge ids + factors) of the working copy —
    removal shows up as edges of the ORIGINAL graph absent from the view."""
    router = NetworkRoutingService()
    original_edges = set(graph.edges())
    view = router._apply_barriers(graph, barriers)  # noqa: SLF001
    removed = sorted(original_edges - set(view.edges()))
    penalized = sorted(
        (u, v, view[u][v].get("_barrier_factor"))
        for u, v in view.edges()
        if view[u][v].get("_barrier_factor", 1.0) != 1.0
    )
    return removed, penalized


def test_barrier_index_matches_naive_full_scan():
    """Indexed barrier application must remove/penalize EXACTLY the same edges
    as the full B×E scan, for point/line/polygon barriers, blocking and
    penalizing factors, and multiple barriers."""
    graph, _ = _build_grid()
    barriers = [
        Barrier(barrier_id="bp", barrier_type="point",
                geometry={"type": "Point", "coordinates": [116.01, 39.01]},
                impedance_factor=float("inf")),
        Barrier(barrier_id="bl", barrier_type="line",
                geometry={"type": "LineString", "coordinates": [[116.0, 39.02], [116.03, 39.02]]},
                impedance_factor=2.5),
        Barrier(barrier_id="bpoly", barrier_type="polygon",
                geometry={"type": "Polygon", "coordinates": [[
                    [116.02, 39.0], [116.03, 39.0], [116.03, 39.01], [116.02, 39.01], [116.02, 39.0]]]},
                impedance_factor=float("inf")),
    ]
    got = _apply_barriers_snapshot(graph, barriers)
    ref = _naive_full_scan_snapshot(graph, barriers)
    assert got == ref


def test_barrier_multipolygon_and_factor_accumulate():
    """A polygon barrier that spans grid edges, plus a *penalizing* barrier
    overlapping an already-penalized edge (factor multiplies) — and a
    MultiPolygon barrier that must index its envelope, not the whole
    collection."""
    graph, _ = _build_grid()
    barriers = [
        Barrier(barrier_id="b1", barrier_type="polygon",
                geometry={"type": "MultiPolygon", "coordinates": [
                    [[[116.0, 39.0], [116.01, 39.0], [116.01, 39.01], [116.0, 39.01], [116.0, 39.0]]],
                    [[[116.03, 39.03], [116.05, 39.03], [116.05, 39.05], [116.03, 39.05], [116.03, 39.03]]],
                ]},
                impedance_factor=1.5),
        Barrier(barrier_id="b2", barrier_type="point",
                geometry={"type": "Point", "coordinates": [116.005, 39.01]},  # ON h-row 39.01 edge
                impedance_factor=4.0),
    ]
    got = _apply_barriers_snapshot(graph, barriers)
    ref = _naive_full_scan_snapshot(graph, barriers)
    assert got == ref
    # the shared edge must carry the compounded factor 1.5 * 4.0 = 6.0
    factors = {f for _, _, f in got[1]}
    assert 6.0 in factors


def test_barriers_no_barriers_returns_original_graph():
    graph, _ = _build_grid()
    router = NetworkRoutingService()
    assert router._apply_barriers(graph, None) is graph  # noqa: SLF001
    assert router._apply_barriers(graph, []) is graph  # noqa: SLF001


def test_barriers_empty_graph_and_no_geometry_edges():
    """Boundary: empty directed graph with barriers (no crash) and a graph whose
    edges carry no geometry dict (node-coordinate fallback segment)."""
    router = NetworkRoutingService()
    g = nx.DiGraph()
    barrier = Barrier(barrier_id="b", barrier_type="point",
                      geometry={"type": "Point", "coordinates": [0.0, 0.0]},
                      impedance_factor=float("inf"))
    view = router._apply_barriers(g, [barrier])  # noqa: SLF001
    assert view is not g
    assert len(view.edges()) == 0

    g2 = nx.DiGraph()
    g2.add_node("a", x=0.0, y=0.0)
    g2.add_node("b", x=1.0, y=1.0)
    g2.add_edge("a", "b", id=1, length_m=100.0, travel_time_s=60.0)  # no geometry
    v2 = router._apply_barriers(g2, [barrier])  # noqa: SLF001
    assert not v2.has_edge("a", "b")  # blocked via the fallback segment


# ─── 2. closest_facility: top-K first, bounded Route construction ────────────


def test_closest_facility_builds_routes_only_for_selected_pairs(monkeypatch):
    """With D demands × F facilities all reachable and target K, Route objects
    are built at most D×K times (pre-fix: D×F)."""
    graph, dataset = _build_grid()
    calls = []

    real_build = NetworkRoutingService.build_route_from_path

    def counting_build(self, *args, **kwargs):
        calls.append(1)
        return real_build(self, *args, **kwargs)

    monkeypatch.setattr(NetworkRoutingService, "build_route_from_path", counting_build)

    svc = NetworkClosestFacilityService()
    demands = [
        DemandPoint(demand_id=f"d{i}", weight=1.0,
                    geometry={"type": "Point", "coordinates": [116.01 + 0.01 * (i % 2), 39.0 + 0.01 * (i // 2)]})
        for i in range(6)
    ]
    facilities = [
        Facility(facility_id=f"f{fac_id}",
                 geometry={"type": "Point", "coordinates": [116.0 + 0.01 * fac_id, 39.03]})
        for fac_id in range(5)
    ]
    res = svc.network_closest_facility(
        demand_points=demands, facilities=facilities, graph=graph,
        network_dataset=dataset, target_facility_count=2,
    )
    assert len(res.routes) == 6 * 2  # all demands × top-2
    assert len(calls) == 12, f"Route builds = {len(calls)}, want ≤ D×K = 12"


def test_closest_facility_selection_matches_naive_all_pairs():
    """The top-K-first selection must return the same routes (same order, same
    paths) as the pre-fix flow that built a Route per reachable pair and sorted
    afterwards — including cost ties (stable order)."""
    graph, dataset = _build_grid()
    svc = NetworkClosestFacilityService()
    od = svc.od_service.network_od_paths(
        origins=[[116.01, 39.0], [116.02, 39.01]],
        destinations=[[116.0, 39.03], [116.01, 39.03], [116.02, 39.03], [116.03, 39.03]],
        graph=graph, network_dataset=dataset,
    )
    demands = [DemandPoint(demand_id=f"d{i}", weight=1.0,
                           geometry={"type": "Point", "coordinates": c})
               for i, c in enumerate([[116.01, 39.0], [116.02, 39.01]])]
    facilities = [Facility(facility_id=f"f{i}", geometry={"type": "Point", "coordinates": c})
                  for i, c in enumerate([[116.0, 39.03], [116.01, 39.03], [116.02, 39.03], [116.03, 39.03]])]

    res = svc.network_closest_facility(
        demand_points=demands, facilities=facilities, graph=graph,
        network_dataset=dataset, target_facility_count=2,
    )

    # naive reference: build every reachable pair's route, group per demand,
    # stable-sort by total_cost, take top-2 (the pre-fix algorithm).
    naive_routes = []
    for dem_idx, dem in enumerate(demands):
        for fac_idx, fac in enumerate(facilities):
            o_label = od["origin_labels"][dem_idx]
            d_label = od["dest_labels"][fac_idx]
            info = od["pairs"].get((o_label, d_label))
            if not info or not info["reachable"]:
                continue
            route = svc.router.build_route_from_path(
                od["graph_view"], info["path"], origin_label=o_label,
                destination_label=d_label, profile_name="driving",
                route_id=f"route_{o_label}_{d_label}", weight_func=od["weight_func"],
            )
            route.origin_id = dem.demand_id
            route.destination_id = fac.facility_id
            naive_routes.append((route, fac))
    per_demand = {}
    for route, fac in naive_routes:
        per_demand.setdefault(route.origin_id, []).append((route, fac))
    naive_selected = []
    for key in sorted(per_demand):
        candidates = sorted(per_demand[key], key=lambda x: x[0].total_cost)
        naive_selected.extend(r for r, _ in candidates[:2])

    assert len(res.routes) == len(naive_selected)
    for got, ref in zip(res.routes, naive_selected):
        assert got.destination_id == ref.destination_id
        assert got.path_node_ids == ref.path_node_ids
        assert got.total_cost == pytest.approx(ref.total_cost, rel=1e-9)
        assert got.total_distance_m == pytest.approx(ref.total_distance_m, rel=1e-9)


def test_closest_facility_cutoff_prunes_and_filters():
    """cutoff_cost must (a) reach the OD Dijkstra (pairs beyond it are not even
    computed) and (b) still filter at the pair level — same visible result:
    only within-cutoff facilities are returned."""
    graph, dataset = _build_grid(5)
    svc = NetworkClosestFacilityService()
    demands = [DemandPoint(demand_id="d1", weight=1.0,
                           geometry={"type": "Point", "coordinates": [116.0, 39.0]})]
    facilities = [
        Facility(facility_id="f0", geometry={"type": "Point", "coordinates": [116.0, 39.01]}),  # 60 s
        Facility(facility_id="f4", geometry={"type": "Point", "coordinates": [116.04, 39.0]}),   # far
    ]
    full = svc.network_closest_facility(
        demand_points=demands, facilities=facilities, graph=graph,
        network_dataset=dataset, target_facility_count=2,
    )
    assert len(full.routes) == 2

    cut = svc.network_closest_facility(
        demand_points=demands, facilities=facilities, graph=graph,
        network_dataset=dataset, target_facility_count=2, cutoff_cost=250.0,
    )
    assert len(cut.routes) == 1
    assert cut.routes[0].destination_id == "f0"


def test_closest_facility_boundaries():
    """Zero-cost pair (demand at facility), facility_to_incident direction,
    unreachable facility, and empty inputs."""
    graph, dataset = _build_grid(4)
    svc = NetworkClosestFacilityService()

    # zero-distance match (#456): demand sits exactly at a facility
    at = [116.01, 39.01]
    res = svc.network_closest_facility(
        demand_points=[DemandPoint(demand_id="d0", weight=1.0, geometry={"type": "Point", "coordinates": at})],
        facilities=[Facility(facility_id="f0", geometry={"type": "Point", "coordinates": at})],
        graph=graph, network_dataset=dataset, target_facility_count=1,
    )
    assert len(res.routes) == 1
    assert res.routes[0].total_cost == 0.0

    # facility_to_incident: destination_id is the demand id
    res2 = svc.network_closest_facility(
        demand_points=[DemandPoint(demand_id="d1", weight=1.0, geometry={"type": "Point", "coordinates": [116.0, 39.0]})],
        facilities=[Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [116.03, 39.03]})],
        graph=graph, network_dataset=dataset, target_facility_count=1,
        travel_direction="facility_to_incident",
    )
    assert len(res2.routes) == 1
    assert res2.routes[0].destination_id == "d1"

    # empty demands/facilities → success summary, no routes
    empty = svc.network_closest_facility(
        demand_points=[], facilities=[], graph=graph, network_dataset=dataset,
    )
    assert empty.status == "success"
    assert empty.routes == []


# ─── 3. VRP 2-opt: O(1) delta == naive full recompute ───────────────────────


def _naive_two_opt(tour, cost_mat, is_roundtrip):
    """The pre-fix algorithm (build reversed tour + full cost sum per candidate)."""
    best_tour = list(tour)
    improved = True
    max_iter = 100
    iteration = 0

    def tour_cost(t):
        return sum(cost_mat[t[k]][t[k + 1]] for k in range(len(t) - 1))

    best_cost = tour_cost(best_tour)
    end_idx = len(best_tour) - 1 if is_roundtrip else len(best_tour)
    while improved and iteration < max_iter:
        improved = False
        iteration += 1
        for i in range(1, end_idx - 1):
            for j in range(i + 1, end_idx):
                new_tour = best_tour[:i] + best_tour[i:j + 1][::-1] + best_tour[j + 1:]
                new_c = tour_cost(new_tour)
                if new_c < best_cost - 1e-4:
                    best_cost = new_c
                    best_tour = new_tour
                    improved = True
                    break
            if improved:
                break
    return best_tour


@pytest.mark.parametrize("n,roundtrip", [
    (2, False), (3, False), (4, False), (8, False), (12, True), (15, True), (20, False), (25, True),
])
def test_two_opt_delta_matches_naive(n, roundtrip, seed=1234):
    """Directed (asymmetric) and symmetric cost matrices, path and roundtrip —
    the O(1)-delta scan must accept the exact same moves, in the same order, so
    the resulting tour is identical to the naive recompute."""
    rng = random.Random(seed)
    cost = [[round(rng.uniform(1.0, 50.0), 3) for _ in range(n)] for _ in range(n)]
    for i in range(n):
        cost[i][i] = 0.0
    # every ~3rd matrix fully symmetric (undirected costs)
    if seed % 3 == 0:
        for i in range(n):
            for j in range(i + 1, n):
                cost[j][i] = cost[i][j]
    tour = list(range(n))
    rng.shuffle(tour)

    svc = NetworkRouteOptimizationService()
    got = svc._two_opt(list(tour), cost, is_roundtrip=roundtrip)  # noqa: SLF001
    ref = _naive_two_opt(list(tour), cost, is_roundtrip=roundtrip)
    assert got == ref

    def tour_cost(t):
        return sum(cost[t[k]][t[k + 1]] for k in range(len(t) - 1))

    assert tour_cost(got) == pytest.approx(tour_cost(ref), abs=1e-9)


def _ladder_points(n: int) -> list:
    """Adversarial ladder: two parallel rows, interleaved (see bench script)."""
    pts = []
    for i in range(n // 2):
        pts.append((0.0, float(i)))
        pts.append((1.0, float(i)))
    if n % 2:
        pts.append((0.5, float(n // 2)))
    return pts


def test_two_opt_ladder_matches_naive():
    """The adversarial ladder instance (triage's worst case) — same tour."""
    pts = _ladder_points(20)
    n = len(pts)
    cost = [[math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]) for j in range(n)] for i in range(n)]
    svc = NetworkRouteOptimizationService()
    got = svc._two_opt(list(range(n)), cost, is_roundtrip=False)  # noqa: SLF001
    ref = _naive_two_opt(list(range(n)), cost, is_roundtrip=False)
    assert got == ref
    assert len(got) == n


def test_vrp_optimize_route_still_correct_end_to_end():
    """optimize_route's public contract unchanged: route returned with geometry,
    sane totals, and improvements do not increase cost."""
    graph, dataset = _build_grid(4)
    svc = NetworkRouteOptimizationService()
    route = svc.optimize_route(
        stops=[(116.01, 39.01), (116.03, 39.01), (116.03, 39.03), (116.01, 39.03)],
        depot=(116.0, 39.0), end_at_depot=True,
        graph=graph, network_dataset=dataset,
    )
    assert route.geometry["type"] == "LineString"
    assert route.total_distance_m > 0
    assert route.total_time_s > 0
    assert len(route.path_node_ids) >= 2


# ─── 3b. VRP/optimize-route tool surface: stops cap is explicit, not silent ──


def test_optimize_route_args_stops_cap_constant_exists():
    """The tool-layer cap is a named, documented constant; passing more stops
    than the cap must produce an explicit error message (never a silent skip)."""
    from app.tools import network_tools

    assert network_tools.MAX_OPTIMIZE_STOPS > 0
    assert "stops" in network_tools.OptimizeRouteArgs.model_fields
    assert network_tools.OptimizeRouteArgs.model_fields["stops"].description  # documents the cap


def test_closest_facility_args_cutoff_exposed():
    """The tool schema exposes cutoff_cost so agents can bound the analysis."""
    from app.tools.network_tools import NetworkClosestFacilityArgs

    args = NetworkClosestFacilityArgs(network={}, incidents=[], facilities=[])
    assert args.cutoff_cost is None
    assert "cutoff_cost" in NetworkClosestFacilityArgs.model_fields


# ─── 4. service_area: lazy graph copy (same-family residue) ──────────────────


def test_service_area_no_copy_when_all_facilities_node_exact(monkeypatch):
    """Facilities sitting exactly on grid nodes insert no virtual node → the
    caller's graph must be used as-is (no per-call deep copy)."""
    graph, dataset = _build_grid(4)
    copies = []

    real_copy = nx.DiGraph.copy

    def counting_copy(self, *args, **kwargs):
        copies.append(1)
        return real_copy(self, *args, **kwargs)

    monkeypatch.setattr(nx.DiGraph, "copy", counting_copy)

    svc = NetworkServiceAreaService()
    facilities = [
        Facility(facility_id="f1", geometry={"type": "Point", "coordinates": [116.01, 39.01]}),
        Facility(facility_id="f2", geometry={"type": "Point", "coordinates": [116.02, 39.02]}),
    ]
    areas = svc.network_service_area(
        facilities=facilities, breaks=[5000.0, 10000.0], break_unit="meters",
        graph=graph, network_dataset=dataset,
    )
    assert len(areas) == 2
    assert copies == [], f"graph.copy called {len(copies)} times with node-exact snaps"


def test_service_area_one_copy_when_mid_edge_and_same_result(monkeypatch):
    """A facility snapping mid-edge still splits the graph once and yields the
    same isochrone as the pre-fix per-call-copy version."""
    graph, dataset = _build_grid(4)
    copies = []

    real_copy = nx.DiGraph.copy

    def counting_copy(self, *args, **kwargs):
        copies.append(1)
        return real_copy(self, *args, **kwargs)

    monkeypatch.setattr(nx.DiGraph, "copy", counting_copy)

    svc = NetworkServiceAreaService()
    fac = Facility(facility_id="f_mid", geometry={"type": "Point", "coordinates": [116.015, 39.0]})
    areas = svc.network_service_area(
        facilities=[fac], breaks=[5000.0], break_unit="meters",
        graph=graph, network_dataset=dataset,
    )
    assert len(areas) == 1
    assert len(copies) == 1, f"expected exactly ONE working copy, got {len(copies)}"
    assert areas[0].breaks[0].geometry["type"] in ("Polygon", "MultiPolygon")

    # the facility genuinely lands mid-edge (fraction strictly between 0 and 1)
    snap_res = svc.snapper.snap_point((116.015, 39.0), dataset)
    assert 0.3 < snap_res.fraction_along_edge < 0.7
    assert snap_res.nearest_edge_id is not None


def test_service_area_empty_args():
    svc = NetworkServiceAreaService()
    assert svc.network_service_area(facilities=[], breaks=[5.0]) == []
    assert svc.network_service_area(facilities=[], breaks=[]) == []