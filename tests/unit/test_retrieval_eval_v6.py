"""Retrieval V6 open-loop query→tool 评测门（ADR-0119 决策 D1/D2/D3）。

语料：``app/evaluation/retrieval_eval_corpus.py`` —— 358 条人工金标
（direct 209 / near_duplicate 26 对 / hard_negative 52 / ambiguous 31 /
out_of_scope 14；zh+en+中英混排），与 lexical 索引、capability 反查
**不同源**，查询为口语措辞。

口径：开环（只给 user_message，剥离 planner 的 active_capabilities
提示）；precision@1 在去 CORE 常驻工具的检索排序段上计算；评测钉死
纯确定性通道（embedding 为 additive 生产增强，独立 skipif 测试）。

同语料对照基线（GIS_TOOL_RETRIEVAL_V6=0，即 master 词法行为）：
- p@1 0.4944 / r@5 0.6731 / r@10 0.7289 / invalid 0.2500；
V6 hybrid 实测：p@1 0.5587 / r@5 0.7523 / r@10 0.8059 / invalid 0.2500
（p@1 +6.4pp、r@5 +7.9pp、r@10 +7.7pp，invalid 持平 —— 无免费午餐成
本）。门限全部由实测值保守下修/上修后钉死。
"""
import pytest

from app.evaluation.retrieval_eval_corpus import (
    MIN_EVAL_CORPUS_SIZE,
    build_retrieval_eval_corpus,
    get_retrieval_eval_corpus,
    retrieval_eval_report,
)

#: 实测 0.5587 → 钉 0.53（必须显著高于同语料 V5 基线 0.4944 ——
#: 若 kill switch 意外关闭本门必炸，混合通道不得静默失效）
PINNED_PRECISION_AT_1 = 0.53
#: 实测 0.7523 → 钉 0.72
PINNED_RECALL_AT_5 = 0.72
#: 实测 0.8059 → 钉 0.78
PINNED_RECALL_AT_10 = 0.78
#: 实测 0.25 → 上限钉 0.30（与 V5 基线持平；语料真实暴露面，不粉饰）
PINNED_INVALID_SELECTION_MAX = 0.30
PINNED_FALLBACK_MAX = 0.02
#: V6 弃权校准（实测 oos 0.2143 / over 0.0 / ece 0.3095）
PINNED_OOS_ABSTENTION_MIN = 0.15
PINNED_OVER_ABSTENTION_MAX = 0.05
PINNED_CALIBRATION_ECE_MAX = 0.40


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def test_corpus_size_and_kinds():
    cases = get_retrieval_eval_corpus()
    assert len(cases) >= MIN_EVAL_CORPUS_SIZE
    kinds = {c.kind for c in cases}
    assert kinds == {
        "direct", "near_duplicate", "hard_negative", "ambiguous",
        "out_of_scope", "paraphrase",
    }
    # 歧义类合法集 ≥2；hard_negative/near_duplicate 必带禁选集；
    # out_of_scope 必无期望工具（registry 无此能力）
    for c in cases:
        if c.kind == "ambiguous":
            assert len(c.valid_tools) >= 2, c.case_id
        if c.kind in ("near_duplicate", "hard_negative"):
            assert c.must_not_select, c.case_id
            assert set(c.must_not_select).isdisjoint(c.expected_tools), c.case_id
        if c.kind == "out_of_scope":
            assert not c.expected_tools and not c.valid_tools, c.case_id


def test_corpus_deterministic():
    a = build_retrieval_eval_corpus()
    b = build_retrieval_eval_corpus()
    assert [c.case_id for c in a] == [c.case_id for c in b]
    assert a == b


def test_gold_tools_exist_in_registry(registry):
    """金标工具必须真实存在且可见（tier<3）—— 金标不指向幻影工具。"""
    for c in get_retrieval_eval_corpus():
        for name in set(c.valid_tools) | set(c.must_not_select):
            desc = registry.descriptor(name)  # KeyError → 失败
            assert int(desc.tier) < 3, (c.case_id, name)


def test_open_loop_metrics_pinned(registry):
    report = retrieval_eval_report(registry, get_retrieval_eval_corpus())
    assert report.cases >= MIN_EVAL_CORPUS_SIZE
    assert report.precision_at_1 >= PINNED_PRECISION_AT_1
    assert report.recall_at_5 >= PINNED_RECALL_AT_5
    assert report.recall_at_10 >= PINNED_RECALL_AT_10
    assert report.invalid_selection_rate <= PINNED_INVALID_SELECTION_MAX
    assert report.fallback_rate <= PINNED_FALLBACK_MAX
    assert report.tier3_leak == 0
    # V6 弃权/校准（低置信度不乱选 + 置信度是可审计的概率语义）
    assert report.oos_abstention_rate >= PINNED_OOS_ABSTENTION_MIN
    assert report.over_abstention_rate <= PINNED_OVER_ABSTENTION_MAX
    assert report.calibration_ece <= PINNED_CALIBRATION_ECE_MAX
    assert 0.0 < report.mean_confidence < 1.0

    # 分 kind 视角：direct 必须显著优于 hard_negative（语料区分度自检）
    by_kind = report.by_kind
    assert by_kind["direct"]["precision_at_1"] > \
        by_kind["hard_negative"]["precision_at_1"]
    # 歧义类不产生 invalid（valid 集合法则上不撞 must_not）
    assert by_kind["ambiguous"]["invalid_selection_rate"] == 0.0
    # oos 类 p@1 恒 0（无合法工具），弃权率分 kind 可见
    assert by_kind["out_of_scope"]["precision_at_1"] == 0.0


def test_v5_kill_switch_preserves_lexical_baseline(registry, monkeypatch):
    """GIS_TOOL_RETRIEVAL_V6=0 → 词法基线行为（hybrid 全通道关闭）。

    基线门钉在同语料实测 0.4944 的下修线 —— 防基线静默劣化。
    """
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V6", "0")
    cases = [
        c for c in get_retrieval_eval_corpus()
        if c.kind in ("direct", "hard_negative")
    ]
    report = retrieval_eval_report(registry, cases)
    assert report.precision_at_1 >= 0.50  # 子集基线实测 ~0.55
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V6", "1")
    report_v6 = retrieval_eval_report(registry, cases)
    assert report_v6.precision_at_1 >= report.precision_at_1


def test_report_to_dict_shape(registry):
    report = retrieval_eval_report(
        registry, get_retrieval_eval_corpus()[:6])
    d = report.to_dict()
    for key in ("cases", "precision_at_1", "recall_at_5", "recall_at_10",
                "invalid_selection_rate", "fallback_rate", "tier3_leak",
                "by_kind", "oos_abstention_rate", "over_abstention_rate",
                "calibration_ece", "mean_confidence"):
        assert key in d
