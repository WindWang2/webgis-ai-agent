"""Evidence Grounding Benchmark Corpus（V2；GOAL 里程碑 7/10）。

证据情境语料：每个案例声明一组 ``ExpectedEvidence`` 情境（supported /
unsupported / missing_evidence / cross_tenant / stale / stale_propagation /
contradicted），runner 的 evidence tier 以**生产** ClaimStore + verify_claim
管线构建情境并裁决 —— 语料锁的是裁决面（正证明不变量：缺证据永不升为
supported；narrative 永不自动变数值；跨租户 fail-closed）。

ExpectedEvidence 是 #1335 热区（evidence_claim/verify.py）的**纯消费者**：
fail-closed 语义变化会以语料 diff 形式浮出 —— 这正是想要的回归语义。
全部离线、确定、零 LLM。
"""
from __future__ import annotations

from typing import List

from app.evaluation.case import ExpectedEvidence, GISBenchmarkCase


def build_evidence_corpus() -> List[GISBenchmarkCase]:
    """证据情境语料（按 id 排序 + 构建期守卫）。

    query 仅作案例载体（evidence tier 不消费 plan 断言）；plan_only=True。
    """
    cases: List[GISBenchmarkCase] = [
        GISBenchmarkCase(
            id="EV-positive-proof-full", name="完整正证明四件套 → supported",
            group="benchmark-evidence", query="青羊区小学数量",
            description="stat + dataset_version + method + uncertainty 齐备 → "
                        "SUPPORTED 且 positive_proof=True（正证明不变量）",
            plan_only=True, tags=["evidence", "supported", "positive-proof"],
            expected_evidence=[
                ExpectedEvidence(
                    claim_type="count", subject="青羊区", scenario="supported",
                    require_positive_proof=True,
                ),
            ],
        ),
        GISBenchmarkCase(
            id="EV-narrative-never-supported", name="narrative 永不自动 supported",
            group="benchmark-evidence", query="青羊区小学很多",
            description="无数值证据的描述性结论 → UNSUPPORTED（LLM 叙述不作为"
                        "数值权威的红旗不变量）",
            plan_only=True, tags=["evidence", "unsupported", "narrative"],
            expected_evidence=[
                ExpectedEvidence(claim_type="narrative", scenario="unsupported"),
            ],
        ),
        GISBenchmarkCase(
            id="EV-missing-evidence-unsupported", name="证据缺失 → unsupported",
            group="benchmark-evidence", query="青羊区小学数量",
            description="supporting refs 全缺失 → UNSUPPORTED；缺证据永不"
                        "升为 supported（fail-closed 红线）",
            plan_only=True, tags=["evidence", "unsupported", "missing-ref"],
            expected_evidence=[
                ExpectedEvidence(claim_type="count", scenario="missing_evidence"),
            ],
        ),
        GISBenchmarkCase(
            id="EV-cross-tenant-failclosed", name="跨租户证据 fail-closed",
            group="benchmark-evidence", query="青羊区小学数量",
            description="tenant-b 证据对 tenant-a 校验 → UNSUPPORTED"
                        "（租户隔离；静默通过 = P0）",
            plan_only=True, tags=["evidence", "unsupported", "tenancy"],
            expected_evidence=[
                ExpectedEvidence(claim_type="count", scenario="cross_tenant"),
            ],
        ),
        GISBenchmarkCase(
            id="EV-stale-evidence", name="证据过期 → stale",
            group="benchmark-evidence", query="青羊区小学数量",
            description="正证明情境 + 证据标记 stale → STALE"
                        "（freshness 进入裁决）",
            plan_only=True, tags=["evidence", "stale", "freshness"],
            expected_evidence=[
                ExpectedEvidence(claim_type="count", scenario="stale"),
            ],
        ),
        GISBenchmarkCase(
            id="EV-stale-propagation-descendant", name="数据集更新 → 后代 claim stale",
            group="benchmark-evidence", query="青羊区小学数量",
            description="数据集版本 v1 更新 → 依赖其后代 claim 全部 STALE"
                        "（不急重算，只宣告受影响面）",
            plan_only=True, tags=["evidence", "stale", "propagation"],
            expected_evidence=[
                ExpectedEvidence(claim_type="count", scenario="stale_propagation"),
            ],
        ),
        GISBenchmarkCase(
            id="EV-contradiction-hard", name="同轴双 highest → 硬矛盾",
            group="benchmark-evidence", query="哪个区小学密度最高",
            description="同方法同 scope 轴两个 highest（不同 subject）→ 双双 "
                        "CONTRADICTED + contradicts 边（矛盾检测器裁决面）",
            plan_only=True, tags=["evidence", "contradicted", "contradiction"],
            expected_evidence=[
                ExpectedEvidence(claim_type="density", scenario="contradicted"),
            ],
        ),
        GISBenchmarkCase(
            id="EV-mixed-panel", name="混合情境面板（一次多情境）",
            group="benchmark-evidence", query="青羊区小学统计",
            description="单案例聚合四情境（supported / unsupported / stale / "
                        "cross_tenant）—— EvidenceGrounding 指标聚合面",
            plan_only=True, tags=["evidence", "mixed", "aggregation"],
            expected_evidence=[
                ExpectedEvidence(
                    claim_type="count", scenario="supported",
                    require_positive_proof=True,
                ),
                ExpectedEvidence(claim_type="narrative", scenario="unsupported"),
                ExpectedEvidence(claim_type="rate", scenario="stale"),
                ExpectedEvidence(claim_type="count", scenario="cross_tenant"),
            ],
        ),
    ]
    cases.sort(key=lambda c: c.id)
    # 构建期守卫：id 唯一 / 全部声明证据契约 / 七情境全覆盖
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), f"duplicate evidence case ids: {ids}"
    all_scenarios = {
        ev.scenario for c in cases for ev in c.expected_evidence
    }
    assert all_scenarios >= {
        "supported", "unsupported", "missing_evidence", "cross_tenant",
        "stale", "stale_propagation", "contradicted",
    }, f"evidence scenario coverage hole: {all_scenarios}"
    for c in cases:
        assert c.expected_evidence, f"{c.id} missing expected_evidence"
        assert c.plan_only
    return cases
