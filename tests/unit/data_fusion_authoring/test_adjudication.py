"""WP4 裁决引擎测试：规则确定性、人工决定优先、来源保真、幂等组装。"""
from __future__ import annotations

from typing import Dict, List

import pytest
from shapely.geometry import Point

from app.services.data_fusion_authoring import contracts
from app.services.data_fusion_authoring.adjudication import (
    adjudicate_fields,
    adjudicate_geometry,
    entity_fingerprint,
    merged_feature,
    pair_stable_key,
    single_feature,
    unresolved_fields,
)
from app.services.data_fusion_authoring.matching import (
    EntityRecord,
    EvidenceItem,
    PairCandidate,
)
from tests.unit.data_fusion_authoring.test_matching import (
    _plan,
    _plan_with_id_space,
)


def _rec(idx: int, row: Dict, lon: float, lat: float,
         temporal: float | None = None) -> EntityRecord:
    return EntityRecord(
        index=idx, row=row, geom=Point(lon, lat),
        name_norm=str(row.get("name") or ""),
        name_missing=not row.get("name"),
        ext_id=row.get("code"), ext_id_present=bool(row.get("code")),
        addr_norm="", addr_present=False,
        category_canon=None, category_known=False,
        temporal=temporal,
    )


def _pair(score: float = 0.9, conflicts: Dict[str, str] | None = None,
          status: str = "matched") -> PairCandidate:
    return PairCandidate(
        idx_a=0, idx_b=0, status=status, score=score,
        evidence=[EvidenceItem("name", 0.9, {})],
        field_conflicts=conflicts or {}, geometry_evidence={},
    )


PLAN = _plan()


def _recs_a_conflict():
    return _rec(0, {"name": "希望小学", "addr": "旧街5号", "tel": "028-111"},
                104.065, 30.652)


def _recs_b_conflict(tel=None):
    return _rec(0, {"name": "希望小学", "addr": "新街9号", "tel": tel},
                104.06501, 30.65201)


def test_trust_priority_resolves_conflict_and_keeps_both_values():
    ra = _recs_a_conflict()
    rb = _recs_b_conflict()
    adjs = adjudicate_fields(ra, rb, {"address": "conflict",
                                      "name": "identical",
                                      "phone": "missing_b"}, PLAN)
    by = {a.output_field: a for a in adjs}
    # trust_priority：地址冲突 → 高优先源 a
    assert by["address"].outcome == "resolved_rule"
    assert by["address"].rule == "trust_priority"
    assert by["address"].side == "a"
    assert by["address"].value == "旧街5号"
    assert by["address"].values == {"a": "旧街5号", "b": "新街9号"}  # 保真
    # identical → 同值
    assert by["name"].outcome == "resolved_rule"
    assert by["name"].value == "希望小学"
    # 单侧缺失 → 取在场值
    assert by["phone"].outcome == "resolved_rule"
    assert by["phone"].side == "a"


def test_same_value_rule_conflict_stays_unresolved():
    ra = _recs_a_conflict()
    rb = _recs_b_conflict()
    plan = _plan(
        source_a=_src_a_same_value(),
        source_b=_src_b_same_value())
    adjs = adjudicate_fields(ra, rb, {"address": "conflict"}, plan)
    assert adjs[0].outcome == "unresolved"
    assert unresolved_fields(adjs) == ["address"]


def _src_a_same_value():
    from tests.unit.data_fusion_authoring.test_matching import _src_a
    from app.services.data_fusion_authoring.plan import FieldMapping
    return _src_a(field_mappings=[
        FieldMapping(source_field="addr", output_field="address",
                     rule="same_value")])


def _src_b_same_value():
    from tests.unit.data_fusion_authoring.test_matching import _src_b
    from app.services.data_fusion_authoring.plan import FieldMapping
    return _src_b(field_mappings=[
        FieldMapping(source_field="addr", output_field="address",
                     rule="same_value")])


def test_newest_temporal_picks_newer_and_unresolved_on_missing():
    from tests.unit.data_fusion_authoring.test_matching import _src_a, _src_b
    from app.services.data_fusion_authoring.plan import FieldMapping
    plan = _plan(
        source_a=_src_a(temporal_field="updated",
                        field_mappings=[
                            FieldMapping(source_field="addr",
                                         output_field="address",
                                         rule="newest_temporal")]),
        source_b=_src_b(temporal_field="checked",
                        field_mappings=[
                            FieldMapping(source_field="addr",
                                         output_field="address",
                                         rule="newest_temporal")]))
    ra = _rec(0, {"addr": "旧值"}, 104.0, 30.0, temporal=1700000000)
    rb = _rec(0, {"addr": "新值"}, 104.0, 30.0, temporal=1770000000)
    adjs = adjudicate_fields(ra, rb, {"address": "conflict"}, plan)
    assert adjs[0].outcome == "resolved_rule"
    assert adjs[0].side == "b"     # 时间新者
    assert adjs[0].value == "新值"
    # 时间缺失 → unresolved（绝不静默回退优先序）
    rb2 = _rec(0, {"addr": "新值"}, 104.0, 30.0, temporal=None)
    adjs2 = adjudicate_fields(ra, rb2, {"address": "conflict"}, plan)
    assert adjs2[0].outcome == "unresolved"


def test_keep_multi_dedups_stable_order():
    from tests.unit.data_fusion_authoring.test_matching import _src_a, _src_b
    from app.services.data_fusion_authoring.plan import FieldMapping
    plan = _plan(
        source_a=_src_a(field_mappings=[
            FieldMapping(source_field="cat", output_field="categories",
                         rule="keep_multi")]),
        source_b=_src_b(field_mappings=[
            FieldMapping(source_field="cat", output_field="categories",
                         rule="keep_multi")]))
    ra = _rec(0, {"cat": "小学"}, 104.0, 30.0)
    rb = _rec(0, {"cat": "小学"}, 104.0, 30.0)
    adjs = adjudicate_fields(ra, rb, {"categories": "conflict"}, plan)
    assert adjs[0].value == ["小学"]      # 同值去重


def test_manual_value_overrides_rule():
    ra = _recs_a_conflict()
    rb = _recs_b_conflict()
    adjs = adjudicate_fields(
        ra, rb, {"address": "conflict", "name": "identical",
                 "phone": "missing_b"}, PLAN,
        manual_values={"address": "人工确认地址"})
    by = {a.output_field: a for a in adjs}
    assert by["address"].outcome == "resolved_manual"
    assert by["address"].rule == "manual"
    assert by["address"].value == "人工确认地址"
    assert by["address"].values == {"a": "旧街5号", "b": "新街9号"}  # 仍保真


# ── 几何裁决 ─────────────────────────────────────────────────

def test_geometry_primary_source_default():
    ra = _recs_a_conflict()
    rb = _recs_b_conflict()
    geom, outcome, side = adjudicate_geometry(ra, rb, PLAN)
    assert outcome == "resolved_rule"
    assert side == "a"                    # a 优先
    assert geom.equals(ra.geom)


def test_geometry_newest_temporal_and_missing():
    from tests.unit.data_fusion_authoring.test_matching import _src_a, _src_b
    from app.services.data_fusion_authoring.plan import GeometryPolicy
    plan = _plan(
        source_a=_src_a(temporal_field="updated"),
        source_b=_src_b(temporal_field="checked"),
        geometry=GeometryPolicy(adoption="newest_temporal"))
    ra = _rec(0, {"name": "x"}, 104.0, 30.0, temporal=1700000000)
    rb = _rec(0, {"name": "x"}, 104.0, 30.0, temporal=1770000000)
    geom, outcome, side = adjudicate_geometry(ra, rb, plan)
    assert side == "b" and outcome == "resolved_rule"
    # 时间缺失 → unresolved（几何保持 a 侧原样 + 标记）
    rb2 = _rec(0, {"name": "x"}, 104.0, 30.0, temporal=None)
    geom2, outcome2, side2 = adjudicate_geometry(ra, rb2, plan)
    assert outcome2 == "unresolved"
    assert geom2.equals(ra.geom)   # 绝不编造，保持原样


def test_geometry_manual_side_and_invalid_guard():
    ra = _recs_a_conflict()
    rb = _recs_b_conflict()
    geom, outcome, side = adjudicate_geometry(ra, rb, PLAN, manual_side="b")
    assert outcome == "resolved_manual" and side == "b"
    # 非法侧 → unresolved
    geom2, outcome2, side2 = adjudicate_geometry(ra, rb, PLAN, manual_side="z")
    assert outcome2 == "unresolved"


# ── 稳定键与指纹 ─────────────────────────────────────────────

def test_entity_fingerprint_stable_and_content_sensitive():
    ra = _recs_a_conflict()
    ra2 = _recs_a_conflict()
    assert entity_fingerprint("a", ra, PLAN, "ref:x") == \
        entity_fingerprint("a", ra2, PLAN, "ref:x")
    rb = _recs_b_conflict()
    assert entity_fingerprint("a", ra, PLAN, "ref:x") != \
        entity_fingerprint("b", rb, PLAN, "ref:y")
    # 内容变化 → 指纹变化（名称）
    ra3 = _rec(0, {"name": "改名小学", "addr": "旧街5号", "tel": "028-111"},
               104.065, 30.652)
    assert entity_fingerprint("a", ra, PLAN, "ref:x") != \
        entity_fingerprint("a", ra3, PLAN, "ref:x")
    # 同内容不同 index → 同指纹（刷新重挂靠的关键性质）
    ra4 = _rec(7, {"name": "希望小学", "addr": "旧街5号", "tel": "028-111"},
               104.065, 30.652)
    assert entity_fingerprint("a", ra, PLAN, "ref:x") == \
        entity_fingerprint("a", ra4, PLAN, "ref:x")


def test_pair_stable_key_independent_of_row_order():
    ra = _recs_a_conflict()
    rb = _recs_b_conflict()
    k1 = pair_stable_key(ra, rb, PLAN, "ref:a", "ref:b")
    ra_i = _rec(9, ra.row, 104.065, 30.652)
    rb_i = _rec(4, rb.row, 104.06501, 30.65201)
    k2 = pair_stable_key(ra_i, rb_i, PLAN, "ref:a", "ref:b")
    assert k1 == k2


# ── 特征组装 ─────────────────────────────────────────────────

def test_merged_feature_provenance_completeness():
    ra = _recs_a_conflict()
    rb = _recs_b_conflict()
    pair = _pair(conflicts={"address": "conflict", "name": "identical",
                            "phone": "missing_b"})
    feat, adjs = merged_feature(ra, rb, pair, PLAN, "run1", "ref:a", "ref:b")
    assert feat["type"] == "Feature"
    prov = feat["properties"]["_fusion"]
    assert prov["origin"] == "merged"
    assert prov["run_id"] == "run1"
    assert len(prov["sources"]) == 2
    assert prov["match"]["score"] == pytest.approx(0.9)
    assert prov["match"]["method_version"] == contracts.METHOD_VERSION
    assert prov["fields"]["address"]["values"]["b"] == "新街9号"
    assert feat["properties"]["address"] == "旧街5号"   # trust_priority → a
    assert feat["geometry"]["coordinates"] == [104.065, 30.652]
    # 幂等：同输入同输出
    feat2, _ = merged_feature(ra, rb, pair, PLAN, "run1", "ref:a", "ref:b")
    assert feat == feat2


def test_single_feature_passthrough_and_withheld():
    ra = _recs_a_conflict()
    feat = single_feature("a", ra, PLAN, "run1", "ref:a")
    prov = feat["properties"]["_fusion"]
    assert prov["origin"] == "single_a"
    assert feat["properties"]["address"] == "旧街5号"
    feat_w = single_feature("a", ra, PLAN, "run1", "ref:a", withheld=True,
                            partner_key="fp:x")
    assert feat_w["properties"]["_fusion"]["origin"] == "withheld_review"
    assert feat_w["properties"]["_fusion"]["partner_key"] == "fp:x"


def test_merged_feature_geometry_null_guard():
    ra = _recs_a_conflict()
    rb = _rec(0, {"name": "希望小学", "addr": "新街9号", "tel": None},
              104.06501, 30.65201)
    rb.geom = None
    pair = _pair(conflicts={})
    feat, _ = merged_feature(ra, rb, pair, PLAN, "run1", "ref:a", "ref:b")
    # a 侧有几何 → primary 落 a；两侧都无 → unresolved + null 几何
    assert feat["geometry"]["coordinates"] == [104.065, 30.652]
    ra2 = _recs_a_conflict()
    ra2.geom = None
    feat2, _ = merged_feature(ra2, rb, pair, PLAN, "run1", "ref:a", "ref:b")
    assert feat2["geometry"] is None
    prov = feat2["properties"]["_fusion"]
    assert prov["geometry"]["outcome"] == "unresolved"
