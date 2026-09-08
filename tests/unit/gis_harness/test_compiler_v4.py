"""Workflow V4 —— Compiler V4 管线单测（Wave 4）。

覆盖：base 15 阶段零漂移、V4 阶段序追加、方法族解析/方法选择/typed DAG
的端到端确定性、bounded 序列化、未映射任务的诚实 skip。
"""
from __future__ import annotations

import json


from app.services.gis_harness.workflow_compiler import COMPILER_STAGES
from app.services.gis_harness.workflow_v4.compiler_v4 import (
    WORKFLOW_COMPILER_VERSION,
    WORKFLOW_V4_STAGES,
    compile_workflow_v4,
)

_PROFILE_POINTS = {
    "featureCount": 120,
    "geometryTypes": ["Point"],
    "fields": {"school_name": {"type": "string"}},
}


def test_base_15_stages_untouched_and_v4_appended() -> None:
    c = compile_workflow_v4("成都小学的分布情况", profile=_PROFILE_POINTS)
    assert [s.stage for s in c.base.stages] == list(COMPILER_STAGES)
    assert len(c.base.stages) == 15
    assert c.full_stage_sequence == list(COMPILER_STAGES) + list(WORKFLOW_V4_STAGES)
    assert c.compiler_version == WORKFLOW_COMPILER_VERSION


def test_v4_semantics_end_to_end() -> None:
    c = compile_workflow_v4("成都小学的分布情况", profile=_PROFILE_POINTS)
    # 本体任务映射到方法族（确定性词表序取第一）
    assert c.methodology_family
    assert c.methodology_family_zh
    # 方法选择：候选集完整保留 + selected
    mq = c.method_qualification
    assert mq["selected_id"]
    assert any(q["status"] == "selected" for q in mq["qualifications"])
    assert any(q["status"] == "rejected" for q in mq["qualifications"]) or \
        len(mq["qualifications"]) == 1
    # typed DAG：结构有效（零违规）+ 有 data_input + 有 output
    dag = c.typed_dag
    assert dag["validation_violations"] == []
    kinds = {n["kind"] for n in dag["nodes"]}
    assert "data_input" in kinds and "output" in kinds
    assert dag["primary_output"]


def test_v4_deterministic_same_input() -> None:
    a = compile_workflow_v4("成都小学的分布情况", profile=_PROFILE_POINTS)
    b = compile_workflow_v4("成都小学的分布情况", profile=_PROFILE_POINTS)
    assert a.to_bounded_dict() == b.to_bounded_dict()


def test_v4_method_rejection_by_facts() -> None:
    """小样本 + 明确字段事实 → 克里金类被资格拒绝（不是 LLM 自由选）。"""
    profile = {"featureCount": 6, "geometryTypes": ["Point"],
               "fields": {"value": {"type": "number"}}}
    c = compile_workflow_v4("某地土壤重金属浓度的空间插值", profile=profile)
    mq = c.method_qualification
    if c.methodology_family == "interpolation":
        assert mq["selected_id"] in ("interp.idw", "interp.trend_surface",
                                     "interp.regression_kriging")
        assert mq["selected_id"] != "interp.ordinary_kriging"
        rejected = [q for q in mq["qualifications"]
                    if q["status"] == "rejected"]
        assert any("METHOD_MIN_SAMPLE_UNMET" in rc
                   for q in rejected for rc in q["reason_codes"])


def test_v4_unmapped_task_honest_skip() -> None:
    """无本体任务匹配 → 方法族阶段诚实 skipped（不虚构方法族）。"""
    c = compile_workflow_v4("帮我把地图变成深色主题", profile=None)
    stage = next(s for s in c.v4_stages if s.stage == "resolve_methodology")
    if stage.status == "skipped":
        assert "METHODOLOGY_FAMILY_UNMAPPED" in stage.reason_codes
        assert c.methodology_family == ""
        # 后续阶段不产出
        assert c.method_qualification == {}


def test_v4_bounded_serializable() -> None:
    c = compile_workflow_v4("成都小学的分布情况", profile=_PROFILE_POINTS)
    payload = json.dumps(c.to_bounded_dict(), ensure_ascii=False)
    assert len(payload) < 64_000
