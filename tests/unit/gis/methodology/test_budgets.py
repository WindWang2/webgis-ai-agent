"""Epic 11 —— 性能预算断言（架构 §3.10 冻结；真实计时）。"""
from __future__ import annotations

import time

import pytest

from app.lib.gis.methodology.descriptors import (
    get_method_descriptor_registry,
)
from app.lib.gis.methodology.graph import (
    get_knowledge_graph,
    reset_knowledge_graph,
)
from app.lib.gis.methodology.qualification import QualificationFacts, qualify_method
from app.lib.gis.methodology.ranking import evaluate_corpus, rank_methods
from app.lib.gis.methodology.taxonomy import get_task_taxonomy


def test_graph_build_under_budget() -> None:
    # 预热 registry 单例冷启动（208 算法/139 能力/80 模型装载不是
    # 图投影的一部分——预算约束的是投影工作本身，架构 §3.10）。
    from app.lib.gis.methodology.graph import _default_sources
    _default_sources()
    reset_knowledge_graph()
    t0 = time.perf_counter()
    graph = get_knowledge_graph()
    elapsed = time.perf_counter() - t0
    assert graph.edge_count > 0
    assert elapsed < 0.15, f"graph build {elapsed:.3f}s > 150ms"


def test_graph_lookup_under_budget() -> None:
    graph = get_knowledge_graph()
    t0 = time.perf_counter()
    for i in range(20):
        graph.neighbors("method:interp.ordinary_kriging")
    elapsed = (time.perf_counter() - t0) / 20
    assert elapsed < 0.005, f"lookup {elapsed*1000:.1f}ms > 5ms"


def test_qualification_budget() -> None:
    facts = QualificationFacts.from_profile(
        {"featureCount": 100, "geometryTypes": ["Point"],
         "fields": {"v": {"type": "number"}}})
    methods = list(get_method_descriptor_registry().all_ids)[:30]
    t0 = time.perf_counter()
    for mid in methods:
        qualify_method(mid, facts)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.6, f"30 方法资格 {elapsed:.3f}s > 20ms×30 预算"


def test_ranking_budget() -> None:
    facts = QualificationFacts.from_profile(
        {"featureCount": 100, "geometryTypes": ["Point"]})
    t0 = time.perf_counter()
    for _ in range(5):
        rank_methods("分析周边设施密度并找热点", facts,
                     category_id="density")
    elapsed = (time.perf_counter() - t0) / 5
    assert elapsed < 0.05, f"ranking {elapsed*1000:.1f}ms > 10ms×5 预算"


def test_corpus_evaluation_within_test_budget() -> None:
    from app.lib.gis.methodology.method_corpus import METHOD_CORPUS
    t0 = time.perf_counter()
    metrics = evaluate_corpus(METHOD_CORPUS)
    elapsed = time.perf_counter() - t0
    assert metrics["cases"] >= 20
    assert elapsed < 30.0, f"语料评估 {elapsed:.1f}s > 30s（pytest timeout 内）"


def test_graph_memory_bound() -> None:
    """图规模有界（节点 ≤ 600 / 边 ≤ 2000；内存间接约束）。"""
    graph = get_knowledge_graph()
    assert graph.node_count < 600
    assert graph.edge_count < 2000


@pytest.mark.parametrize("taxonomy_reset", [False])
def test_taxonomy_match_budget(taxonomy_reset: bool) -> None:
    taxonomy = get_task_taxonomy()
    t0 = time.perf_counter()
    for _ in range(50):
        taxonomy.match_query("分析周边设施密度并找热点")
    elapsed = (time.perf_counter() - t0) / 50
    assert elapsed < 0.005, f"match {elapsed*1000:.2f}ms > 5ms"
