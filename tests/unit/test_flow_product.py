"""G21 OD flow product — pure domain core tests (public API only).

Known-answer driven: the canonical A→B=8 / B→A=3 scenario must produce
gross=11, net=+5 toward A→B across all three direction modes, with
conservation statistics (§G21 behavior contract).
"""
import json
import math
import time

import pytest

from app.services.flow_product import (
    MAX_INPUT_ROWS,
    MAX_OUTPUT_EDGES,
    FlowProductConfig,
    FlowProductError,
    build_flow_product,
)

NODES = {
    "A": (104.0, 30.0),
    "B": (104.1, 30.1),
    "C": (104.2, 30.2),
    "D": (104.3, 30.3),
}


def row(o, d, w, **extra):
    olng, olat = NODES[o]
    dlng, dlat = NODES[d]
    r = {
        "origin_id": o,
        "destination_id": d,
        "origin_lng": olng,
        "origin_lat": olat,
        "destination_lng": dlng,
        "destination_lat": dlat,
        "weight": w,
    }
    r.update(extra)
    return r


def build(rows, nodes=None, payload=None, **cfg_kw):
    config = FlowProductConfig(**cfg_kw)
    if payload is None:
        payload = {"type": "od_table", "rows": rows}
    return build_flow_product(
        payload,
        config,
        source_scope="test-scope",
        source_revision=7,
        node_payload=nodes,
    )


def edges_by_pair(res):
    out = {}
    for f in res.edge_fc["features"]:
        p = f["properties"]
        out[(p["origin_id"], p["destination_id"])] = p
    return out


# ── Known answers: 8/3 across the three direction modes ────────────────


def test_directed_preserves_both_directions():
    res = build([row("A", "B", 8), row("B", "A", 3)], direction_mode="directed")
    pairs = edges_by_pair(res)
    assert set(pairs) == {("A", "B"), ("B", "A")}
    assert pairs[("A", "B")]["weight"] == 8
    assert pairs[("B", "A")]["weight"] == 3
    assert all(p["directed"] for p in pairs.values())
    # Link-level gross bookkeeping is preserved on each direction.
    assert pairs[("A", "B")]["gross"] == 11
    assert pairs[("B", "A")]["gross"] == 11


def test_undirected_total_gross():
    res = build([row("A", "B", 8), row("B", "A", 3)],
                direction_mode="undirected-total")
    feats = res.edge_fc["features"]
    assert len(feats) == 1
    p = feats[0]["properties"]
    assert p["weight"] == 11
    assert p["gross"] == 11
    assert p["directed"] is False, "undirected-total must not mark direction"
    assert p["net"] is None


def test_net_known_answer_positive_five():
    res = build([row("A", "B", 8), row("B", "A", 3)], direction_mode="net")
    feats = res.edge_fc["features"]
    assert len(feats) == 1
    p = feats[0]["properties"]
    # Canonical node-id-ascending direction is A→B; net +5 toward A→B.
    assert (p["origin_id"], p["destination_id"]) == ("A", "B")
    assert p["net"] == 5
    assert p["weight"] == 5, "standard weight is the raw magnitude"
    assert p["gross"] == 11
    assert p["net_status"] == "observed"
    assert p["directed"] is True


def test_net_equal_reversed_produces_no_edge():
    res = build([row("A", "B", 5), row("B", "A", 5)], direction_mode="net")
    assert res.edge_fc["features"] == [], "zero net must not render"
    stats = res.product["stats"]
    assert stats["edges"]["aggregated"] == 0, "zero net projects to no edge"
    assert stats["gross"]["population"] == 10, "the link still exists statistically"
    assert stats["net"]["population_magnitude"] == 0


def test_net_missing_reverse_unknown_disclosed_not_zero():
    with pytest.raises(FlowProductError, match="净流.*未观测"):
        build([row("A", "B", 8)], direction_mode="net",
              missing_reverse="unknown")


def test_net_missing_reverse_zero_computes():
    res = build([row("A", "B", 8)], direction_mode="net",
                missing_reverse="zero")
    feats = res.edge_fc["features"]
    assert len(feats) == 1
    p = feats[0]["properties"]
    assert p["net"] == 8
    assert p["net_status"] == "assumed_zero"
    assert p["gross"] == 8


def test_self_loop_gross_once_nodes_in_out_once():
    res = build([row("A", "A", 5)], direction_mode="directed")
    pairs = edges_by_pair(res)
    assert pairs[("A", "A")]["weight"] == 5, "self-loop counted once"
    nodes = {f["properties"]["legacy_id"]: f["properties"]
             for f in res.node_fc["features"]}
    assert nodes["A"]["inbound"] == 5
    assert nodes["A"]["outbound"] == 5
    assert nodes["A"]["net"] == 0
    # Net mode: self-loop nets to zero → no render edge.
    res_net = build([row("A", "A", 5)], direction_mode="net")
    assert res_net.edge_fc["features"] == []


def test_node_totals_from_directed_vectors_not_pseudo_buses():
    res = build([row("A", "B", 8), row("B", "A", 3)])
    nodes = {f["properties"]["legacy_id"]: f["properties"]
             for f in res.node_fc["features"]}
    assert nodes["A"]["outbound"] == 8
    assert nodes["A"]["inbound"] == 3
    assert nodes["A"]["net"] == 5
    assert nodes["B"]["outbound"] == 3
    assert nodes["B"]["inbound"] == 8
    assert nodes["B"]["net"] == -5
    assert nodes["A"]["total"] == 11
    assert nodes["A"]["radius_value"] == pytest.approx(math.sqrt(11))
    assert nodes["A"]["total_scope"] == "filtered"


def test_stats_conservation_8_3():
    res = build([row("A", "B", 8), row("B", "A", 3)], direction_mode="net")
    stats = res.product["stats"]
    assert stats["rows"]["raw"] == 2
    assert stats["rows"]["valid"] == 2
    assert stats["rows"]["filtered"] == 2
    assert stats["rows"]["excluded"] == {}
    assert stats["weights"]["filtered"] == 11
    assert stats["gross"]["population"] == 11
    assert stats["gross"]["displayed"] == 11
    assert stats["gross"]["retained"] == 1.0, "fully retained → 100% gross"
    assert stats["net"]["displayed_magnitude"] == 5
    assert stats["net"]["magnitude_coverage"] == 1.0


def test_schema_and_product_envelope():
    res = build([row("A", "B", 8)])
    product = res.product
    assert product["schema_version"] == "od_flow_product.v1"
    assert product["family_id"].startswith("odf_")
    assert product["source_ref"] == "" or isinstance(product["source_ref"], str)
    assert product["source_revision"] == 7
    assert product["source_fingerprint"]
    assert product["analysis_fingerprint"] == product["fingerprint"]
    assert product["supported_modes"] == ["directed", "undirected-total"]
    assert product["mode_availability"]["net"]["available"] is False
    assert isinstance(product["warnings"], list)
    assert isinstance(product["degradations"], list)
    assert product["config"]["direction_mode"] == "directed"
    # JSON must be clean everywhere.
    json.dumps(product, allow_nan=False)
    json.dumps(res.edge_fc, allow_nan=False)
    json.dumps(res.node_fc, allow_nan=False)


# ── Identity: stable render IDs, legacy mapping, member fingerprints ─────


def _edge_ids(res):
    return sorted(f["id"] for f in res.edge_fc["features"])


def test_render_ids_stable_under_row_reorder():
    rows = [row("A", "B", 8), row("B", "A", 3), row("B", "C", 2),
            row("A", "C", 6)]
    r1 = build(rows)
    r2 = build(list(reversed(rows)))
    assert _edge_ids(r1) == _edge_ids(r2)
    assert (r1.product["analysis_fingerprint"]
            == r2.product["analysis_fingerprint"])


def test_analysis_fingerprint_stable_under_reverse_swap():
    r1 = build([row("A", "B", 8), row("B", "A", 3)], direction_mode="net")
    r2 = build([row("B", "A", 3), row("A", "B", 8)], direction_mode="net")
    assert (r1.product["analysis_fingerprint"]
            == r2.product["analysis_fingerprint"])
    assert _edge_ids(r1) == _edge_ids(r2)


def test_render_ids_not_arrow_strings_and_unique():
    res = build([row("A", "B", 8), row("B", "A", 3)])
    ids = _edge_ids(res)
    assert len(set(ids)) == len(ids)
    assert all("->" not in i for i in ids), "no bare a->b identity"
    node_ids = [f["id"] for f in res.node_fc["features"]]
    assert all(not i[0].isdigit() for i in node_ids), "no array-index identity"


def test_legacy_id_mapping_bidirectional():
    res = build([row("A", "B", 8)])
    mapping = res.product["id_mapping"]
    for f in res.node_fc["features"]:
        legacy = mapping["render_to_legacy"][f["id"]]
        assert mapping["legacy_to_render"][legacy] == f["id"]
        assert f["properties"]["legacy_id"] == legacy


def test_id_containing_arrow_no_collision():
    rows = [
        {"origin_id": "a->b", "destination_id": "C",
         "origin_lng": 104.4, "origin_lat": 30.4,
         "destination_lng": 104.2, "destination_lat": 30.2, "weight": 1},
        {"origin_id": "a", "destination_id": "b->C",
         "origin_lng": 104.0, "origin_lat": 30.0,
         "destination_lng": 104.5, "destination_lat": 30.5, "weight": 1},
    ]
    res = build(rows)
    ids = _edge_ids(res)
    assert len(set(ids)) == 2, "IDs containing '->' must not collide"


def test_derived_coord_identity_and_limits_disclosed():
    rows = [
        {"origin_lng": 104.0, "origin_lat": 30.0,
         "destination_lng": 104.1, "destination_lat": 30.0, "weight": 1},
        {"origin_lng": 104.0, "origin_lat": 30.1,  # same lng, different lat
         "destination_lng": 104.1, "destination_lat": 30.1, "weight": 1},
    ]
    res = build(rows)
    codes = [w["code"] for w in res.product["warnings"]]
    assert "derived_node_identity" in codes, "derived identity must be disclosed"
    node_coords = sorted(
        (f["properties"]["lng"], f["properties"]["lat"])
        for f in res.node_fc["features"]
    )
    assert len(node_coords) == 4
    assert len(set(node_coords)) == 4, "same lng / different lat must not collide"
    # Stable under reordering.
    res2 = build(list(reversed(rows)))
    assert _edge_ids(res) == _edge_ids(res2)


def test_explicit_id_coordinate_conflict_raises():
    rows = [
        row("A", "B", 1),
        {"origin_id": "A", "destination_id": "B",
         "origin_lng": 104.9, "origin_lat": 30.9,  # A at a different place
         "destination_lng": 104.1, "destination_lat": 30.1, "weight": 1},
    ]
    with pytest.raises(FlowProductError, match="冲突"):
        build(rows)


def test_member_mapping_uses_row_fingerprint_not_indices():
    rows = [row("A", "B", 4), row("A", "B", 4), row("A", "B", 2)]
    res = build(rows)
    assert len(res.edge_fc["features"]) == 1
    edge_id = res.edge_fc["features"][0]["id"]
    members = res.product["aggregation_members"][edge_id]
    assert sum(m["count"] for m in members) == 3
    assert len(members) == 2, "duplicate rows collapse by fingerprint"
    # Row order must not change the member mapping.
    res2 = build([rows[2], rows[0], rows[1]])
    assert (res.product["aggregation_members"][edge_id]
            == res2.product["aggregation_members"][edge_id])
    assert res.edge_fc["features"][0]["properties"]["weight"] == 10


def test_dimension_fields_split_aggregation():
    rows = [row("A", "B", 4, mode="car"), row("A", "B", 6, mode="rail")]
    res = build(rows, dimension_fields=["mode"])
    feats = res.edge_fc["features"]
    assert len(feats) == 2
    dims = sorted(f["properties"]["dimensions"]["mode"] for f in feats)
    assert dims == ["car", "rail"]
    assert sorted(f["properties"]["weight"] for f in feats) == [4, 6]


# ── Time window / category / threshold / top-N determinism ───────────────


def test_unparseable_time_bound_rejected_not_silently_ignored():
    rows = [row("A", "B", 1, ts="2024-03-01T00:00:00Z")]
    with pytest.raises(FlowProductError, match="time_start"):
        build(rows, time_field="ts", time_start="not-a-time")
    with pytest.raises(FlowProductError, match="time_end"):
        build(rows, time_field="ts", time_end="not-a-time")


def test_time_window_half_open_with_timezone():
    rows = [
        row("A", "B", 1, ts="2024-03-01T00:00:00+08:00"),   # 03-01 00:00 +08
        row("A", "B", 1, ts="2024-03-01T16:00:00+08:00"),   # boundary start (UTC 08:00)
        row("A", "B", 1, ts="2024-03-02T00:00:00+08:00"),   # boundary end   (UTC 16:00)
        row("A", "B", 1, ts="2024-03-01T23:00:00+08:00"),   # 15:00Z, inside
    ]
    res = build(
        rows,
        time_field="ts", time_start="2024-03-01T08:00:00Z",
        time_end="2024-03-01T16:00:00Z", timezone="Asia/Shanghai",
    )
    # [start, end): the end boundary row is excluded, before-window excluded.
    assert res.edge_fc["features"][0]["properties"]["weight"] == 2
    reasons = res.product["stats"]["rows"]["excluded"]
    assert reasons.get("time_out_of_window") == 2


def test_timezone_affects_naive_timestamps():
    rows = [
        row("A", "B", 1, ts="2024-03-01 09:00:00"),  # naive → Asia/Shanghai
        row("A", "B", 1, ts="2024-03-01 07:00:00"),  # 07:00 +08 = 23:00Z prev day
    ]
    res = build(
        rows,
        time_field="ts", time_start="2024-03-01T00:00:00Z",
        time_end="2024-03-02T00:00:00Z", timezone="Asia/Shanghai",
    )
    # 09:00+08 = 01:00Z ✓ in window; 07:00+08 = 2024-02-29T23:00Z ✗.
    assert res.edge_fc["features"][0]["properties"]["weight"] == 1


def test_invalid_time_excluded_with_reason():
    rows = [row("A", "B", 1, ts="not-a-time"), row("A", "B", 2, ts="2024-03-01T00:00:00Z")]
    res = build(rows, time_field="ts", time_start="2024-01-01T00:00:00Z",
                time_end="2025-01-01T00:00:00Z")
    assert res.product["stats"]["rows"]["excluded"].get("invalid_time") == 1
    assert res.edge_fc["features"][0]["properties"]["weight"] == 2


def test_category_filter():
    rows = [row("A", "B", 1, kind="car"), row("A", "B", 2, kind="rail"),
            row("A", "B", 4, kind="ship")]
    res = build(rows, category_field="kind", category_values=["car", "rail"])
    assert res.edge_fc["features"][0]["properties"]["weight"] == 3
    assert res.product["stats"]["rows"]["excluded"].get("category_excluded") == 1


def test_min_weight_applied_after_projection():
    rows = [row("A", "B", 1), row("B", "C", 8), row("C", "D", 3)]
    res = build(rows, min_weight=5, direction_mode="directed")
    weights = sorted(f["properties"]["weight"] for f in res.edge_fc["features"])
    assert weights == [8]
    assert res.product["stats"]["edges"]["thresholded"] == 1


def test_topn_deterministic_tie_break():
    rows = [row("A", "B", 5), row("A", "C", 5), row("B", "C", 5)]
    r1 = build(rows, top_n=2)
    r2 = build(list(reversed(rows)), top_n=2)
    assert _edge_ids(r1) == _edge_ids(r2), "tie break must be deterministic"
    assert len(r1.edge_fc["features"]) == 2


def test_topn_growth_preserves_domain_and_node_totals():
    rows = [row("A", "B", 8), row("B", "A", 3), row("A", "C", 2)]
    r20 = build(rows, top_n=2, weight_domain=(0.0, 100.0))
    r200 = build(rows, top_n=200, weight_domain=(0.0, 100.0))
    assert r20.product["weight_domain"] == r200.product["weight_domain"] == [0.0, 100.0]
    n20 = {f["id"]: f["properties"] for f in r20.node_fc["features"]}
    n200 = {f["id"]: f["properties"] for f in r200.node_fc["features"]}
    for nid, props in n20.items():
        assert props["total"] == n200[nid]["total"], "topN must not change node totals"
    # Fixed-domain encoding is scale-invariant under topN.
    enc20 = {f["id"]: f["properties"]["weight_encoded"]
             for f in r20.edge_fc["features"]}
    enc200 = {f["id"]: f["properties"]["weight_encoded"]
              for f in r200.edge_fc["features"]}
    assert all(enc200[k] == v for k, v in enc20.items()), \
        "shared edges must encode identically regardless of topN"


def test_weight_scaling_deformation_encoded_invariant():
    rows = [row("A", "B", 8), row("B", "A", 3), row("A", "C", 2)]
    r1 = build(rows)
    r10 = build([dict(r, weight=r["weight"] * 10) for r in rows])
    e1 = {f["id"]: f["properties"]["weight_encoded"]
          for f in r1.edge_fc["features"]}
    e10 = {f["id"]: f["properties"]["weight_encoded"]
           for f in r10.edge_fc["features"]}
    assert e1 == e10, "auto domain + transform ⇒ scale-invariant encoding"
    # Raw weights still scale.
    w1 = {f["id"]: f["properties"]["weight"] for f in r1.edge_fc["features"]}
    w10 = {f["id"]: f["properties"]["weight"] for f in r10.edge_fc["features"]}
    assert w10 == {k: v * 10 for k, v in w1.items()}


# ── NaN / Inf / negative / missing weights ───────────────────────────────


def test_bad_weights_excluded_with_reasons_and_clean_json():
    nan = float("nan")
    rows = [
        row("A", "B", 5),
        dict(row("A", "B", 1), weight=nan),
        dict(row("A", "B", 1), weight=float("inf")),
        dict(row("A", "B", 1), weight=-3),
        dict(row("A", "B", 1), weight=None),
        dict(row("A", "B", 1), weight="abc"),
    ]
    res = build(rows)
    reasons = res.product["stats"]["rows"]["excluded"]
    assert reasons.get("invalid_weight") == 3   # nan, inf, "abc"
    assert reasons.get("negative_weight") == 1
    assert reasons.get("missing_weight") == 1
    assert res.edge_fc["features"][0]["properties"]["weight"] == 5
    json.dumps(res.product, allow_nan=False)
    json.dumps(res.edge_fc, allow_nan=False)


# ── Encoding transforms + legend ─────────────────────────────────────────


def test_weight_encoded_transforms_with_fixed_domain():
    rows = [row("A", "B", 0), row("B", "C", 25), row("C", "D", 100)]
    lin = build(rows, weight_domain=(0.0, 100.0), weight_transform="linear")
    enc = sorted(f["properties"]["weight_encoded"] for f in lin.edge_fc["features"])
    assert enc == [0.0, 0.25, 1.0]
    sqrt = build(rows, weight_domain=(0.0, 100.0), weight_transform="sqrt")
    enc = sorted(f["properties"]["weight_encoded"] for f in sqrt.edge_fc["features"])
    assert enc == [0.0, 0.5, 1.0]
    logp = build(rows, weight_domain=(0.0, 100.0), weight_transform="log1p")
    enc = sorted(f["properties"]["weight_encoded"] for f in logp.edge_fc["features"])
    assert enc[0] == 0.0 and enc[-1] == 1.0
    assert enc[1] == pytest.approx(math.log1p(25) / math.log1p(100), abs=1e-3)
    legend = lin.product["legend"]
    assert legend["transform"] == "linear"
    assert legend["unit"] == ""
    assert legend["fixed_domain"] is True


def test_negative_fixed_domain_sqrt_log1p_does_not_crash():
    rows = [row("A", "B", 8), row("B", "A", 3)]
    for transform in ("sqrt", "log1p"):
        res = build(rows, weight_domain=(-10.0, -1.0), weight_transform=transform)
        assert all(
            f["properties"]["weight_encoded"] == 0.0
            for f in res.edge_fc["features"]
        )
        json.dumps(res.product, allow_nan=False)


# ── Rejections: CRS / cost semantics / unresolvable input / caps ─────────


def test_reject_non_wgs84_crs():
    with pytest.raises(FlowProductError, match="CRS"):
        build([row("A", "B", 1)], source_crs="EPSG:3857")
    with pytest.raises(FlowProductError, match="CRS"):
        build([row("A", "B", 1)],
              payload={"type": "od_table", "crs": "EPSG:3857",
                       "rows": [row("A", "B", 1)]})


def test_reject_cost_semantics_as_flow():
    with pytest.raises(FlowProductError, match="成本|流量"):
        build([row("A", "B", 1)], value_semantics="cost")
    # Auto-detection: cost-like weight field names are rejected.
    with pytest.raises(FlowProductError, match="成本|流量"):
        build([dict(row("A", "B", 1), distance_km=3)],
              weight_field="distance_km")
    with pytest.raises(FlowProductError, match="成本|流量"):
        build([dict(row("A", "B", 1), travel_time=3)],
              weight_field="travel_time")
    # Explicit flow semantics is accepted.
    res = build([dict(row("A", "B", 1), distance_km=3)],
                weight_field="distance_km", value_semantics="flow")
    assert res.edge_fc["features"]


def test_reject_no_coords_no_node_table():
    rows = [{"origin_id": "A", "destination_id": "B", "weight": 1}]
    with pytest.raises(FlowProductError, match="坐标|节点表|地理编码"):
        build(rows)


def test_input_cap_rejected():
    one = row("A", "B", 1)
    with pytest.raises(FlowProductError, match="上限"):
        build([one] * (MAX_INPUT_ROWS + 1))


def test_output_cap_clamped_with_warning():
    rows = [row("A", "B", 1), row("B", "C", 2)]
    res = build(rows, top_n=MAX_OUTPUT_EDGES + 1000)
    assert len(res.edge_fc["features"]) <= MAX_OUTPUT_EDGES
    assert "top_n_clamped" in [w["code"] for w in res.product["warnings"]]


# ── Node table resolution + FC input ─────────────────────────────────────


def test_node_table_point_fc_resolution():
    nodes = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "A",
             "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
             "properties": {"node_id": "A", "name": "Alpha"}},
            {"type": "Feature", "id": "B",
             "geometry": {"type": "Point", "coordinates": [104.1, 30.1]},
             "properties": {"node_id": "B", "name": "Beta"}},
        ],
    }
    rows = [{"origin_id": "A", "destination_id": "B", "weight": 4},
            {"origin_id": "Z", "destination_id": "B", "weight": 9}]
    res = build(rows, nodes=nodes)
    feats = res.edge_fc["features"]
    assert len(feats) == 1, "unmapped node row excluded, not guessed"
    assert res.product["stats"]["rows"]["excluded"].get("unmapped_node") == 1
    names = {f["properties"]["legacy_id"]: f["properties"]["name"]
             for f in res.node_fc["features"]}
    assert names == {"A": "Alpha", "B": "Beta"}


def test_node_table_point_fc_null_node_id_falls_back_to_feature_id():
    nodes = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "A",
             "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
             "properties": {"node_id": None, "name": "Alpha"}},
            {"type": "Feature", "id": "B",
             "geometry": {"type": "Point", "coordinates": [104.1, 30.1]},
             "properties": {"node_id": None, "name": "Beta"}},
        ],
    }
    rows = [{"origin_id": "A", "destination_id": "B", "weight": 4}]
    res = build(rows, nodes=nodes)
    feats = res.edge_fc["features"]
    assert len(feats) == 1
    assert feats[0]["properties"]["weight"] == 4
    names = {f["properties"]["legacy_id"]: f["properties"]["name"]
             for f in res.node_fc["features"]}
    assert names == {"A": "Alpha", "B": "Beta"}


def test_node_table_rows_resolution():
    nodes = {"rows": [
        {"node_id": "A", "node_lng": 104.0, "node_lat": 30.0},
        {"node_id": "B", "node_lng": 104.1, "node_lat": 30.1},
    ]}
    res = build([{"origin_id": "A", "destination_id": "B", "weight": 2}],
                nodes=nodes)
    assert res.edge_fc["features"][0]["properties"]["weight"] == 2


def test_feature_collection_od_input():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0, 30.0]},
             "properties": row("A", "B", 7)},
        ],
    }
    res = build(None, payload=fc)
    assert res.edge_fc["features"][0]["properties"]["weight"] == 7


def test_show_nodes_false_still_computes_totals_and_cache_payload():
    res = build([row("A", "B", 8)], show_nodes=False)
    assert len(res.node_fc["features"]) == 2
    assert res.product["stats"]["nodes"]["total"] == 2


# ── §G21: 50k input bounded (records actual elapsed, loose bound) ────────


def test_50k_input_bounded_and_recorded():
    rows = []
    n_nodes = 300
    for i in range(50_000):
        o, d = i % n_nodes, (i * 7 + 3) % n_nodes
        if o == d:
            d = (d + 1) % n_nodes
        rows.append({
            "origin_id": f"n{o:03d}", "destination_id": f"n{d:03d}",
            "origin_lng": 100.0 + (o % 100) * 0.01,
            "origin_lat": 20.0 + (o // 100) * 0.01,
            "destination_lng": 100.0 + (d % 100) * 0.01,
            "destination_lat": 20.0 + (d // 100) * 0.01,
            "weight": (i % 97) + 1,
        })
    started = time.monotonic()
    res = build(rows, top_n=1000, min_weight=2)
    elapsed = time.monotonic() - started
    print(f"\n[flow-product] 50k rows build took {elapsed:.2f}s")
    stats = res.product["stats"]
    assert stats["rows"]["raw"] == 50_000
    assert len(res.edge_fc["features"]) <= 1000
    assert len(res.node_fc["features"]) <= 5000
    # Loose sanity bound: an accidental O(N²) pair expansion would blow past
    # this by orders of magnitude; the recorded value is the real evidence.
    assert elapsed < 30, f"50k build took {elapsed:.1f}s — complexity regression?"
    json.dumps(res.product, allow_nan=False)
    json.dumps(res.edge_fc, allow_nan=False)



def test_family_id_is_scoped_and_legacy_custom_weight_is_standardized():
    rows = [row("A", "B", 8)]
    first = build_flow_product(
        {"type": "od_table", "rows": rows},
        FlowProductConfig(weight_field="weight"),
        source_scope="ref:source-a",
        source_revision=1,
    )
    second = build_flow_product(
        {"type": "od_table", "rows": rows},
        FlowProductConfig(weight_field="weight"),
        source_scope="ref:source-b",
        source_revision=1,
    )
    assert first.product["family_id"].startswith("odf_")
    assert second.product["family_id"].startswith("odf_")
    assert first.product["family_id"] != second.product["family_id"]
    assert first.product["legend"]["field"] == "weight"
    assert first.product["legend"]["source_field"] == "weight"


def test_directed_topn_gross_counts_only_selected_directions():
    res = build([row("A", "B", 8), row("B", "A", 3)],
                direction_mode="directed", top_n=1)
    stats = res.product["stats"]
    assert stats["gross"]["population"] == 11
    assert stats["gross"]["displayed"] == 8
    assert stats["gross"]["retained"] == pytest.approx(8 / 11, abs=1e-6)


def test_thresholded_node_totals_use_raw_directed_thresholds():
    res = build([row("A", "B", 8), row("B", "A", 3)],
                direction_mode="undirected-total", min_weight=5,
                node_totals_scope="thresholded")
    nodes = {f["properties"]["legacy_id"]: f["properties"] for f in res.node_fc["features"]}
    assert nodes["A"]["outbound"] == 8
    assert nodes["A"]["inbound"] == 0
    assert nodes["B"]["inbound"] == 8
    assert nodes["B"]["outbound"] == 0


def test_net_mode_is_unavailable_when_every_reverse_is_unobserved():
    res = build([row("A", "B", 8)], direction_mode="directed")
    assert "net" not in res.product["supported_modes"]
    assert res.product["mode_availability"]["net"]["available"] is False
    with pytest.raises(FlowProductError, match="净流"):
        build([row("A", "B", 8)], direction_mode="net")


def test_focus_aliases_use_stable_render_ids():
    res = build([row("A", "B", 8), row("B", "A", 3)])
    feature = res.edge_fc["features"][0]
    props = feature["properties"]
    assert props["origin_node_id"] == props["origin_render_id"]
    assert props["destination_node_id"] == props["destination_render_id"]
