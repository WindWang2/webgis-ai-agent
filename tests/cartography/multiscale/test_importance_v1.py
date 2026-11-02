"""语义重要性规则引擎测试（M2）—— 先行测试。

核心纪律（D4 fail-honest）：
- 证据 = field_schema 类型 + 值分布（真实基数）+ 词表全匹配；
- 任一证据缺失 / 值域不匹配词表 → importance=None（宁不取舍，不伪造）；
- 用户显式 override 赢（user-wins）；
- 多候选字段裁决确定性。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.multiscale.importance import (
    DEFAULT_KEEP_RANKS,
    derive_importance_model,
    kept_values_for_band,
)


def _field_schema(fields: dict) -> dict:
    return {k: {"type": v} for k, v in fields.items()}


ROAD_DIST = {
    "道路等级": {"国道": 120, "省道": 300, "县道": 800, "乡道": 1500, "高速公路": 40},
}
ROAD_SCHEMA = _field_schema({"道路等级": "string", "name": "string"})


class TestTrustedFieldDerivation:
    def test_road_class_chinese_vocabulary_full_match(self):
        d = derive_importance_model(ROAD_SCHEMA, ROAD_DIST, feature_count=2760)
        assert d.selected is not None
        m = d.selected
        assert m.field == "道路等级"
        assert m.matched_vocabulary == "road_class"
        # 最重要在前：高速公路 > 国道 > 省道 > 县道 > 乡道
        assert m.value_order == ["高速公路", "国道", "省道", "县道", "乡道"]
        assert m.evidence == "field_schema+aggregate+vocabulary"

    def test_osm_highway_tags_english_aliases(self):
        dist = {"highway": {"motorway": 5, "trunk": 8, "primary": 20,
                            "secondary": 40, "tertiary": 60, "residential": 200}}
        schema = _field_schema({"highway": "string"})
        d = derive_importance_model(schema, dist, feature_count=333)
        assert d.selected is not None
        assert d.selected.value_order[0] == "motorway"
        assert d.selected.matched_vocabulary == "road_class"

    def test_admin_level_numeric_ordering(self):
        dist = {"admin_level": {0: 1, 1: 21, 2: 180, 3: 2000, 4: 12000}}
        schema = _field_schema({"admin_level": "number"})
        d = derive_importance_model(schema, dist, feature_count=14202)
        assert d.selected is not None
        # 数值升序 = 行政级别从高到低（0 国家 … 4 村级）
        assert d.selected.value_order == [0, 1, 2, 3, 4]

    def test_generic_category_without_order_rejected(self):
        # 土地利用类别：无序词表 → 不伪造重要性
        dist = {"类别": {"耕地": 100, "林地": 80, "水域": 30, "建设用地": 60}}
        schema = _field_schema({"类别": "string"})
        d = derive_importance_model(schema, dist, feature_count=270)
        assert d.selected is None
        assert any("no_ordered_vocabulary" in r for r in d.reason_codes)

    def test_unknown_values_break_full_coverage(self):
        # 值域混入词表外值（“便道”）→ 全匹配失败 → None
        dist = {"道路等级": {"国道": 10, "省道": 20, "便道": 5}}
        schema = _field_schema({"道路等级": "string"})
        d = derive_importance_model(schema, dist, feature_count=35)
        assert d.selected is None
        assert any("values_outside_vocabulary" in r for r in d.reason_codes)

    def test_field_not_matching_synonyms_ignored(self):
        # 字段名与重要性无关（“备注”）→ 即使词表匹配也不采信
        dist = {"备注": {"国道": 10, "省道": 20}}
        schema = _field_schema({"备注": "string"})
        d = derive_importance_model(schema, dist, feature_count=30)
        assert d.selected is None

    def test_missing_distribution_evidence_rejected(self):
        # 字段在 schema 但无值分布（aggregate 未跑）→ None（不猜）
        schema = _field_schema({"道路等级": "string"})
        d = derive_importance_model(schema, {}, feature_count=100)
        assert d.selected is None
        assert any("no_distribution_evidence" in r for r in d.reason_codes)

    def test_single_distinct_value_rejected(self):
        # 只有一个取值：无取舍意义（排序无据）→ None
        dist = {"道路等级": {"国道": 50}}
        schema = _field_schema({"道路等级": "string"})
        d = derive_importance_model(schema, dist, feature_count=50)
        assert d.selected is None


class TestUserOverride:
    def test_user_order_wins_and_validated_against_data(self):
        d = derive_importance_model(
            ROAD_SCHEMA, ROAD_DIST, feature_count=2760,
            user_field="道路等级",
            user_value_order=["省道", "国道", "县道", "乡道", "高速公路"],
        )
        assert d.selected is not None
        assert d.selected.evidence == "user_override"
        assert d.selected.value_order[0] == "省道"  # 用户序赢过词表序

    def test_user_field_missing_from_data_rejected(self):
        d = derive_importance_model(
            ROAD_SCHEMA, ROAD_DIST, feature_count=2760,
            user_field="不存在的字段", user_value_order=["x", "y"],
        )
        assert d.selected is None
        assert any("user_field_absent" in r for r in d.reason_codes)


class TestDeterministicAdjudication:
    def test_multiple_candidates_pick_more_specific_synonym(self):
        # 「道路等级」比「等级」更specific → 恒选前者
        dist = {
            "道路等级": {"国道": 10, "省道": 20},
            "等级": {"一级": 5, "二级": 6},
        }
        schema = _field_schema({"道路等级": "string", "等级": "string"})
        # “一级/二级” 不在词表 → 只有道路等级可入选
        d = derive_importance_model(schema, dist, feature_count=41)
        assert d.selected.field == "道路等级"

    def test_derivation_is_deterministic(self):
        a = derive_importance_model(ROAD_SCHEMA, ROAD_DIST, feature_count=2760)
        b = derive_importance_model(ROAD_SCHEMA, ROAD_DIST, feature_count=2760)
        assert a.selected.model_dump() == b.selected.model_dump()
        assert [c.field for c in a.candidates] == [c.field for c in b.candidates]

    def test_candidates_trace_explains_rejections(self):
        d = derive_importance_model(
            _field_schema({"备注": "string", "level": "number"}),
            {"备注": {"国道": 1, "省道": 2}, "level": {1: 3, 2: 4}},
            feature_count=10,
        )
        fields = {c.field: c.reason_codes for c in d.candidates}
        assert "备注" in fields and "level" in fields
        # 每个被拒候选都有可解释理由码
        assert all(c.reason_codes or c.accepted for c in d.candidates)


class TestBandSelection:
    def test_default_keep_ranks_by_tier(self):
        d = derive_importance_model(ROAD_SCHEMA, ROAD_DIST, feature_count=2760)
        m = d.selected
        world = kept_values_for_band(m, "world", DEFAULT_KEEP_RANKS)
        province = kept_values_for_band(m, "province", DEFAULT_KEEP_RANKS)
        city = kept_values_for_band(m, "city", DEFAULT_KEEP_RANKS)
        street = kept_values_for_band(m, "street", DEFAULT_KEEP_RANKS)
        # 粗尺度保主干：world ⊆ province ⊆ city ⊆ street；street = 全部。
        # 测试词表只有 5 值 → city(K=6) 已取满，与 street 相等（非严格链）。
        assert set(world) < set(province) <= set(city) <= set(street)
        assert world == ["高速公路", "国道", "省道"]
        assert street == m.value_order

    def test_mandatory_values_always_appended(self):
        d = derive_importance_model(ROAD_SCHEMA, ROAD_DIST, feature_count=2760)
        kept = kept_values_for_band(
            d.selected, "world", DEFAULT_KEEP_RANKS,
            mandatory_values=["乡道"],
        )
        assert "乡道" in kept

    def test_unknown_tier_fail_closed(self):
        d = derive_importance_model(ROAD_SCHEMA, ROAD_DIST, feature_count=2760)
        with pytest.raises(ValueError):
            kept_values_for_band(d.selected, "galaxy", DEFAULT_KEEP_RANKS)
