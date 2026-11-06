"""字段角色候选提案引擎 V1 —— M3 单测（双语语料 + 负例 + 置信度口径）。

覆盖：中文/英文同义词、坐标角色值域佐证与反证（xy 对调子集）、admin_code
vs id 负例、denominator/weight/address、temporal/category、用户提示覆盖与
dtype 反证、needs_clarification 判定、确定性（同输入同输出）。
纯函数面：不触 DB / store / IO。
"""
from __future__ import annotations

import pytest

from app.lib.data.profile import FieldProfile
from app.services.data_preparation import (
    AUTO_ADOPT_CONFIDENCE,
    RoleCandidate,
    propose_field_roles,
    proposals_summary,
)
from app.services.data_preparation.vocabulary import ReasonCode, to_semantic_roles


def _fp(name: str, *, dtype: str = "number", mn=None, mx=None,
        unique=None, temporal=False, samples=None) -> FieldProfile:
    return FieldProfile(
        name=name, dtype=dtype, min=mn, max=mx,
        unique_count=unique, temporal_hint=temporal, samples=samples or [],
    )


def _top(fields, name) -> RoleCandidate:
    props = {p.field: p for p in propose_field_roles(fields)}
    assert props[name].top is not None, f"{name}: no candidate"
    return props[name].top


class TestBilingualLexicon:
    def test_zh_exact_lon_lat(self):
        fields = {
            "经度": _fp("经度", mn=104.0, mx=104.1),
            "纬度": _fp("纬度", mn=30.5, mx=30.7),
        }
        assert _top(fields, "经度").role == "lon"
        assert _top(fields, "纬度").role == "lat"
        # 名称 + 值域 + dtype 三证据 → 自动采纳级
        assert _top(fields, "经度").confidence >= AUTO_ADOPT_CONFIDENCE

    def test_en_lng_lat_normalization(self):
        fields = {
            "Lng": _fp("Lng", mn=104.0, mx=104.1),
            "Latitude": _fp("Latitude", mn=30.5, mx=30.7),
        }
        assert _top(fields, "Lng").role == "lon"
        assert _top(fields, "Latitude").role == "lat"

    def test_school_name_and_address(self):
        fields = {
            "学校名称": _fp("学校名称", dtype="string"),
            "地址": _fp("地址", dtype="string"),
        }
        assert _top(fields, "学校名称").role == "name"
        assert _top(fields, "地址").role == "address"

    def test_projected_xy(self):
        fields = {
            "东坐标": _fp("东坐标", mn=480000.0, mx=490000.0),
            "北坐标": _fp("北坐标", mn=3400000.0, mx=3450000.0),
        }
        assert _top(fields, "东坐标").role == "x"
        assert _top(fields, "北坐标").role == "y"
        assert ReasonCode.RANGE_PLANE_PROJECTED.value in _top(fields, "东坐标").reasons

    def test_admin_code_not_id(self):
        fields = {
            "行政区划代码": _fp("行政区划代码", dtype="string", unique=20),
            "学校编号": _fp("学校编号", dtype="string", unique=200),
        }
        assert _top(fields, "行政区划代码").role == "admin_code"
        assert _top(fields, "学校编号").role == "id"

    def test_admin_name_column(self):
        fields = {"所属区县": _fp("所属区县", dtype="string", unique=20)}
        assert _top(fields, "所属区县").role == "admin_code"

    def test_denominator_and_weight(self):
        fields = {
            "常住人口": _fp("常住人口", mn=1000, mx=900000),
            "权重": _fp("权重", dtype="number", mn=0.0, mx=1.0),
        }
        assert _top(fields, "常住人口").role == "denominator"
        assert _top(fields, "权重").role == "weight"

    def test_time_by_hint_and_samples(self):
        fields = {
            "建校年份": _fp("建校年份", dtype="string", temporal=True,
                          samples=["2001", "1998"]),
        }
        assert _top(fields, "建校年份").role == "time"
        assert ReasonCode.TIME_PARSED.value in _top(fields, "建校年份").reasons

    def test_category_low_cardinality(self):
        fields = {"办学类型": _fp("办学类型", dtype="string", unique=3)}
        assert _top(fields, "办学类型").role == "category"


class TestValueRangeEvidence:
    def test_lat_name_with_impossible_range_rejected(self):
        """xy 对调子集：名为纬度但值域 [73,135] → lat 被值域反证拒绝。"""
        fields = {"纬度": _fp("纬度", mn=73.0, mx=135.0)}
        props = {p.field: p for p in propose_field_roles(fields)}
        p = props["纬度"]
        lat_roles = [c.role for c in p.candidates if c.role == "lat"]
        assert lat_roles == []
        assert any(r == ("lat", ReasonCode.RANGE_CONFLICT.value) for r in p.rejected)
        assert p.needs_clarification

    def test_numeric_name_on_string_dtype_rejected(self):
        fields = {"经度": _fp("经度", dtype="string")}
        props = {p.field: p for p in propose_field_roles(fields)}
        assert all(c.role != "lon" for c in props["经度"].candidates)

    def test_range_only_candidate_without_name(self):
        """无语义列名的 CSV：纯值域也可立弱候选（但必须澄清）。"""
        fields = {
            "col_a": _fp("col_a", mn=104.0, mx=104.2),   # ⊆ lon 带且非完整 lon 带
            "col_b": _fp("col_b", mn=30.0, mx=30.9),
        }
        props = {p.field: p for p in propose_field_roles(fields)}
        # 两列都落在 [-90,90]∩[-180,180]：lon/lat 候选按词表纪律保守不自动指派
        for p in props.values():
            roles = [c.role for c in p.candidates]
            assert roles in ([], ["value"]) or p.needs_clarification

    def test_generic_numeric_falls_back_to_value(self):
        fields = {"score_xyz": _fp("score_xyz", mn=0.0, mx=100.0)}
        assert _top(fields, "score_xyz").role == "value"


class TestUserHints:
    def test_hint_overrides_to_full_confidence(self):
        fields = {"col1": _fp("col1", mn=1.0, mx=100.0)}
        props = propose_field_roles(fields, user_hints={"col1": "denominator"})
        assert props[0].top.role == "denominator"
        assert props[0].top.confidence == 1.0
        assert not props[0].needs_clarification

    def test_hint_with_dtype_conflict_rejected(self):
        fields = {"备注": _fp("备注", dtype="string")}
        props = propose_field_roles(fields, user_hints={"备注": "lat"})
        assert props[0].top is None
        assert ("lat", ReasonCode.RANGE_CONFLICT.value) in props[0].rejected


class TestClarificationAndBounds:
    def test_no_candidates_needs_clarification(self):
        fields = {"备注列": _fp("备注列", dtype="string")}
        props = propose_field_roles(fields)
        assert props[0].candidates == ()
        assert props[0].needs_clarification

    def test_determinism(self):
        fields = {
            "经度": _fp("经度", mn=104.0, mx=104.1),
            "学校名称": _fp("学校名称", dtype="string"),
        }
        a = [p.model_dump() for p in propose_field_roles(fields)]
        b = [p.model_dump() for p in propose_field_roles(fields)]
        assert a == b

    def test_field_count_bounded_at_64(self):
        fields = {f"c{i}": _fp(f"c{i}", dtype="string") for i in range(80)}
        assert len(propose_field_roles(fields)) == 64

    def test_summary_bounded_no_samples(self):
        fields = {
            "学校名称": _fp("学校名称", dtype="string"),
            "神秘列": _fp("神秘列", dtype="string"),
        }
        s = proposals_summary(propose_field_roles(fields))
        assert s["field_count"] == 2
        assert s["mapped"] == {"学校名称": "name"}
        assert "神秘列" in s["needs_clarification"]
        assert "samples" not in str(s)


class TestSemanticRoleProjection:
    def test_projection_honest_defaults(self):
        # address 无 SemanticFieldRole 对应 → 空投影（不虚构 label）
        assert to_semantic_roles("address") == ()
        assert to_semantic_roles("lon") == ("geographic_coordinate",)
        assert to_semantic_roles("admin_code") == ("admin_dimension",)
        assert to_semantic_roles("denominator") == ("normalization_denominator",)

    def test_candidate_role_must_be_in_vocab(self):
        with pytest.raises(ValueError):
            RoleCandidate(role="bogus", confidence=0.9)
