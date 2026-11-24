"""WP3 匹配引擎测试：brute-force oracle、消歧、硬证据、CRS、稳定序。

oracle 纪律：小规模 fixture 同时跑引擎与独立暴力实现（全对全评分 +
同一状态机），断言两者候选集/状态/分值一致 —— 引擎的 blocking 不漏
关键候选由 oracle 证明，而不是由被测函数自证。

fixture 坐标为受控合成值（成都语义语境），不含真实用户坐标。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pytest
from shapely.geometry import Point, Polygon

from app.services.data_fusion_authoring import matching
from app.services.data_fusion_authoring.errors import DataFusionError
from app.services.data_fusion_authoring.matching import (
    SpatialContext,
    build_records,
    match_sources,
)
from app.services.data_fusion_authoring.plan import (
    FieldMapping,
    FusionPlan,
    FusionSourceSpec,
    MatchStrategy,
)

# 默认权重（无可比 id 空间的真实场景：external_id 显式置 0）
W_NO_ID = {
    "external_id": 0.0, "name": 0.40, "address": 0.15,
    "category": 0.15, "distance": 0.30,
}
# 共享标识空间场景
W_ID = {
    "external_id": 0.30, "name": 0.35, "address": 0.10,
    "category": 0.10, "distance": 0.15,
}


def _src_a(**over) -> FusionSourceSpec:
    kw = dict(
        kind="session_ref", session_id="s1", ref="ref:pois/a",
        label="教育局名录", priority=1, priority_reason="官方审校名录",
        name_field="name", external_id_field="code",
        address_field="addr", category_field="cat",
        category_map={"小学": "school_primary", "中学": "school_middle"},
        field_mappings=[
            FieldMapping(source_field="name", output_field="name",
                         rule="same_value"),
            FieldMapping(source_field="addr", output_field="address",
                         rule="trust_priority"),
            FieldMapping(source_field="tel", output_field="phone",
                         rule="trust_priority"),
        ],
    )
    kw.update(over)
    return FusionSourceSpec.model_validate(kw)


def _src_b(**over) -> FusionSourceSpec:
    kw = dict(
        kind="session_ref", session_id="s1", ref="ref:pois/b",
        label="开放地图", priority=2, priority_reason="众包无审校",
        name_field="name", external_id_field="osm_id",
        address_field="addr", category_field="cat",
        category_map={"primary_school": "school_primary",
                      "secondary_school": "school_middle"},
        temporal_field="checked",
        field_mappings=[
            FieldMapping(source_field="name", output_field="name",
                         rule="same_value"),
            FieldMapping(source_field="addr", output_field="address",
                         rule="trust_priority"),
            FieldMapping(source_field="tel", output_field="phone",
                         rule="trust_priority"),
        ],
    )
    kw.update(over)
    return FusionSourceSpec.model_validate(kw)


def _plan(**over) -> FusionPlan:
    kw = dict(
        entity_kind="point_poi",
        source_a=_src_a(), source_b=_src_b(),
        strategy=MatchStrategy(weights=dict(W_NO_ID),
                               name_normalizers=["trim", "casefold",
                                                 "collapse_spaces",
                                                 "remove_bracketed"]),
    )
    kw.update(over)
    return FusionPlan.model_validate(kw)


def _plan_with_id_space(**over) -> FusionPlan:
    return _plan(
        strategy=MatchStrategy(weights=dict(W_ID)),
        source_a=_src_a(id_space="edu_osm_shared"),
        source_b=_src_b(id_space="edu_osm_shared"),
        **over)


def _rows(rows: List[Dict]) -> Tuple[List[Dict], List]:
    geoms = [Point(r.pop("_lon"), r.pop("_lat")) for r in rows]
    return rows, geoms


# ── 基础旅程：成都小学融合 ────────────────────────────────────

def _chengdu_fixture():
    rows_a, geoms_a = _rows([
        {"_lon": 104.0650, "_lat": 30.6520, "code": "J001",
         "name": "成都市第七中学", "addr": "天府大道中段661号",
         "cat": "中学", "tel": "028-85221"},
        {"_lon": 104.0700, "_lat": 30.6550, "code": "J002",
         "name": "锦江区第一小学", "addr": "琉璃场街2号",
         "cat": "小学", "tel": "028-85331"},
        {"_lon": 104.0600, "_lat": 30.6400, "code": "J003",
         "name": "高新区实验小学", "addr": "天府三街199号",
         "cat": "小学", "tel": "028-85441"},
    ])
    rows_b, geoms_b = _rows([
        # 与 J001 同实体：真别名（成都七中）+ 坐标偏差 ~12m + 类别同义
        # → 名称证据不足 → 审阅裁决（别名不可能被诚实打满分）
        {"_lon": 104.0651, "_lat": 30.6521, "osm_id": "O9001",
         "name": "成都七中", "addr": "天府大道中段661号",
         "cat": "secondary_school", "checked": "2026-01-12"},
        # 与 J002 同实体：括号校区名（remove_bracketed 归一后全等）+
        # 坐标 11m + 地址一致 → 满分自动合并
        {"_lon": 104.0700, "_lat": 30.6551, "osm_id": "O9002",
         "name": "锦江区第一小学（琉璃场校区）", "addr": "琉璃场街2号",
         "cat": "primary_school", "checked": "2026-02-01"},
        # 独立实体：B 独有
        {"_lon": 104.0550, "_lat": 30.6480, "osm_id": "O9003",
         "name": "芳草街便民幼儿园", "addr": "芳草东街55号",
         "cat": "kindergarten", "checked": "2026-03-08"},
    ])
    return rows_a, geoms_a, rows_b, geoms_b


def test_chengdu_pois_alias_reviews_bracket_matches():
    rows_a, geoms_a, rows_b, geoms_b = _chengdu_fixture()
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    # 括号校区名 → 自动合并；真别名 → 审阅（诚实，不打满分）
    assert result.stats["pairs_matched"] == 1
    assert result.stats["pairs_needs_review"] == 1
    assert result.stats["unmatched_a"] == 1          # J003
    assert result.stats["unmatched_b"] == 1          # O9003
    by_key = {(p.idx_a, p.idx_b): p for p in result.pairs}
    assert by_key[(1, 1)].status == "matched"
    assert by_key[(0, 0)].status == "needs_review"
    # 稳定序
    assert [p.idx_a for p in result.pairs] == sorted(
        p.idx_a for p in result.pairs)
    # matched 对记录了距离证据（米）
    for p in result.pairs:
        d_e = next(e for e in p.evidence if e.kind == "distance")
        assert d_e.detail["distance_m"] < plan.strategy.max_match_distance_m
        assert p.geometry_evidence["distance_m"] == d_e.detail["distance_m"]


def test_id_space_required_for_id_evidence():
    """不同 id 体系 + external_id 正权重 → 计划期拒绝（fail-closed）。"""
    with pytest.raises(Exception, match="id_space"):
        _plan(strategy=MatchStrategy(weights=dict(W_ID)))


def test_id_space_mismatch_between_sources_rejected():
    with pytest.raises(Exception, match="id_space"):
        _plan(
            strategy=MatchStrategy(weights=dict(W_ID)),
            source_a=_src_a(id_space="edu_code"),
            source_b=_src_b(id_space="osm"))


def test_id_mismatch_is_hard_block():
    rows_a, geoms_a = _rows([
        {"_lon": 104.065, "_lat": 30.652, "code": "X1", "name": "同一学校",
         "addr": "地址一", "cat": "小学", "tel": "1"},
    ])
    rows_b, geoms_b = _rows([
        {"_lon": 104.0651, "_lat": 30.6521, "osm_id": "Y9", "name": "同一学校",
         "addr": "地址一", "cat": "primary_school", "checked": "2026-01-01"},
    ])
    plan = _plan_with_id_space()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    assert len(result.pairs) == 1
    p = result.pairs[0]
    assert p.hard_block == "id_mismatch"
    assert p.status == "needs_review"   # 绝不自动合并
    assert not p.id_pinned
    assert result.stats["hard_blocked"] == 1


def test_id_pin_auto_match_even_with_alias_name():
    """共享 id 空间精确匹配：身份钉定 → 越过名称别名不足自动合并。"""
    rows_a, geoms_a = _rows([
        {"_lon": 104.065, "_lat": 30.652, "code": "SAME1", "name": "完全异写",
         "addr": "", "cat": "小学", "tel": "1"},
    ])
    rows_b, geoms_b = _rows([
        {"_lon": 104.06501, "_lat": 30.65201, "osm_id": "SAME1",
         "name": "另一个名字", "addr": None, "cat": "primary_school",
         "checked": "2026-01-01"},
    ])
    plan = _plan_with_id_space()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    p = result.pairs[0]
    assert p.id_pinned
    id_e = next(e for e in p.evidence if e.kind == "external_id")
    assert id_e.value == 1.0
    assert p.status == "matched"       # id 钉定 + 距离在内 → 自动合并
    assert p.score < plan.strategy.auto_match_min  # 模糊分仍如实偏低


def test_id_pin_far_apart_goes_to_review():
    """同 id 但相距数公里：数据可疑（同标识跨城）→ 审阅。"""
    rows_a, geoms_a = _rows([
        {"_lon": 104.065, "_lat": 30.652, "code": "SAME1", "name": "城东学校",
         "addr": "甲街", "cat": "小学", "tel": "1"},
    ])
    rows_b, geoms_b = _rows([
        {"_lon": 104.500, "_lat": 30.900, "osm_id": "SAME1", "name": "城西学校",
         "addr": "乙街", "cat": "primary_school", "checked": "2026-01-01"},
    ])
    plan = _plan_with_id_space()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    p = result.pairs[0]
    assert p.id_pinned
    assert p.status == "needs_review"


def test_same_name_far_apart_goes_to_review():
    """同名不同实体（距离数公里）：不自动合并。

    默认权重下 name .40 + addr 部分相似 + cat .15 ≈ 0.625 落入审阅带
    —— 系统不假设「远距离同名一定不是同一实体」（可能是同城分校区/
    搬迁），交给审阅裁决；这比强行 unmatched 更诚实。
    """
    rows_a, geoms_a = _rows([
        {"_lon": 104.065, "_lat": 30.652, "code": "A1", "name": "实验小学",
         "addr": "甲街1号", "cat": "小学", "tel": "1"},
    ])
    rows_b, geoms_b = _rows([
        {"_lon": 104.500, "_lat": 30.900, "osm_id": "B1", "name": "实验小学",
         "addr": "乙街2号", "cat": "primary_school", "checked": "2026-01-01"},
    ])
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    assert result.stats["pairs_matched"] == 0
    assert result.stats["pairs_needs_review"] == 1
    d_e = next(e for e in result.pairs[0].evidence if e.kind == "distance")
    assert d_e.value == 0.0  # 距离证据如实为 0


def test_different_name_far_apart_unmatched():
    """名称/地址均无相似、距离超界 → 无可评证据优势 → unmatched。"""
    rows_a, geoms_a = _rows([
        {"_lon": 104.065, "_lat": 30.652, "code": "A1", "name": "实验小学",
         "addr": "甲街1号", "cat": "小学", "tel": "1"},
    ])
    rows_b, geoms_b = _rows([
        {"_lon": 104.500, "_lat": 30.900, "osm_id": "B1", "name": "城北货运站",
         "addr": "仓储大道88号", "cat": "primary_school", "checked": "2026-01-01"},
    ])
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    assert result.stats["pairs_matched"] == 0
    assert result.stats["unmatched_a"] == 1
    assert result.stats["unmatched_b"] == 1


def test_contested_pair_is_ambiguous():
    """一个 A 实体与两个近等距 B 候选 → 争抢 → ambiguous（不自动合并）。"""
    base = 104.0650, 30.6520
    rows_a, geoms_a = _rows([
        {"_lon": base[0], "_lat": base[1], "code": "A1",
         "name": "ambiguous学校", "addr": "共同地址", "cat": "小学",
         "tel": "1"},
    ])
    rows_b, geoms_b = _rows([
        {"_lon": base[0] + 0.0002, "_lat": base[1], "osm_id": "B1",
         "name": "ambiguous学校", "addr": "共同地址",
         "cat": "primary_school", "checked": "2026-01-01"},
        {"_lon": base[0] + 0.0002, "_lat": base[1] + 0.0001, "osm_id": "B2",
         "name": "ambiguous学校", "addr": "共同地址",
         "cat": "primary_school", "checked": "2026-01-02"},
    ])
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    assert result.stats["pairs_matched"] == 0
    assert result.stats["pairs_ambiguous"] == 2
    for p in result.pairs:
        assert p.status == "ambiguous"


def test_review_band_lands_needs_review():
    rows_a, geoms_a = _rows([
        {"_lon": 104.065, "_lat": 30.652, "code": "A1",
         "name": "成都天使儿童医院", "addr": "望江路44号", "cat": "医院",
         "tel": "1"},
    ])
    rows_b, geoms_b = _rows([
        # 名称部分相似、距离 ~52m、类别词表外（unknown 语义）
        {"_lon": 104.0653, "_lat": 30.6524, "osm_id": "", "name": "天使医院",
         "addr": "望江路44号", "cat": "clinic", "checked": "2026-01-01"},
    ])
    plan = _plan(
        source_b=_src_b(category_map={}),  # clinic 不在词表 → unknown
    )
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    p = result.pairs[0]
    cat_e = next(e for e in p.evidence if e.kind == "category")
    assert cat_e.detail["comparison"] == "unknown"
    assert p.status == "needs_review"
    assert plan.strategy.review_min <= p.score < plan.strategy.auto_match_min


def test_unknown_category_still_uses_declared_map():
    """unknown ≠ different：词表外类别诚实计 unknown（中性系数）。"""
    rows_a, geoms_a = _rows([
        {"_lon": 104.065, "_lat": 30.652, "code": "A1", "name": "希望小学",
         "addr": "同街1号", "cat": "特殊教育", "tel": "1"},
    ])
    rows_b, geoms_b = _rows([
        {"_lon": 104.06501, "_lat": 30.65201, "osm_id": "B1",
         "name": "希望小学", "addr": "同街1号", "cat": "special_education",
         "checked": "2026-01-01"},
    ])
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    p = result.pairs[0]
    cat_e = next(e for e in p.evidence if e.kind == "category")
    assert cat_e.detail["comparison"] == "unknown"
    # name .40 + addr .15 + dist .30 + unknown .15*0.5 = 0.925 → matched
    assert p.status == "matched"


def test_field_conflicts_detected_on_matched_pair():
    rows_a, geoms_a = _rows([
        {"_lon": 104.065, "_lat": 30.652, "code": "A1", "name": "希望小学",
         "addr": "旧街5号", "cat": "小学", "tel": "028-111"},
    ])
    rows_b, geoms_b = _rows([
        {"_lon": 104.06501, "_lat": 30.65201, "osm_id": "B1",
         "name": "希望小学", "addr": "新街9号", "cat": "primary_school",
         "checked": "2026-01-01", "tel": None},
    ])
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    p = result.pairs[0]
    assert p.status == "matched"       # 实体同一性成立
    assert p.field_conflicts["address"] == "conflict"     # 地址冲突保留
    assert p.field_conflicts["phone"] == "missing_b"      # 缺失≠零
    assert p.field_conflicts["name"] == "identical"


def test_numeric_tolerance_identical():
    rows_a, geoms_a = _rows([
        {"_lon": 104.065, "_lat": 30.652, "code": "A1", "name": "X学校",
         "addr": "s", "cat": "小学", "tel": "028-1111"},
    ])
    rows_b, geoms_b = _rows([
        {"_lon": 104.06501, "_lat": 30.65201, "osm_id": "B1", "name": "X学校",
         "addr": "s", "cat": "primary_school", "checked": "2026",
         "tel": "028-1111 "},
    ])
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    assert result.pairs[0].field_conflicts["phone"] == "identical"


# ── 空/边界 ──────────────────────────────────────────────────

def test_empty_sources_contract_empty_result():
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources([], [], [], [], plan, ctx)
    assert result.stats["candidates"] == 0
    assert result.pairs == []
    assert result.stats["unmatched_a"] == 0
    assert result.stats["unmatched_b"] == 0


def test_missing_geometry_entity_is_evidence_gap_not_crash():
    rows_a = [{"code": "A1", "name": "无几何点", "addr": "x", "cat": "小学",
               "tel": "1"}]
    rows_b, geoms_b = _rows([
        {"_lon": 104.065, "_lat": 30.652, "osm_id": "B1", "name": "无几何点",
         "addr": "x", "cat": "primary_school", "checked": "2026"},
    ])
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, [None], rows_b, geoms_b, plan, ctx)
    p = result.pairs[0]
    dist_e = next(e for e in p.evidence if e.kind == "distance")
    assert dist_e.value is None        # 证据缺失（重归一），不崩溃、不编造
    assert p.renormalized


# ── 稳定性与幂等 ─────────────────────────────────────────────

def test_deterministic_repeat_and_input_order_stability():
    rows_a, geoms_a, rows_b, geoms_b = _chengdu_fixture()
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    r1 = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    r2 = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    assert [p.to_dict() for p in r1.pairs] == [p.to_dict() for p in r2.pairs]
    assert r1.stats == r2.stats
    # B 侧重排不改变配对结果集合（状态/对象一致）
    order = [1, 2, 0]
    rb = [rows_b[i] for i in order]
    gb = [geoms_b[i] for i in order]
    r3 = match_sources(rows_a, geoms_a, rb, gb, plan, ctx)
    # 业务键对齐比较（r1 用 rows_b 序，r3 用重排后的 rb 序）
    key1 = sorted((rows_a[p.idx_a]["code"], rows_b[p.idx_b]["osm_id"])
                  for p in r1.pairs)
    key3 = sorted((rows_a[p.idx_a]["code"], rb[p.idx_b]["osm_id"])
                  for p in r3.pairs)
    assert key1 == key3
    status1 = sorted((rows_a[p.idx_a]["code"], p.status, p.score)
                     for p in r1.pairs)
    status3 = sorted((rows_a[p.idx_a]["code"], p.status, p.score)
                     for p in r3.pairs)
    assert status1 == status3


# ── 面实体（仅关联） ─────────────────────────────────────────

def _area_plan(**over) -> FusionPlan:
    kw = dict(
        entity_kind="area_link",
        source_a=_src_a(category_map={"行政区": "district"}),
        source_b=_src_b(category_map={"行政区": "district"}),
        strategy=MatchStrategy(weights={
            "external_id": 0.0, "name": 0.20, "address": 0.0,
            "category": 0.10, "distance": 0.70,
        }, overlap_min=0.3, overlap_full=0.6,
            name_normalizers=["trim", "casefold", "collapse_spaces",
                              "remove_bracketed"]),
    )
    kw.update(over)
    return FusionPlan.model_validate(kw)


def _square(lon: float, lat: float, half: float = 0.002) -> Polygon:
    return Polygon([
        (lon - half, lat - half), (lon + half, lat - half),
        (lon + half, lat + half), (lon - half, lat + half),
    ])


def test_area_overlap_matching_and_no_geometry_merge_assertion():
    rows_a = [
        {"code": "Q1", "name": "一街区", "addr": "", "cat": "行政区", "tel": ""},
        {"code": "Q2", "name": "二街区", "addr": "", "cat": "行政区", "tel": ""},
    ]
    geoms_a = [_square(104.06, 30.65), _square(104.08, 30.66)]
    rows_b = [
        {"osm_id": "R1", "name": "一街区（调整前）", "addr": "", "cat": "行政区",
         "checked": "2025"},
        {"osm_id": "R2", "name": "三街区", "addr": "", "cat": "行政区",
         "checked": "2025"},
    ]
    # R1 与 Q1 近全重叠（IoU ≥ overlap_full → 自动合并）；R2 独立
    geoms_b = [_square(104.0602, 30.6501), _square(104.10, 30.70)]
    plan = _area_plan()
    ctx = SpatialContext("geographic", 500.0)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    p = result.pairs[0]
    assert p.idx_a == 0 and p.idx_b == 0
    assert p.status == "matched"
    ov_e = next(e for e in p.evidence if e.kind == "distance")
    assert "overlap_ratio" in ov_e.detail
    assert p.geometry_evidence["kind"] == "overlap_ratio"
    # 证据诚实：非精确重叠（0.999999 门槛）→ exact_overlap False
    assert p.geometry_evidence["exact_overlap"] is False
    assert result.stats["unmatched_a"] == 1  # Q2 无重叠候选
    assert result.stats["unmatched_b"] == 1  # R2 独立


def test_area_weak_overlap_stays_review():
    rows_a = [{"code": "Q1", "name": "大地块", "addr": "", "cat": "行政区",
               "tel": ""}]
    geoms_a = [_square(104.06, 30.65, half=0.01)]
    rows_b = [{"osm_id": "R1", "name": "大地块（修测）", "addr": "",
               "cat": "行政区", "checked": "2026"}]
    geoms_b = [_square(104.064, 30.654, half=0.012)]  # 部分重叠（IoU≈0.5）
    plan = _area_plan()
    ctx = SpatialContext("geographic", 500.0)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    p = result.pairs[0]
    assert p.status == "needs_review"  # 重叠不足满分 → 修测边界需人审


# ── brute-force oracle ───────────────────────────────────────

def _oracle_bruteforce(
    rows_a, geoms_a, rows_b, geoms_b, plan, ctx
) -> List[Tuple[int, int, str, Optional[float]]]:
    """独立朴素实现（oracle）：全对全评分 + 简化状态机。

    oracle 候选集 = 所有 ``score ≥ review_min`` **或** id 钉定的对
    —— 这正是引擎 blocking 必须完整保留的关键候选集（不漏关键候选
    的性质由此证明）；引擎在此之上可以多披露硬阻断候选。
    """
    recs_a = build_records(rows_a, geoms_a, plan.source_a, plan)
    recs_b = build_records(rows_b, geoms_b, plan.source_b, plan)
    st = plan.strategy

    def rank_key(p):
        return (-int(p.id_pinned), -p.score, p.idx_a, p.idx_b)

    evaluable = []
    for ra in recs_a:
        for rb in recs_b:
            pc = matching._score_pair(ra, rb, ctx, plan)
            if pc.score is None:
                continue
            if pc.score >= st.review_min or pc.id_pinned:
                evaluable.append(pc)
    best_a: Dict[int, List] = {}
    best_b: Dict[int, List] = {}
    for p in evaluable:
        best_a.setdefault(p.idx_a, []).append(p)
        best_b.setdefault(p.idx_b, []).append(p)

    def top(lst):
        return sorted(lst, key=rank_key)[0] if lst else None

    mutual = set()
    for i, lst in best_a.items():
        t = top(lst)
        if t is None or t.hard_block is not None:
            continue
        bt = top(best_b.get(t.idx_b, []))
        if bt is not None and bt.idx_a == i and bt.hard_block is None:
            mutual.add((i, t.idx_b))

    out = []
    for p in evaluable:
        if (p.idx_a, p.idx_b) in mutual:
            sa = sorted(best_a[p.idx_a], key=rank_key)
            sb = sorted(best_b[p.idx_b], key=rank_key)
            ma = (sa[0].score - sa[1].score) if len(sa) > 1 else 1.0
            mb = (sb[0].score - sb[1].score) if len(sb) > 1 else 1.0
            within = True
            if plan.entity_kind == "point_poi":
                for e in p.evidence:
                    if e.kind == "distance" and e.value is not None:
                        d = e.detail.get("distance_m")
                        within = d is not None and \
                            d <= plan.strategy.max_match_distance_m
            margin_ok = ma >= st.ambiguous_margin and mb >= st.ambiguous_margin
            if (not p.hard_block and within and margin_ok
                    and (p.id_pinned or p.score >= st.auto_match_min)):
                status = "matched"
            elif p.hard_block:
                status = "needs_review"
            elif p.id_pinned and not within:
                status = "needs_review"
            elif not margin_ok:
                status = "ambiguous"
            else:
                status = "needs_review"
        else:
            ta = top(best_a.get(p.idx_a, []))
            tb = top(best_b.get(p.idx_b, []))
            contested = ((ta is not None and ta.idx_b == p.idx_b)
                         or (tb is not None and tb.idx_a == p.idx_a))
            status = ("ambiguous"
                      if contested and p.hard_block is None
                      else "needs_review")
        out.append((p.idx_a, p.idx_b, status, p.score))
    return sorted(out)


def _assert_oracle_coverage(rows_a, geoms_a, rows_b, geoms_b, plan, ctx):
    """引擎必须完整覆盖 oracle 关键候选集，且交集上状态一致。"""
    engine = sorted(
        (p.idx_a, p.idx_b, p.status, p.score)
        for p in match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx).pairs)
    oracle = _oracle_bruteforce(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    eng_map = {(a, b): (st_, sc) for a, b, st_, sc in engine}
    for a, b, status, score in oracle:
        assert (a, b) in eng_map, f"blocking 丢失关键候选 {(a, b)}"
        eng_status, eng_score = eng_map[(a, b)]
        assert eng_status == status, f"{(a, b)} 状态漂移: {eng_status} != {status}"
        assert eng_score == pytest.approx(score, abs=1e-6)


def test_oracle_agreement_on_mixed_fixture():
    rows_a, geoms_a, rows_b, geoms_b = _chengdu_fixture()
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    _assert_oracle_coverage(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)


def test_oracle_agreement_with_id_space_fixture():
    rows_a, geoms_a, rows_b, geoms_b = _chengdu_fixture()
    # 两源共用一个 id 值制造 id 钉定 + 其余保持别名/括号混合
    rows_a[0]["code"] = "PIN9"
    rows_b[0]["osm_id"] = "PIN9"
    plan = _plan_with_id_space()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    _assert_oracle_coverage(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)


# ── 投影 CRS ─────────────────────────────────────────────────

def test_projected_crs_planar_distance():
    ctx = SpatialContext("projected", 150.0)
    # UTM 48N 受控合成坐标
    a = Point(335000.0, 3391000.0)
    b = Point(335050.0, 3391000.0)
    assert ctx.distance_m(a, b) == pytest.approx(50.0, abs=0.01)
    assert ctx.base_cell(a) == (335000 // 150, 3391000 // 150)
    b2 = Point(335000.0, 3391150.0)  # 正好一格
    assert ctx.base_cell(b2)[1] == ctx.base_cell(a)[1] + 1


# ── 名称归一化与相似度原语（textsim 单元） ────────────────────

def test_normalizers_closed_pipeline():
    from app.services.data_fusion_authoring.textsim import normalize_text
    assert normalize_text("  成都市第七中学（高中部） ",
                          ["trim", "casefold", "collapse_spaces",
                           "remove_bracketed"]) == "成都市第七中学"
    assert normalize_text("First St. School", ["trim", "casefold",
                                               "strip_punctuation"]) == \
        "first st school"


def test_similarity_missing_semantics():
    from app.services.data_fusion_authoring.textsim import name_similarity
    assert name_similarity("", "")["value"] is None
    sim = name_similarity("成都七中", "成都七中")
    assert sim["value"] == 1.0
    sim2 = name_similarity("锦江区第一小学", "锦江区第一小学（琉璃场校区）")
    assert 0.0 < sim2["value"] < 1.0


def test_temporal_parsing():
    from app.services.data_fusion_authoring.textsim import parse_temporal
    assert parse_temporal("2026-01-12") is not None
    assert parse_temporal("2026-01-12T08:00:00Z") is not None
    assert parse_temporal(1770000000) is not None
    assert parse_temporal(1770000000000) is not None  # 毫秒
    assert parse_temporal("") is None
    assert parse_temporal("not-a-date") is None
    assert parse_temporal(None) is None


def test_blocking_budget_exceeded_typed():
    plan = _plan(budgets={"max_candidate_pairs": 5})
    rows_a, geoms_a = [], []
    for i in range(10):
        rows_a.append({"code": f"A{i}", "name": "同名学校", "addr": "x",
                       "cat": "小学", "tel": "1", "_lon": 104.06,
                       "_lat": 30.65})
    rows_b, geoms_b = [], []
    for j in range(10):
        rows_b.append({"osm_id": f"B{j}", "name": "同名学校", "addr": "x",
                       "cat": "primary_school", "checked": "2026",
                       "_lon": 104.06 + 0.0001 * j, "_lat": 30.65})
    geoms_a = [Point(r.pop("_lon"), r.pop("_lat")) for r in rows_a]
    geoms_b = [Point(r.pop("_lon"), r.pop("_lat")) for r in rows_b]
    with pytest.raises(DataFusionError, match="budget_exceeded|预算"):
        match_sources(rows_a, geoms_a, rows_b, geoms_b, plan,
                      SpatialContext("geographic", 150.0))
