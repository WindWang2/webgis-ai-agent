"""G21 OD flow product — geometry tests (public API only).

Straight legacy default, deterministic curves, antimeridian splitting,
self-loops, zero-length disclosure, and the ≤24 segments/edge budget.
"""
import json

import pytest

from app.services.flow_product import MAX_EDGE_SEGMENTS, build_flow_product


def row(o_lng, o_lat, d_lng, d_lat, w, o_id=None, d_id=None):
    r = {"origin_lng": o_lng, "origin_lat": o_lat,
         "destination_lng": d_lng, "destination_lat": d_lat, "weight": w}
    if o_id is not None:
        r["origin_id"] = o_id
    if d_id is not None:
        r["destination_id"] = d_id
    return r


def build(rows, **cfg_kw):
    config = __import__("app.services.flow_product",
                        fromlist=["FlowProductConfig"]).FlowProductConfig(**cfg_kw)
    return build_flow_product(
        {"type": "od_table", "rows": rows}, config,
        source_scope="geo-scope", source_revision=1,
    )


def _segments(geom):
    if geom["type"] == "LineString":
        return len(geom["coordinates"]) - 1
    return sum(len(p) - 1 for p in geom["coordinates"])


def _all_points(geom):
    if geom["type"] == "LineString":
        return [tuple(c) for c in geom["coordinates"]]
    return [tuple(c) for part in geom["coordinates"] for c in part]


def test_straight_default_two_vertices():
    res = build([row(104.0, 30.0, 104.1, 30.1, 5, o_id="A", d_id="B")])
    geom = res.edge_fc["features"][0]["geometry"]
    assert geom["type"] == "LineString"
    assert len(geom["coordinates"]) == 2, "legacy straight default"
    props = res.edge_fc["features"][0]["properties"]
    assert props["geometry_kind"] == "straight"
    assert props["vertices"] == 2


def test_curve_deterministic_and_reverse_separated():
    rows = [row(104.0, 30.0, 104.1, 30.1, 8, o_id="A", d_id="B"),
            row(104.1, 30.1, 104.0, 30.0, 3, o_id="B", d_id="A")]
    r1 = build(rows, geometry="curve")
    r2 = build(list(reversed(rows)), geometry="curve")
    geoms1 = {f["id"]: f["geometry"] for f in r1.edge_fc["features"]}
    geoms2 = {f["id"]: f["geometry"] for f in r2.edge_fc["features"]}
    assert geoms1 == geoms2, "curve geometry must be deterministic"
    a, b = geoms1.values()
    assert _segments(a) > 2, "curve is sampled, not a straight chord"
    assert a != b, "reversed pairs must not overlap"
    # Reverse geometry is the mirror: reversing B→A's coordinates yields A→B's.
    fa = {f["id"]: f for f in r1.edge_fc["features"]}
    fwd = next(f for f in fa.values()
               if f["properties"]["origin_id"] == "A")
    bwd = next(f for f in fa.values()
               if f["properties"]["origin_id"] == "B")
    fwd_pts = [tuple(c) for c in fwd["geometry"]["coordinates"]]
    bwd_pts = [tuple(c) for c in bwd["geometry"]["coordinates"]]
    assert fwd_pts[0] == pytest.approx(bwd_pts[-1], abs=1e-6)
    assert fwd_pts[-1] == pytest.approx(bwd_pts[0], abs=1e-6)
    props = fwd["properties"]
    assert props["geometry_kind"] == "curve"
    assert props["curve_version"]
    assert props["vertices"] == len(fwd_pts)


def test_antimeridian_split_multilinestring():
    res = build([row(179.5, 10.0, -179.5, 10.0, 5, o_id="E", d_id="W")])
    geom = res.edge_fc["features"][0]["geometry"]
    assert geom["type"] == "MultiLineString", "must split, not cross the globe"
    assert len(geom["coordinates"]) == 2
    pts = _all_points(geom)
    assert all(-180.0 <= p[0] <= 180.0 for p in pts)
    # No individual segment may jump more than half the globe.
    for part in geom["coordinates"]:
        for (x1, _), (x2, _) in zip(part, part[1:]):
            assert abs(x2 - x1) < 180.0


def test_antimeridian_curve_also_split():
    res = build([row(179.5, 10.0, -179.5, 20.0, 5, o_id="E", d_id="W")],
                geometry="curve")
    geom = res.edge_fc["features"][0]["geometry"]
    assert geom["type"] == "MultiLineString"
    assert _segments(geom) <= MAX_EDGE_SEGMENTS


def test_self_loop_small_loop():
    res = build([row(104.0, 30.0, 104.0, 30.0, 5, o_id="A", d_id="A")])
    geom = res.edge_fc["features"][0]["geometry"]
    pts = _all_points(geom)
    assert len(pts) > 2, "self-loop gets a dedicated loop"
    props = res.edge_fc["features"][0]["properties"]
    assert props["geometry_kind"] == "loop"
    # All loop points stay near the node (≈1.5 km ⇒ < 0.05°).
    assert all(abs(p[0] - 104.0) < 0.05 and abs(p[1] - 30.0) < 0.05
               for p in pts)


def test_zero_length_distinct_nodes_disclosed():
    rows = [row(104.0, 30.0, 104.0, 30.0, 5, o_id="A", d_id="B")]
    res = build(rows)
    codes = [w["code"] for w in res.product["warnings"]]
    assert "zero_length_edge" in codes
    json.dumps(res.edge_fc, allow_nan=False)


def test_segment_budget_curve():
    res = build([row(-170.0, -60.0, 170.0, 60.0, 5, o_id="A", d_id="B")],
                geometry="curve")
    geom = res.edge_fc["features"][0]["geometry"]
    assert _segments(geom) <= MAX_EDGE_SEGMENTS


def test_straight_bidirectional_separation():
    rows = [row(104.0, 30.0, 104.1, 30.1, 8, o_id="A", d_id="B"),
            row(104.1, 30.1, 104.0, 30.0, 3, o_id="B", d_id="A")]
    res = build(rows)  # straight, both directions rendered
    feats = res.edge_fc["features"]
    assert len(feats) == 2
    ga, gb = (f["geometry"] for f in feats)
    assert ga["coordinates"] != gb["coordinates"], \
        "bidirectional straight edges must not overlap"
    for f in feats:
        assert _segments(f["geometry"]) <= 2
        assert f["properties"]["geometry_kind"] == "straight-offset"


def test_undirected_total_marks_no_direction():
    res = build([row(104.0, 30.0, 104.1, 30.1, 8, o_id="A", d_id="B"),
                 row(104.1, 30.1, 104.0, 30.0, 3, o_id="B", d_id="A")],
                direction_mode="undirected-total")
    props = res.edge_fc["features"][0]["properties"]
    assert props["directed"] is False


def test_net_geometry_follows_flow_direction():
    res = build([row(104.0, 30.0, 104.1, 30.1, 8, o_id="A", d_id="B"),
                 row(104.1, 30.1, 104.0, 30.0, 3, o_id="B", d_id="A")],
                direction_mode="net")
    geom = res.edge_fc["features"][0]["geometry"]
    pts = geom["coordinates"]
    assert pts[0][0] == pytest.approx(104.0, abs=1e-6), "geometry starts at A"
    assert pts[-1][0] == pytest.approx(104.1, abs=1e-6), "geometry ends at B"
