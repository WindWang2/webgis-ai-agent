"""Epic 11 —— 方法知识图 / provenance / descriptors 单测。"""
from __future__ import annotations

import pytest

from app.lib.gis.methodology.descriptors import (
    QUALIFICATION_DIMENSIONS,
    get_method_descriptor_registry,
)
from app.lib.gis.methodology.graph import (
    EDGE_RELATIONS,
    GraphBuildError,
    GraphSources,
    get_knowledge_graph,
    reset_knowledge_graph,
)
from app.lib.gis.methodology.provenance import (
    MIN_ACTIONABLE_CONFIDENCE,
    PROVENANCE_LEDGER,
    content_fingerprint as provenance_fingerprint,
    get_provenance,
    validate_ledger,
)
from app.lib.gis.methodology.taxonomy import reset_task_taxonomy


@pytest.fixture()
def graph():
    reset_knowledge_graph()
    reset_task_taxonomy()
    return get_knowledge_graph()


# ── provenance ──────────────────────────────────────────────────────────

def test_provenance_ledger_valid() -> None:
    assert validate_ledger() == []
    assert provenance_fingerprint() != ""


def test_provenance_no_llm_source_kind() -> None:
    """Non-goal 的机器可读表达：LLM 生成不是合法来源。"""
    for record in PROVENANCE_LEDGER.values():
        assert "llm" not in str(record.source_kind).lower()
        assert record.confidence >= MIN_ACTIONABLE_CONFIDENCE or \
            record.source_kind != "curated"


def test_provenance_get() -> None:
    assert get_provenance("prov.taxonomy.alignment") is not None
    assert get_provenance("no.such") is None


# ── descriptors ─────────────────────────────────────────────────────────

def test_descriptors_cover_all_v4_methods() -> None:
    """V4 每个候选方法必须有增强条目（伴随义务；中央校验同样锁定）。"""
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )
    reg = get_method_descriptor_registry()
    methods = get_methodology_registry()
    for fam in methods.families():
        for m in fam.candidate_methods:
            assert reg.has(m.method_id), m.method_id
    assert reg.validate() == []


def test_continuous_measure_scoping() -> None:
    """R1-F4：连续假设按方法声明——克里金族 True、指示克里金 False。"""
    reg = get_method_descriptor_registry()
    assert reg.get("interp.ordinary_kriging").assumes_continuous_measure is True
    assert reg.get("interp.idw").assumes_continuous_measure is True
    assert reg.get("interp.indicator_kriging").assumes_continuous_measure is False
    assert reg.get("stats.point_cluster_dbscan").assumes_continuous_measure is False


def test_descriptor_invalid_when_dimensions_legal() -> None:
    reg = get_method_descriptor_registry()
    for mid in reg.all_ids:
        d = reg.get(mid)
        assert d is not None
        for cond in d.invalid_when + d.degraded_when:
            assert cond.dimension in QUALIFICATION_DIMENSIONS


# ── graph ───────────────────────────────────────────────────────────────

def test_graph_builds_with_all_relations(graph) -> None:
    assert graph.node_count > 300
    assert graph.edge_count > 500
    for relation in EDGE_RELATIONS:
        if relation == "consumes_artifact":
            continue  # 预留给 viz bridge 消费侧边
        assert graph.edges_of(relation=relation), relation


def test_graph_fingerprint_and_cache(graph) -> None:
    fp = graph.fingerprint
    assert len(fp) == 64
    assert get_knowledge_graph() is graph  # fingerprint-keyed 缓存命中
    reset_knowledge_graph()
    assert get_knowledge_graph().fingerprint == fp


def test_graph_self_diff_empty(graph) -> None:
    diff = graph.diff(graph)
    assert diff.empty


def test_graph_dangling_reference_fails_closed() -> None:
    """悬空引用 = build 失败（不产半截图）。"""
    from app.lib.gis.methodology.taxonomy import TaskCategoryDescriptor
    from app.lib.gis.methodology.taxonomy import TaskTaxonomy

    bad_cat = TaskCategoryDescriptor(
        category_id="spatial_distribution", label_zh="x",
        ontology_task_ids=("no.such_task",),
        methodology_family_ids=("descriptive_mapping",),
    )
    good = get_knowledge_graph()
    sources = GraphSources(
        taxonomy=TaskTaxonomy((bad_cat,)),
        descriptors=get_method_descriptor_registry(),
        ontology=_default_ontology(),
        methodology=_default_methodology(),
        capabilities=_default_capabilities(),
        algorithms=_default_algorithms(),
        artifacts=_default_artifacts(),
        map_models=_default_models(),
        components=_default_components(),
    )
    with pytest.raises(GraphBuildError):
        from app.lib.gis.methodology.graph import build_graph
        build_graph(sources)
    # 原图不受失败构建影响
    assert get_knowledge_graph() is good


def test_graph_bounded_projection(graph) -> None:
    d = graph.to_bounded_dict()
    assert d["node_count"] == graph.node_count
    assert "relation_counts" in d
    assert len(d["fingerprint"]) == 64


# ── 注入默认值（延迟对接 canonical 单例）────────────────────────────────

def _default_ontology():
    from app.services.gis_harness.gis_ontology import get_task_ontology
    return get_task_ontology()


def _default_methodology():
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )
    return get_methodology_registry()


def _default_capabilities():
    from app.lib.gis.capability_registry import get_capability_registry
    return get_capability_registry()


def _default_algorithms():
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    return get_algorithm_registry()


def _default_artifacts():
    from app.lib.gis.artifacts import get_artifact_type_registry
    return get_artifact_type_registry()


def _default_models():
    from app.lib.cartography.model_library import get_map_model_registry
    return get_map_model_registry()


def _default_components():
    from app.lib.cartography.component_registry import get_component_registry
    return get_component_registry()
