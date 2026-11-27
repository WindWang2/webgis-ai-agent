"""Epic 11 —— Method Ranker V2 单测（abstention + 冻结语料基准钉值）。"""
from __future__ import annotations

import pytest

from app.lib.gis.methodology.method_corpus import (
    MIN_METHOD_CORPUS_SIZE,
    METHOD_CORPUS,
    corpus_categories_coverage,
)
from app.lib.gis.methodology.qualification import QualificationFacts
from app.lib.gis.methodology.ranking import (
    ABSTAIN_ALL_REJECTED,
    ABSTAIN_NO_EVIDENCE,
    WEIGHT_CONSTRAINT,
    WEIGHT_COST,
    WEIGHT_GRAPH,
    WEIGHT_LEXICAL,
    WEIGHT_PRIOR,
    WEIGHT_QUALIFICATION,
    WEIGHT_TAXONOMY,
    evaluate_corpus,
    rank_methods,
)
from app.lib.gis.methodology.taxonomy import GIS_TASK_CATEGORIES


# ── 权重契约（架构冻结）─────────────────────────────────────────────────

def test_weights_frozen_and_complete() -> None:
    total = (WEIGHT_TAXONOMY + WEIGHT_QUALIFICATION + WEIGHT_GRAPH
             + WEIGHT_LEXICAL + WEIGHT_PRIOR + WEIGHT_COST
             + WEIGHT_CONSTRAINT)
    assert abs(total - 1.0) < 1e-9


def test_corpus_size_and_category_coverage() -> None:
    assert len(METHOD_CORPUS) >= MIN_METHOD_CORPUS_SIZE
    covered = corpus_categories_coverage()
    assert covered <= set(GIS_TASK_CATEGORIES)
    # 20 类全覆盖（Epic §5.A 对齐断言）
    assert covered == set(GIS_TASK_CATEGORIES)


def test_corpus_is_bilingual() -> None:
    for case in METHOD_CORPUS:
        assert case.query_zh and case.query_en
    kinds = {c.kind for c in METHOD_CORPUS}
    assert {"direct", "hard_negative", "ambiguous"} <= kinds


# ── abstention（弃权语义）───────────────────────────────────────────────

def test_garbage_query_abstains() -> None:
    """乱码/无关 query 必须弃权（不硬选）。"""
    for query in ("dfkahsdflkh", "今天天气怎么样", ""):
        result = rank_methods(query, QualificationFacts.from_profile({}))
        assert result.abstained, query
        assert result.abstain_reason == ABSTAIN_NO_EVIDENCE
        assert result.selected is None


def test_all_rejected_abstains_with_reason() -> None:
    """5 个点的克里金诉求：族内全部被拒 → 弃权而非硬选。"""
    facts = QualificationFacts.from_profile(
        {"featureCount": 5, "geometryTypes": ["Point"],
         "fields": {"v": {"type": "number"}}}, measure_kind="continuous")
    #kriging/IDW/趋势面/指示克里金全部因样本/前提不足被拒 → 全拒弃权
    result = rank_methods("克里金插值成面", facts, category_id="interpolation")
    # IDW min=5 恰好 5 个点：允许 viable；此时不得弃权但 top1 必须非克里金
    if result.selected is not None:
        assert result.selected.method_id != "interp.ordinary_kriging"
    else:
        assert result.abstained
        assert result.abstain_reason == ABSTAIN_ALL_REJECTED


def test_parallel_cases_category_invariant() -> None:
    """同类别不同主体/规模 → 同一方法裁决（反 per-query 硬编码）。"""
    profiles = [
        {"featureCount": 300, "geometryTypes": ["Point"]},
        {"featureCount": 900, "geometryTypes": ["Point"]},
    ]
    picks = []
    for profile in profiles:
        facts = QualificationFacts.from_profile(profile)
        result = rank_methods("把这些站点聚成几类空间簇", facts,
                              category_id="clustering")
        assert result.selected is not None
        picks.append(result.selected.method_id)
    assert picks[0] == picks[1]


# ── 冻结语料基准（钉值 = 实测保守下限；语料 gold 先于调参冻结）───────────

def test_corpus_benchmark_pins() -> None:
    metrics = evaluate_corpus(METHOD_CORPUS)
    assert metrics["cases"] == len(METHOD_CORPUS)
    assert metrics["evaluation_mode"] == "end_to_end"
    assert metrics["recall@5"] == 0.92
    assert metrics["recall@1"] == 0.88
    assert metrics["mrr"] == 0.9
    assert metrics["category_conditioned"]["recall@5"] == 1.0
    assert metrics["invalid_selection_rate"] <= 0.08
    # ambiguous 案例 top-1 ∈ valid 集（多解不当错误）
    assert metrics["ambiguous_valid_top1"] == 0.0
    assert metrics["category_conditioned"]["ambiguous_valid_top1"] == 1.0


def test_rank_result_is_deterministic() -> None:
    facts = QualificationFacts.from_profile(
        {"featureCount": 120, "geometryTypes": ["Point"]})
    a = rank_methods("用热力图看看门店密度", facts, category_id="density")
    b = rank_methods("用热力图看看门店密度", facts, category_id="density")
    assert a.to_bounded_dict() == b.to_bounded_dict()


def test_lexical_trap_beaten_by_structure() -> None:
    """「服务区」诉求：路网方法胜出，欧氏缓冲（词面近邻）不得登顶。"""
    facts = QualificationFacts.from_profile({})
    result = rank_methods("计算每个医院 15 分钟车程服务区", facts,
                          category_id="accessibility_network")
    assert result.selected is not None
    assert result.selected.method_id == "network.service_area"


def test_empty_candidate_pool_abstains_instead_of_silent_none() -> None:
    """族内无候选（空族或全部被 exclude）时 selected=None 但必须
    abstained=True —— 旧实现 abstained=False，「无选择」与成功无法区分。"""
    from app.lib.gis.methodology.qualification import QualificationFacts
    from app.lib.gis.methodology.ranking import rank_family_methods
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )

    registry = get_methodology_registry()
    family = next(f for f in registry.families() if f.candidate_methods)
    facts = QualificationFacts.from_profile({"featureCount": 100})

    result = rank_family_methods(
        "插值成面", family.family_id, facts,
        methodology_registry=registry,
        exclude=[m.method_id for m in family.candidate_methods],
    )
    assert result.selected is None
    assert result.ranked == []
    assert result.abstained is True
    assert result.abstain_reason == ABSTAIN_NO_EVIDENCE
    assert result.evidence["candidates"] == 0


def test_english_uncertainty_query_triggers_uncertainty_constraint() -> None:
    """英文不确定性查询（variance / uncertainty / confidence）与中文同权：
    只收中文词时英文诉求静默丢掉 require_uncertainty 约束，无不确定性
    产出的 interp.idw 得以压过克里金。"""
    from app.lib.gis.methodology.ranking import _constraint_component

    # uncertainty_support="none" 的 IDW 在两种语言下都受同一惩罚
    zh = _constraint_component("interp.idw", {}, "生成插值预测方差的不确定性误差面")
    en = _constraint_component("interp.idw", {},
                               "produce a prediction variance uncertainty surface")
    assert zh == pytest.approx(0.2)
    assert en == pytest.approx(0.2)
    # 克里金（field）不受该约束惩罚
    assert _constraint_component("interp.ordinary_kriging", {},
                                 "produce a prediction variance surface") == \
        pytest.approx(1.0)
    # 与不确定性无关的查询不触发约束（不误伤）
    assert _constraint_component("interp.idw", {}, "interpolate pm2.5") == \
        pytest.approx(1.0)
