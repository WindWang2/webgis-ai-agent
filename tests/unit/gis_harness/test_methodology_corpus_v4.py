"""Workflow V4 —— 双语方法语料 + 编译器评估 单测（Wave 11）。

语料是审定工件：期望为语义计划（族/角色/拒绝集/义务提示），不是工具
序列；编译器评估五维（正确性/完备性/拒绝/确定性/可解释）全绿是验收
红线。零 LLM、零 I/O。
"""
from __future__ import annotations


from app.evaluation.methodology_corpus import (
    METHODOLOGY_CORPUS,
    METHODOLOGY_FAMILIES_COVERAGE,
    MethodologyCase,
)
from app.services.gis_harness.workflow_v4.compiler_v4 import WORKFLOW_V4_STAGES
from app.services.gis_harness.workflow_v4.evaluation import (
    evaluate_case,
    evaluate_compiler,
)
from app.services.gis_harness.workflow_v4.methodology import (
    METHODOLOGY_FAMILIES,
    resolve_methodology_family_for_query,
)


# ── 语料完整性（审定工件的红线）─────────────────────────────────────────

def test_corpus_covers_all_twelve_families() -> None:
    covered = {c.family for c in METHODOLOGY_CORPUS}
    assert covered == set(METHODOLOGY_FAMILIES)
    assert METHODOLOGY_FAMILIES_COVERAGE == set(METHODOLOGY_FAMILIES)


def test_corpus_is_bilingual() -> None:
    for c in METHODOLOGY_CORPUS:
        assert c.utterance_zh and c.utterance_en
    has_ambiguous = any(c.ambiguous_variants for c in METHODOLOGY_CORPUS)
    has_negative = any(c.expected_rejected_methods for c in METHODOLOGY_CORPUS)
    assert has_ambiguous and has_negative


# ── 专业词决定方法族透镜 ─────────────────────────────────────────────────

def test_keyword_disambiguates_distribution_vs_density() -> None:
    """同一本体任务（点分布）："分布情况"→描述制图，"空间密度"→密度族。"""
    task = "distribution.point_distribution"
    assert resolve_methodology_family_for_query(
        "成都市小学分布情况", task).family_id == "descriptive_mapping"
    assert resolve_methodology_family_for_query(
        "分析成都便利店的空间密度", task).family_id == "distribution_density"


def test_professional_keyword_overrides_task_coverage_divergence() -> None:
    """任务匹配漂移时专业词纠偏（family ≠ task 覆盖族，分歧进 evidence）。"""
    # 任务模糊匹配到点分布，但 "自相关" 明确指向空间统计族
    fam = resolve_methodology_family_for_query(
        "检验各区房价的空间自相关", "distribution.point_distribution")
    assert fam.family_id == "spatial_statistics"


def test_zero_keyword_falls_back_to_task_coverage() -> None:
    fam = resolve_methodology_family_for_query(
        "成都市小学分布情况", "distribution.point_distribution")
    assert fam is not None  # 分布情况 命中描述族；再验纯回退路径
    fam2 = resolve_methodology_family_for_query(
        "", "decision.suitability")
    assert fam2.family_id == "suitability"


# ── 编译器评估（五维全绿红线）────────────────────────────────────────────

def test_compiler_evaluation_full_corpus_green() -> None:
    report = evaluate_compiler()
    assert report.total >= 36  # 18 案例 × zh/en（+歧义变体）
    assert report.passed == report.total
    assert report.dimensions["family_correct"] == report.total
    assert report.dimensions["data_requirements_complete"] == report.total
    assert report.dimensions["deterministic"] == report.total
    assert report.dimensions["explainable"] == report.total
    # 负例与义务维度有真实覆盖（不是 None 空转）
    assert report.dimensions["invalid_method_rejected"] >= 2
    assert report.dimensions["obligation_complete"] >= 2
    assert report.failed_cases == []


def test_single_case_evaluation_exposes_failures() -> None:
    case = MethodologyCase(
        case_id="synthetic.fail",
        family="interpolation",
        utterance_zh="成都市小学分布情况",
        utterance_en="primary schools in Chengdu",
    )
    evals = evaluate_case(case)
    assert all(not e.passed for e in evals)  # 期望插值族 → 描述表述必失败
    assert any("family mismatch" in f for e in evals for f in e.failures)


def test_method_selection_not_arbitrary_llm_choice() -> None:
    """小样本克里金被资格拒绝（不是让 LLM 自由猜方法）。"""
    case = next(c for c in METHODOLOGY_CORPUS
                if c.case_id == "interp.krige_negative")
    evals = evaluate_case(case)
    assert evals and all(e.passed for e in evals)
    assert all(e.invalid_method_rejected for e in evals
               if e.invalid_method_rejected is not None)


def test_v4_stage_sequence_in_evaluation_oracle() -> None:
    """评估消费的编译产物阶段序 = 15 + V4（8 阶段）。"""
    assert len(WORKFLOW_V4_STAGES) == 8
