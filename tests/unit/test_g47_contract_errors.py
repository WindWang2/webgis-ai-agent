"""G47 计划合同与 typed 错误（WP01 / WP02）单测。

合同层要保证"看起来对"的参数进不来：

* 率/密度不是可加总量 → ``RATE_OR_MIXED_UNIT``；
* 只实现 ``dorling_circle`` → 其它 mode 明确指出"未实现"，不静默降级；
* 硬帽（要素/迭代/秒数/邻接边）不可越过；
* ``plan_digest`` 包含算法版本，``source_anchor`` 只影响 ``candidate_digest``。
"""
from __future__ import annotations

import pytest

from app.services.cartogram_authoring import contract as contract_mod
from app.services.cartogram_authoring.contract import (
    ALGORITHM_ID,
    ALGORITHM_VERSION,
    MAX_FEATURES_HARD,
    clamp_plan_budget,
    parse_plan,
)
from app.services.cartogram_authoring.errors import (
    AuthoringError,
    error_payload,
    http_status,
)
from tests.unit.g47_fixtures import plan


def _raw(**overrides):
    body = {
        "schema_version": 1,
        "mode": "dorling_circle",
        "layer_id": "layer-1",
        "quantity": {"field": "pop", "unit": "人", "meaning": "count",
                     "aggregation": "sum"},
    }
    body.update(overrides)
    return body


# ── 模式 ────────────────────────────────────────────────────────────────

def test_only_dorling_circle_is_supported():
    with pytest.raises(AuthoringError) as ei:
        parse_plan(_raw(mode="gastner_newman"))
    err = ei.value
    assert err.code == "UNSUPPORTED_MODE"
    assert "未实现" in err.message
    # 必须点名"另一个可用做法"，而不是只说不行。
    assert "比例圆" in err.correction_hint


# ── 数量语义 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("unit", ["rate", "ratio", "%", "percent",
                                  "per_capita", "density", " Density "])
def test_rate_like_units_are_rejected(unit):
    """率/比例/密度乘以面积没有意义 —— 在计划层就拒绝，而不是在结果里解释。"""
    with pytest.raises(AuthoringError) as ei:
        parse_plan(_raw(quantity={"field": "pop", "unit": unit,
                                  "meaning": "count", "aggregation": "sum"}))
    assert ei.value.code == "RATE_OR_MIXED_UNIT"


def test_blank_unit_is_rejected():
    with pytest.raises(AuthoringError) as ei:
        parse_plan(_raw(quantity={"field": "pop", "unit": "  ",
                                  "meaning": "count", "aggregation": "sum"}))
    assert ei.value.code == "INVALID_QUANTITY_SPEC"


def test_unknown_meaning_is_rejected():
    with pytest.raises(AuthoringError) as ei:
        parse_plan(_raw(quantity={"field": "pop", "unit": "人",
                                  "meaning": "average", "aggregation": "sum"}))
    assert ei.value.code == "INVALID_QUANTITY_SPEC"


def test_non_sum_aggregation_is_rejected():
    with pytest.raises(AuthoringError) as ei:
        parse_plan(_raw(quantity={"field": "pop", "unit": "人",
                                  "meaning": "count", "aggregation": "mean"}))
    assert ei.value.code == "INVALID_QUANTITY_SPEC"


def test_missing_quantity_block_is_invalid_plan():
    body = _raw()
    body.pop("quantity")
    with pytest.raises(AuthoringError) as ei:
        parse_plan(body)
    assert ei.value.code == "INVALID_PLAN"


def test_unknown_plan_key_is_rejected_not_ignored():
    """``extra="forbid"``：拼错的键不能被静默忽略（否则用户以为生效了）。"""
    with pytest.raises(AuthoringError) as ei:
        parse_plan(_raw(quantitiy={"field": "pop"}))
    assert ei.value.code == "INVALID_PLAN"


def test_non_object_plan_is_rejected():
    with pytest.raises(AuthoringError) as ei:
        parse_plan(["not", "a", "plan"])
    assert ei.value.code == "INVALID_PLAN"


# ── 硬帽 ────────────────────────────────────────────────────────────────

def test_solver_iterations_above_hard_cap_are_rejected():
    with pytest.raises(AuthoringError) as ei:
        parse_plan(_raw(solver={"max_iterations": 99_999}))
    assert ei.value.code == "INVALID_SOLVER_PARAMS"


def test_feature_budget_above_hard_cap_is_rejected():
    with pytest.raises(AuthoringError) as ei:
        parse_plan(_raw(budgets={"max_features": MAX_FEATURES_HARD + 1}))
    assert ei.value.code == "INVALID_PLAN"


def test_quality_gate_must_be_positive():
    with pytest.raises(AuthoringError) as ei:
        parse_plan(_raw(quality_gate={"max_area_rel_error": 0}))
    assert ei.value.code == "INVALID_QUALITY_GATE"


def test_empty_include_ids_is_rejected():
    """空数组与"省略"语义不同（前者常是程序 bug），必须显式拒绝。"""
    with pytest.raises(AuthoringError) as ei:
        parse_plan(_raw(scope={"include_ids": []}))
    assert ei.value.code == "INVALID_PLAN"


# ── 身份（digest）────────────────────────────────────────────────────────

def test_plan_digest_is_stable_for_equal_plans():
    a = plan()
    b = plan()
    assert a.plan_digest() == b.plan_digest()
    assert a.plan_digest().startswith("g47p-")


def test_plan_digest_changes_with_any_meaningful_parameter():
    base = plan().plan_digest()
    variants = [
        plan(max_radius=40.0),
        plan(extent=1200.0),
        plan(max_iterations=200),
        plan(field="gdp"),
        plan(unit="万元"),
        plan(meaning="amount"),
        plan(title="不同的标题"),
    ]
    digests = {v.plan_digest() for v in variants}
    assert base not in digests
    assert len(digests) == len(variants)  # 每个变化都产生新身份


def test_source_anchor_is_excluded_from_plan_digest_but_not_candidate_digest():
    """计划身份 = 参数；候选身份 = 参数 + 源代次。两者不能混为一谈。"""
    anchor_a = {"ref_id": "ref:x", "content_revision": 3,
                "content_fingerprint": "g47f-aaa"}
    anchor_b = {"ref_id": "ref:x", "content_revision": 4,
                "content_fingerprint": "g47f-bbb"}
    p = plan()
    q = p.model_copy(update={"source_anchor": anchor_a})
    r = p.model_copy(update={"source_anchor": anchor_b})
    assert p.plan_digest() == q.plan_digest() == r.plan_digest()
    # 同一份计划配不同源代次 → 两个不同的候选身份（旧候选不能冒充新结果）。
    assert q.candidate_digest(anchor_a) != r.candidate_digest(anchor_b)
    assert q.candidate_digest(anchor_a) != q.candidate_digest(anchor_b)


def test_algorithm_version_participates_in_identity(monkeypatch):
    """改算法/质量语义必须换身份 —— 否则旧候选会冒充新结果。"""
    before = plan().plan_digest()
    monkeypatch.setattr(contract_mod, "ALGORITHM_VERSION", "dorling.v2")
    after = plan().plan_digest()
    assert before != after


def test_effective_max_features_respects_include_list():
    p = plan(include_ids=["a", "b", "c"])
    assert p.effective_max_features() == 3
    assert plan(max_features=2).effective_max_features() == 2


def test_budget_clamp_checks_edges_too():
    p = plan()
    with pytest.raises(AuthoringError) as ei:
        clamp_plan_budget(p, features=9, adjacency_edges=10**9)
    assert ei.value.code == "BUDGET_EXCEEDED"
    assert "降采样" in ei.value.correction_hint


# ── typed 错误面 ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "code,status",
    [
        ("INVALID_PLAN", 422),
        ("NO_STRONG_REVISION", 409),
        ("STALE_SOURCE", 409),
        ("SOURCE_TOO_LARGE", 413),
        ("BUDGET_EXCEEDED", 413),
        ("LAYOUT_NOT_FOUND", 404),
        ("EXPORT_UNAVAILABLE", 503),
        ("QUALITY_GATE_FAILED", 409),
    ],
)
def test_error_codes_map_to_http_status(code, status):
    assert http_status(AuthoringError(code, "x")) == status


def test_unknown_error_code_falls_back_to_explicit_status():
    err = AuthoringError("SOMETHING_NEW", "x", status_code=418)
    assert http_status(err) == 418


def test_error_payload_is_bounded_and_actionable():
    err = AuthoringError("FIELD_NOT_FOUND", "找不到字段", field="quantity.field",
                         correction_hint="先 inspect", details={"tried": ["pop"]})
    payload = error_payload(err)
    assert payload["success"] is False
    assert payload["code"] == "FIELD_NOT_FOUND"
    assert payload["field"] == "quantity.field"
    assert payload["correction_hint"] == "先 inspect"
    assert set(payload) <= {"success", "code", "message", "status_code",
                            "field", "correction_hint", "details"}
    assert "traceback" not in str(payload).lower()
