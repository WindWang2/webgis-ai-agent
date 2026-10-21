"""#1716 回归 —— CRS 有效性令牌判定 + 时态间隙 naive/aware 归一。

覆盖 `app/services/data_quality/rule_functions.py` 两处正确性缺陷：

1. ``crs_validity`` 旧实现用裸 ``"0"`` 做**子串**标记，任何含数字 0 的
   真实 CRS（EPSG:4490 CGCS2000 / EPSG:32650 UTM 50N / ``+zone=50`` /
   含 ``PARAMETER["False_northing",0]`` 的 WKT）都被误报「不可识别」；
2. ``temporal_gaps`` 旧实现把 naive 与 aware 时间戳混存后直接
   ``sort()``/相减 → ``TypeError: can't compare offset-naive and
   offset-aware datetimes``，被 engine 单规则隔离吞成 error 态并计入
   ``failed`` → 整场评估判 fail。
"""
from __future__ import annotations

import pytest

from app.services.data_quality.engine import evaluate_payload
from app.services.data_quality.rule_functions import (
    RULE_FUNCTIONS,
    RuleEvalContext,
)


def _ctx(features=None, crs="", params=None):
    return RuleEvalContext(
        kind="vector",
        features=features if features is not None else [],
        raster_stats={},
        crs=crs,
        params=params or {},
    )


def _feat(props):
    return {"type": "Feature", "geometry": None, "properties": props}


# ── 1) crs_validity：令牌/码级判定，绝不裸子串 ──────────────────────


@pytest.mark.parametrize("crs", [
    "EPSG:4490",                       # CGCS2000
    "EPSG:32650",                      # WGS 84 / UTM zone 50N
    "EPSG:4326",
    "EPSG:3857",
    "+proj=utm +zone=50 +datum=WGS84",
    "+proj=longlat +datum=WGS84 +no_defs",
    'PROJCS["WGS 84 / UTM zone 50N",PARAMETER["False_northing",0]]',
    'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]]]',
    "ESRI:102100",
    "WGS 84 / UTM zone 50N",
])
def test_crs_validity_accepts_real_crs_containing_digit_zero(crs):
    out = RULE_FUNCTIONS["crs_validity"](_ctx(crs=crs))
    assert out.status == "pass", f"{crs!r} 被误报为 {out.status}: {out.message}"


@pytest.mark.parametrize("crs", [
    "EPSG:0", "epsg:0", "EPSG::0", "+epsg=0", "EPSG: 0",
    "urn:ogc:def:crs:EPSG::0",
    "0",
    "unknown", "UNKNOWN", "none", "undefined", "N/A", "+proj=?",
])
def test_crs_validity_still_flags_unrecognized_crs(crs):
    out = RULE_FUNCTIONS["crs_validity"](_ctx(crs=crs))
    assert out.status == "warn", f"{crs!r} 应判不可识别，得到 {out.status}"
    assert out.autofixable and out.fix_operations == ("reproject",)


def test_crs_validity_missing_still_fails():
    assert RULE_FUNCTIONS["crs_validity"](_ctx(crs="")).status == "fail"


# ── 2) temporal_gaps：naive/aware 混存不得抛 TypeError ──────────────


def test_temporal_gaps_mixed_naive_and_aware_offsets():
    """同一字段混存 naive / Z / +08:00：必须判断裂，绝不崩。"""
    feats = [
        _feat({"t": "2026-01-01T00:00:00"}),          # naive
        _feat({"t": "2026-01-01T01:00:00Z"}),         # aware UTC
        _feat({"t": "2026-01-01T02:00:00"}),          # naive
        _feat({"t": "2026-01-01T10:00:00+00:00"}),    # aware UTC，8h 断裂
    ]
    out = RULE_FUNCTIONS["temporal_gaps"](
        _ctx(feats, params={"time_field": "t", "gap_factor": 4.0}))
    assert out.status == "warn", f"混存时区不应崩/降级：{out}"
    assert out.metric["gaps"] == 1
    assert out.metric["naive_normalized_to_utc"] == 2


def test_temporal_gaps_same_instant_in_different_offsets_is_no_gap():
    """跨时区表达的同一时刻不得被当成时间倒流/断裂。"""
    feats = [
        _feat({"t": "2026-01-01T08:00:00+08:00"}),    # == 00:00Z
        _feat({"t": "2026-01-01T00:00:00Z"}),         # == 00:00Z
        _feat({"t": "2026-01-01T01:00:00"}),          # naive → 01:00Z
        _feat({"t": "2026-01-01T02:00:00Z"}),
    ]
    out = RULE_FUNCTIONS["temporal_gaps"](
        _ctx(feats, params={"time_field": "t", "gap_factor": 4.0}))
    assert out.status == "pass", f"同时刻不应产生断裂：{out}"
    assert out.metric["max_gap_s"] == pytest.approx(3600.0)


def test_temporal_gaps_pure_naive_unchanged():
    """全 naive 的行为不变（既有语义不回归）。"""
    feats = [
        _feat({"t": "2026-01-01T00:00:00"}),
        _feat({"t": "2026-01-01T01:00:00"}),
        _feat({"t": "2026-01-01T02:00:00"}),
        _feat({"t": "2026-01-02T02:00:00"}),          # 24h 断裂
    ]
    out = RULE_FUNCTIONS["temporal_gaps"](
        _ctx(feats, params={"time_field": "t", "gap_factor": 4.0}))
    assert out.status == "warn" and out.metric["gaps"] == 1


def test_engine_mixed_timezone_does_not_fail_whole_evaluation():
    """端到端：混存时区的时间字段不再让整场质量评估判 fail。"""
    feats = [
        {"type": "Feature", "geometry": None,
         "properties": {"t": t}} for t in (
            "2026-01-01T00:00:00", "2026-01-01T01:00:00Z",
            "2026-01-01T02:00:00", "2026-01-01T03:00:00+00:00")
    ]
    payload = {"geojson": {"type": "FeatureCollection", "features": feats},
               "crs": "EPSG:4326"}
    rule_defs = [{"rule_id": "dq.t", "rule_type": "temporal_gaps",
                  "severity": "warn",
                  "params": {"time_field": "t", "gap_factor": 4.0}}]
    report = evaluate_payload(payload, rule_defs)
    row = report["results"][0]
    assert row["status"] != "error", f"规则不应抛错：{row}"
    assert report["overall_status"] != "fail"
    assert report["failed_count"] == 0
