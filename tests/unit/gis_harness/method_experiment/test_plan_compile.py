"""计划编译确定性/预检拒绝回归锁（method-experiment WP2）。"""
from __future__ import annotations

import pytest

from app.services.gis_harness.method_experiment.cards import (
    EXPERIMENT_FAMILY_IDS,
    validate_cards,
)
from app.services.gis_harness.method_experiment.plan import (
    MAX_TRIALS_HARD_CAP,
    MEXP_EXTRA_INPUT_REQUIRED,
    MEXP_NO_INPUT_REF,
    MEXP_UNKNOWN_CANDIDATE,
    MEXP_UNKNOWN_FAMILY,
    MEXP_VALUE_FIELD_REQUIRED,
    PlanCompileError,
    compile_experiment_plan,
)
from app.services.gis_harness.method_experiment.protocol import (
    build_protocol_from_card,
)


# ── 方法卡引用对账（注册面可用时悬空 fatal）─────────────────────────────

def test_cards_no_dangling_references():
    """全部方法卡的 algorithm/metric 引用可对账（工具面在 e2e 套件对账）。"""
    errors = validate_cards()
    assert errors == [], f"卡片引用悬空: {errors}"


def test_cards_vocab_integrity():
    assert EXPERIMENT_FAMILY_IDS == (
        "interpolation", "density", "clustering",
        "network_accessibility", "spatial_autocorrelation")


# ── 确定性编译 ──────────────────────────────────────────────────────────

def _compile(**kw):
    base = dict(
        family_id="interpolation",
        input_ref="ref:geojson-demo",
        value_field="price",
    )
    base.update(kw)
    return compile_experiment_plan(**base)


def test_compile_deterministic_and_idempotent():
    p1 = _compile(candidate_ids=["idw", "ordinary_kriging"])
    p2 = _compile(candidate_ids=["idw", "ordinary_kriging"])
    assert p1.experiment_id == p2.experiment_id
    assert p1.fingerprint() == p2.fingerprint()
    assert [t.trial_id for t in p1.trials] == [t.trial_id for t in p2.trials]
    # 参数网格展开确定性：idw 3 个 power + kriging 1 个 = 4 试验
    assert p1.trial_count() == 4


def test_compile_candidates_subset_preserves_card_order():
    plan = _compile(candidate_ids=["ordinary_kriging", "idw"])
    ids = [c.candidate_id for c in plan.candidates]
    assert ids == ["idw", "ordinary_kriging"]   # 卡声明序，非调用序


def test_compile_rejects_unknown_family():
    with pytest.raises(PlanCompileError) as ei:
        compile_experiment_plan(family_id="nope", input_ref="ref:x")
    assert ei.value.code == MEXP_UNKNOWN_FAMILY


def test_compile_rejects_missing_ref():
    with pytest.raises(PlanCompileError) as ei:
        compile_experiment_plan(family_id="interpolation", input_ref="",
                                value_field="v")
    assert ei.value.code == MEXP_NO_INPUT_REF


def test_compile_rejects_missing_value_field():
    with pytest.raises(PlanCompileError) as ei:
        compile_experiment_plan(family_id="interpolation",
                                input_ref="ref:x", value_field="")
    assert ei.value.code == MEXP_VALUE_FIELD_REQUIRED


def test_compile_rejects_unknown_candidate():
    with pytest.raises(PlanCompileError) as ei:
        _compile(candidate_ids=["idw", "ghost"])
    assert ei.value.code == MEXP_UNKNOWN_CANDIDATE


def test_compile_rejects_missing_extra_inputs():
    with pytest.raises(PlanCompileError) as ei:
        compile_experiment_plan(
            family_id="network_accessibility",
            input_ref="ref:facs", value_field="",
            extra_inputs={"network": "ref:net"})   # 缺 facilities
    assert ei.value.code == f"{MEXP_EXTRA_INPUT_REQUIRED}:facilities"


def test_compile_budget_cap_is_server_clamped():
    """调用方传入超上限预算 → 服务端钳制（安全上限不可协商）。"""
    plan = _compile(max_trials=10_000)
    assert plan.budget.max_trials == MAX_TRIALS_HARD_CAP


def test_compile_budget_truncation_disclosed():
    """预算小于展开需求 → 网格截断 + typed 披露（绝不静默丢试验）。"""
    plan = _compile(candidate_ids=["idw"], max_trials=2)
    assert plan.trial_count() == 2        # idw 网格 3 值 → 截到 2
    assert any("trial_budget_truncated" in f for f in plan.preflight_findings)


def test_compile_protocol_invalid_metric_rejected():
    with pytest.raises(PlanCompileError) as ei:
        _compile(metric_ids=["cv_rmse", "network_coverage"])
    assert ei.value.code == "MEXP_INVALID_PROTOCOL"


# ── 协议构建 fail-closed ────────────────────────────────────────────────

def test_protocol_scheme_vocab_guard():
    # 卡默认（spatial_block）生效：缺省 scheme 用卡默认
    p = build_protocol_from_card("interpolation", scheme="")
    assert p.scheme == "spatial_block"
    # 非法 scheme 名 → fail-closed
    with pytest.raises(ValueError, match="缺失或越界"):
        build_protocol_from_card("interpolation", scheme="random_shuffle")
    # 非 cv 族不带折语义
    p2 = build_protocol_from_card("clustering")
    assert p2.scheme == ""
    assert p2.kind == "stability"


def test_protocol_family_metric_guard():
    with pytest.raises(ValueError, match="不适用于族"):
        build_protocol_from_card(
            "clustering", metric_ids=("cv_rmse",))


def test_plan_summary_bounded():
    plan = _compile()
    s = plan.to_bounded_summary()
    assert s["experiment_id"] == plan.experiment_id
    assert len(s["trials"]) == plan.trial_count()
    assert all(len(t["trial_id"]) <= 8 for t in s["trials"])
    assert set(s["protocol"]) >= {"kind", "family_id", "metrics", "fingerprint"}
