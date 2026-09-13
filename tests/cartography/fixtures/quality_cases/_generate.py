"""Generate the P0 minimal repro fixtures for the 15 diagnostic-code matrix.

Run from the worktree root:
    ./.venv/Scripts/python tests/cartography/fixtures/quality_cases/_generate.py

Each case dir: input.geojson + expected.json (the contract the regression test
asserts). Deterministic coordinates; no randomness.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent


def fc(features, crs=None):
    data = {"type": "FeatureCollection", "features": features}
    if crs:
        data["crs"] = {"type": "name", "properties": {"name": crs}}
    return data


def feat(coords, props=None, gtype="Polygon", geom=None):
    if geom is False:
        # sentinel: true null geometry (EMPTY_GEOMETRY blocking branch)
        return {"type": "Feature", "properties": props or {}, "geometry": None}
    return {
        "type": "Feature",
        "properties": props or {},
        "geometry": geom
        if geom is not None
        else {"type": gtype, "coordinates": coords},
    }


CASES = {}

# 1. MISSING_CRS — clean FC, no crs member (info; inference resolves 4326;
#    plan must NOT contain a no-op crs_transform).
CASES["MISSING_CRS"] = {
    "input": fc([
        feat([[[116.0, 39.8], [116.5, 39.8], [116.5, 40.2], [116.0, 40.2], [116.0, 39.8]]], {"v": 1}),
    ]),
    "expected": {
        "code": "MISSING_CRS", "audit_level": "info", "gate_verdict": "pass",
        "audit_crs": "UNKNOWN",
        "plan_ops_must_not_contain": ["crs_transform"],
        "repair": {"ops": [], "allow_destructive": False},
        "post": {"feature_count": 1},
    },
}

# 2. SUSPICIOUS_CRS — Web Mercator coords declared as 4326. The suspicious
#    warning co-fires with the blocking IMPOSSIBLE_LAT_LON (same evidence),
#    so the gate verdict is block; repair = crs_transform from inferred 3857.
CASES["SUSPICIOUS_CRS"] = {
    "input": fc([
        feat([[[14026255.8, 5621521.5], [14036718.2, 5621521.5],
               [14036718.2, 5631096.2], [14026255.8, 5631096.2],
               [14026255.8, 5621521.5]]], {"v": 1}),
    ], crs="EPSG:4326"),
    "expected": {
        "code": "SUSPICIOUS_CRS", "audit_level": "warning", "gate_verdict": "block",
        "plan_ops": ["crs_transform"],
        "repair": {"ops": ["crs_transform"], "allow_destructive": False},
        "post": {"feature_count": 1, "bbox_max_abs_xy": 180.0},
    },
}

# 3. IMPOSSIBLE_LAT_LON — same as 2 but blocking branch (lat>90 after mislabel).
CASES["IMPOSSIBLE_LAT_LON"] = {
    "input": fc([
        feat([[[12600000.0, 2630000.0], [12610000.0, 2630000.0],
               [12610000.0, 2640000.0], [12600000.0, 2640000.0],
               [12600000.0, 2630000.0]]], {"v": 1}),
    ], crs="EPSG:4326"),
    "expected": {
        "code": "IMPOSSIBLE_LAT_LON", "audit_level": "blocking", "gate_verdict": "block",
        "plan_ops": ["crs_transform"],
        "repair": {"ops": ["crs_transform"], "allow_destructive": False},
        "post": {"feature_count": 1, "bbox_max_abs_xy": 180.0},
    },
}

# 4. NULL_ISLAND — Point at exactly (0,0) (warning; flag default; destructive
#    drop only with allow_destructive). centroid must be within 1e-7 of origin.
CASES["NULL_ISLAND"] = {
    "input": fc([
        feat(None, {"v": 1}, gtype="Point", geom={"type": "Point", "coordinates": [0.0, 0.0]}),
        feat([[[116.0, 39.8], [116.5, 39.8], [116.5, 40.2], [116.0, 40.2], [116.0, 39.8]]], {"v": 2}),
    ]),
    "expected": {
        "code": "NULL_ISLAND", "audit_level": "warning", "gate_verdict": "warn",
        "audit_crs": "UNKNOWN",
        "plan_ops": ["remove_empty"],
        "repair": {"ops": ["remove_empty"], "allow_destructive": False},
        "post": {"feature_count": 2},
    },
}

# 5. EMPTY_GEOMETRY — true null geometry (blocking; remove_empty deletes it).
CASES["EMPTY_GEOMETRY"] = {
    "input": fc([
        feat(None, {"v": 1}, geom=False),  # False sentinel -> geometry: null
        feat([[[1.0, 1.0], [1.5, 1.0], [1.5, 1.5], [1.0, 1.5], [1.0, 1.0]]], {"v": 2}),
    ]),
    "expected": {
        "code": "EMPTY_GEOMETRY", "audit_level": "blocking", "gate_verdict": "block",
        "audit_crs": "UNKNOWN",
        "plan_ops": ["remove_empty"],
        "repair": {"ops": ["remove_empty"], "allow_destructive": False},
        "post": {"feature_count": 1},
    },
}

# 6. INVALID_GEOMETRY — hole outside shell (error).
CASES["INVALID_GEOMETRY"] = {
    "input": fc([
        {
            "type": "Feature",
            "properties": {"v": 1},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]],
                    [[20.0, 20.0], [21.0, 20.0], [21.0, 21.0], [20.0, 21.0], [20.0, 20.0]],
                ],
            },
        },
    ]),
    "expected": {
        "code": "INVALID_GEOMETRY", "audit_level": "error", "gate_verdict": "warn",
        "plan_ops": ["make_valid"],
        "repair": {"ops": ["make_valid"], "allow_destructive": False},
        "post": {"feature_count": 1, "code_gone_after_repair": True},
    },
}

# 7. RING_CHECK_FAILED — audit-unreachable via standard GeoJSON: shapely
#    auto-closes a 3-coordinate ring (len==4, closed) and a 2-coordinate ring
#    raises ValueError -> INVALID_GEOMETRY_SYNTAX (blocking). The case pins
#    this honest absence in CI and demonstrates the surrogate blocking path;
#    the RING_CHECK_FAILED -> make_valid planner mapping is covered by
#    tests/unit/test_repair_orchestrator.py (synthetic issue).
CASES["RING_CHECK_FAILED"] = {
    "input": fc([
        feat([[[0.0, 0.0], [2.0, 0.0]]], {"v": 1}),
    ]),
    "expected": {
        "code": "RING_CHECK_FAILED", "audit_unreachable": True,
        "surrogate_code": "INVALID_GEOMETRY_SYNTAX",
        "note": "shapely auto-closes >=3-point rings; 2-point rings raise -> SYNTAX",
        "audit_level": "blocking", "gate_verdict": "block",
        "audit_crs": "UNKNOWN",
        "plan_ops": [],
        "repair": {"ops": [], "allow_destructive": False},
        "post": {},
    },
}

# 8. SELF_INTERSECTION — bowtie (blocking; make_valid).
CASES["SELF_INTERSECTION"] = {
    "input": fc([
        feat([[[0.0, 0.0], [2.0, 2.0], [2.0, 0.0], [0.0, 2.0], [0.0, 0.0]]], {"v": 1}),
    ]),
    "expected": {
        "code": "SELF_INTERSECTION", "audit_level": "blocking", "gate_verdict": "block",
        "plan_ops": ["make_valid"],
        "repair": {"ops": ["make_valid"], "allow_destructive": False},
        "post": {"feature_count": 1, "code_gone_after_repair": True},
    },
}

# 9. DUPLICATE_GEOMETRY — identical geometry, DIFFERENT props (warning).
#    Pipeline dedup key = (wkb, sorted props): different-attr duplicates are
#    NOT removed (honest semantic narrowing from the recon matrix) — post
#    count stays 2 and the advisory/repair-plan discloses it.
CASES["DUPLICATE_GEOMETRY"] = {
    "input": fc([
        feat([[[1.0, 1.0], [2.0, 1.0], [2.0, 2.0], [1.0, 2.0], [1.0, 1.0]]], {"name": "a", "v": 1}),
        feat([[[1.0, 1.0], [2.0, 1.0], [2.0, 2.0], [1.0, 2.0], [1.0, 1.0]]], {"name": "b", "v": 2}),
    ]),
    "expected": {
        "code": "DUPLICATE_GEOMETRY", "audit_level": "warning", "gate_verdict": "warn",
        "plan_ops": ["deduplicate"],
        "repair": {"ops": ["deduplicate"], "allow_destructive": False},
        "post": {"feature_count": 2},
        "note": "dedup key includes props; different-attr duplicates honestly kept",
    },
}

# 10. DUPLICATE_FEATURE — identical geometry AND props (warning; dedup).
CASES["DUPLICATE_FEATURE"] = {
    "input": fc([
        feat([[[1.0, 1.0], [2.0, 1.0], [2.0, 2.0], [1.0, 2.0], [1.0, 1.0]]], {"name": "a"}),
        feat([[[1.0, 1.0], [2.0, 1.0], [2.0, 2.0], [1.0, 2.0], [1.0, 1.0]]], {"name": "a"}),
    ]),
    "expected": {
        "code": "DUPLICATE_FEATURE", "audit_level": "warning", "gate_verdict": "warn",
        "plan_ops": ["deduplicate"],
        "repair": {"ops": ["deduplicate"], "allow_destructive": False},
        "post": {"feature_count": 1},
    },
}

# 11. DUPLICATE_PRIMARY_KEY — same id, different attrs (error; dedup cannot
#     honestly fix different-attr rows → plan discloses skipped dedup).
CASES["DUPLICATE_PRIMARY_KEY"] = {
    "input": fc([
        feat([[[1.0, 1.0], [2.0, 1.0], [2.0, 2.0], [1.0, 2.0], [1.0, 1.0]]], {"id": "pk-1", "v": 1}),
        feat([[[3.0, 1.0], [4.0, 1.0], [4.0, 2.0], [3.0, 2.0], [3.0, 1.0]]], {"id": "pk-1", "v": 2}),
    ]),
    "expected": {
        "code": "DUPLICATE_PRIMARY_KEY", "audit_level": "error", "gate_verdict": "warn",
        "plan_ops": [],
        "plan_skipped_ops_contains": ["deduplicate"],
        "repair": {"ops": [], "allow_destructive": False},
        "post": {"feature_count": 2},
    },
}

# 12. HIGH_NULL_RATIO — field null in >50% (warning; attribute flag default).
CASES["HIGH_NULL_RATIO"] = {
    "input": fc([
        feat([[[1.0, 1.0], [2.0, 1.0], [2.0, 2.0], [1.0, 2.0], [1.0, 1.0]]], {"a": 1, "sparse": 5}),
        feat([[[1.1, 1.0], [2.1, 1.0], [2.1, 2.0], [1.1, 2.0], [1.1, 1.0]]], {"a": 2}),
        feat([[[1.2, 1.0], [2.2, 1.0], [2.2, 2.0], [1.2, 2.0], [1.2, 1.0]]], {"a": 3}),
    ]),
    "expected": {
        "code": "HIGH_NULL_RATIO", "audit_level": "warning", "gate_verdict": "warn",
        "plan_ops": ["attribute_drop_or_flag"],
        "repair": {"ops": ["attribute_drop_or_flag"], "allow_destructive": False},
        "post": {"feature_count": 3},
    },
}

# 13. TOPOLOGY_OVERLAP — two overlapping polygons (error → block; overlap
#     ratio 1/2 ≥ 1% → difference mode adjudicated on).
CASES["TOPOLOGY_OVERLAP"] = {
    "input": fc([
        feat([[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]], {"name": "first"}),
        feat([[[1.0, 0.0], [3.0, 0.0], [3.0, 2.0], [1.0, 2.0], [1.0, 0.0]]], {"name": "second"}),
    ]),
    "expected": {
        "code": "TOPOLOGY_OVERLAP", "audit_level": "error", "gate_verdict": "warn",
        "plan_ops": ["fix_topology_overlap"],
        "repair": {"ops": ["fix_topology_overlap"], "allow_destructive": False},
        "post": {"feature_count": 2, "code_gone_after_repair": True},
    },
}

# 14. TOPOLOGY_GAP — adjacent polygons with a small gap (5e-6 < 1e-5 degree
#     threshold; warning; fix_gaps flag default).
CASES["TOPOLOGY_GAP"] = {
    "input": fc([
        feat([[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]], {"name": "left"}),
        feat([[[1.000005, 0.0], [2.0, 0.0], [2.0, 1.0], [1.000005, 1.0], [1.000005, 0.0]]], {"name": "right"}),
    ]),
    "expected": {
        "code": "TOPOLOGY_GAP", "audit_level": "warning", "gate_verdict": "warn",
        "plan_ops": ["fix_gaps", "snap_within_tolerance"],
        "repair": {"ops": ["fix_gaps"], "allow_destructive": False},
        "post": {"feature_count": 2},
    },
}

# 15. NUMERIC_OUTLIER — 14 normal values + 1 extreme (n=15 ⇒ classic >3σ
#     unmasked: z=(14/√15)·(range factor) > 3) (warning; flag default).
_outlier_feats = []
for i in range(14):
    _outlier_feats.append(
        feat([[[float(i), 0.0], [float(i) + 0.5, 0.0], [float(i) + 0.5, 0.5], [float(i), 0.5], [float(i), 0.0]]],
             {"idx": i, "val": 10 + i})
    )
_outlier_feats.append(
    feat([[[20.0, 0.0], [20.5, 0.0], [20.5, 0.5], [20.0, 0.5], [20.0, 0.0]]], {"idx": 14, "val": 100000})
)
CASES["NUMERIC_OUTLIER"] = {
    "input": fc(_outlier_feats),
    "expected": {
        "code": "NUMERIC_OUTLIER", "audit_level": "warning", "gate_verdict": "warn",
        "plan_ops": ["drop_outliers_or_flag"],
        "repair": {"ops": ["drop_outliers_or_flag"], "allow_destructive": False},
        "post": {"feature_count": 15, "flagged": True},
    },
}


def main() -> None:
    for name, case in CASES.items():
        case_dir = HERE / name
        case_dir.mkdir(exist_ok=True)
        (case_dir / "input.geojson").write_text(
            json.dumps(case["input"], ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (case_dir / "expected.json").write_text(
            json.dumps(case["expected"], ensure_ascii=False, indent=1), encoding="utf-8"
        )
    print(f"wrote {len(CASES)} cases to {HERE}")


if __name__ == "__main__":
    main()
