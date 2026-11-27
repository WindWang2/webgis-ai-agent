"""R1 审查修复回归（P0/P1/P2 项）。

对应 planning/GOAL-20261127-03-SAMPLING-ACCURACY-AREA/REVIEW_R1.md：
- P1-1  digest/estimate_id 路径注入阻断（跨 owner 读取）
- P2-1  n_resp > N_h / N_h < 1 typed 拒绝；fpc<0 防御
- P2-2  版本号锁内分配 + CAS expected=None 收紧
- P2-3  分配逐层封顶 + 超小层 typed INVALID_DESIGN
- P2-4  import 校验异常 typed 包装（带行号）
- P2-6  工具无会话上下文 fail-closed；owner_dir 拒绝空值
- P3-6  MAX_SURVEYS_PER_SCOPE 执行
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.gis.scientific_errors import DegenerateData
from app.lib.survey_sampling import estimation as est
from app.lib.survey_sampling.design import StratumSpec, preview_sample_size
from app.services.survey_sampling import service, store
from app.services.survey_sampling.contracts import SurveySamplingError

from .conftest import _register_and_build_frame


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


import asyncio


def test_p1_path_injection_blocked(survey_env):
    """frame_digest/estimate_id 注入 → typed 404（不跨 owner、不 500）。"""
    scope = {"session_id": "sess-r1"}
    for evil in ("../other/frames/abc", "..", "a/b", "a\\b", ".hidden",
                 "abc", ""):
        with pytest.raises(SurveySamplingError) as exc:
            store.get_frame_payload(scope, evil)
        assert exc.value.code == "SURVEY_NOT_FOUND"
        assert exc.value.status_code == 404
    with pytest.raises(SurveySamplingError):
        store.get_estimate(scope, "svy-ok", "../../data")
    with pytest.raises(SurveySamplingError):
        store.get_estimate(scope, "svy-ok", "short")


def test_p2_1_resp_exceeds_population_rejected() -> None:
    """n_resp > N_h ⇒ DegenerateData（此前负 fpc 产 500/假 SE）。"""
    with pytest.raises(DegenerateData, match="n_resp > N_h"):
        counts = est.StratifiedCounts(
            strata=["a", "b"], ref_classes=["a", "b"],
            W=np.array([0.5, 0.5]), N=np.array([10.0, 10.0]),
            counts=np.array([[20, 10], [0, 0]], dtype=np.int64),  # 30>N=10
            n_planned=np.array([30, 0], dtype=np.int64),
            excluded=np.zeros(2, dtype=np.int64),
        )
        est.stratified_estimate(counts, total_area=100.0)


def test_p2_1_population_below_one_rejected() -> None:
    with pytest.raises(DegenerateData, match="≥1"):
        est.StratifiedCounts(
            strata=["a"], ref_classes=["a"],
            W=np.array([1.0]), N=np.array([0.0]),
            counts=np.array([[0]], dtype=np.int64),
            n_planned=np.array([0], dtype=np.int64),
            excluded=np.zeros(1, dtype=np.int64),
        )


def _mk(survey_env, n_total=12):
    frame, cls, truth, ref, scope = _run(_register_and_build_frame(survey_env))
    created = _run(service.create_survey(
        scope, frame["frame_digest"], objective="R1 回归", target_time="",
        strategy="equal", n_total=n_total, seed=5, backup_per_stratum=1))
    return scope, created["survey_id"]


def test_p2_2_cas_required_after_creation(survey_env):
    """创建后省略 expected_revision ⇒ 409（CAS 不再可被 None 绕过）。"""
    scope, survey_id = _mk(survey_env)
    doc = service.survey_detail(scope, survey_id, blind=False)
    main = next(s for s in doc["samples"] if not s["is_backup"])
    with pytest.raises(SurveySamplingError) as exc:
        _run(service.record_reference(
            scope, survey_id, sample_id=main["sample_id"],
            status="labelled", reference_class="forest",
            expected_revision=None))
    assert exc.value.code == "SURVEY_REVISION_CONFLICT"
    assert store.load_records(scope, survey_id) == []   # 未落库
    # 携带正确 revision 则成功；版本号锁内分配正确
    res = _run(service.record_reference(
        scope, survey_id, sample_id=main["sample_id"],
        status="labelled", reference_class="forest",
        expected_revision=store.get_revision(scope, survey_id)))
    assert res["version"] == 1


def test_p2_2_concurrent_records_no_duplicate_version(survey_env):
    """并发同 sample 记录：版本号在锁内分配，不产生重复版本。"""
    scope, survey_id = _mk(survey_env)
    doc = service.survey_detail(scope, survey_id, blind=False)
    main = next(s for s in doc["samples"] if not s["is_backup"])

    async def _two():
        # 两个并发写入（不同 expected：一个会用新鲜值，另一个故意过期）
        rev = store.get_revision(scope, survey_id)
        results = await __import__("asyncio").gather(
            service.record_reference(
                scope, survey_id, sample_id=main["sample_id"],
                status="labelled", reference_class="forest",
                expected_revision=rev),
            service.record_reference(
                scope, survey_id, sample_id=main["sample_id"],
                status="undetermined",
                expected_revision=rev),
            return_exceptions=True)
        return results

    results = _run(_two())
    ok = [r for r in results if not isinstance(r, BaseException)]
    conflicts = [r for r in results if isinstance(r, SurveySamplingError)
                 and r.code == "SURVEY_REVISION_CONFLICT"]
    assert len(ok) + len(conflicts) == 2
    versions = [r["version"] for r in ok]
    assert len(set(versions)) == len(versions)          # 版本唯一
    recs = store.load_records(scope, survey_id)
    s_versions = [r["version"] for r in recs
                  if r["sample_id"] == main["sample_id"]]
    assert len(set(s_versions)) == len(s_versions)      # 无重复版本落库


def test_p2_3_allocation_capped_and_typed(survey_env):
    """小层超额分配：preview 封顶+披露；create typed INVALID_DESIGN。"""
    strata = [StratumSpec("tiny", 10, 10.0),
              StratumSpec("big", 100000, 100000.0)]
    p = preview_sample_size(strata, strategy="equal", n_total=10000)
    assert p["allocation"]["tiny"] <= 10                # 不超层总体
    assert any("capped at N_h" in w for w in p["warnings"])
    # create 路径：封顶后分配可行（main_count = 框全部单元）；
    # 但 backup 使 n_h+backup 超出 N_h ⇒ typed INVALID_DESIGN 422（不再 500）
    frame, cls, truth, ref, scope = _run(_register_and_build_frame(survey_env))
    with pytest.raises(SurveySamplingError) as exc:
        _run(service.create_survey(
            scope, frame["frame_digest"], objective="超额", target_time="",
            strategy="equal", n_total=100000, seed=1,
            backup_per_stratum=1))
    assert exc.value.code == "INVALID_DESIGN"
    assert exc.value.status_code == 422
    created = _run(service.create_survey(
        scope, frame["frame_digest"], objective="封顶可行", target_time="",
        strategy="equal", n_total=100000, seed=1, backup_per_stratum=0))
    assert created["sample_count"] == frame["total_units"]


def test_p2_4_import_row_validation_typed(survey_env):
    """导入行合同违规 → RECORD_INVALID（带行号），非裸 ValueError。"""
    scope, survey_id = _mk(survey_env)
    doc = service.survey_detail(scope, survey_id, blind=False)
    main = next(s for s in doc["samples"] if not s["is_backup"])
    rev = store.get_revision(scope, survey_id)
    # labelled 且带 reference_class 为非法组合（合同互斥）
    with pytest.raises(SurveySamplingError) as exc:
        _run(service.import_records(
            scope, survey_id,
            [{"sample_id": main["sample_id"], "status": "undetermined",
              "reference_class": "forest"}],
            expected_revision=rev))
    assert exc.value.code == "RECORD_INVALID"
    assert "row 0" in str(exc.value)


def test_p2_6_empty_scope_fail_closed(survey_env):
    """owner_scope 空值 ⇒ UNAUTHORIZED（不落共享空目录）。"""
    with pytest.raises(SurveySamplingError) as exc:
        service.list_surveys({"session_id": ""})
    assert exc.value.code == "UNAUTHORIZED"


def test_p3_6_max_surveys_enforced(survey_env):
    """单 scope 调查数达上限 ⇒ BUDGET_EXCEEDED。"""
    from app.services.survey_sampling import contracts

    frame, cls, truth, ref, scope = _run(_register_and_build_frame(survey_env))
    n_existing = len(service.list_surveys(scope))
    for _ in range(contracts.MAX_SURVEYS_PER_SCOPE - n_existing):
        _run(service.create_survey(
            scope, frame["frame_digest"], objective="上限", target_time="",
            strategy="equal", n_total=12, seed=1))
    with pytest.raises(SurveySamplingError) as exc:
        _run(service.create_survey(
            scope, frame["frame_digest"], objective="越限", target_time="",
            strategy="equal", n_total=12, seed=1))
    assert exc.value.code == "BUDGET_EXCEEDED"
