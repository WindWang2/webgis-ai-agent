"""低置信语义角色闸回归锁（DQH V1，data_qualification additive）。

不变式：
- measure 族角色绑定的字段，语义证据必须 ≥ rule_derived 或用户声明；
  仅名称级（metadata_derived）绑定 → degraded + FIELD_ROLE_AMBIGUOUS，
  绝不静默绑定 measure/rate/count（Oracle 判据）；
- feature-off（不传 semantic_profile）→ 与既有行为逐字段一致；
- unknown ≠ unsatisfied 红线不被破坏：无 profile 事实时仍 unknown；
- 无度量绑定语义画像 → 不添加检查（不虚构事实）；
- 全部确定性：同输入同裁决。
"""
from __future__ import annotations

from app.services.gis_harness.data_qualification import qualify_data_role
from app.services.gis_harness.recipe_packs._kit import role
from app.lib.gis.semantic_profile import (
    FieldRoleAssignment,
    RoleConfidence,
    SemanticDatasetProfile,
)

_PROFILE = {
    "featureCount": 40,
    "geometryTypes": ["Point"],
    "fields_status": "explicit",
    "fields": {"value": {"type": "number"}, "population": {"type": "number"}},
}

_MEASURE_REQ = role("measure", capability="", artifacts=(), geometry=())
_DENOM_REQ = role("denominator", acquisition="local")


def _sem(bindings: dict) -> SemanticDatasetProfile:
    """bindings: field → (roles, confidence_value)。"""
    assignments = [
        FieldRoleAssignment(
            field=f, roles=list(roles), confidence=RoleConfidence(conf))
        for f, (roles, conf) in bindings.items()
    ]
    role_index = {}
    for a in assignments:
        for r in a.roles:
            role_index.setdefault(r.value if hasattr(r, "value") else str(r), a.field)
    return SemanticDatasetProfile(field_roles=assignments, role_index=role_index)


def _checks(q):
    return {c["check"]: c["passed"] for c in q.checks}


class TestSemanticRoleGuard:
    def test_rule_derived_binding_stays_eligible(self):
        q = qualify_data_role(
            _MEASURE_REQ, "bound", resolver_profile=_PROFILE,
            semantic_profile=_sem({"value": (["count_measure"], "rule_derived")}))
        assert q.state == "eligible"

    def test_user_declared_binding_not_flagged(self):
        q = qualify_data_role(
            _MEASURE_REQ, "bound", resolver_profile=_PROFILE,
            semantic_profile=_sem({"value": (["continuous_measure"], "user_declared")}))
        assert q.state == "eligible"

    def test_name_only_measure_binding_degrades(self):
        q = qualify_data_role(
            _MEASURE_REQ, "bound", resolver_profile=_PROFILE,
            semantic_profile=_sem({"value": (["continuous_measure"], "metadata_derived")}))
        assert q.state == "degraded"
        assert q.reason_code == "FIELD_ROLE_AMBIGUOUS"
        assert _checks(q).get("semantic_role_confidence") is False

    def test_denominator_name_only_binding_degrades(self):
        # 分母只由名称级低置信绑定 → 不得下人均/率结论 → degraded。
        q = qualify_data_role(
            _DENOM_REQ, "bound", resolver_profile=_PROFILE,
            semantic_profile=_sem({"population": (["population_measure"], "metadata_derived")}))
        assert q.state == "degraded"
        assert q.reason_code == "FIELD_ROLE_AMBIGUOUS"

    def test_non_measure_semantic_bindings_invisible_to_measure_role(self):
        # 语义画像只有 label/category 绑定 → 不添加语义检查（不虚构事实）。
        q = qualify_data_role(
            _MEASURE_REQ, "bound", resolver_profile=_PROFILE,
            semantic_profile=_sem({"name": (["label"], "metadata_derived")}))
        assert q.state == "eligible"
        assert "semantic_role_confidence" not in _checks(q)

    def test_feature_off_is_byte_identical(self):
        a = qualify_data_role(_MEASURE_REQ, "bound", resolver_profile=_PROFILE)
        b = qualify_data_role(_MEASURE_REQ, "bound", resolver_profile=_PROFILE,
                              semantic_profile=None)
        assert a.to_bounded_dict() == b.to_bounded_dict()

    def test_unknown_inequality_preserved_without_profile(self):
        # 无 profile 事实 → unknown 短路语义保持在语义闸之前（红线）。
        q = qualify_data_role(
            _MEASURE_REQ, "bound", resolver_profile=None,
            semantic_profile=_sem({"value": (["count_measure"], "metadata_derived")}))
        assert q.state == "unknown"
        assert q.reason_code == "PROFILE_FACTS_UNAVAILABLE"

    def test_deterministic(self):
        kwargs = dict(resolver_profile=_PROFILE,
                      semantic_profile=_sem({"value": (["count_measure"], "metadata_derived")}))
        a = qualify_data_role(_MEASURE_REQ, "bound", **kwargs)
        b = qualify_data_role(_MEASURE_REQ, "bound", **kwargs)
        assert a.to_bounded_dict() == b.to_bounded_dict()
