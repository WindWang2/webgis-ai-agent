"""V2 Benchmark Manifest / Registry tests（D3）。

清单完整性：跨语料 id 唯一（GISBenchmarkCase 语料）、计数稳定、
version_hash 确定性、iter_all_cases 枚举与清单计数一致。
"""
from __future__ import annotations

from app.evaluation.index import (
    corpus_manifest,
    corpus_version_hash,
    iter_all_cases,
)


def test_manifest_is_nonempty_and_deterministic():
    m1 = corpus_manifest()
    m2 = corpus_manifest()
    assert m1 == m2, "manifest must be deterministic across calls"
    assert len(m1) >= 10, f"expected >=10 registered corpora, got {len(m1)}"


def test_manifest_covers_v2_capability_domains():
    m = corpus_manifest()
    for name in (
        "skill_policy", "hard_negative", "security_injection",
        "evidence_grounding", "mission_scenario", "cartography_axes",
    ):
        assert name in m, f"V2 corpus {name} missing from manifest"
        assert m[name]["count"] >= 5, f"{name} too thin: {m[name]['count']}"


def test_iter_all_cases_matches_manifest_counts():
    m = corpus_manifest()
    cases = list(iter_all_cases())
    expected = sum(
        reg["count"] for name, reg in m.items()
        if name in (
            "anti_claim_plan", "golden_matrix", "conformance",
            "evidence_grounding", "hard_negative", "quality_scenario",
            "security_injection", "skill_policy",
        )
    )
    assert len(cases) == expected


def test_iter_all_cases_ids_unique_across_corpus():
    ids = [c.id for c in iter_all_cases()]
    assert len(ids) == len(set(ids)), "cross-corpus duplicate case ids"


def test_version_hash_changes_when_content_changes():
    from app.evaluation.case import GISBenchmarkCase
    from app.evaluation.skill_policy_corpus import build_skill_policy_corpus

    cases = build_skill_policy_corpus()
    base = corpus_version_hash(cases)
    assert base == corpus_version_hash(cases), "hash must be stable"
    mutated = [c.model_copy(update={"name": c.name + "!"}) for c in cases]
    assert corpus_version_hash(mutated) != base, "hash must track content drift"
    assert isinstance(GISBenchmarkCase, type)


def test_manifest_counts_match_builders():
    from app.evaluation.hard_negative_corpus import build_hard_negative_corpus
    from app.evaluation.mission_corpus import build_mission_corpus

    m = corpus_manifest()
    assert m["hard_negative"]["count"] == len(build_hard_negative_corpus())
    assert m["mission_scenario"]["count"] == len(build_mission_corpus())
