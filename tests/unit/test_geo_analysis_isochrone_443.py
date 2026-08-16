"""Regression tests for issue #443 (P3 perf): ``calculate_isochrones``
(app/lib/geo_analysis/network.py — the isochrone_network tool path) ran one
cutoff-bounded Dijkstra per facility but then scanned ALL E graph edges per
facility with per-edge shapely substring clipping — O(F×E). On a 50k-edge
network with 10 facilities that was measured at ~300 s.

Fix: an edge is reachable iff at least one endpoint is in the facility's
cutoff-bounded Dijkstra set, so the scan walks the adjacency of the reachable
nodes only (in G.nodes order, deduping undirected edges exactly like
networkx's EdgeView) — identical reachable-edge sets, clipping and polygons,
at O(reachable) instead of O(E) per facility.

The per-facility Dijkstra trees are kept deliberately: the output is one
polygon per facility, which a merged multi-source tree cannot produce.
"""
import json


import networkx as nx

from shapely.geometry import LineString
from shapely.ops import substring

from app.lib.geo_analysis.network import calculate_isochrones


def _grid_geojson(k=10, step=0.001, origin=(116.0, 39.0)):
    """Pre-segmented k x k grid (one 2-point feature per segment)."""
    feats = []
    for r in range(k):
        for c in range(k - 1):
            feats.append({"type": "Feature", "properties": {}, "geometry": {
                "type": "LineString",
                "coordinates": [[origin[0] + c * step, origin[1] + r * step],
                                [origin[0] + (c + 1) * step, origin[1] + r * step]]}})
    for c in range(k):
        for r in range(k - 1):
            feats.append({"type": "Feature", "properties": {}, "geometry": {
                "type": "LineString",
                "coordinates": [[origin[0] + c * step, origin[1] + r * step],
                                [origin[0] + c * step, origin[1] + (r + 1) * step]]}})
    return {"type": "FeatureCollection", "features": feats}


def _facility(id_, x, y):
    return {"type": "Feature", "properties": {"id": id_},
            "geometry": {"type": "Point", "coordinates": [x, y]}}


def _reference_reachable_edges(network_geojson, facility, minutes, mode="walking"):
    """The pre-fix (#443) algorithm: full ``G.edges(data=True)`` scan with
    identical clipping semantics. Returns ``(lengths, reachable_geometries)``
    where the geometries are in the exact order/multiplicity the old scan
    produced — the behavioral reference for the optimized adjacency walk."""
    import numpy as np
    from scipy.spatial import cKDTree

    from app.lib.geo_processor.core import to_utm_gdf
    from app.lib.geo_analysis.network import _speed_m_per_min

    gdf_network, utm_crs = to_utm_gdf(network_geojson)
    gdf_facilities, _ = to_utm_gdf(facility)
    gdf_facilities = gdf_facilities.to_crs(utm_crs)

    G = nx.MultiGraph()
    for idx, row in gdf_network.iterrows():
        coords = list(row.geometry.coords)
        G.add_edge(coords[0], coords[-1], weight=float(row.geometry.length), geometry=row.geometry)

    max_dist = float(minutes) * _speed_m_per_min(mode)
    nodes = list(G.nodes())
    node_tree = cKDTree(np.array(nodes))
    fac_geom = gdf_facilities.iloc[0].geometry
    _, nearest_idx = node_tree.query(np.array([fac_geom.x, fac_geom.y]), k=1)
    lengths = nx.single_source_dijkstra_path_length(G, nodes[nearest_idx], cutoff=max_dist, weight="weight")

    reachable_edges = []
    for u, v, edata in G.edges(data=True):
        eg = edata.get("geometry")
        if eg is None:
            continue
        du = lengths.get(u)
        dv = lengths.get(v)
        if du is None and dv is None:
            continue
        du_ok = du is not None and du <= max_dist
        dv_ok = dv is not None and dv <= max_dist
        if du_ok and dv_ok:
            reachable_edges.append(eg)
            continue
        w = edata.get("weight") or eg.length
        if w <= 0:
            w = eg.length or 1.0
        if du_ok:
            frac = min(1.0, max(0.0, (max_dist - du) / w))
            if frac > 0:
                seg = substring(eg, 0.0, frac, normalized=True)
                if not seg.is_empty:
                    reachable_edges.append(seg)
        if dv_ok:
            frac = min(1.0, max(0.0, (max_dist - dv) / w))
            if frac > 0:
                seg = substring(eg, 1.0 - frac, 1.0, normalized=True)
                if not seg.is_empty:
                    reachable_edges.append(seg)
    return lengths, reachable_edges, utm_crs


def _reference_reachable_edge_count(network_geojson, facility, minutes, mode="walking"):
    lengths, edges, _ = _reference_reachable_edges(network_geojson, facility, minutes, mode)
    return len(lengths), len(edges)


class TestIsochroneEquivalence:
    def test_reachable_counts_match_reference_scan(self):
        """reachable_nodes_count / reachable_edges_count must equal the
        pre-fix full-scan reference on a mid-size grid."""
        net = _grid_geojson(10)
        facs = {"type": "FeatureCollection", "features": [_facility("f1", 116.004, 39.004)]}
        res = calculate_isochrones(json.dumps(net), json.dumps(facs), 3.0, mode="walking")
        assert res.success, res.summary

        props = res.data["features"][0]["properties"]
        ref_nodes, ref_edges = _reference_reachable_edge_count(net, facs, 3.0, "walking")
        assert props["reachable_nodes_count"] == ref_nodes
        assert props["reachable_edges_count"] == ref_edges

    def test_multiple_facilities_all_present(self):
        net = _grid_geojson(12)
        facs = {"type": "FeatureCollection", "features": [
            _facility("a", 116.002, 39.002), _facility("b", 116.008, 39.006),
            _facility("c", 116.005, 39.009),
        ]}
        res = calculate_isochrones(json.dumps(net), json.dumps(facs), 5.0, mode="walking")
        assert res.success
        ids = [f["properties"]["facility_id"] for f in res.data["features"]]
        assert ids == ["a", "b", "c"]
        for f in res.data["features"]:
            assert f["properties"]["reachable"]
            assert f["properties"]["reachable_edges_count"] > 0
            assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")

    def test_polygon_contains_facility_area(self):
        """The buffered reachable network polygon must contain the facility."""
        from shapely.geometry import shape, Point

        net = _grid_geojson(10)
        facs = {"type": "FeatureCollection", "features": [_facility("f1", 116.004, 39.004)]}
        res = calculate_isochrones(json.dumps(net), json.dumps(facs), 3.0, mode="walking")
        poly = shape(res.data["features"][0]["geometry"])
        assert poly.contains(Point(116.004, 39.004))

    def test_larger_cutoff_reaches_strictly_more(self):
        """Monotonicity: 6-min reach must strictly contain 2-min reach."""
        net = _grid_geojson(12)
        facs = {"type": "FeatureCollection", "features": [_facility("f1", 116.005, 39.005)]}
        small = calculate_isochrones(json.dumps(net), json.dumps(facs), 2.0, mode="walking")
        large = calculate_isochrones(json.dumps(net), json.dumps(facs), 6.0, mode="walking")
        p_small = small.data["features"][0]["properties"]
        p_large = large.data["features"][0]["properties"]
        assert p_large["reachable_nodes_count"] > p_small["reachable_nodes_count"]
        assert p_large["reachable_edges_count"] > p_small["reachable_edges_count"]

    def test_polygon_geometry_matches_reference_full_scan(self):
        """Full-geometry equivalence: the production polygon must equal the
        polygon the pre-fix full-scan algorithm would emit (union of the same
        clipped segments, same buffer, same reprojection) — not just match
        its counts."""
        from shapely.geometry import shape
        from shapely.ops import unary_union

        net = _grid_geojson(10)
        facs = {"type": "FeatureCollection", "features": [_facility("f1", 116.004, 39.004)]}
        res = calculate_isochrones(json.dumps(net), json.dumps(facs), 3.0, mode="walking")
        assert res.success

        _, ref_edges, utm_crs = _reference_reachable_edges(net, facs, 3.0, "walking")
        assert ref_edges, "reference must be reachable for this fixture"
        import geopandas as gpd
        ref_poly = gpd.GeoSeries(
            [unary_union(ref_edges).buffer(30.0)], crs=utm_crs
        ).to_crs("EPSG:4326").iloc[0]

        prod_poly = shape(res.data["features"][0]["geometry"])
        assert prod_poly.geom_type == ref_poly.geom_type
        assert prod_poly.symmetric_difference(ref_poly).area < 1e-12, (
            "production isochrone diverges from the pre-fix full-scan geometry"
        )


class TestEdgeViewOrientationParity:
    def test_reversed_geometry_parallel_edge_clips_like_edgeview(self):
        """Orientation parity on the sneaky topology: an edge whose only
        reachable endpoint is the node that appears LATER in G.nodes order.

        networkx's EdgeView yields such an edge as (earlier, later) — i.e.
        EdgeView-u is the UNREACHABLE endpoint — and the pre-fix scan clipped
        from the geometry END for it. The adjacency walk first encounters the
        edge from the reachable LATER node and must reorient to (earlier,
        later) exactly like EdgeView — otherwise the clipped segment lands on
        the opposite end of the road and the polygon shifts by hundreds of
        metres."""
        from shapely.geometry import shape
        from shapely.ops import unary_union
        import geopandas as gpd

        # Edge 1 (added first): A=(0,0) -> B=(0.004,0), straight. Node order
        # becomes A=0, B=1.
        # Edge 2: parallel, geometry B -> A along a bowed path.
        net = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "LineString",
                          "coordinates": [[0.0, 0.0], [0.004, 0.0]]}},
            {"type": "Feature", "properties": {},
             "geometry": {"type": "LineString",
                          "coordinates": [[0.004, 0.0], [0.002, 0.001], [0.0, 0.0]]}},
        ]}
        # Facility at B: d(B)=0, A unreachable beyond the budget, so both
        # edges are first encountered from B — the LATER node — and are only
        # PARTIALLY reachable, so clipping orientation matters.
        facs = {"type": "FeatureCollection", "features": [_facility("f1", 0.004, 0.0)]}
        res = calculate_isochrones(json.dumps(net), json.dumps(facs), 3.0, mode="walking")
        assert res.success
        assert res.data["features"][0]["properties"]["reachable_edges_count"] == 2

        _, ref_edges, utm_crs = _reference_reachable_edges(net, facs, 3.0, "walking")
        assert len(ref_edges) == 2
        ref_poly = gpd.GeoSeries(
            [unary_union(ref_edges).buffer(30.0)], crs=utm_crs
        ).to_crs("EPSG:4326").iloc[0]
        prod_poly = shape(res.data["features"][0]["geometry"])
        assert prod_poly.symmetric_difference(ref_poly).area < 1e-12


class TestAdversarialTopologies:
    def test_disconnected_graph_stays_within_own_component(self):
        """Two disjoint grids: the facility's reachable set must come from its
        own component only, and still match the pre-fix reference."""
        a = _grid_geojson(6, origin=(116.0, 39.0))
        b = _grid_geojson(6, origin=(117.0, 39.0))  # ~1 deg away: no shared nodes
        net = {"type": "FeatureCollection", "features": a["features"] + b["features"]}
        facs = {"type": "FeatureCollection", "features": [_facility("f1", 116.002, 39.002)]}
        res = calculate_isochrones(json.dumps(net), json.dumps(facs), 5.0, mode="walking")
        assert res.success
        props = res.data["features"][0]["properties"]
        assert props["reachable"]
        ref_nodes, ref_edges = _reference_reachable_edge_count(net, facs, 5.0, "walking")
        assert props["reachable_nodes_count"] == ref_nodes
        assert props["reachable_edges_count"] == ref_edges
        # A 5-min walk (~400 m) cannot leave the 6x6 (~550 m) grid A.
        assert ref_nodes < 6 * 6

    def test_facility_far_off_network_snaps_to_nearest_node(self):
        """A facility 0.5 deg from any road must not crash: it snaps to the
        nearest node and gets an honest tiny isochrone there."""
        net = _grid_geojson(4, origin=(116.0, 39.0))
        facs = {"type": "FeatureCollection", "features": [_facility("far", 116.5, 39.5)]}
        res = calculate_isochrones(json.dumps(net), json.dumps(facs), 0.5, mode="walking")
        assert res.success
        props = res.data["features"][0]["properties"]
        # 0.5 min walking = 40 m < one ~110 m grid cell: at most the four
        # edges incident to the snapped node, each partially clipped.
        assert props["reachable"]
        assert 0 < props["reachable_edges_count"] <= 4
        assert props["reachable_nodes_count"] == 1
        assert res.data["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")

    def test_zero_travel_time_yields_marker_not_crash(self):
        """travel_time_min=0 -> max_dist=0: only the snapped node is in the
        Dijkstra set and every clip fraction is 0, so the honest-unreachable
        marker path must trigger (no crash, no fabricated coverage)."""
        net = _grid_geojson(6)
        facs = {"type": "FeatureCollection", "features": [_facility("f1", 116.002, 39.002)]}
        res = calculate_isochrones(json.dumps(net), json.dumps(facs), 0.0, mode="walking")
        assert res.success
        props = res.data["features"][0]["properties"]
        assert props["reachable"] is False
        assert props["reachable_edges_count"] == 0
        assert props["reachable_nodes_count"] == 1
        assert res.data["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")


class TestPartialEdgeClipping:
    def test_partial_edge_clipped_from_reachable_end(self):
        """Unit check of the clipping semantics on a hand-built MultiGraph:
        an edge with one endpoint beyond the budget is clipped at the
        remaining fraction from the reachable end."""
        G = nx.MultiGraph()
        # 400 m straight road; facility at u's position, budget 300 m.
        G.add_node((0.0, 0.0))
        G.add_node((0.0, 0.0036))
        line = LineString([(0.0, 0.0), (0.0, 0.0036)])
        G.add_edge((0.0, 0.0), (0.0, 0.0036), weight=400.0, geometry=line)

        lengths = {(0.0, 0.0): 0.0}
        max_dist = 300.0

        # Reproduce the production clipping for this edge (reference copy).
        for u, v, edata in G.edges(data=True):
            eg = edata.get("geometry")
            w = edata.get("weight") or eg.length
            du = lengths.get(u)
            dv = lengths.get(v)
            assert du is not None and dv is None
            frac = min(1.0, max(0.0, (max_dist - du) / w))
            seg = substring(eg, 0.0, frac, normalized=True)
            assert abs(seg.length - frac * eg.length) < 1e-9, (
                f"clipped {seg.length:.6f} of a {eg.length:.6f} edge — expected "
                f"{frac:.3f} of it at a {max_dist:.0f}/{w:.0f} m budget"
            )
            assert abs(frac - 0.75) < 1e-9


class TestPerf:
    def test_small_reach_on_large_network_is_fast(self):
        """Structural performance guard: a tiny travel budget over a large
        network must not pay the full O(F x E) edge scan — the reachable set
        is a few dozen edges out of ~20k. Generous wall bound (the pre-fix
        code needed ~10+ s for this shape)."""
        import time

        net = _grid_geojson(100)  # 19,800 edges
        facs = {"type": "FeatureCollection", "features": [
            _facility("f1", 116.05, 39.05), _facility("f2", 116.07, 39.03),
        ]}
        t0 = time.perf_counter()
        res = calculate_isochrones(json.dumps(net), json.dumps(facs), 1.0, mode="walking")
        elapsed = time.perf_counter() - t0
        assert res.success
        # 1 min walking = 80 m ≈ 1 grid cell: tiny reachable sets.
        for f in res.data["features"]:
            assert f["properties"]["reachable_edges_count"] <= 200
        assert elapsed < 5.0, f"tiny-budget isochrone over 19.8k edges took {elapsed:.1f} s"
