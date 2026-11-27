"""WP3 参考标签闭环专项：批量导入/争议处置/多版本历史/盲标选项。"""
from __future__ import annotations

import asyncio

import pytest

from app.services.survey_sampling import service, store
from app.services.survey_sampling.contracts import SurveySamplingError

from .conftest import _register_and_build_frame


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _mk_survey(survey_env):
    frame, cls_path, truth_path, ref_id, scope = _run(
        _register_and_build_frame(survey_env))
    created = _run(service.create_survey(
        scope, frame["frame_digest"], objective="WP3 闭环", target_time="2026-11",
        strategy="equal", n_total=12, seed=3, backup_per_stratum=2))
    survey_id = created["survey_id"]
    doc = service.survey_detail(scope, survey_id, blind=False)
    mains = [s for s in doc["samples"] if not s["is_backup"]]
    return scope, survey_id, mains


def test_import_and_dispute_resolution(survey_env):
    """导入：同类重复幂等跳过；异类冲突→disputed（双方保留）→裁定终版。"""
    scope, survey_id, mains = _mk_survey(survey_env)
    rows = [
        {"sample_id": mains[0]["sample_id"], "reference_class": "forest",
         "evidence_type": "existing_survey", "interpreter": "A",
         "quality": "high"},
        {"sample_id": mains[1]["sample_id"], "reference_class": "crop",
         "evidence_type": "high_res_imagery", "interpreter": "B"},
    ]
    res = _run(service.import_records(scope, survey_id, rows, actor="import",
                                      expected_revision=store.get_revision(
                                          scope, survey_id)))
    assert res["imported"] == 2 and res["skipped_same"] == 0

    # 同类重复 → 幂等跳过
    res2 = _run(service.import_records(scope, survey_id, rows[:1],
                                       actor="import",
                                       expected_revision=res["revision"]))
    assert res2["imported"] == 0 and res2["skipped_same"] == 1

    # 异类冲突 → disputed 落库（不静默覆盖）
    res3 = _run(service.import_records(
        scope, survey_id,
        [{"sample_id": mains[0]["sample_id"], "reference_class": "water",
          "interpreter": "C"}],
        actor="import", expected_revision=res2["revision"]))
    assert res3["conflicts_disputed"] == 1
    recs = store.load_records(scope, survey_id)
    s0 = [r for r in recs if r["sample_id"] == mains[0]["sample_id"]]
    assert s0[-1]["status"] == "disputed"          # 冲突记录保留
    assert s0[0]["reference_class"] == "forest"    # 原记录不覆盖

    # 裁定终版（labelled），历史可追溯
    adjudication = _run(service.adjudicate(
        scope, survey_id, sample_id=mains[0]["sample_id"],
        reference_class="water", actor="expert",
        expected_revision=res3["revision"], note="裁定依据：高分影像"))
    assert adjudication["recorded"] is True
    effective = service.effective_labels(scope, survey_id)
    assert effective[mains[0]["sample_id"]]["reference_class"] == "water"
    assert effective[mains[0]["sample_id"]]["version"] == 3
    history = [r for r in store.load_records(scope, survey_id)
               if r["sample_id"] == mains[0]["sample_id"]]
    assert [r["status"] for r in history] == ["labelled", "disputed",
                                              "labelled"]


def test_import_rejects_unknown_sample_and_class(survey_env):
    """导入边界：未知 sample_id / 字典外参考类 → typed 拒绝（无部分落库）。"""
    scope, survey_id, mains = _mk_survey(survey_env)
    with pytest.raises(SurveySamplingError) as exc:
        _run(service.import_records(
            scope, survey_id,
            [{"sample_id": "nonexistent0000", "reference_class": "forest"}]))
    assert exc.value.code == "SAMPLE_NOT_FOUND"
    with pytest.raises(SurveySamplingError) as exc2:
        _run(service.import_records(
            scope, survey_id,
            [{"sample_id": mains[0]["sample_id"],
              "reference_class": "ghost_class"}]))
    assert exc2.value.code == "CLASS_DICTIONARY_DRIFT"
    # 无部分落库
    assert store.load_records(scope, survey_id) == []


def test_undetermined_excluded_and_disclosed(survey_env):
    """不可判/拒绝：顶替后 excluded/原因披露进入估计。"""
    scope, survey_id, mains = _mk_survey(survey_env)
    rev = store.get_revision(scope, survey_id)
    # 主样本 0：不可判（顶替由替补承担）；主样本 1：拒绝；主样本 2：正常标注
    _run(service.record_reference(
        scope, survey_id, sample_id=mains[0]["sample_id"],
        status="undetermined", evidence_type="other",
        expected_revision=rev))
    rev += 1
    _run(service.record_reference(
        scope, survey_id, sample_id=mains[1]["sample_id"],
        status="refused", expected_revision=rev))
    rev += 1
    _run(service.record_reference(
        scope, survey_id, sample_id=mains[2]["sample_id"],
        status="labelled", reference_class="forest", evidence_type="field_visit",
        expected_revision=rev))
    progress = service.survey_progress(scope, survey_id)
    assert progress["total_labelled"] == 1
    assert any(b["nonusable"] >= 2 for b in progress["per_stratum"].values())
