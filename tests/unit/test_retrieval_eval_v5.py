"""Retrieval V5 open-loop query→tool 评测门（ADR-0118 决策 D7）。

语料：``app/evaluation/retrieval_eval_corpus.py`` —— 66 条人工金标
（direct 30 / near_duplicate 6 对 / hard_negative 12 / ambiguous 12，
zh+en），与 lexical 索引、capability 反查**不同源**，查询为口语措辞。

口径：开环（只给 user_message，剥离 planner 的 active_capabilities
提示）；precision@1 在去 CORE 常驻工具的检索排序段上计算。

门限：全部由实测值保守下修后钉死（测量见各常量注释；分析见 PR）：
- 实测 p@1 0.6515 / r@5 0.8093 / r@10 0.8674；
- 实测 invalid_selection_rate 0.3333（hard_negative 0.5 —— 语料真实
  暴露「纯词面检索易撞陷阱工具」，本门不粉饰，作为 V5 基线记录）；
- fallback 0 / tier3_leak 0。
"""
import pytest

from app.evaluation.retrieval_eval_corpus import (
    MIN_EVAL_CORPUS_SIZE,
    build_retrieval_eval_corpus,
    get_retrieval_eval_corpus,
    retrieval_eval_report,
)

#: 实测 p@1 0.6515 → 钉 0.60
PINNED_PRECISION_AT_1 = 0.60
#: 实测 r@5 0.8093 → 钉 0.75
PINNED_RECALL_AT_5 = 0.75
#: 实测 r@10 0.8674 → 钉 0.82
PINNED_RECALL_AT_10 = 0.82
#: 实测 invalid 0.3333 → 上限钉 0.40（暴露线，不是达标线）
PINNED_INVALID_SELECTION_MAX = 0.40
PINNED_FALLBACK_MAX = 0.05


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
    assert kinds == {"direct", "near_duplicate", "hard_negative", "ambiguous"}
    # 歧义类合法集 ≥2；hard_negative 必带禁选集
    for c in cases:
        if c.kind == "ambiguous":
            assert len(c.valid_tools) >= 2, c.case_id
        if c.kind in ("near_duplicate", "hard_negative"):
            assert c.must_not_select, c.case_id
            assert set(c.must_not_select).isdisjoint(c.expected_tools), c.case_id


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

    # 分 kind 视角：direct 必须显著优于 hard_negative（语料区分度自检）
    by_kind = report.by_kind
    assert by_kind["direct"]["precision_at_1"] > \
        by_kind["hard_negative"]["precision_at_1"]
    # 歧义类不产生 invalid（valid 集合法则上不撞 must_not）
    assert by_kind["ambiguous"]["invalid_selection_rate"] == 0.0


def test_report_to_dict_shape(registry):
    report = retrieval_eval_report(
        registry, get_retrieval_eval_corpus()[:6])
    d = report.to_dict()
    for key in ("cases", "precision_at_1", "recall_at_5", "recall_at_10",
                "invalid_selection_rate", "fallback_rate", "tier3_leak",
                "by_kind"):
        assert key in d
