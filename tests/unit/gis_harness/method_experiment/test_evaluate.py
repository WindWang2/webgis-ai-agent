"""评价裁决回归锁（method-experiment WP4）—— 可比组/方向/tie-break/
Pareto/弃权/采纳回执。"""
from __future__ import annotations

from app.services.gis_harness.method_experiment.evaluate import (
    build_adoption_receipt,
    evaluate_experiment,
)
from app.services.gis_harness.method_experiment.metrics import (
    direction_prefers,
)
from app.services.gis_harness.method_experiment.plan import (
    compile_experiment_plan,
)
from app.services.gis_harness.method_experiment.state import (
    TrialRecord,
    new_state,
)


def _plan(candidate_ids=("idw", "ordinary_kriging"), **kw):
    return compile_experiment_plan(
        family_id="interpolation", input_ref="ref:geojson-x",
        value_field="v", candidate_ids=list(candidate_ids), **kw)


def _mk_trial(trial_id, candidate_id, metrics, state="completed", **kw):
    return TrialRecord(
        trial_id=trial_id, candidate_id=candidate_id, state=state,
        metrics=dict(metrics), content_fingerprint=f"fp-{trial_id}",
        metric_basis={m: {"folds": 4} for m in metrics}, **kw)


def test_direction_semantics():
    assert direction_prefers("cv_rmse", 1.0, 2.0)      # lower better
    assert not direction_prefers("cv_rmse", 2.0, 1.0)
    assert direction_prefers("cluster_silhouette", 0.8, 0.2)  # higher better
    assert not direction_prefers("cv_rmse", 1.0, 1.0)  # 相等不偏
    assert not direction_prefers("ghost_metric", 1.0, 2.0)  # 未知不猜


def test_ranking_direction_and_deterministic_tiebreak():
    plan = _plan()
    state = new_state(plan.to_bounded_summary())
    state.trials = {
        "t01": _mk_trial("t01", "idw", {"cv_rmse": 5.0}),
        "t02": _mk_trial("t02", "ordinary_kriging", {"cv_rmse": 3.0}),
        "t03": _mk_trial("t03", "idw", {"cv_rmse": 3.0}, variant_index=1),
    }
    ev = evaluate_experiment(plan, state)
    g = next(g for g in ev.comparable_groups if g.metric_id == "cv_rmse")
    # 值并列（3.0）时 candidate_id 字典序 tie-break：idw < ordinary_kriging
    assert [r.trial_id for r in g.rows] == ["t03", "t02", "t01"]
    assert "t02" in g.ties             # 并列披露（并列对的后者进 ties）
    assert g.n_evaluated == 3


def test_failed_and_not_evaluated_excluded_with_disclosure():
    plan = _plan()
    state = new_state(plan.to_bounded_summary())
    state.trials = {
        "t01": _mk_trial("t01", "idw", {"cv_rmse": 5.0}),
        "t02": _mk_trial("t02", "ordinary_kriging", {}, state="failed",
                         reason_code="MEXP_TOOL_ERROR"),
        "t03": _mk_trial("t03", "idw", {}, state="not_evaluated",
                         variant_index=1,
                         reason_code="MEXP_METRICS_ABSENT"),
    }
    ev = evaluate_experiment(plan, state)
    g = next(g for g in ev.comparable_groups if g.metric_id == "cv_rmse")
    assert g.n_evaluated == 1
    assert g.n_excluded == 2
    assert any("t02:failed" in r for r in g.exclusion_reasons)
    assert any("not_evaluated" in s for s in ev.incomparable)


def test_abstain_when_insufficient_evidence():
    plan = _plan()
    state = new_state(plan.to_bounded_summary())
    state.trials = {"t01": _mk_trial("t01", "idw", {"cv_rmse": 5.0})}
    ev = evaluate_experiment(plan, state)
    assert ev.recommendation is not None
    assert ev.recommendation.status == "abstained"
    assert ev.recommendation.abstain_reason == "insufficient_completed_trials"


def test_recommendation_carries_evidence_and_sensitivity():
    plan = _plan(candidate_ids=["idw"])
    state = new_state(plan.to_bounded_summary())
    state.trials = {
        "t01": _mk_trial("t01", "idw", {"cv_rmse": 5.0, "cv_mae": 4.0}),
        "t02": _mk_trial("t02", "idw", {"cv_rmse": 8.0, "cv_mae": 6.0},
                         variant_index=1),
        "t03": _mk_trial("t03", "idw", {"cv_rmse": 2.0, "cv_mae": 1.5},
                         variant_index=2),
    }
    ev = evaluate_experiment(plan, state)
    rec = ev.recommendation
    assert rec.status == "recommended"
    assert rec.trial_id == "t03"       # rmse 最小
    assert rec.metric_evidence["cv_rmse"] == 2.0
    sens = rec.sensitivity
    assert sens.get("spread") == 6.0   # max-min 跨参数敏感性
    assert any("量纲" in c or "本数据集" in c for c in rec.caveats)


def test_pareto_front_identifies_nondominated():
    plan = _plan(candidate_ids=["idw"])
    state = new_state(plan.to_bounded_summary())
    # A: rmse 好 bias 差；B: rmse 差 bias 好；C: 双差（被支配）
    state.trials = {
        "t01": _mk_trial("t01", "idw",
                         {"cv_rmse": 1.0, "cv_abs_bias": 5.0}),
        "t02": _mk_trial("t02", "idw",
                         {"cv_rmse": 5.0, "cv_abs_bias": 1.0},
                         variant_index=1),
        "t03": _mk_trial("t03", "idw",
                         {"cv_rmse": 5.0, "cv_abs_bias": 5.0},
                         variant_index=2),
    }
    ev = evaluate_experiment(plan, state)
    rec = ev.recommendation
    assert set(rec.pareto_front) == {"t01", "t02"}
    assert "t03" not in rec.pareto_front


def test_adoption_receipt_requires_completed_trial():
    plan = _plan()
    state = new_state(plan.to_bounded_summary())
    state.trials = {
        "t01": _mk_trial("t01", "idw", {"cv_rmse": 5.0}),
        "t02": _mk_trial("t02", "ordinary_kriging", {}, state="not_evaluated",
                         reason_code="MEXP_METRICS_ABSENT"),
    }
    receipt, decline = build_adoption_receipt(plan, state, trial_id="t01")
    assert receipt is not None
    assert receipt.candidate_id == "idw"
    assert receipt.metric_snapshot == {"cv_rmse": 5.0}
    # 未评估拒绝
    receipt2, decline2 = build_adoption_receipt(plan, state, trial_id="t02")
    assert receipt2 is None
    assert decline2 == "MEXP_TRIAL_NOT_COMPLETED"
    # 不存在拒绝
    receipt3, decline3 = build_adoption_receipt(plan, state, trial_id="ghost")
    assert receipt3 is None
    assert decline3 == "MEXP_TRIAL_NOT_FOUND"


def test_evaluation_bounded_projection():
    plan = _plan()
    state = new_state(plan.to_bounded_summary())
    state.trials = {
        "t01": _mk_trial("t01", "idw", {"cv_rmse": 5.0, "cv_mae": 4.0}),
        "t02": _mk_trial("t02", "ordinary_kriging",
                         {"cv_rmse": 3.0, "cv_mae": 2.5}),
    }
    d = evaluate_experiment(plan, state).to_bounded_dict()
    assert d["experiment_id"] == state.experiment_id
    assert len(d["groups"]) >= 1
    for g in d["groups"]:
        assert len(g["rows"]) <= 12
    assert d["recommendation"]["status"] == "recommended"
