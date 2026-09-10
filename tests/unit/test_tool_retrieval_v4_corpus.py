"""Tool Retrieval V4 离线语料门（ADR-0104 决策 5 / audit gap #10）。

语料：app/evaluation/retrieval_corpus.py —— 从 306 意图语料（golden +
case_matrix）与 conformance 家族表确定性生成的 ≥2000 条 query→期望工具集
案例（zh 口语包装 ×3 + conformance×scope 直陈层）。无 LLM、无随机。

门限：全部由本仓实测值保守下修后钉死（测量记录见各常量注释）：
- 实测（full 3028 / sample 757，默认上下文 = 证据门下 V3 精确行为）：
  recall@5 0.4508/0.4541，recall@10 0.9883/0.9881，recall@30 1.0/1.0，
  tier3_leak 0，avg schema bytes 21602/22362（max 24568 ≤ 24KB 预算）。
- 实测（V4 消融 workflow_stage=analysis，rerank 激活）：
  recall@5 0.4375，recall@10 0.9533/0.9539，recall@30 0.9961/0.9959，
  tier3_leak 0。
"""
import pytest

from app.evaluation.retrieval_corpus import (
    MIN_CORPUS_SIZE,
    corpus_source_counts,
    expected_tools_for,
    get_retrieval_corpus,
    get_retrieval_sample,
)
from app.evaluation.runtime_metrics import surface_retrieval_report

#: 实测 recall@5 ∈ [0.438, 0.454]（多工具注册后）→ 钉 0.43
PINNED_RECALL_AT_5 = 0.43
#: 实测 recall@10 ∈ [0.9881, 0.9883] → 钉 0.98
PINNED_RECALL_AT_10 = 0.98
#: 实测 recall@30 = 1.0 → 钉 0.995
PINNED_RECALL_AT_30 = 0.995
#: V4 消融（phase rerank 激活）实测 r@5 0.4375 / r@10 0.9539 / r@30 0.9959
PINNED_ABLATION_RECALL_AT_5 = 0.42
PINNED_ABLATION_RECALL_AT_10 = 0.94
PINNED_ABLATION_RECALL_AT_30 = 0.99
#: 实测 avg schema bytes 21602（full）/ 22362（sample），max 24568 → 钉 24000
PINNED_AVG_SCHEMA_BYTES = 24000
_DEFAULT_BYTE_BUDGET = 24576  # 与 tier-2 既有 24KB 预算同门


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


@pytest.fixture(scope="module")
def corpus():
    return get_retrieval_corpus()


def test_corpus_size_determinism_and_layering(corpus):
    assert len(corpus) >= MIN_CORPUS_SIZE, "size contract: >= 2000 cases"
    again = get_retrieval_corpus()
    assert [c.case_id for c in corpus] == [c.case_id for c in again]
    assert all(a == b for a, b in zip(corpus, again)), "build must be deterministic"
    assert len({c.case_id for c in corpus}) == len(corpus), "ids must be unique"
    counts = corpus_source_counts(corpus)
    assert set(counts) == {"intent-306", "intent-paraphrase", "conformance-direct"}
    assert counts["intent-306"] >= 250
    assert counts["intent-paraphrase"] >= 800
    assert counts["conformance-direct"] >= 1500
    assert all(c.query.strip() for c in corpus)
    assert all(c.expected_capabilities for c in corpus)


def test_expected_tools_resolution_single_truth(registry, corpus):
    """期望工具集经 AlgorithmRegistry 反查 + registry 可见/tier 过滤解析。"""
    sample = corpus[:50]
    resolved = 0
    for case in sample:
        tools = expected_tools_for(case, registry)
        for name in tools:
            desc = registry.descriptor(name)
            assert desc.model_visible and int(desc.tier) < 3
        resolved += bool(tools)
    assert resolved >= 40, "capability→tool resolution must cover the corpus"


def test_retrieval_gate_metrics(registry, corpus):
    sample = get_retrieval_sample(800)
    assert 700 <= len(sample) <= 800
    rep = surface_retrieval_report(
        registry, sample, k_max=30, top_ks=(5, 10, 30),
        byte_sample=60, byte_budget=_DEFAULT_BYTE_BUDGET,
    )
    d = rep.as_dict()
    assert d["tier3_leak"] == 0, "tier-3 must never enter any projected surface"
    assert d["budget_violations"] == 0, "schema byte budget must be respected"
    assert d["recall_at_k"]["5"] >= PINNED_RECALL_AT_5, d
    assert d["recall_at_k"]["10"] >= PINNED_RECALL_AT_10, d
    assert d["recall_at_k"]["30"] >= PINNED_RECALL_AT_30, d
    assert 10 <= d["avg_active_tools"] <= 30
    assert 0 < d["avg_schema_bytes"] <= PINNED_AVG_SCHEMA_BYTES, d
    assert d["max_schema_bytes"] <= _DEFAULT_BYTE_BUDGET


def test_retrieval_gate_v4_rerank_ablation(registry, corpus):
    """V4 rerank 激活（workflow phase 证据）不得跌破实测下修门限。"""
    sample = get_retrieval_sample(800)
    rep = surface_retrieval_report(
        registry, sample, k_max=30, top_ks=(5, 10, 30),
        byte_sample=0,
        context_overrides={"workflow_stage": "analysis"},
    )
    d = rep.as_dict()
    assert d["tier3_leak"] == 0
    assert d["recall_at_k"]["5"] >= PINNED_ABLATION_RECALL_AT_5, d
    assert d["recall_at_k"]["10"] >= PINNED_ABLATION_RECALL_AT_10, d
    assert d["recall_at_k"]["30"] >= PINNED_ABLATION_RECALL_AT_30, d


def test_gate_report_deterministic(registry, corpus):
    sub = get_retrieval_corpus()[:40]
    kw = dict(k_max=30, top_ks=(5, 10, 30), byte_sample=0)
    a = surface_retrieval_report(registry, sub, **kw).as_dict()
    b = surface_retrieval_report(registry, sub, **kw).as_dict()
    assert a == b


def test_default_context_corpus_run_is_v3_identical(registry, corpus):
    """证据门不变式（语料规模版）：默认上下文（无 phase/预算/会话信号）下
    V4 与 kill-switch 关闭在 306 意图层逐位一致（抽样 80 例深比较）。"""
    from app.services.chat.tool_surface_v3 import DynamicToolSurface, ToolSelectionContext

    surface = DynamicToolSurface(registry)
    intent = [c for c in corpus if c.source in ("intent-306", "intent-paraphrase")][:80]
    assert intent
    monkey_env = pytest.MonkeyPatch()
    monkey_env.setenv("GIS_TOOL_RETRIEVAL_V4", "1")
    v4 = [
        surface.select(ToolSelectionContext(
            user_message=c.query,
            active_capabilities=c.expected_capabilities,
            k_max=30,
        )).as_dict()
        for c in intent
    ]
    monkey_env.setenv("GIS_TOOL_RETRIEVAL_V4", "0")
    v3 = [
        surface.select(ToolSelectionContext(
            user_message=c.query,
            active_capabilities=c.expected_capabilities,
            k_max=30,
        )).as_dict()
        for c in intent
    ]
    monkey_env.undo()
    assert v4 == v3
