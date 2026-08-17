"""Issue #539: spatial quality audit topology dimension is O(P²) unbounded.

Fix: bounded pairwise work with TRUTHFUL truncation reporting.
  - per-feature candidate budget (pairs examined per feature),
  - a global topology-issue budget,
  - a bounded dangling-endpoint candidate scan (conservative: an endpoint whose
    verdict is unknown after the budget is left UNFLAGGED and counted).
The report explicitly says so (report.truncated / truncated_count /
truncation_details) — nothing is silently skipped. On small datasets where the
budgets never bind, results are byte-identical to the uncapped pairwise audit
(equivalence tests below).
"""
from app.services.spatial_quality_service import (
    SpatialQualityEngine,
    SpatialQualityReport,
)


def _ring_geojson(k: int, n_pts: int = 24) -> dict:
    """k concentric filled polygons with nested bboxes — every pair survives
    the STRtree bbox prune and genuinely overlaps (issue's worst case: each
    pair emits a TOPOLOGY_OVERLAP because the inner ring is contained in the
    outer one)."""
    import math

    features = []
    for i in range(1, k + 1):
        r = 1.0 + 0.02 * i
        ring = []
        for a in range(n_pts):
            ang = 2.0 * math.pi * a / n_pts
            ring.append([round(r * math.cos(ang), 6), round(r * math.sin(ang), 6)])
        ring.append(ring[0])
        features.append({
            "type": "Feature",
            "properties": {"id": i, "ring": i},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    return {"type": "FeatureCollection", "features": features}


def _square_ring_geojson(k: int, gap: float, start_r: float = 10.0) -> dict:
    """k concentric SQUARE LineString rings: bboxes strictly nested (bbox of
    ring i inside bbox of ring i+1) but boundaries never touch (radial gap
    > thresholds) — MANY candidate pairs, ZERO pairwise issues. This is the
    case where the per-feature candidate budget binds (no issue cap reached)."""
    features = []
    for i in range(1, k + 1):
        r = start_r + gap * i
        corners = [[r, r], [-r, r], [-r, -r], [r, -r]]
        ring = [corners[j] for j in (0, 1, 2, 3, 0)]
        features.append({
            "type": "Feature",
            "properties": {"id": i},
            "geometry": {"type": "LineString", "coordinates": ring},
        })
    return {"type": "FeatureCollection", "features": features}


def _topology_issue_codes(report) -> set:
    return {i.code for i in report.issues}


# ─── naive reference: the pre-fix uncapped pairwise topology audit ───────────


def _naive_topology_issues(geojson, crs="EPSG:4326"):
    """Reference reimplementation of the pre-fix topology block: FULL pairwise
    scan with no budgets. Returns (issues, pairs_examined)."""
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    features = geojson.get("features", [])
    parsed = []
    for idx, feat in enumerate(features):
        g = shape(feat["geometry"])
        if g.is_empty:
            continue
        parsed.append((idx, g, feat.get("properties") or {}))
    valid_shapes = [g for _, g, _ in parsed]
    idx_map = [idx for idx, _, _ in parsed]
    props_map = [p for _, _, p in parsed]
    tree = STRtree(valid_shapes)

    issues = []
    pairs = 0
    from shapely.geometry import Polygon, MultiPolygon

    for i, (f_idx_i, geom_i, props_i) in enumerate(parsed):
        for cand_pos in tree.query(geom_i):
            j = int(cand_pos)
            if j <= i:
                continue
            pairs += 1
            f_idx_j, geom_j, props_j = idx_map[j], valid_shapes[j], props_map[j]
            if geom_i.equals(geom_j) and props_i == props_j:
                issues.append(("DUPLICATE_FEATURE", f_idx_j))
            if isinstance(geom_i, (Polygon, MultiPolygon)) and isinstance(geom_j, (Polygon, MultiPolygon)):
                if geom_i.overlaps(geom_j) or (geom_i.intersects(geom_j) and geom_i.intersection(geom_j).area > 1e-7):
                    issues.append(("TOPOLOGY_OVERLAP", f_idx_i))
            dist = geom_i.distance(geom_j)
            is_geo = crs.upper() in ["EPSG:4326", "WGS84", "CRS84", "EPSG:4490"]
            gap_threshold = 1e-5 if is_geo else 1.0
            dup_threshold = 1e-6 if is_geo else 0.1
            if 0 < dist < gap_threshold:
                issues.append(("TOPOLOGY_GAP", f_idx_i))
            if 0 < dist < dup_threshold:
                issues.append(("NEAR_DUPLICATE_VERTICES", f_idx_i))
    return issues, pairs


def test_audit_matches_naive_pairwise_on_small_input():
    """On data where the budgets never bind, the audit's topology issues must
    be identical to the uncapped naive reference (code + feature index)."""
    # mixed dataset: concentric rings (overlaps) + exact duplicate + touching
    # polygons (tiny gap) + far-away geometry
    rings = _ring_geojson(4)["features"]
    geo = {
        "type": "FeatureCollection",
        "features": rings + [
            {"type": "Feature", "properties": {"id": "dup", "v": 1},
             "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 5], [5, 5], [5, 0], [0, 0]]]}},
            {"type": "Feature", "properties": {"id": "dup2", "v": 1},
             "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 5], [5, 5], [5, 0], [0, 0]]]}},
            {"type": "Feature", "properties": {"id": "adj"},
             "geometry": {"type": "Polygon", "coordinates": [[[5.0, 0], [5.0000005, 0], [5.0000005, 5], [5.0, 5], [5.0, 0]]]}},
            {"type": "Feature", "properties": {"id": "far"},
             "geometry": {"type": "Polygon", "coordinates": [[[100, 100], [100, 101], [101, 101], [101, 100], [100, 100]]]}},
        ],
    }
    report = SpatialQualityEngine.audit_dataset(geo, crs="EPSG:4326")
    assert report.truncated is False
    assert report.truncated_count == 0

    ref, ref_pairs = _naive_topology_issues(geo, crs="EPSG:4326")
    got = [(i.code, i.feature_index) for i in report.issues
           if i.code in {"DUPLICATE_FEATURE", "TOPOLOGY_OVERLAP", "TOPOLOGY_GAP", "NEAR_DUPLICATE_VERTICES"}]
    assert sorted(got) == sorted(ref)
    assert ref_pairs >= 1


def test_truncation_when_issue_cap_binds():
    """400 concentric rings → every pair is a genuine overlap, but the audit
    must stop at the issue budget and SAY it truncated, while still reporting
    total_features and the non-topology dimensions correctly."""
    geo = _ring_geojson(400)
    report = SpatialQualityEngine.audit_dataset(geo, crs="EPSG:4326")
    assert report.truncated is True
    assert report.total_features == 400
    topo = [i for i in report.issues if i.dimension == "topology"]
    assert len(topo) == SpatialQualityEngine.MAX_TOPOLOGY_ISSUES, (
        f"topology issues {len(topo)} must stop exactly at the cap"
    )
    details = report.truncation_details
    assert details is not None
    assert "max_issues" in details.get("reasons", [])
    assert details["topology_issues_reported"] == SpatialQualityEngine.MAX_TOPOLOGY_ISSUES
    # one overlap issue per examined pair, so exactly the cap pairs ran
    assert details["topology_pairs_examined"] == SpatialQualityEngine.MAX_TOPOLOGY_ISSUES
    # (earlier features may also have spent their per-feature candidate budget,
    # so `candidates_skipped_by_budget` can be > 0 — the issue cap bound last;
    # whatever is skipped is COUNTED, never silent)
    assert details["topology_pairs_examined"] <= SpatialQualityEngine.MAX_TOPOLOGY_ISSUES
    # every dimension still runs — geometry issues were emitted for the rings
    assert report.overall_status in ("warning", "blocking")


def test_truncation_when_pair_budget_binds():
    """Concentric SQUARE LINE rings: bboxes nested (all-pairs candidates) but
    boundaries never touch → ZERO pairwise issues, so the per-feature candidate
    budget (not the issue cap) binds. The skipped pairs are counted and
    reported; the report never pretends the audit was exhaustive."""
    k = 600
    geo = _square_ring_geojson(k, gap=0.05)

    old = SpatialQualityEngine.MAX_CANDIDATES_PER_FEATURE
    SpatialQualityEngine.MAX_CANDIDATES_PER_FEATURE = 10
    try:
        report = SpatialQualityEngine.audit_dataset(geo, crs="EPSG:4326")
    finally:
        SpatialQualityEngine.MAX_CANDIDATES_PER_FEATURE = old

    assert report.truncated is True
    details = report.truncation_details
    assert details is not None
    assert "candidates_budget" in details.get("reasons", [])
    # each feature examines min(10, remaining candidates): 590×10 + 9+…+1
    expected_examined = 590 * 10 + sum(range(9, 0, -1))
    assert details["topology_pairs_examined"] == expected_examined
    # total j>i pairs over 600 features = 599·600/2; the rest were budget-skipped
    total_pairs = 599 * 600 // 2
    assert details["candidates_skipped_by_budget"] == total_pairs - expected_examined
    assert report.truncated_count == details["candidates_skipped_by_budget"] + details["dangling_endpoints_undetermined"]
    # zero pairwise issues found (correct — rings don't touch); the partial
    # scan is explicit, not masked
    assert len([i for i in report.issues if i.dimension == "topology"] ) == 0


def test_dangling_endpoint_budget_conservative():
    """An endpoint whose buffered query returns MORE candidates than the scan
    budget, none of them connecting: the audit must NOT guess — the endpoint
    stays unflagged and is reported as undetermined, while genuinely free ends
    are still flagged."""
    old = SpatialQualityEngine.MAX_DANGLING_CANDIDATES
    SpatialQualityEngine.MAX_DANGLING_CANDIDATES = 2
    try:
        # Cluster geometry (degrees): observer O at (1e-4, 1e-4); corner ends
        # at O + (±0.99e-6, ±0.99e-6) — each within O's buffered bbox
        # (±1e-6) but at real distance ~1.40e-6 > the 1e-6 connection
        # threshold, so none connects. O sees 4 candidates > budget 2.
        o = (0.0001, 0.0001)
        d = 0.99e-6
        corners = [
            (o[0] + d, o[1] + d), (o[0] - d, o[1] + d),
            (o[0] - d, o[1] - d), (o[0] + d, o[1] - d),
        ]
        lines = [[(0.0, 0.0), list(o)]]
        for i, c in enumerate(corners):
            lines.append([[5e-6 * (i + 1), 0.0], list(c)])
        geo = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"id": i},
                 "geometry": {"type": "LineString", "coordinates": coords}}
                for i, coords in enumerate(lines)
            ],
        }
        report = SpatialQualityEngine.audit_dataset(geo, crs="EPSG:4326")
    finally:
        SpatialQualityEngine.MAX_DANGLING_CANDIDATES = old

    dangling = [i for i in report.issues if i.code == "DANGLING_ENDPOINT"]
    # 5 isolated starts + 4 corner ends (each genuinely free) are flagged;
    # the observer's end is NOT (its verdict is unknown within budget).
    assert len(dangling) == 9, f"dangling={len(dangling)}"
    # feature 0 (the observer line) has exactly ONE dangling flag: its start
    o_flags = [(i.details["endpoint"]) for i in dangling if i.feature_index == 0]
    assert len(o_flags) == 1
    assert abs(o_flags[0][0] - 0.0) < 1e-9  # the START, not the observer end
    assert report.truncated is True
    details = report.truncation_details or {}
    assert details.get("dangling_endpoints_undetermined", 0) == 1
    assert "dangling_budget" in details.get("reasons", [])


def test_dangling_budget_never_punishes_real_free_ends():
    """Isolated free ends (zero candidates) are still flagged dangling — small
    budgets must not suppress genuine findings."""
    old = SpatialQualityEngine.MAX_DANGLING_CANDIDATES
    SpatialQualityEngine.MAX_DANGLING_CANDIDATES = 4
    try:
        geo = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"id": 1},
                 "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [10.0, 10.0]]}},
                {"type": "Feature", "properties": {"id": 2},
                 "geometry": {"type": "LineString", "coordinates": [[30.0, 30.0], [40.0, 40.0]]}},
            ],
        }
        report = SpatialQualityEngine.audit_dataset(geo, crs="EPSG:4326")
    finally:
        SpatialQualityEngine.MAX_DANGLING_CANDIDATES = old
    dangling = [i for i in report.issues if i.code == "DANGLING_ENDPOINT"]
    assert len(dangling) == 4  # all four ends are genuinely free
    assert report.truncated is False


def test_report_contract_additive():
    """New fields are additive; to_dict() carries them; the classic fields stay."""
    geo = _ring_geojson(2)
    report = SpatialQualityEngine.audit_dataset(geo, crs="EPSG:4326")
    d = report.to_dict()
    assert d["truncated"] is False
    assert d["truncated_count"] == 0
    assert d["truncation_details"] is None
    assert d["total_features"] == 2
    assert set(d["issue_summary"]) == {"info", "warning", "error", "blocking"}
    assert isinstance(report, SpatialQualityReport)


def test_empty_and_singleton_datasets_still_pass():
    """Boundary: empty feature list and a single feature — no topology pairs,
    no truncation, correct totals."""
    empty = SpatialQualityEngine.audit_dataset(
        {"type": "FeatureCollection", "features": []}, crs="EPSG:4326"
    )
    assert empty.total_features == 0
    assert empty.truncated is False

    single = SpatialQualityEngine.audit_dataset(_ring_geojson(1), crs="EPSG:4326")
    assert single.total_features == 1
    assert single.truncated is False