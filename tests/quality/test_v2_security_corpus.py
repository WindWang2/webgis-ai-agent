"""V2 security / prompt-injection corpus tests（D5 security tier）。

双向 ratchet：contained 行必须全绿；known-escalation 行必须被检测器以
升级签名**检出**（静默通过 = 检测器回归）。检出签名本身被锁定。
"""
from __future__ import annotations

import pytest

from app.evaluation.runner import GISBenchmarkRunner
from app.evaluation.security_corpus import build_security_corpus


@pytest.fixture()
def runner():
    return GISBenchmarkRunner()


def test_corpus_covers_both_containment_truths():
    cases = build_security_corpus()
    truths = {c.security_expectation.expected_contained for c in cases}
    assert truths == {True, False}


async def test_contained_rows_pass(runner):
    cases = [
        c for c in build_security_corpus()
        if c.security_expectation.expected_contained
    ]
    assert len(cases) >= 3
    for r in await runner.run(cases):
        assert r.passed, f"{r.case_id}: {r.failures[:3]}"
        assert r.metrics.get("injection_contained") is True


async def test_known_escalations_are_detected_not_silent(runner):
    """known-escalation：runner 必须失败且给出升级/containment 签名。"""
    cases = [
        c for c in build_security_corpus()
        if not c.security_expectation.expected_contained
    ]
    assert len(cases) >= 2
    for r in await runner.run(cases):
        assert not r.passed, (
            f"{r.case_id}: known-escalation silently passed — detector regression"
        )
        assert r.metrics.get("injection_contained") is False
        signatures = tuple(r.failures)
        assert any(
            s.startswith(("injection escalation", "injection containment broken"))
            for s in signatures
        ), f"{r.case_id}: escalation detected without canonical signature: {signatures}"


async def test_twin_diff_makes_escalation_deterministic(runner):
    corpus = build_security_corpus()
    first = await runner.run(corpus)
    second = await runner.run(corpus)
    for a, b in zip(first, second):
        assert a.passed == b.passed
        assert a.failures == b.failures
