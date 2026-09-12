"""V9 P1 —— 质量规则引擎逐规则单测（小 fixture，无 heavy 依赖）。

任务书 §5 门禁：≥15 类质量规则各有单测。本文件对 16 类内置规则逐一给出
正例/反例，另覆盖 DSL 解析与词表校验的 fail-fast 行为。
"""
from __future__ import annotations

import pytest

from app.services.data_quality.rule_functions import (
    RULE_FUNCTIONS,
    RuleEvalContext,
)
from app.services.data_quality.rules import (
    DEFAULT_RULES,
    RULE_TYPES,
    RuleSpec,
    load_rule_preset,
    parse_rule_defs,
    ruleset_digest,
)


def _ctx(features=None, raster_stats=None, crs="", params=None, kind="vector"):
    return RuleEvalContext(
        kind=kind,
        features=features if features is not None else [],
        raster_stats=raster_stats or {},
        crs=crs,
        params=params or {},
    )


def _poly(coords):
    return {"type": "Polygon", "coordinates": [coords]}


def _feat(geometry, props=None):
    return {"type": "Feature", "geometry": geometry, "properties": props or {}}


# ── 词表/注册表完整性 ────────────────────────────────────────────────


def test_rule_type_vocabulary_matches_registry():
    assert set(RULE_TYPES) == set(RULE_FUNCTIONS.keys()), "词表与注册表必须一字不差"
    assert len(RULE_TYPES) >= 15, "任务书门禁：规则类型 ≥15"
    default_ids = {s.rule_type for s in DEFAULT_RULES}
    assert default_ids == set(RULE_TYPES), "默认规则集必须覆盖全部内置类型"


def test_ruleset_digest_stable_and_sensitive():
    d1 = ruleset_digest(list(DEFAULT_RULES))
    d2 = ruleset_digest(list(DEFAULT_RULES))
    assert d1 == d2
    tweaked = [RuleSpec("dq.x", "null_rate", params={"max_null_rate": 0.9})]
    assert ruleset_digest(tweaked) != d1


def test_dsl_rejects_unknown_type_and_duplicate_ids():
    with pytest.raises(ValueError):
        parse_rule_defs([{"rule_id": "a", "rule_type": "no_such_type"}])
    with pytest.raises(ValueError):
        parse_rule_defs([
            {"rule_id": "a", "rule_type": "null_rate"},
            {"rule_id": "a", "rule_type": "crs_validity"},
        ])
    with pytest.raises(ValueError):
        parse_rule_defs([{"rule_id": "a", "rule_type": "null_rate", "bogus": 1}])


def test_dsl_rejects_fix_op_outside_vocabulary():
    with pytest.raises(ValueError):
        parse_rule_defs([{
            "rule_id": "a", "rule_type": "null_rate",
            "params": {"fix_operations": ["drop_table"]},
        }])


def test_yaml_preset_loading():
    preset = {"rules": [
        {"rule_id": "pk", "rule_type": "primary_key_uniqueness",
         "severity": "error", "params": {"field": "fid"}},
    ]}
    specs = load_rule_preset(preset)
    assert len(specs) == 1 and specs[0].params["field"] == "fid"
    with pytest.raises(ValueError):
        load_rule_preset({"rules": [...], "extra": 1})


# ── 矢量规则 ─────────────────────────────────────────────────────────


def test_null_rate_warns_and_fails():
    feats = [_feat(None, {"a": 1}), _feat(None, {"a": None}),
             _feat(None, {"a": None})]
    out = RULE_FUNCTIONS["null_rate"](_ctx(feats, params={"max_null_rate": 0.5,
                                                          "fail_null_rate": 0.8}))
    assert out.status == "warn" and out.autofixable
    assert out.fix_operations == ("filter_null",)
    assert out.metric["worst_null_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_crs_validity_missing_vs_unknown():
    assert RULE_FUNCTIONS["crs_validity"](_ctx(crs="")).status == "fail"
    assert RULE_FUNCTIONS["crs_validity"](_ctx(crs="EPSG:0")).status == "warn"
    assert RULE_FUNCTIONS["crs_validity"](_ctx(crs="EPSG:4326")).status == "pass"


def test_geometry_validity_detects_unclosed_degenerate_selfintersect():
    unclosed = _feat(_poly([[0, 0], [0, 2], [2, 2], [2, 0]]))          # 未闭合
    degenerate = _feat(_poly([[0, 0], [0, 0], [0, 0], [0, 0]]))        # 零面积
    bowtie = _feat(_poly([[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]))    # 自交
    healthy = _feat(_poly([[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]))
    out = RULE_FUNCTIONS["geometry_validity"](
        _ctx([unclosed, degenerate, bowtie, healthy]))
    assert out.status == "fail"
    assert out.metric["unclosed"] == 1
    assert out.metric["degenerate"] == 1
    assert out.metric["self_intersected"] == 1
    assert out.affected_count == 3
    ok = RULE_FUNCTIONS["geometry_validity"](_ctx([healthy]))
    assert ok.status == "pass"


def test_geometry_validity_accepts_points_and_flags_empty():
    out = RULE_FUNCTIONS["geometry_validity"](
        _ctx([_feat({"type": "Point", "coordinates": [1, 1]}),
              _feat(None), _feat({"type": "Point", "coordinates": []})]))
    assert out.status == "fail" and out.affected_count == 2


def test_envelope_sanity_flags_out_of_range_and_nonfinite():
    far = _feat({"type": "Point", "coordinates": [500.0, 10.0]})
    nan = _feat({"type": "Point", "coordinates": [float("nan"), 0]})
    fine = _feat({"type": "Point", "coordinates": [116.0, 39.0]})
    out = RULE_FUNCTIONS["envelope_sanity"](_ctx([far, nan, fine], crs="EPSG:4326"))
    assert out.status == "warn"
    assert out.metric["out_of_range"] == 1 and out.metric["non_finite"] == 1
    # 投影 CRS 不做经纬度范围判定（诚实口径）
    out2 = RULE_FUNCTIONS["envelope_sanity"](
        _ctx([far], crs="EPSG:3857"))
    assert out2.status == "pass"


def test_envelope_sanity_flags_inverted_declared_bbox():
    f = _feat({"type": "Point", "coordinates": [10.0, 10.0]})
    f["bbox"] = [20.0, 20.0, 0.0, 0.0]
    out = RULE_FUNCTIONS["envelope_sanity"](_ctx([f], crs="EPSG:4326"))
    assert out.metric["inverted_bbox"] == 1


def test_attribute_domain_enum_and_range():
    feats = [
        _feat(None, {"cat": "road", "speed": 80}),
        _feat(None, {"cat": "rail", "speed": 999}),
        _feat(None, {"cat": "AIR", "speed": 50}),
    ]
    params = {"fields": {"cat": {"enum": ["road", "rail"]},
                         "speed": {"min": 0, "max": 130}}}
    out = RULE_FUNCTIONS["attribute_domain"](_ctx(feats, params=params))
    assert out.status == "warn"
    assert out.metric["violations"] == {"cat": 1, "speed": 1}
    # 未配置 → 诚实 skipped
    assert RULE_FUNCTIONS["attribute_domain"](_ctx(feats)).status == "skipped"


def test_primary_key_uniqueness():
    feats = [_feat(None, {"id": 1}), _feat(None, {"id": 1}), _feat(None, {"id": 2}),
             _feat(None, {})]
    out = RULE_FUNCTIONS["primary_key_uniqueness"](_ctx(feats, params={"field": "id"}))
    assert out.status == "fail"
    assert out.metric["duplicate_keys"] == 1 and out.metric["missing"] == 1
    assert out.affected_count == 2


def test_fk_referential():
    feats = [_feat(None, {"zone": "A"}), _feat(None, {"zone": "Z"})]
    out = RULE_FUNCTIONS["fk_referential"](
        _ctx(feats, params={"field": "zone", "valid_values": ["A", "B"]}))
    assert out.status == "warn" and out.affected_count == 1
    assert RULE_FUNCTIONS["fk_referential"](_ctx(feats)).status == "skipped"


def test_duplicate_features():
    g = {"type": "Point", "coordinates": [1.0, 2.0]}
    feats = [_feat(g, {"name": "x"}), _feat(g, {"name": "x"}),
             _feat(g, {"name": "y"})]
    out = RULE_FUNCTIONS["duplicate_features"](_ctx(feats))
    assert out.status == "warn"
    assert out.metric["duplicate_rows"] == 1 and out.metric["duplicate_groups"] == 1


def test_field_type_drift_mixed_and_expected():
    feats = [_feat(None, {"a": 1}), _feat(None, {"a": "text"}),
             _feat(None, {"b": 1}), _feat(None, {"b": 2})]
    out = RULE_FUNCTIONS["field_type_drift"](_ctx(feats))
    assert out.status == "warn"
    assert out.metric["drifted_fields"] == 1  # 仅字段 a（混型）
    out2 = RULE_FUNCTIONS["field_type_drift"](
        _ctx(feats, params={"expected_types": {"b": "string"}}))
    assert any(k == "b" for k in out2.metric["detail"])


def test_temporal_gaps():
    feats = [
        _feat(None, {"t": "2026-01-01T00:00:00"}),
        _feat(None, {"t": "2026-01-01T01:00:00"}),
        _feat(None, {"t": "2026-01-01T02:00:00"}),
        _feat(None, {"t": "2026-01-02T02:00:00"}),  # 24h 断裂
    ]
    out = RULE_FUNCTIONS["temporal_gaps"](
        _ctx(feats, params={"time_field": "t", "gap_factor": 4.0}))
    assert out.status == "warn" and out.metric["gaps"] == 1
    assert RULE_FUNCTIONS["temporal_gaps"](_ctx(feats)).status == "skipped"


def test_attribute_encoding_detects_mojibake():
    bad = "ä¸­å›½åœ°å›¾"  # UTF-8 bytes read as Latin-1（"中国地图" 的乱码形）
    feats = [_feat(None, {"name": bad}), _feat(None, {"name": "北京"})]
    out = RULE_FUNCTIONS["attribute_encoding"](
        _ctx(feats, params={"max_mojibake_ratio": 0.02}))
    assert out.status in ("warn", "fail")
    assert out.autofixable and out.fix_operations == ("normalize",)
    clean = RULE_FUNCTIONS["attribute_encoding"](
        _ctx([_feat(None, {"name": "北京"})], params={"max_mojibake_ratio": 0.02}))
    assert clean.status == "pass"


def test_topology_adjacency_detects_overlap_and_bounded_skip():
    a = _feat(_poly([[0, 0], [0, 2], [2, 2], [2, 0], [0, 0]]))
    b = _feat(_poly([[1, 1], [1, 3], [3, 3], [3, 1], [1, 1]]))
    out = RULE_FUNCTIONS["topology_adjacency"](_ctx([a, b]))
    assert out.status == "warn" and out.metric["overlapping_pairs"] == 1
    # 超帽诚实跳过（构造 > max_polygons 个面）
    many = [_feat(_poly([[i, 0], [i, 1], [i + 1, 1], [i + 1, 0], [i, 0]]))
            for i in range(210)]
    skipped = RULE_FUNCTIONS["topology_adjacency"](
        _ctx(many, params={"max_polygons": 200}))
    assert skipped.status == "skipped" and "bounded_skip" in skipped.message


def test_mixed_geometry_types():
    feats = [_feat({"type": "Point", "coordinates": [0, 0]}),
             _feat(_poly([[0, 0], [0, 1], [1, 1], [0, 0]]))]
    out = RULE_FUNCTIONS["mixed_geometry_types"](_ctx(feats))
    assert out.status == "warn" and "point" in out.metric["families"]


# ── 栅格规则 ─────────────────────────────────────────────────────────


def _raster(**overrides):
    base = {
        "crs": "EPSG:4326",
        "resolution_x": 0.01,
        "resolution_y": 0.01,
        "band_stats": [
            {"band": 1, "mean": 100.0, "valid_pixel_ratio": 0.95},
            {"band": 2, "mean": 102.0, "valid_pixel_ratio": 0.93},
            {"band": 3, "mean": 98.0, "valid_pixel_ratio": 0.97},
        ],
    }
    base.update(overrides)
    return base


def test_nodata_ratio():
    out = RULE_FUNCTIONS["nodata_ratio"](
        _ctx(raster_stats=_raster(), params={"max_nodata_ratio": 0.6}, kind="raster"))
    assert out.status == "pass"
    bad = _raster(band_stats=[{"band": 1, "valid_pixel_ratio": 0.2}])
    out2 = RULE_FUNCTIONS["nodata_ratio"](
        _ctx(raster_stats=bad, params={"max_nodata_ratio": 0.6}, kind="raster"))
    assert out2.status == "warn" and out2.fix_operations == ("filter_nodata",)
    assert RULE_FUNCTIONS["nodata_ratio"](_ctx(kind="raster")).status == "skipped"


def test_resolution_drift():
    ok = RULE_FUNCTIONS["resolution_drift"](
        _ctx(raster_stats=_raster(), kind="raster"))
    assert ok.status == "pass"
    aniso = RULE_FUNCTIONS["resolution_drift"](
        _ctx(raster_stats=_raster(resolution_y=0.5), kind="raster"))
    assert aniso.status == "warn"
    assert RULE_FUNCTIONS["resolution_drift"](_ctx(kind="raster")).status == "skipped"


def test_raster_stats_outlier():
    stats = _raster(band_stats=[
        {"band": 1, "mean": 10.0}, {"band": 2, "mean": 10.5},
        {"band": 3, "mean": 9.8}, {"band": 4, "mean": 500.0},
    ])
    out = RULE_FUNCTIONS["raster_stats_outlier"](
        _ctx(raster_stats=stats, params={"z_threshold": 3.0}, kind="raster"))
    assert out.status == "warn" and len(out.metric["outliers"]) == 1
    uniform = RULE_FUNCTIONS["raster_stats_outlier"](
        _ctx(raster_stats=_raster(), kind="raster"))
    assert uniform.status == "pass"
