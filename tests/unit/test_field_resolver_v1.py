"""Semantic Field Resolver 测试（ADR-0207 S4）。

验收矩阵对应：六类必测短语区分（人口/人口增长率/学校数量/每平方公里
学校数/土地利用类型/变化率）、A8 别名歧义、A9 temporal coverage mismatch。
"""
import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.lib.gis.field_resolver import (
    FieldResolution,
    parse_measure_phrase,
    resolve_measure_field,
)
from app.lib.gis.measurement import (
    CHECK_RATE_MISSING_TEMPORAL,
    derive_measurement_profile,
)
from app.lib.gis.semantic_profile import derive_semantic_profile


def _fixture_dataset():
    fields = {
        "population": "number",
        "population_growth_rate": "number",
        "school_count": "integer",
        "school_density_km2": "number",
        "land_use_type": "string",
        "area_km2": "number",
    }
    samples = {
        "population": [100000.0, 200000.0, 300000.0],
        "population_growth_rate": [0.02, 0.03, 0.01],
        "school_count": [10, 20, 30],
        "school_density_km2": [1.5, 3.2, 5.7],
        "land_use_type": ["residential", "industrial", "park"],
        "area_km2": [120.5, 300.7, 89.2],
    }
    profile = DatasetProfile(source="synthetic", fields=fields)
    sem = derive_semantic_profile(profile, value_samples=samples)
    mp = derive_measurement_profile(profile, sem, value_samples=samples)
    return profile, sem, mp


# ── 短语解析（双语）─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase,kind,subject,denominator,temporal",
    [
        ("人口", "absolute_quantity", "人口", "", False),
        ("人口增长率", "rate", "人口", "", True),
        ("学校数量", "count", "学校", "", False),
        ("每平方公里学校数", "density", "学校", "area", False),
        ("土地利用类型", "category", "土地利用", "", False),
        ("变化率", "rate", "", "", True),
        ("人均GDP", "density", "GDP", "population", False),
        ("schools per square kilometer", "density", "schools", "area", False),
        ("how many hospitals", "count", "hospitals", "", False),
    ],
)
def test_parse_measure_phrase_bilingual(phrase, kind, subject, denominator, temporal):
    q = parse_measure_phrase(phrase)
    assert q.kind == kind, phrase
    assert q.subject == subject, phrase
    assert q.denominator == denominator, phrase
    assert q.temporal_required == temporal, phrase


def test_parse_empty_and_unconstrained():
    assert parse_measure_phrase("").kind == ""
    assert parse_measure_phrase("画一张地图").kind == ""


# ── 解析与消歧 ──────────────────────────────────────────────────────────────


def test_six_required_phrases_resolve_distinctly():
    """方向验收：六类短语必须落到不同字段（kind/分母/时间区分）。"""
    profile, sem, mp = _fixture_dataset()
    expectations = {
        "人口": "population",
        "人口增长率": "population_growth_rate",
        "学校数量": "school_count",
        "每平方公里学校数": "school_density_km2",
        "土地利用类型": "land_use_type",
    }
    for phrase, expected_field in expectations.items():
        r = resolve_measure_field(phrase, profile, sem, measurement_profile=mp)
        assert r.best is not None, phrase
        assert r.best.field == expected_field, phrase
        assert not r.needs_clarification, (phrase, r.disclosures)


def test_change_rate_requires_temporal_disclosure():
    """A9：temporal coverage mismatch —— 率查询无时间字段 → 披露+稳定码。"""
    profile, sem, mp = _fixture_dataset()
    r = resolve_measure_field("人口增长率", profile, sem, measurement_profile=mp)
    assert CHECK_RATE_MISSING_TEMPORAL in r.check_codes
    assert r.disclosures  # 诚实披露给用户


def test_ambiguous_tie_requires_clarification():
    """A8：同分多候选 → needs_clarification + ambiguity（不替用户猜）。"""
    fields = {"学校数_甲校": "integer", "学校数_乙校": "integer"}
    samples = {"学校数_甲校": [1, 2, 3], "学校数_乙校": [2, 3, 4]}
    profile = DatasetProfile(source="synthetic", fields=fields)
    sem = derive_semantic_profile(profile, value_samples=samples)
    r = resolve_measure_field("学校数量", profile, sem)
    assert r.needs_clarification
    assert len(r.ambiguity) == 2
    assert r.best is None or r.selected


def test_alias_breaks_tie_and_scores_high():
    """项目别名命中 → 候选胜出且 evidence 留痕。"""
    profile, sem, mp = _fixture_dataset()
    r = resolve_measure_field(
        "学校数量", profile, sem,
        measurement_profile=mp,
        project_aliases={"学校": "school_count"},
    )
    assert r.best.field == "school_count"
    assert any(ev.startswith("alias:") for ev in r.best.evidence)


def test_no_match_is_clarification_not_guess():
    """无匹配 → needs_clarification + 披露（fail-closed）。"""
    profile, sem, mp = _fixture_dataset()
    r = resolve_measure_field(" lava flow volume ", profile, sem, measurement_profile=mp)
    assert r.needs_clarification
    assert not r.selected or r.best is None
    assert r.disclosures


def test_category_query_rejects_numeric_field_without_evidence():
    """类别查询对无角色数值字段降权（dtype 守卫）。"""
    fields = {"zone_code_num": "number", "land_use_type": "string"}
    samples = {
        "zone_code_num": [1, 2, 3],
        "land_use_type": ["a", "b", "a"],
    }
    profile = DatasetProfile(source="synthetic", fields=fields)
    sem = derive_semantic_profile(profile, value_samples=samples)
    r = resolve_measure_field("土地利用类型", profile, sem)
    assert r.best.field == "land_use_type"


def test_resolution_bounded_and_serializable():
    """解析结论有界可序列化（to_bounded_dict 稳定形状）。"""
    profile, sem, mp = _fixture_dataset()
    r = resolve_measure_field("学校数量", profile, sem, measurement_profile=mp)
    d = r.to_bounded_dict()
    assert set(d.keys()) == {
        "query", "selected", "ambiguity", "needs_clarification",
        "disclosures", "check_codes",
    }
    assert len(d["selected"]) <= 3
    assert isinstance(d["needs_clarification"], bool)


def test_measurement_profile_dict_accepted():
    """measurement_profile 可传 dict（契约反序列化路径）。"""
    profile, sem, mp = _fixture_dataset()
    r1 = resolve_measure_field(
        "学校数量", profile, sem, measurement_profile=mp.to_dict())
    r2 = resolve_measure_field(
        "学校数量", profile, sem, measurement_profile=mp)
    assert r1.best.field == r2.best.field
    # 坏版本 dict → fail-closed 退化为无画像（不虚构）。
    bad = mp.to_dict()
    bad["measurement_profile_version"] = 99
    r3 = resolve_measure_field("学校数量", profile, sem, measurement_profile=bad)
    assert isinstance(r3, FieldResolution)


def test_alias_requires_phrase_evidence():
    """review P2：无约束短语 + 别名在场 → 不得自信选定字段。"""
    profile, sem, mp = _fixture_dataset()
    r = resolve_measure_field(
        "帮我画个图", profile, sem,
        measurement_profile=mp,
        project_aliases={"学校": "school_count"},
    )
    # 无任何约束证据 → 澄清，而非被别名拉成猜测。
    assert r.needs_clarification
    assert not r.selected
