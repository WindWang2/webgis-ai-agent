"""WP1 契约测试：词表封闭、错误码 fail-closed、计划校验、digest 稳定性。

锁定纪律（与 tests/unit/data_join_authoring/test_contract.py 同款）：
- 任何词表漂移在此即刻失败（先于任何执行路径）；
- typed 错误未知码构造即抛 ValueError；
- 计划校验拒绝：同优先级、非法权重、非法阈值、保留前缀、歧义规则。
"""
from __future__ import annotations

import pytest

from app.services.data_fusion_authoring import contracts
from app.services.data_fusion_authoring.errors import DataFusionError
from app.services.data_fusion_authoring.plan import (
    FieldMapping,
    FusionPlan,
    FusionSourceSpec,
    GeometryPolicy,
    MatchStrategy,
    parse_plan,
)


def _src(
    *,
    label: str = "来源A",
    priority: int = 1,
    reason: str = "官方教育名录（2024 年审）",
    name_field: str = "name",
    extra: dict | None = None,
) -> FusionSourceSpec:
    base = dict(
        kind="session_ref",
        session_id="s1",
        ref="ref:pois/a",
        label=label,
        priority=priority,
        priority_reason=reason,
        name_field=name_field,
        field_mappings=[
            FieldMapping(source_field="name", output_field="name", rule="same_value"),
            FieldMapping(source_field="addr", output_field="address",
                         rule="trust_priority"),
        ],
    )
    if extra:
        base.update(extra)
    return FusionSourceSpec.model_validate(base)


def _weights(**over: float) -> dict:
    # 默认无可比 id 空间 → external_id 显式 0；_src 未声明 category_field
    # → category 显式 0（各自校验单独覆盖）
    w = {
        "external_id": 0.0,
        "name": 0.55,
        "address": 0.15,
        "category": 0.0,
        "distance": 0.30,
    }
    w.update(over)
    return w


def _strategy(**over) -> MatchStrategy:
    kw = dict(weights=_weights())
    kw.update(over)
    return MatchStrategy.model_validate(kw)


def _plan(**over) -> FusionPlan:
    kw = dict(
        entity_kind="point_poi",
        source_a=_src(),
        source_b=_src(label="来源B", priority=2,
                      reason="开放众包数据（无官方审校）"),
        strategy=_strategy(),
    )
    kw.update(over)
    return FusionPlan.model_validate(kw)


# ── 词表封闭 ──────────────────────────────────────────────────

def test_vocab_closed_sets():
    assert contracts.ENTITY_KINDS == frozenset(
        {"point_poi", "area_link", "line_link"})
    assert contracts.EVIDENCE_KINDS == frozenset(
        {"external_id", "name", "address", "category", "distance"})
    assert contracts.PAIR_STATUSES == frozenset(
        {"matched", "needs_review", "ambiguous", "unmatched", "rejected"})
    assert contracts.ADJUDICATION_RULES == frozenset(
        {"same_value", "trust_priority", "newest_temporal", "keep_multi",
         "manual"})
    assert contracts.ADJUDICATION_OUTCOMES == frozenset(
        {"resolved_rule", "resolved_manual", "unresolved"})
    assert contracts.FIELD_COMPARISONS == frozenset(
        {"identical", "conflict", "missing_a", "missing_b", "missing_both"})
    assert contracts.CATEGORY_MATCH_VALUES == frozenset(
        {"equal", "different", "unknown"})
    assert contracts.GEOMETRY_ADOPTIONS == frozenset(
        {"primary_source", "newest_temporal", "manual"})
    assert contracts.COMMIT_POLICIES == frozenset(
        {"allow_unresolved", "require_resolved"})
    assert contracts.OUTPUT_ORIGINS == frozenset(
        {"merged", "single_a", "single_b", "withheld_review"})
    assert contracts.SOURCES_PER_PLAN == 2


def test_error_unknown_code_fails_closed():
    with pytest.raises(ValueError, match="unknown data-fusion error code"):
        DataFusionError("totally_unknown_code", "x")


def test_error_payload_stable():
    exc = DataFusionError("weights_invalid", "权重非法")
    p = exc.payload()
    assert p["error_code"] == "weights_invalid"
    assert "correction_hint" in p
    assert exc.status == 400
    assert DataFusionError("not_owned", "x").status == 404
    assert DataFusionError("commit_blocked", "x").status == 409


# ── 计划校验 ──────────────────────────────────────────────────

def test_plan_happy_path_and_defaults():
    plan = _plan()
    assert plan.schema_version == contracts.FUSION_PLAN_SCHEMA_VERSION
    assert plan.commit_policy == "require_resolved"
    assert plan.invalid_geometry_policy == "exclude_count"
    assert plan.geometry.adoption == "primary_source"
    assert plan.normalized_digest() and len(plan.normalized_digest()) == 32


def test_plan_rejects_equal_priority():
    with pytest.raises(Exception, match="priority 必须不同"):
        _plan(
            source_a=_src(priority=1),
            source_b=_src(label="B", priority=1,
                          reason="另一个理由字符串甲"))


def test_plan_rejects_partial_weights():
    bad = {k: v for k, v in _weights().items() if k != "address"}
    with pytest.raises(Exception, match="恰好覆盖"):
        _plan(strategy=_strategy(weights=bad))


def test_plan_rejects_unnormalized_weights():
    with pytest.raises(Exception, match="权重和"):
        _plan(strategy=_strategy(weights=_weights(distance=0.9)))


def test_plan_rejects_negative_weight():
    with pytest.raises(Exception, match="不能为负"):
        _plan(strategy=_strategy(weights=_weights(name=-0.1)))


def test_plan_zero_weight_explicit_ok():
    w = _weights(address=0.0, name=0.70)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    plan = _plan(strategy=_strategy(weights=w))
    assert plan.strategy.weights["address"] == 0.0


def test_plan_category_weight_requires_declared_field():
    """category 正权重但未声明 category_field → 计划期拒绝。"""
    with pytest.raises(Exception, match="category_field"):
        _plan(strategy=_strategy(weights=_weights(
            name=0.40, category=0.15)))


def test_plan_rejects_threshold_inversion():
    with pytest.raises(Exception, match="review_min"):
        _plan(strategy=_strategy(review_min=0.9, auto_match_min=0.8))


def test_plan_rejects_distance_window_inversion():
    with pytest.raises(Exception, match="close_distance_m"):
        _plan(strategy=_strategy(close_distance_m=300.0,
                                 max_match_distance_m=150.0))


def test_plan_point_poi_requires_distance_weight():
    w = _weights(distance=0.0, name=0.85)
    with pytest.raises(Exception, match="point_poi"):
        _plan(strategy=_strategy(weights=w))


def test_plan_rejects_unknown_normalizer():
    with pytest.raises(Exception, match="name_normalizers"):
        _plan(strategy=_strategy(
            name_normalizers=["trim", "shouty_case"]))


def test_plan_rejects_reserved_output_prefix():
    with pytest.raises(Exception, match="保留前缀"):
        _plan(source_a=_src(extra={
            "field_mappings": [
                FieldMapping(source_field="name", output_field="_fusion:x"),
            ]}))


def test_plan_rejects_conflicting_rules_across_sources():
    with pytest.raises(Exception, match="不同裁决规则"):
        _plan(
            source_a=_src(extra={
                "field_mappings": [
                    FieldMapping(source_field="name", output_field="name",
                                 rule="same_value"),
                ]}),
            source_b=_src(label="B", priority=2,
                          reason="开放众包数据（无官方审校）", extra={
                "field_mappings": [
                    FieldMapping(source_field="name", output_field="name",
                                 rule="keep_multi"),
                ]}))


def test_plan_category_map_requires_category_field():
    with pytest.raises(Exception, match="category_field"):
        _plan(source_a=_src(extra={"category_map": {"小学": "school"}}))


def test_plan_newest_temporal_requires_temporal_field():
    with pytest.raises(Exception, match="temporal_field"):
        _plan(geometry=GeometryPolicy(adoption="newest_temporal"))


def test_plan_newest_temporal_with_fields_ok():
    plan = _plan(
        source_a=_src(extra={"temporal_field": "updated_at"}),
        source_b=_src(label="B", priority=2, reason="开放众包数据（无官方审校）",
                      extra={"temporal_field": "checked_at"}),
        geometry=GeometryPolicy(adoption="newest_temporal"),
    )
    assert plan.geometry.adoption == "newest_temporal"


def test_plan_extra_forbid():
    with pytest.raises(Exception):
        _plan(nonsense_key=1)


def test_plan_rejects_unknown_budget_key():
    with pytest.raises(Exception, match="未知预算项"):
        _plan(budgets={"max_unlimited_rows": 10})


# ── digest ───────────────────────────────────────────────────

def test_digest_stable_and_sensitive():
    p1 = _plan()
    p2 = _plan()
    assert p1.normalized_digest() == p2.normalized_digest()
    # 阈值变化 → digest 变化
    p3 = _plan(strategy=_strategy(auto_match_min=0.86))
    assert p1.normalized_digest() != p3.normalized_digest()
    # 源 ref 变化 → digest 变化
    p4 = _plan(source_b=_src(label="来源B", priority=2,
                             reason="开放众包数据（无官方审校）",
                             extra={"ref": "ref:pois/b2"}))
    assert p1.normalized_digest() != p4.normalized_digest()


def test_effective_budgets_clamped_to_hard_caps():
    plan = _plan(budgets={
        "max_source_rows": 10_000_000,
        "deadline_s": 999_999,
        "max_output_features": 50_000,
    })
    b = plan.effective_budgets()
    assert b["max_source_rows"] == contracts.HARD_MAX_SOURCE_ROWS
    assert b["deadline_s"] == contracts.HARD_MAX_DEADLINE_S
    assert b["max_output_features"] == 50_000
    assert b["max_candidate_pairs"] == contracts.DEFAULT_MAX_CANDIDATE_PAIRS


def test_source_by_priority():
    plan = _plan()
    assert plan.source_by_priority() is plan.source_a
    plan2 = _plan(
        source_a=_src(priority=2, reason="理由字符串A超过四字"),
        source_b=_src(label="B", priority=1, reason="理由字符串B超过四字"))
    assert plan2.source_by_priority() is plan2.source_b


def test_parse_plan_wraps_validation_error():
    with pytest.raises(DataFusionError, match="invalid_plan|融合计划无效"):
        parse_plan({"entity_kind": "nope"})
    with pytest.raises(DataFusionError):
        parse_plan({"entity_kind": "point_poi"})  # 缺 source/strategy
