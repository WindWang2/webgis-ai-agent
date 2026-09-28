"""语料 schema 完整性（G08）：语料即评测基准 —— id 卫生、闭合词表、规模锚。

覆盖入口：golden_cases / case_matrix / conformance / quality_corpus /
runtime_corpus / scenario_corpus / methodology_corpus / retrieval_corpus /
retrieval_eval_corpus。Benchmark-Factory 语料族（anti_claim、security、
skill_policy、hard_negative、evidence、chaos、closed_loop、cartography
轴、mission）见 test_benchmark_factory_corpora.py。
"""
from __future__ import annotations


def _assert_unique_ids(ids: list) -> None:
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate corpus ids: {sorted(dupes)[:5]}"


def test_golden_corpus_schema():
    from app.evaluation.case_matrix import build_matrix_cases
    from app.evaluation.golden_cases import GOLDEN_CASES, get_all_cases

    assert len(GOLDEN_CASES) == 33
    _assert_unique_ids([c.id for c in GOLDEN_CASES])
    for c in GOLDEN_CASES:
        assert c.query.strip(), c.id
    # get_all_cases = golden + matrix（消费入口的总量契约）。
    all_cases = get_all_cases()
    assert all_cases[: len(GOLDEN_CASES)] == GOLDEN_CASES
    assert len(all_cases) == len(GOLDEN_CASES) + len(build_matrix_cases())


def test_matrix_cases_and_expected_total_consistency():
    from app.evaluation.case_matrix import build_matrix_cases, get_expected_total
    from app.evaluation.golden_cases import GOLDEN_CASES

    matrix = build_matrix_cases()
    assert matrix
    _assert_unique_ids([c.id for c in matrix])
    # 矩阵案例克隆 golden 的 group 语义（M001 → poi 等），非单一 "form"。
    golden_groups = {c.group for c in matrix}
    assert golden_groups and not golden_groups - {
        "poi", "raster", "network", "od", "repair", "semantics",
        "interpolation", "decision", "negative", "form", "scope", "compound",
    }
    assert get_expected_total(len(GOLDEN_CASES)) == len(GOLDEN_CASES) + len(matrix)


def test_conformance_corpus_family_hygiene_and_slicing():
    from app.evaluation.conformance import (
        CONFORMANCE_FAMILIES,
        build_conformance_corpus,
    )

    assert len(CONFORMANCE_FAMILIES) == 60
    cases = build_conformance_corpus()
    assert len(cases) >= 20000
    _assert_unique_ids([c.id for c in cases])
    assert all(c.group.startswith("conformance-") for c in cases)
    family = CONFORMANCE_FAMILIES[0]
    slice_ = build_conformance_corpus(family_ids=[family.family_id])
    assert slice_ and {c.group for c in slice_} == {family.group}


def test_quality_scenario_corpus_shape():
    from app.evaluation.quality_corpus import (
        build_quality_scenario_corpus,
        quality_corpus_reference_count,
    )

    cases = build_quality_scenario_corpus()
    assert len(cases) >= 6000
    _assert_unique_ids([c.id for c in cases])
    assert all(c.group.startswith("quality-") for c in cases)
    assert all(c.scenario_kind for c in cases)
    assert quality_corpus_reference_count() > 0


def test_runtime_corpus_tiers():
    from app.evaluation.runtime_corpus import (
        build_e2e_scenario_corpus,
        build_runtime_corpus,
        build_runtime_execution_corpus,
    )

    assert len(build_runtime_corpus()) >= 3000
    assert len(build_e2e_scenario_corpus()) >= 100
    exec_cases = build_runtime_execution_corpus()
    assert len(exec_cases) >= 50
    _assert_unique_ids([c.case_id for c in exec_cases])


def test_scenario_corpus_sampling_and_coverage_report():
    from app.evaluation.scenario_corpus import (
        EVAL_SAMPLE_SIZE,
        MIN_CORPUS_SIZE,
        build_scenario_corpus,
        coverage_report,
        sample_cases,
    )

    assert MIN_CORPUS_SIZE >= 1000 and EVAL_SAMPLE_SIZE >= 100
    cases = build_scenario_corpus(max_cases=60)
    assert len(cases) == 60
    _assert_unique_ids([c.case_id for c in cases])
    sample = sample_cases(cases, 10)
    assert len(sample) == 10
    assert {c.case_id for c in sample} <= {c.case_id for c in cases}
    report = coverage_report(cases=cases)
    assert report["total"] == 60
    assert {"families", "coverage_gaps", "registry_missing"} <= set(report)


def test_methodology_corpus_families_closed():
    from app.evaluation.methodology_corpus import (
        METHODOLOGY_CORPUS,
        METHODOLOGY_FAMILIES_COVERAGE,
        corpus_cases,
    )

    cases = corpus_cases()
    assert len(cases) == len(METHODOLOGY_CORPUS) == 20
    _assert_unique_ids([c.case_id for c in cases])
    assert METHODOLOGY_FAMILIES_COVERAGE == {c.family for c in cases}


def test_retrieval_corpus_hygiene_and_source_partition():
    from app.evaluation.retrieval_corpus import (
        MIN_CORPUS_SIZE,
        corpus_source_counts,
        get_retrieval_corpus,
    )

    corpus = get_retrieval_corpus()
    assert len(corpus) >= MIN_CORPUS_SIZE
    _assert_unique_ids([c.case_id for c in corpus])
    counts = corpus_source_counts(corpus)
    assert sum(counts.values()) == len(corpus)
    assert set(counts) >= {"conformance-direct", "intent-306", "intent-paraphrase"}


def test_retrieval_eval_corpus_closed_kinds():
    from app.evaluation.retrieval_eval_corpus import get_retrieval_eval_corpus

    cases = get_retrieval_eval_corpus()
    assert len(cases) >= 590
    _assert_unique_ids([c.case_id for c in cases])
    assert {c.kind for c in cases} <= {
        "direct",
        "near_duplicate",
        "hard_negative",
        "ambiguous",
        "out_of_scope",
        "paraphrase",
    }
    direct = [c for c in cases if c.kind == "direct"]
    assert direct and all(c.expected_tools for c in direct)
