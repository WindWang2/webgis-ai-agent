"""Semantic Retrieval V6 结构性性能预算（ADR-0119 W13）。

纪律：结构性预算优先，不以脆弱的单次 wall-clock 作唯一 gate。

- hybrid 信号通道工作量有界（扩展词 ≤24、别名 ≤12、方法论工具 ≤12、
  词表封闭）—— 语料全量 358 条开环评测在预算内完成（wall-clock 上界
  仅作回归哨兵，阈值宽松）；
- trace 增量读跳过旧段（O(新段) 而非 O(全量)）—— 由
  test_trace_store_v6.py 的文件访问计数证明；
- recovery ledger 每 session ≤256 条 LRU —— 由
  test_recovery_ledger_v6.py 的有界测试证明。
"""
import time

import pytest

pytestmark = [pytest.mark.perf]


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def test_hybrid_signals_table_bounds():
    """词表封闭有界（防词表互相引爆）。"""
    from app.services.chat.semantic_retrieval import (
        _ASCII_SYNONYMS,
        _CAPABILITY_ALIASES,
        _MAX_CAPABILITY_ALIASES,
        _MAX_EXPANDED_TERMS,
        _MAX_METHODOLOGY_TOOLS,
        _NEGATION_PHRASE_EXPANSIONS,
        _ZH_SYNONYMS,
    )

    assert len(_ZH_SYNONYMS) + len(_ASCII_SYNONYMS) <= 400
    assert len(_CAPABILITY_ALIASES) <= 150
    assert len(_NEGATION_PHRASE_EXPANSIONS) <= 32
    assert _MAX_EXPANDED_TERMS == 24
    assert _MAX_CAPABILITY_ALIASES == 12
    assert _MAX_METHODOLOGY_TOOLS == 12


def test_full_corpus_eval_within_budget(registry):
    """358 条开环评测（每条一次完整 hybrid select）预算 ≤ 60s。

    结构性上界：每次 select = 词法 2 趟（base+扩展）× O(terms×tools)
    + 封闭表 boosts —— 与语料规模线性。实测 ~3.5s（CI 单核裕量 15x）。"""
    from app.evaluation.retrieval_eval_corpus import (
        get_retrieval_eval_corpus,
        retrieval_eval_report,
    )

    cases = get_retrieval_eval_corpus()
    assert len(cases) >= 350
    t0 = time.monotonic()
    report = retrieval_eval_report(registry, cases)
    elapsed = time.monotonic() - t0
    assert report.cases == len(cases)
    assert elapsed < 60.0, f"全量评测超预算: {elapsed:.1f}s"


def test_single_select_latency_sanity(registry):
    """单次 select 有界（复合查询构造 + hybrid 通道不引入数量级开销）。"""
    from app.services.chat.tool_surface_v3 import (
        DynamicToolSurface,
        ToolSelectionContext,
    )

    surface = DynamicToolSurface(registry)
    surface._semantic = None  # noqa: SLF001 — 纯确定性通道口径
    worst = 0.0
    for q in (
        "给学校图层加500米缓冲区看看覆盖范围",
        "用克里金把土壤重金属铺成网格并给估计方差然后导出出版级专题图",
        "分析哪些片区热点在增强哪些在减弱并生成监测报告",
    ):
        t0 = time.monotonic()
        surface.select(ToolSelectionContext(user_message=q))
        worst = max(worst, time.monotonic() - t0)
    assert worst < 2.0, f"单次 select 超预算: {worst:.2f}s"
