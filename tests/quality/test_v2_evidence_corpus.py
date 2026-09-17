"""V2 evidence grounding corpus tests（D8）。

证据情境语料回放：正证明四件套 / narrative 永不 supported / 缺证据
fail-closed / 跨租户拒绝 / stale 传播 / 硬矛盾 —— 全部走生产
ClaimStore + verify_claim 管线（语料是 #1335 热区的纯消费者）。
"""
from __future__ import annotations

import pytest

from app.evaluation.evidence_corpus import build_evidence_corpus
from app.evaluation.runner import GISBenchmarkRunner


@pytest.fixture()
def runner():
    return GISBenchmarkRunner()


def test_corpus_covers_all_scenarios():
    cases = build_evidence_corpus()
    scenarios = {ev.scenario for c in cases for ev in c.expected_evidence}
    assert scenarios >= {
        "supported", "unsupported", "missing_evidence", "cross_tenant",
        "stale", "stale_propagation", "contradicted",
    }
    assert len(cases) >= 7


async def test_full_corpus_replay(runner):
    for r in await runner.run(build_evidence_corpus()):
        assert r.passed, f"{r.case_id}: {r.failures[:3]}"
        assert r.metrics.get("evidence_grounding_correct") is True


async def test_positive_proof_required_rows(runner):
    cases = [
        c for c in build_evidence_corpus()
        if any(ev.require_positive_proof for ev in c.expected_evidence)
    ]
    assert cases
    for r in await runner.run(cases):
        assert r.metrics.get("evidence_positive_proof_ok") is True


async def test_narrative_and_missing_never_supported(runner):
    """红旗不变量：narrative / 缺证据 / 跨租户情境的裁决必须是 unsupported。"""
    cases = {
        c.id: c for c in build_evidence_corpus()
    }
    for cid in (
        "EV-narrative-never-supported", "EV-missing-evidence-unsupported",
        "EV-cross-tenant-failclosed",
    ):
        r = (await runner.run([cases[cid]]))[0]
        assert r.passed, f"{cid}: {r.failures[:3]}"
