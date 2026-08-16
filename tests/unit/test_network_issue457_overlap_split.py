"""Regression tests for issue #457 (P3): the graph builder never split
COLLINEAR OVERLAPPING lines (``_process_intersections`` collected only
Point/MultiPoint intersections — an overlap intersects as a LineString /
GeometryCollection, silently dropped), leaving the shared sub-segment as a
disconnected parallel edge. Additionally, the DiGraph silently OVERWROTE
parallel (u, v) edges while the dataset kept every duplicate edge id,
desyncing dataset edge ids from the graph.

Fix: overlap endpoints join the split-point set (both lines are cut where
the shared run begins/ends, so the shared sub-segment becomes real junction-
connected edges), and same-(u,v) additions are deduplicated so the dataset
and graph stay 1:1 in edge ids.
"""
import pytest

import networkx as nx

from shapely.geometry import shape

from app.services.network.graph_builder import NetworkGraphBuilder
from app.services.network.models import TravelProfile


def _line(coords, props=None):
    return {
        "type": "Feature",
        "properties": props or {"speed_kmh": 60.0, "one_way": False},
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def _node_at(graph, x, y):
    for n, d in graph.nodes(data=True):
        if abs(d["x"] - x) < 1e-9 and abs(d["y"] - y) < 1e-9:
            return n
    return None


class TestPartialCollinearOverlap:
    def _build(self):
        """line1 spans lng 116.000→116.004; line2 overlaps 116.001→116.003."""
        builder = NetworkGraphBuilder()
        return builder.build_graph(
            _fc(
                _line([[116.0, 39.0], [116.004, 39.0]]),
                _line([[116.001, 39.0], [116.003, 39.0]]),
            ),
            profile=TravelProfile(),
        )

    def test_overlap_endpoints_become_split_points(self):
        """The overlap endpoints must SPLIT line1: the sub-edge from 116.000
        to 116.001 must exist. (Without splitting, 116.001 exists only as
        line2's endpoint — an isolated node with no connection to 116.000.)"""
        graph, dataset = self._build()
        n0 = _node_at(graph, 116.0, 39.0)
        n1 = _node_at(graph, 116.001, 39.0)
        n4 = _node_at(graph, 116.004, 39.0)
        assert n1 is not None and n4 is not None
        assert graph.has_edge(n0, n1), (
            "no sub-edge 116.000→116.001: line1 was not cut at the overlap start"
        )
        assert graph.has_edge(n1, n4) or any(
            graph.has_edge(n1, x) for x in graph.successors(n1)
        ), "overlap start node has no outgoing continuation"

    def test_shared_segment_is_connected(self):
        """The two lines' node sets must be connected through the shared run —
        routing from a point on line2 to a point on line1's exclusive end must
        work instead of returning no path."""
        graph, dataset = self._build()
        n0 = _node_at(graph, 116.0, 39.0)
        n1 = _node_at(graph, 116.001, 39.0)
        assert nx.has_path(graph, n1, n0), (
            "line2's endpoint cannot reach line1's endpoint — the overlap "
            "stayed a floating parallel edge (#457)"
        )

    def test_route_across_overlap_has_full_length(self):
        from app.services.network.routing import NetworkRoutingService
        from app.services.network.graph_builder import haversine_distance
        graph, dataset = self._build()
        router = NetworkRoutingService()
        # Mid-shared-segment → line1's exclusive west end: only routable when
        # the shared run is junction-connected to the outer sub-edges.
        route = router.network_shortest_path(
            graph, dataset, (116.002, 39.0), (116.0, 39.0), profile=TravelProfile(),
        )
        expected = haversine_distance((116.002, 39.0), (116.0, 39.0))
        assert route.total_cost < float("inf"), "no route across the overlap"
        assert route.total_distance_m == pytest.approx(expected, rel=0.02)

    def test_reversed_orientation_overlap_also_splits(self):
        """line2 drawn right-to-left ([116.003]→[116.001]) overlaps the same."""
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(
            _fc(
                _line([[116.0, 39.0], [116.004, 39.0]]),
                _line([[116.003, 39.0], [116.001, 39.0]]),
            ),
            profile=TravelProfile(),
        )
        n0 = _node_at(graph, 116.0, 39.0)
        n1 = _node_at(graph, 116.001, 39.0)
        assert n1 is not None
        assert graph.has_edge(n0, n1), "reversed-orientation overlap not split"
        assert nx.has_path(graph, n1, _node_at(graph, 116.004, 39.0))

    def test_containment_overlap_splits_at_inner_line_endpoints(self):
        """line2 entirely inside line1 (116.001→116.002 within 116.0→116.004)."""
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(
            _fc(
                _line([[116.0, 39.0], [116.004, 39.0]]),
                _line([[116.001, 39.0], [116.002, 39.0]]),
            ),
            profile=TravelProfile(),
        )
        n0 = _node_at(graph, 116.0, 39.0)
        n1 = _node_at(graph, 116.001, 39.0)
        n2 = _node_at(graph, 116.002, 39.0)
        n4 = _node_at(graph, 116.004, 39.0)
        assert n1 is not None and n2 is not None
        assert graph.has_edge(n0, n1) and graph.has_edge(n2, n4)
        assert nx.has_path(graph, n1, n4)


class TestParallelEdgeDedupe:
    def test_identical_duplicate_lines_keep_graph_and_dataset_in_sync(self):
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(
            _fc(
                _line([[116.0, 39.0], [116.01, 39.0]]),
                _line([[116.0, 39.0], [116.01, 39.0]]),
            ),
            profile=TravelProfile(),
        )
        # Two-way street: exactly one u->v and one v->u edge, in BOTH the
        # graph and the dataset (previously the graph kept 2 while the
        # dataset kept 4 — the second graph add silently replaced the first).
        assert graph.number_of_edges() == 2
        assert dataset.edge_count == graph.number_of_edges()

    def test_every_dataset_edge_id_maps_to_a_graph_edge(self):
        """Invariant: dataset edge ids and graph edge ids stay 1:1 — the
        snapper returns dataset edge ids that routing looks up in the graph."""
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(
            _fc(
                _line([[116.0, 39.0], [116.004, 39.0]]),
                _line([[116.001, 39.0], [116.003, 39.0]]),
                _line([[116.0, 39.0], [116.01, 39.0]]),
            ),
            profile=TravelProfile(),
        )
        graph_edge_ids = {d["id"] for _, _, d in graph.edges(data=True)}
        dataset_edge_ids = {str(e.id) for e in dataset.edges}
        assert dataset_edge_ids == graph_edge_ids, (
            f"dataset/graph edge id desync: only-in-dataset="
            f"{sorted(dataset_edge_ids - graph_edge_ids)[:4]}"
        )
        for e in dataset.edges:
            assert graph.has_edge(e.u, e.v), f"dataset edge {e.id} missing in graph"

    def test_one_way_duplicate_still_adds_reverse(self):
        """Dedupe must be per DIRECTED pair: a one-way original followed by a
        two-way duplicate still contributes the v->u direction."""
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(
            _fc(
                _line([[116.0, 39.0], [116.01, 39.0]], {"speed_kmh": 60.0, "one_way": True}),
                _line([[116.0, 39.0], [116.01, 39.0]]),
            ),
            profile=TravelProfile(),
        )
        assert graph.has_edge(_node_at(graph, 116.0, 39.0), _node_at(graph, 116.01, 39.0))
        assert graph.has_edge(_node_at(graph, 116.01, 39.0), _node_at(graph, 116.0, 39.0))
        assert dataset.edge_count == graph.number_of_edges() == 2

    def test_split_subsegments_geometry_oriented(self):
        """Sub-edge geometries keep their traversal orientation (u→v coords)."""
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(
            _fc(
                _line([[116.0, 39.0], [116.004, 39.0]]),
                _line([[116.001, 39.0], [116.003, 39.0]]),
            ),
            profile=TravelProfile(),
        )
        for u, v, d in graph.edges(data=True):
            coords = list(shape(d["geometry"]).coords)
            u_d, v_d = graph.nodes[u], graph.nodes[v]
            assert abs(coords[0][0] - u_d["x"]) < 1e-9 and abs(coords[-1][0] - v_d["x"]) < 1e-9, (
                f"edge {u}->{v} geometry not oriented with its direction"
            )


class TestCrossingStillWorks:
    def test_point_intersections_unchanged(self):
        """Crossing (Point) intersections split exactly as before."""
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(
            _fc(
                _line([[116.0, 39.0], [116.01, 39.0]]),
                _line([[116.005, 38.995], [116.005, 39.005]]),
            ),
            profile=TravelProfile(),
        )
        assert _node_at(graph, 116.005, 39.0) is not None

    def test_touching_endpoints_unchanged(self):
        """Lines sharing an endpoint still share the node (no spurious split)."""
        builder = NetworkGraphBuilder()
        graph, dataset = builder.build_graph(
            _fc(
                _line([[116.0, 39.0], [116.005, 39.0]]),
                _line([[116.005, 39.0], [116.01, 39.0]]),
            ),
            profile=TravelProfile(),
        )
        shared = _node_at(graph, 116.005, 39.0)
        assert shared is not None
        assert nx.has_path(graph, _node_at(graph, 116.0, 39.0), _node_at(graph, 116.01, 39.0))
