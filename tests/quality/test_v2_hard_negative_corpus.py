"""V2 hard-negative corpus regression tests（D9）。

对抗面回放：wrong-AOI 反虚构锚、point-vs-choropleth 诚实降级、
count-vs-rate 语义分流、no-capability 反编造、多轮指代消解
（runner 真实求值绑定，非自证元数据）。
"""
from __future__ import annotations

import pytest

from app.evaluation.hard_negative_corpus import build_hard_negative_corpus
from app.evaluation.runner import GISBenchmarkRunner


@pytest.fixture()
def runner():
    return GISBenchmarkRunner()


def test_corpus_size_and_adversarial_pairs():
    cases = build_hard_negative_corpus()
    assert len(cases) >= 10
    tags = {t for c in cases for t in c.tags}
    for adversarial in (
        "wrong-aoi", "count-vs-rate", "point-vs-choropleth",
        "no-capability", "coreference", "synonym-competition",
    ):
        assert adversarial in tags, f"adversarial face {adversarial} missing"


async def test_full_corpus_replay(runner):
    results = await runner.run(build_hard_negative_corpus())
    for r in results:
        assert r.passed, f"{r.case_id}: {r.failures[:3]}"


async def test_wrong_aoi_never_fabricates_scope(runner):
    """覆盖外 AOI：scope 必须留空（known=False）；良性孪生必须正常绑定。"""
    cases = {c.id: c for c in build_hard_negative_corpus()}
    outside = (await runner.run([cases["HN-aoi-outside-coverage"]]))[0]
    assert outside.passed, outside.failures[:2]
    assert outside.plan_evidence.get("scope", {}).get("name") == ""
    twin = (await runner.run([cases["HN-aoi-benign-twin"]]))[0]
    assert twin.passed, twin.failures[:2]
    assert twin.plan_evidence.get("scope", {}).get("name") == "成都市"


async def test_count_and_rate_select_different_recipes(runner):
    cases = {c.id: c for c in build_hard_negative_corpus()}
    count_r = (await runner.run([cases["HN-pvc-count-choropleth-zh"]]))[0]
    rate_r = (await runner.run([cases["HN-cvr-rate-density-zh"]]))[0]
    assert count_r.passed and rate_r.passed
    assert (
        count_r.plan_evidence["recipe_id"] != rate_r.plan_evidence["recipe_id"]
    ), "count vs rate must select different recipes"


async def test_coreference_carry_and_rebind_evaluated(runner):
    """指代消解由前序轮真实解析驱动：carry 与 re-bind 都被求值。"""
    cases = {c.id: c for c in build_hard_negative_corpus()}
    carry = (await runner.run([cases["HN-turn-coreference-carry"]]))[0]
    assert carry.passed, carry.failures[:3]
    assert carry.metrics.get("coreference_binding_ok") is True
    rebind = (await runner.run([cases["HN-turn-coreference-subject-scope-switch"]]))[0]
    assert rebind.passed, rebind.failures[:3]
    bindings = rebind.plan_evidence["turns"]["bindings"]
    assert bindings["scope"] == "重庆", "turn-2 must re-bind scope"
    assert bindings["subject"] == "小学", "subject must carry over from turn 1"


async def test_determinism_double_run(runner):
    corpus = build_hard_negative_corpus()
    a = await runner.run(corpus)
    b = await runner.run(corpus)
    for ra, rb in zip(a, b):
        assert ra.case_id == rb.case_id
        assert ra.passed == rb.passed
        assert ra.failures == rb.failures
