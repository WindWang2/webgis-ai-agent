"""Regression tests for issue #446 (P1): reverse-direction edge splitting
applied the same fraction.

``_split_edge_at_fraction`` looped over both orientations of a two-way edge
((u,v) and (v,u)) applying the SAME ``fraction``. The fraction is defined
along the u→v geometry, so for the reverse (v→u) edge the correct local
fraction is 1−f. The bug produced swapped sub-edge lengths (a route from a
mid-edge origin to the NEAR endpoint reported (1−f)·L instead of f·L —
measured 691.3 m vs the true 172.8 m) and reverse sub-geometries that did
not contain the virtual node's position.
"""
import math

import pytest

from shapely.geometry import shape

from app.services.network.graph_builder import NetworkGraphBuilder, haversine_distance
from app.services.network.models import TravelProfile
from app.services.network.routing import NetworkRoutingService


def _single_two_way_edge(length_deg=0.01):
    """One two-way edge (116.0,39.0)→(116.01,39.0), ~865 m at 39°N."""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"speed_kmh": 60.0, "one_way": False, "name": "Main Rd"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[116.0, 39.0], [116.0 + length_deg, 39.0]],
                },
            }
        ],
    }
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
    return graph, dataset


def _two_edge_chain():
    """Two collinear two-way edges n0 --e0-- n1 --e1-- n2."""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"speed_kmh": 60.0, "one_way": False},
                "geometry": {"type": "LineString", "coordinates": [[116.0, 39.0], [116.01, 39.0]]},
            },
            {
                "type": "Feature",
                "properties": {"speed_kmh": 60.0, "one_way": False},
                "geometry": {"type": "LineString", "coordinates": [[116.01, 39.0], [116.02, 39.0]]},
            },
        ],
    }
    builder = NetworkGraphBuilder()
    graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
    return graph, dataset


class TestReverseSplitRouteLengths:
    """Issue #446 acceptance: snap at f on a two-way edge, route to BOTH
    endpoints — distances must be f·L and (1−f)·L respectively."""

    @pytest.mark.parametrize("f", [0.2, 0.5, 0.75])
    def test_mid_edge_origin_to_both_endpoints(self, f):
        graph, dataset = _single_two_way_edge()
        router = NetworkRoutingService()
        edge_len_m = haversine_distance((116.0, 39.0), (116.01, 39.0))

        origin = (116.0 + 0.01 * f, 39.0)
        near = (116.0, 39.0) if f <= 0.5 else (116.01, 39.0)
        far = (116.01, 39.0) if f <= 0.5 else (116.0, 39.0)

        route_near = router.network_shortest_path(
            graph=graph, network_dataset=dataset,
            origin=origin, destination=near, profile=TravelProfile(),
        )
        route_far = router.network_shortest_path(
            graph=graph, network_dataset=dataset,
            origin=origin, destination=far, profile=TravelProfile(),
        )

        frac_near = min(f, 1.0 - f)
        frac_far = max(f, 1.0 - f)
        # Snapping/projection round-trip tolerance: 3 m.
        assert abs(route_near.total_distance_m - edge_len_m * frac_near) < 3.0, (
            f"f={f}: route to near endpoint {route_near.total_distance_m:.1f} m, "
            f"expected ~{edge_len_m * frac_near:.1f} m"
        )
        assert abs(route_far.total_distance_m - edge_len_m * frac_far) < 3.0, (
            f"f={f}: route to far endpoint {route_far.total_distance_m:.1f} m, "
            f"expected ~{edge_len_m * frac_far:.1f} m"
        )

    def test_issue_reproduction_691_vs_173(self):
        """The exact case from the issue: ~865 m two-way edge, origin at f=0.2.
        Route to the near (left) endpoint must be ~173 m, not ~691 m."""
        graph, dataset = _single_two_way_edge()
        router = NetworkRoutingService()
        route = router.network_shortest_path(
            graph=graph, network_dataset=dataset,
            origin=(116.002, 39.0), destination=(116.0, 39.0), profile=TravelProfile(),
        )
        assert route.total_distance_m < 200.0, (
            f"reverse-split regression: {route.total_distance_m:.1f} m to the near "
            f"endpoint, expected ~173 m (the pre-fix bug reported ~691 m)"
        )


class TestReverseSplitGeometry:
    """Sub-geometries of BOTH orientations must contain the virtual node, and
    the forward/reverse sub-geometries must be consistent."""

    @pytest.mark.parametrize("f", [0.2, 0.5, 0.75])
    def test_sub_edge_geometry_contains_virtual_node(self, f):
        graph, dataset = _single_two_way_edge()
        router = NetworkRoutingService()
        work = graph.copy()
        snapped = (116.0 + 0.01 * f, 39.0)
        vt = router._split_edge_at_fraction(work, "n0", "n1", f, snapped)

        for u, v in (("n0", vt), (vt, "n1"), ("n1", vt), (vt, "n0")):
            assert work.has_edge(u, v), f"missing sub-edge {u}->{v}"
            geom_dict = work[u][v].get("geometry")
            assert geom_dict, f"sub-edge {u}->{v} has no geometry"
            coords = list(shape(geom_dict).coords)
            # The virtual node is the sub-edge endpoint AT vt: the last coord
            # when vt is the head (u→vt), the first when it is the tail (vt→v).
            endpoint = coords[-1] if v == vt else coords[0]
            assert math.hypot(endpoint[0] - snapped[0], endpoint[1] - snapped[1]) < 1e-9, (
                f"sub-edge {u}->{v} does not touch the virtual node: "
                f"endpoint {endpoint} vs snapped {snapped}"
            )

    @pytest.mark.parametrize("f", [0.2, 0.5, 0.75])
    def test_forward_and_reverse_sub_geometries_mirror(self, f):
        """sub(u→vt) must equal the coordinate-reversal of sub(vt→u): both run
        between the same two physical points."""
        graph, dataset = _single_two_way_edge()
        router = NetworkRoutingService()
        work = graph.copy()
        snapped = (116.0 + 0.01 * f, 39.0)
        vt = router._split_edge_at_fraction(work, "n0", "n1", f, snapped)

        fwd_sub = shape(work["n0"][vt]["geometry"]).coords
        rev_sub = shape(work[vt]["n0"]["geometry"]).coords
        assert list(fwd_sub) == [(x, y) for x, y in reversed(list(rev_sub))], (
            "forward u→vt sub-geometry must be the reverse of vt→u"
        )

    @pytest.mark.parametrize("f", [0.2, 0.5, 0.75])
    def test_reverse_direction_lengths_use_complement_fraction(self, f):
        """For the reverse orientation (n1→n0), the sub-edge adjacent to n1
        must carry (1−f)·L and the one adjacent to n0 must carry f·L."""
        graph, dataset = _single_two_way_edge()
        router = NetworkRoutingService()
        work = graph.copy()
        orig_len = float(graph["n0"]["n1"]["length_m"])
        vt = router._split_edge_at_fraction(work, "n0", "n1", f, (116.0 + 0.01 * f, 39.0))

        assert abs(work["n1"][vt]["length_m"] - orig_len * (1.0 - f)) < 1e-9
        assert abs(work[vt]["n0"]["length_m"] - orig_len * f) < 1e-9
        assert abs(work["n0"][vt]["length_m"] - orig_len * f) < 1e-9
        assert abs(work[vt]["n1"]["length_m"] - orig_len * (1.0 - f)) < 1e-9


class TestSplitSumInvariant:
    """Property: per orientation, split sub-edge lengths sum to the original."""

    @pytest.mark.parametrize("f", [0.2, 0.35, 0.5, 0.75])
    def test_sub_edge_lengths_sum_to_original(self, f):
        graph, _ = _single_two_way_edge()
        router = NetworkRoutingService()
        work = graph.copy()
        vt = router._split_edge_at_fraction(work, "n0", "n1", f, (116.0 + 0.01 * f, 39.0))

        orig_fwd = float(graph["n0"]["n1"]["length_m"])
        orig_rev = float(graph["n1"]["n0"]["length_m"])
        s_fwd = work["n0"][vt]["length_m"] + work[vt]["n1"]["length_m"]
        s_rev = work["n1"][vt]["length_m"] + work[vt]["n0"]["length_m"]
        assert abs(s_fwd - orig_fwd) < 1e-6
        assert abs(s_rev - orig_rev) < 1e-6

    def test_chain_second_split_keeps_total(self):
        """Origin and destination on the same edge of a two-edge chain: after
        both splits the walkable chain length must still equal the edge."""
        graph, dataset = _two_edge_chain()
        router = NetworkRoutingService()
        route = router.network_shortest_path(
            graph=graph, network_dataset=dataset,
            origin=(116.002, 39.0), destination=(116.008, 39.0), profile=TravelProfile(),
        )
        # 0.006° of longitude ≈ 519 m; the true span between the snapped points.
        expected = haversine_distance((116.002, 39.0), (116.008, 39.0))
        assert abs(route.total_distance_m - expected) < 5.0, (
            f"same-edge route across a second split: {route.total_distance_m:.1f} m "
            f"vs expected ~{expected:.1f} m"
        )

    def test_reverse_chain_walk_uses_complement(self):
        """Second snap walking the REVERSE chain (from n1 toward n0) must land
        at 1−f of the original edge, keeping route totals symmetric."""
        graph, dataset = _two_edge_chain()
        router = NetworkRoutingService()
        # Origin on e0 at f=0.2; destination is node n1 (the junction).
        route = router.network_shortest_path(
            graph=graph, network_dataset=dataset,
            origin=(116.002, 39.0), destination="n1", profile=TravelProfile(),
        )
        edge_len = haversine_distance((116.0, 39.0), (116.01, 39.0))
        assert abs(route.total_distance_m - edge_len * 0.8) < 3.0, (
            f"reverse-direction route to junction: {route.total_distance_m:.1f} m, "
            f"expected ~{edge_len * 0.8:.1f} m ((1−f)·L)"
        )


class TestAdversarialCases:
    def test_one_way_edge_split(self):
        """A one-way edge has no reverse orientation — split must still work."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"speed_kmh": 60.0, "one_way": True},
                    "geometry": {"type": "LineString", "coordinates": [[116.0, 39.0], [116.01, 39.0]]},
                },
            ],
        }
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
        router = NetworkRoutingService()
        route = router.network_shortest_path(
            graph=graph, network_dataset=dataset,
            origin=(116.002, 39.0), destination=(116.01, 39.0), profile=TravelProfile(),
        )
        edge_len = haversine_distance((116.0, 39.0), (116.01, 39.0))
        assert abs(route.total_distance_m - edge_len * 0.8) < 3.0

    def test_multivertex_edge_reverse_geometry(self):
        """A bent (3-vertex) edge: reverse sub-geometries must still contain
        the virtual node — the old code interpolated at the wrong fraction and
        could drop it entirely."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"speed_kmh": 60.0, "one_way": False},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[116.0, 39.0], [116.005, 39.0], [116.01, 39.0]],
                    },
                },
            ],
        }
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(geojson, profile=TravelProfile())
        router = NetworkRoutingService()
        work = graph.copy()
        snapped = (116.002, 39.0)
        vt = router._split_edge_at_fraction(work, "n0", "n1", 0.2, snapped)
        for u, v in (("n0", vt), (vt, "n1"), ("n1", vt), (vt, "n0")):
            coords = list(shape(work[u][v]["geometry"]).coords)
            touched = any(math.hypot(c[0] - snapped[0], c[1] - snapped[1]) < 1e-9 for c in coords)
            assert touched, f"sub-edge {u}->{v} ({coords}) misses the virtual node"
