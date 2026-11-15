"""G41 闭合词表与科学资格合同（WP02 / WP06 / B06/B07/B13/B19-B23/B26）。"""
from __future__ import annotations

import copy

import pytest

from app.services.bivariate_reliability_authoring import AuthoringError, parse_plan
from app.services.bivariate_reliability_authoring.contract import (
    ALGORITHM_VERSION,
    PLAN_SCHEMA_VERSION,
)
from tests.unit.bivariate_reliability.helpers import (
    bivariate_plan,
    reliability_plan,
)


def test_closed_vocabulary_rejects_unknown_fields():
    with pytest.raises(AuthoringError) as exc:
        parse_plan({**bivariate_plan(), "paint": {"color": "#fff"}})
    assert exc.value.code in ("INVALID_PLAN", "MODE_FIELD_MISMATCH")


def test_unknown_plan_version_rejected():
    with pytest.raises(AuthoringError) as exc:
        parse_plan({**bivariate_plan(), "schema_version": 999})
    assert exc.value.code == "UNKNOWN_PLAN_VERSION"


def test_same_field_twice_rejected():
    body = bivariate_plan()["bivariate"]
    body["axis_b"]["field"] = "a"
    with pytest.raises(AuthoringError) as exc:
        parse_plan({"layer_id": "L1", "mode": "bivariate_matrix",
                    "bivariate": body})
    assert exc.value.code == "SAME_FIELD_TWICE"


def test_different_units_are_legal_and_independent():
    plan = parse_plan(bivariate_plan())
    assert plan.bivariate.axis_a.unit == "人"
    assert plan.bivariate.axis_b.unit == "%"
    digest_a = plan.plan_digest()
    body = bivariate_plan()["bivariate"]
    body["axis_b"]["unit"] = "‰"
    assert parse_plan({"layer_id": "L1", "mode": "bivariate_matrix",
                       "bivariate": body}).plan_digest() != digest_a


def test_n4_and_manual_break_count_rejected_not_clamped():
    body = bivariate_plan()["bivariate"]
    body["n"] = 4
    with pytest.raises(AuthoringError):
        parse_plan({"layer_id": "L1", "mode": "bivariate_matrix",
                    "bivariate": body})
    body = bivariate_plan()["bivariate"]
    body["axis_a"] = {"field": "a", "method": "manual", "breaks": [10]}
    with pytest.raises(AuthoringError) as exc:
        parse_plan({"layer_id": "L1", "mode": "bivariate_matrix",
                    "bivariate": body})
    assert exc.value.code == "MANUAL_BREAK_COUNT_MISMATCH"


def test_insufficient_colors_rejected_not_clamped():
    body = bivariate_plan()["bivariate"]
    body.pop("matrix_id", None)
    body["colors"] = ["#111111", "#222222", "#333333"]
    with pytest.raises(AuthoringError) as exc:
        parse_plan({"layer_id": "L1", "mode": "bivariate_matrix",
                    "bivariate": body})
    assert exc.value.code == "COLOR_COUNT_MISMATCH"


def test_ambiguous_color_source_rejected():
    body = bivariate_plan()["bivariate"]
    body["matrix_id"] = "BiTealRose"
    body["colors"] = ["#%06x" % i for i in range(9)]
    with pytest.raises(AuthoringError) as exc:
        parse_plan({"layer_id": "L1", "mode": "bivariate_matrix",
                    "bivariate": body})
    assert exc.value.code == "AMBIGUOUS_COLOR_SOURCE"


def test_breaks_not_strictly_increasing_rejected():
    body = bivariate_plan()["bivariate"]
    body["axis_a"] = {"field": "a", "method": "manual", "breaks": [20, 10]}
    with pytest.raises(AuthoringError) as exc:
        parse_plan({"layer_id": "L1", "mode": "bivariate_matrix",
                    "bivariate": body})
    assert exc.value.code == "BREAKS_NOT_STRICTLY_INCREASING"


def test_plan_digest_is_content_addressed_and_versioned():
    p1 = parse_plan(bivariate_plan())
    p2 = parse_plan(bivariate_plan())
    assert p1.plan_digest() == p2.plan_digest()
    body = bivariate_plan()["bivariate"]
    body["boundary_rule"] = "legacy_lower_inclusive"
    assert parse_plan({"layer_id": "L1", "mode": "bivariate_matrix",
                       "bivariate": body}).plan_digest() != p1.plan_digest()
    assert ALGORITHM_VERSION and PLAN_SCHEMA_VERSION == 1
    # source_anchor 不参与 plan_digest（源身份单独核对）
    a = copy.deepcopy(bivariate_plan())
    a["source_anchor"] = {"ref_id": "ref:x", "content_revision": 1}
    assert parse_plan(a).plan_digest() == p1.plan_digest()


# ── 可靠性度量资格 ──────────────────────────────────────────────────────

def test_variance_requires_squared_unit():
    body = reliability_plan()["reliability"]
    body["measure"] = {"kind": "variance", "value_field": "se",
                       "source": "author_declared", "method_note": "测试"}
    with pytest.raises(AuthoringError) as exc:
        parse_plan({"layer_id": "L1", "mode": "reliability_dual_channel",
                    "reliability": body})
    assert exc.value.code == "VARIANCE_UNIT_REQUIRED"


def test_interval_requires_level_and_low_high():
    body = reliability_plan()["reliability"]
    body["measure"] = {"kind": "confidence_interval",
                       "low_field": "lo", "high_field": "hi",
                       "source": "author_declared", "method_note": "测试"}
    with pytest.raises(AuthoringError) as exc:
        parse_plan({"layer_id": "L1", "mode": "reliability_dual_channel",
                    "reliability": body})
    assert exc.value.code == "MISSING_INTERVAL_LEVEL"
    body["measure"]["level"] = 0.95
    assert parse_plan({"layer_id": "L1", "mode": "reliability_dual_channel",
                       "reliability": body}).reliability.measure.level == 0.95


@pytest.mark.parametrize("level", [0.0, 1.0, 95, -0.1])
def test_invalid_level_rejected_no_auto_95(level):
    body = reliability_plan()["reliability"]
    body["measure"] = {"kind": "prediction_interval",
                       "low_field": "lo", "high_field": "hi",
                       "level": level, "source": "author_declared",
                       "method_note": "测试"}
    with pytest.raises(AuthoringError):
        parse_plan({"layer_id": "L1", "mode": "reliability_dual_channel",
                    "reliability": body})


def test_ci_and_pi_are_distinct_in_digest():
    def dg(kind):
        body = reliability_plan()["reliability"]
        body["measure"] = {"kind": kind, "low_field": "lo",
                           "high_field": "hi", "level": 0.9,
                           "source": "author_declared", "method_note": "测试"}
        return parse_plan({"layer_id": "L1", "mode": "reliability_dual_channel",
                           "reliability": body}).plan_digest()
    assert dg("confidence_interval") != dg("prediction_interval")


def test_analysis_evidence_requires_ref():
    body = reliability_plan()["reliability"]
    body["measure"]["source"] = "analysis_evidence"
    with pytest.raises(AuthoringError) as exc:
        parse_plan({"layer_id": "L1", "mode": "reliability_dual_channel",
                    "reliability": body})
    assert exc.value.code == "MISSING_EVIDENCE_REF"


def test_author_declared_requires_method_note():
    body = reliability_plan()["reliability"]
    del body["measure"]["method_note"]
    with pytest.raises(AuthoringError) as exc:
        parse_plan({"layer_id": "L1", "mode": "reliability_dual_channel",
                    "reliability": body})
    assert exc.value.code == "AUTHOR_DECLARATION_UNEXPLAINED"


def test_measure_field_cannot_be_estimate():
    body = reliability_plan()["reliability"]
    body["measure"]["value_field"] = "est"
    with pytest.raises(AuthoringError) as exc:
        parse_plan({"layer_id": "L1", "mode": "reliability_dual_channel",
                    "reliability": body})
    assert exc.value.code == "MEASURE_FIELD_IS_ESTIMATE"


def test_unknown_output_must_be_distinct():
    body = reliability_plan()["reliability"]
    body["visual"]["unknown_output"] = 0.9
    p = parse_plan({"layer_id": "L1", "mode": "reliability_dual_channel",
                    "reliability": body})
    # 形状合法；差异在 compute_reliability 里按桶输出核对
    assert p.reliability.visual.unknown_output == 0.9


def test_reliability_rejects_half_effective_legacy_boundary_rule():
    """F3 复审：图例区间与 paint step 必须同语义，不接受半生效规则。"""
    body = reliability_plan()["reliability"]
    body["boundary_rule"] = "legacy_lower_inclusive"
    with pytest.raises(AuthoringError) as exc:
        parse_plan({"layer_id": "L1", "mode": "reliability_dual_channel",
                    "reliability": body})
    assert exc.value.code == "UNSUPPORTED_RELIABILITY_BOUNDARY_RULE"
    assert exc.value.field == "boundary_rule"


def test_reject_mode_has_no_expression_contract():
    with pytest.raises(AuthoringError):
        parse_plan({"layer_id": "L1", "mode": "revert",
                    "bivariate": bivariate_plan()["bivariate"]})
    assert parse_plan({"layer_id": "L1", "mode": "revert"}).mode == "revert"
