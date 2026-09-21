"""Measurement Semantics 契约测试（ADR-0204 S1/S2/S3 量纲面）。

验收矩阵对应（docs/dev/gis-semantic-foundation-design.md §9）：
A1 度级值冒充 meters、A2 meters/degrees 维度冲突、A3 count vs density、
A4 rate vs absolute、A7 nodata/NaN 不污染、A11 大合成数据集有界推导、
A12 序列化/版本兼容。
"""
import math
import time

import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.lib.gis.measurement import (
    CANONICAL_UNITS,
    CHECK_DEGREE_LIKE_METRIC,
    CHECK_RATE_MISSING_TEMPORAL,
    CHECK_UNIT_DIMENSION_MISMATCH,
    DatasetMeasurementProfile,
    MeasurementKind,
    UnitDimension,
    derive_field_semantics,
    derive_measurement_profile,
    legend_unit_display,
    measurement_diverging_center,
    measurement_to_data_kind,
    name_kind_hint,
)
from app.lib.gis.semantic_profile import SemanticDatasetProfile, derive_semantic_profile

# A12：契约版本（from_dict 对未知版本 fail-closed）。
CONTRACT_VERSION = 1


def _sem(profile, samples=None, user_roles=None):
    return derive_semantic_profile(profile, value_samples=samples or {}, user_roles=user_roles)


# ── kind 推导 ───────────────────────────────────────────────────────────────


def _kind_of(field, samples, fields=None, crs="", sem_profile=None):
    p = DatasetProfile(source="synthetic", fields=fields or {field: "number"}, crs=crs)
    sem = sem_profile or _sem(p, {f: samples for f in p.fields})
    mp = derive_measurement_profile(p, sem, value_samples={field: samples})
    return mp.by_field(field)


def test_count_vs_density_distinction():
    """A3：count vs density —— 名称密度词 → DENSITY，纯计数 → COUNT。"""
    fs_count = _kind_of("school_count", [3, 5, 8], fields={"school_count": "integer"})
    assert fs_count.measurement_kind == MeasurementKind.COUNT.value
    assert fs_count.unit_dimension == UnitDimension.COUNT.value

    fs_density = _kind_of("school_density_km2", [1.5, 3.2, 7.1])
    assert fs_density.measurement_kind == MeasurementKind.DENSITY.value
    fs_density_bare = _kind_of("设施密度", [1.5, 3.2, 7.1])
    assert fs_density_bare.measurement_kind == MeasurementKind.DENSITY.value
    # 密度名暗含常规分母（人口密度→面积、每万人→人口，review 定案）；
    # 分母缺口由 field_resolver 查询期披露，不在字段级推导误报。


def test_rate_vs_absolute_distinction():
    """A4：rate vs absolute —— 增长率（需时间证据）≠ 人口绝对量。"""
    fs_pop = _kind_of("population", [100000.0, 250000.0, 400000.0])
    assert fs_pop.measurement_kind == MeasurementKind.ABSOLUTE_QUANTITY.value
    assert fs_pop.unit == "persons"
    assert fs_pop.unit_dimension == UnitDimension.POPULATION.value
    assert CHECK_RATE_MISSING_TEMPORAL not in [c["code"] for c in fs_pop.checks]

    # 正值率样本 → RATE；无时间字段证据 → RATE_MISSING_TEMPORAL
    # （fail-closed，不静默当作可跨期）。
    fs_rate = _kind_of("population_growth_rate", [0.02, 0.01, 0.03])
    assert fs_rate.measurement_kind == MeasurementKind.RATE.value
    assert CHECK_RATE_MISSING_TEMPORAL in [c["code"] for c in fs_rate.checks]


def test_rate_with_temporal_evidence_no_warning():
    """时间角色在场时率字段不产 RATE_MISSING_TEMPORAL。"""
    p = DatasetProfile(
        source="synthetic",
        fields={"growth_rate": "number", "year": "integer"},
        crs="",
    )
    samples = {"growth_rate": [0.02, 0.03, 0.01], "year": [2020, 2021, 2022]}
    sem = _sem(p, samples)
    mp = derive_measurement_profile(p, sem, value_samples=samples)
    fs = mp.by_field("growth_rate")
    assert fs is not None
    assert CHECK_RATE_MISSING_TEMPORAL not in [c["code"] for c in fs.checks]


def test_percentage_vs_fraction_scale():
    """percent [0,100] vs fraction [0,1] 由值结构细分，unit 随之。"""
    fs_pct = _kind_of("urbanization_share", [12.5, 45.0, 88.2])
    assert fs_pct.measurement_kind == MeasurementKind.PERCENTAGE.value
    assert fs_pct.unit == "percent"
    assert fs_pct.domain_hint == [0.0, 100.0]

    fs_frac = _kind_of("绿地率", [0.1, 0.35, 0.5])
    assert fs_frac.measurement_kind == MeasurementKind.RATIO.value
    assert fs_frac.unit == "fraction"
    assert fs_frac.domain_hint == [0.0, 1.0]


def test_signed_change_cross_zero_gets_center():
    """带符号变化（跨 0 + 变化命名）→ SIGNED_CHANGE，center 0。"""
    fs = _kind_of("population_change", [-3.5, -1.0, 2.0, 7.0])
    assert fs.measurement_kind == MeasurementKind.SIGNED_CHANGE.value
    assert fs.center_hint == 0.0
    assert measurement_diverging_center(fs.measurement_kind) == 0.0


def test_category_kind_from_semantic_role():
    """类别字段 → CATEGORY + none 维度（qualitative 面）。"""
    p = DatasetProfile(source="synthetic", fields={"land_use_type": "string"})
    samples = {"land_use_type": ["residential", "industrial", "residential"]}
    sem = _sem(p, samples)
    mp = derive_measurement_profile(p, sem, value_samples=samples)
    fs = mp.by_field("land_use_type")
    assert fs.measurement_kind == MeasurementKind.CATEGORY.value
    assert fs.unit_dimension == UnitDimension.NONE.value


# ── 危险歧义 fail-closed ────────────────────────────────────────────────────


def test_degree_like_metric_guard_geographic_crs():
    """A1/A2：地理 CRS 下度级值冒充米制 → DEGREE_LIKE_METRIC 证据。"""
    fs = derive_field_semantics(
        "dist_m", ["distance_measure"],
        value_samples=[12.3, 45.6, 200.1],
        crs="EPSG:4326",
    )
    assert fs.unit == "meters"
    assert fs.unit_dimension == UnitDimension.LENGTH.value
    assert CHECK_DEGREE_LIKE_METRIC in [c["code"] for c in fs.checks]


def test_degree_like_no_false_positive_projected_or_integer():
    """投影 CRS / 整数大值 / 无米制单位声明 → 不误报。"""
    # 投影 CRS 下米制度级量级是正常小尺度量。
    fs_proj = derive_field_semantics(
        "dist_m", ["distance_measure"],
        value_samples=[12.3, 45.6, 200.1],
        crs="EPSG:32650",
    )
    assert CHECK_DEGREE_LIKE_METRIC not in [c["code"] for c in fs_proj.checks]
    # 整数大值（米制形态）不判度级。
    fs_int = derive_field_semantics(
        "dist_m", ["distance_measure"],
        value_samples=[1200, 4500, 20000],
        crs="EPSG:4326",
    )
    assert CHECK_DEGREE_LIKE_METRIC not in [c["code"] for c in fs_int.checks]


def test_unit_dimension_mismatch_denominator_bound_to_count():
    """A2：分母角色绑到 count 维字段 → UNIT_DIMENSION_MISMATCH。"""
    p = DatasetProfile(
        source="synthetic",
        fields={"school_count": "integer", "population": "number"},
    )
    samples = {
        "school_count": [5, 9, 12],
        "population": [1000.0, 2000.0, 3000.0],
    }
    sem = _sem(p, samples, user_roles={"school_count": "normalization_denominator"})
    mp = derive_measurement_profile(p, sem, value_samples=samples)
    fs = mp.by_field("school_count")
    assert CHECK_UNIT_DIMENSION_MISMATCH in [c["code"] for c in fs.checks]


def test_unit_override_user_wins():
    """显式 unit override → USER_DECLARED 置信（user-wins）。"""
    fs = derive_field_semantics(
        "value", [], value_samples=[1.0, 2.0], unit_override="kilometers",
    )
    assert fs.unit == "kilometers"
    assert fs.unit_confidence == "user_declared"


def test_unknown_stays_unknown_no_fabrication():
    """无证据字段不虚构 kind/unit（unknown ≠ 未知造数）。"""
    fs = derive_field_semantics("misc_col", [], value_samples=[1.0, 2.5, 7.1])
    assert fs.measurement_kind == ""
    assert fs.unit == ""
    assert fs.unit_dimension == ""
    assert fs.kind_confidence == "unknown"


# ── 卫生与有界性 ────────────────────────────────────────────────────────────


def test_nodata_nan_do_not_poison():
    """A7：NaN/Inf/None/字符串混入 → 过滤后推导，不污染 kind。"""
    dirty = [0.1, 0.4, float("nan"), float("inf"), None, "0.2", 0.9]
    fs = derive_field_semantics("绿地率", [], value_samples=dirty)
    assert fs.measurement_kind == MeasurementKind.RATIO.value
    assert fs.unit == "fraction"


def test_bounded_profiling_on_large_synthetic_dataset():
    """A11：50k 要素合成集 → 每字段样本 ≤200，推导耗时受控。"""
    n = 50_000
    samples = {"pop": [100.0 + (i % 997) for i in range(n)]}
    p = DatasetProfile(source="synthetic", fields={"pop": "number"})
    sem = _sem(p, {"pop": samples["pop"][:200]})
    t0 = time.perf_counter()
    mp = derive_measurement_profile(p, sem, value_samples={k: v[:200] for k, v in samples.items()})
    elapsed = time.perf_counter() - t0
    assert mp.by_field("pop") is not None
    assert elapsed < 1.0  # 纯函数 + 有界样本：毫秒级
    # 字段清单截断到契约上限。
    big_fields = {f"f{i}": "number" for i in range(80)}
    p_big = DatasetProfile(source="synthetic", fields=big_fields)
    mp_big = derive_measurement_profile(p_big, None)
    assert len(mp_big.fields) <= 64


def test_serialization_roundtrip_and_version_gate():
    """A12：to_dict/from_dict roundtrip；未知版本 fail-closed 拒收。"""
    p = DatasetProfile(source="synthetic", fields={"绿地率": "number", "pop": "number"})
    samples = {"绿地率": [0.1, 0.5], "pop": [10.0, 20.0]}
    mp = derive_measurement_profile(p, _sem(p, samples), value_samples=samples)
    data = mp.to_dict()
    assert data["measurement_profile_version"] == CONTRACT_VERSION
    mp2 = DatasetMeasurementProfile.from_dict(data)
    assert mp2.to_dict() == data
    assert mp2.by_field("pop").unit == mp.by_field("pop").unit
    with pytest.raises(ValueError):
        DatasetMeasurementProfile.from_dict(
            {"measurement_profile_version": 99, "fields": []})
    with pytest.raises(ValueError):
        DatasetMeasurementProfile.from_dict("not-a-dict")


# ── 映射契约 ────────────────────────────────────────────────────────────────


def test_measurement_to_data_kind_mapping():
    """语义定族契约：category/ordinal→qualitative、signed→diverging、其余 sequential。"""
    assert measurement_to_data_kind("category") == "qualitative"
    assert measurement_to_data_kind("ordinal") == "qualitative"
    assert measurement_to_data_kind("signed_change") == "diverging"
    assert measurement_to_data_kind("count") == "sequential"
    assert measurement_to_data_kind("percentage") == "sequential"
    assert measurement_to_data_kind("uncertainty") is None
    assert measurement_to_data_kind("") is None
    assert measurement_to_data_kind("nonsense") is None


def test_name_kind_hint_consistency():
    """name_kind_hint 与 derive 词表同源：密度/率/带符号可识别。"""
    assert name_kind_hint("人口密度") == "density"
    assert name_kind_hint("gdp_growth_rate") == "rate"
    assert name_kind_hint("人口增减") == "signed_change"
    assert name_kind_hint("plain_value") == ""


def test_legend_unit_display():
    assert legend_unit_display("persons") == "人"
    assert legend_unit_display("square_kilometers") == "km²"
    assert legend_unit_display("percent") == "%"
    assert legend_unit_display("custom_unit") == "custom_unit"
    assert legend_unit_display("") == ""


def test_canonical_unit_registry_dimensions():
    assert CANONICAL_UNITS["meters"].dimension == UnitDimension.LENGTH
    assert CANONICAL_UNITS["square_kilometers"].dimension == UnitDimension.AREA
    assert CANONICAL_UNITS["percent"].dimension == UnitDimension.RATIO
    assert CANONICAL_UNITS["persons"].dimension == UnitDimension.POPULATION


# ── review 修复回归（P1 度类单位 / P2 采样偏置）────────────────────────────


def test_degree_suffix_names_do_not_become_angle_units():
    """P1：温度/湿度/速度/精度等「度」尾字段不得误判为角度/长度单位。"""
    for name in ("温度", "平均气温", "湿度", "精度", "浓度", "风速"):
        fs = derive_field_semantics(name, [], value_samples=[1.0, 2.5, 7.1])
        assert fs.unit != "degrees", name
        assert fs.unit_dimension != UnitDimension.LENGTH.value, name
    # 温度名 → celsius（master unit_hint 词表）+ TEMP 维度。
    fs_temp = derive_field_semantics("温度", [], value_samples=[1.0, 2.5])
    assert fs_temp.unit == "celsius"
    assert fs_temp.unit_dimension == UnitDimension.TEMP.value
    assert legend_unit_display("celsius") == "°C"


def test_leading_nulls_do_not_starve_sample_budget():
    """P2：前导 None/NaN 不得挤占有界样本预算（先过滤后限额）。"""
    dirty = [None] * 500 + [12.3, 45.6, 200.1]
    fs = derive_field_semantics(
        "dist_m", ["distance_measure"], value_samples=dirty, crs="EPSG:4326",
    )
    assert fs.unit == "meters"
    assert fs.unit_dimension == UnitDimension.LENGTH.value
    assert CHECK_DEGREE_LIKE_METRIC in [c["code"] for c in fs.checks]


def test_category_role_not_suppressed_by_count_collision():
    """P2：category 角色不被 count 名称碰撞压制（specificity 覆盖 CATEGORY）。"""
    fs = derive_field_semantics("类型编码n", ["category", "count_measure"], value_samples=[1, 2, 3])
    assert fs.measurement_kind == MeasurementKind.CATEGORY.value


def test_population_density_no_false_denominator_warning():
    """P2/P3：人口密度是常规合法形态，字段级推导零误报（零 checks）。"""
    fs = derive_field_semantics(
        "人口密度", ["count_measure"], value_samples=[1200.0, 3500.0, 800.0],
    )
    assert fs.measurement_kind == MeasurementKind.DENSITY.value
    assert fs.checks == []
