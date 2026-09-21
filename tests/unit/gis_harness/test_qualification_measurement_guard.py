"""data_qualification 单位维度 fail-closed 闸测试（ADR-0204 S3）。

验收矩阵对应：A14 —— 单位维度错配 → 失败事实 + 专用 reason code，
收敛规则与角色闸一致（仅唯一失败时为 headline）；无画像/无矛盾零增量。
"""
from app.services.gis_harness.data_qualification import (
    qualify_data_role,
)

from app.lib.gis.dataset_profile import DatasetProfile
from app.lib.gis.semantic_profile import derive_semantic_profile


class _Req:
    """DataRoleRequirement 测试替身（duck-typed，与 workflow_schema 同形状）。"""

    def __init__(self, role="denominator", required=True,
                 missing_policy="degrade", capability_hint=""):
        self.role = role
        self.required = required
        self.missing_policy = missing_policy
        self.reason_code = ""
        self.acquisition = "local"
        self.capability_hint = capability_hint
        self.degrade_disclosure = ""
        self.geometry_kinds: tuple = ()


def _sem(fields, samples, user_roles=None):
    p = DatasetProfile(source="synthetic", fields=fields)
    return derive_semantic_profile(
        p, value_samples=samples, user_roles=user_roles)


def test_denominator_bound_to_count_dimension_fails_closed():
    """分母角色绑到 count 维字段 → UNIT_DIMENSION_MISMATCH 失败事实。"""
    sem = _sem(
        {"school_count": "integer", "population": "number"},
        {"school_count": [5, 9, 12], "population": [1000.0, 2000.0, 3000.0]},
        user_roles={"school_count": "normalization_denominator"},
    )
    q = qualify_data_role(
        _Req(role="denominator"), "bound",
        resolver_profile={"featureCount": 3, "geometryTypes": ["Polygon"],
                          "fields": {"school_count": {"type": "integer"}}},
        semantic_profile=sem,
    )
    assert q.state == "degraded"
    assert any(c.get("check") == "unit_dimension" and not c.get("passed")
               for c in q.checks)


def test_unit_mismatch_is_headline_when_sole_failure():
    """量纲错配是唯一失败事实时成为 headline reason（user_declared 绑定
    使角色闸通过；分母名称证据在场使结构检查通过）。"""
    sem = _sem(
        {"population_count_est": "integer"},
        {"population_count_est": [10, 20, 30]},
        user_roles={"population_count_est": "area_measure"},
    )
    q = qualify_data_role(
        _Req(role="denominator"), "bound",
        resolver_profile={"featureCount": 3, "geometryTypes": ["Polygon"],
                          "fields": {"population_count_est": {"type": "integer"}}},
        semantic_profile=sem,
    )
    assert q.state == "degraded"
    assert q.reason_code == "UNIT_DIMENSION_MISMATCH"


def test_valid_population_denominator_passes():
    """正常人口分母 → 单位闸零增量（不出失败事实）。"""
    sem = _sem(
        {"population": "number"},
        {"population": [1000.0, 2000.0, 3000.0]},
    )
    q = qualify_data_role(
        _Req(role="denominator"), "bound",
        resolver_profile={"featureCount": 3, "geometryTypes": ["Polygon"],
                          "fields": {"population": {"type": "number"}}},
        semantic_profile=sem,
    )
    assert not any(
        c.get("check") == "unit_dimension" and not c.get("passed")
        for c in q.checks
    )


def test_rate_without_temporal_evidence_flags():
    """率绑定无时间字段证据 → RATE_MISSING_TEMPORAL 失败事实。"""
    sem = _sem(
        {"growth_rate": "number"},
        {"growth_rate": [0.02, 0.03, 0.01]},
    )
    q = qualify_data_role(
        _Req(role="measure"), "bound",
        resolver_profile={"featureCount": 3, "geometryTypes": ["Polygon"],
                          "fields": {"growth_rate": {"type": "number"}}},
        semantic_profile=sem,
    )
    codes = [c.get("code") for c in q.checks if c.get("check") == "unit_dimension"]
    assert "RATE_MISSING_TEMPORAL" in codes
    assert q.state == "degraded"


def test_no_semantic_profile_guard_is_off():
    """无语义画像 → 闸自动失效（零增量，与角色闸同纪律）。"""
    q = qualify_data_role(
        _Req(role="denominator"), "bound",
        resolver_profile={"featureCount": 3, "geometryTypes": ["Polygon"],
                          "fields": {"anything": {"type": "number"}}},
        semantic_profile=None,
    )
    assert not any(c.get("check") == "unit_dimension" for c in q.checks)


def test_guard_reason_not_headline_when_other_failures_present():
    """多闸并发失败时闸 code 不抢 headline（不掩盖其他维度失败信号）。

    结构性缺分母（DENOMINATOR_FIELD_REQUIRED，非自动修复）按既有收敛
    优先级压过两个语义闸的 code —— 闸只补失败事实，不改收敛序。
    """
    sem = _sem(
        {"school_count": "integer"},
        {"school_count": [5, 9, 12]},
        user_roles={"school_count": "normalization_denominator"},
    )
    q = qualify_data_role(
        _Req(role="denominator"), "bound",
        resolver_profile={"featureCount": 3, "geometryTypes": ["Polygon"],
                          "fields": {"school_count": {"type": "integer"}}},
        semantic_profile=sem,
    )
    failed = [c for c in q.checks if not c.get("passed")]
    assert len(failed) >= 2
    assert q.reason_code == "DENOMINATOR_FIELD_REQUIRED"
    assert q.state == "degraded"
