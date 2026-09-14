"""Goal Satisfaction 验收语料测试（ADR-0183 G6/G9）。

锁点：
- 语料 ≥100 案例（任务书 G9 门槛）；
- **false_pass_rate == 0**（最高优先级指标 —— 「地图好看但任务没完成」
  类案例永不 PASS）；
- 全语料逐条通过（期望全局裁决 / 逐需求状态 / 信号）；
- G6 八大反事实注入以命名案例在语料内逐条锁定。
"""
from __future__ import annotations

from app.evaluation.goal_satisfaction_corpus import (
    build_goal_satisfaction_cases,
    corpus_metrics,
    run_full_corpus,
    run_goal_satisfaction_case,
)

# G6 任务书八大反事实注入 → 语料命名案例（case_id 一一对应）。
G6_COUNTERFACTUAL_IDS = [
    "GC-cf1-empty-result",             # 工具都 200 但数据为空
    "GC-cf2-map-pass-comparison-missing",  # 地图 PASS 但缺 district comparison
    "GC-cf3-chart-filter-differs",     # chart 有但过滤条件不同
    "GC-cf4-visual-pass-scope-wrong",  # visual PASS 但 scope 错
    "GC-cf5-stale-artifact",           # stale artifact
    "GC-cf6-fallback-non-comparable",  # fallback source non-comparable
    "GC-cf7-export-stale-revision",    # export 存在但旧 revision
    "GC-cf8-user-hid-required-view",   # user 显式隐藏 required optional view
]


def test_corpus_has_at_least_100_cases():
    cases = build_goal_satisfaction_cases()
    assert len(cases) >= 100
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"


def test_g6_counterfactuals_all_present_and_anti_pass():
    cases = {c.case_id: c for c in build_goal_satisfaction_cases()}
    for cid in G6_COUNTERFACTUAL_IDS:
        assert cid in cases, f"missing G6 counterfactual: {cid}"
        case = cases[cid]
        if cid != "GC-cf8-user-hid-required-view":
            # CF8 是 user-wins 披露案例（不阻断）；其余七个必须反 PASS。
            assert case.must_not_pass, cid


def test_full_corpus_green_and_zero_false_pass():
    metrics = run_full_corpus()
    assert metrics["total"] >= 100
    assert metrics["false_pass_rate"] == 0.0, metrics["false_pass_case_ids"]
    assert metrics["failed"] == 0, metrics["failed_case_ids"]


def test_corpus_metrics_shape():
    cases = build_goal_satisfaction_cases()
    results = [run_goal_satisfaction_case(c) for c in cases[:5]]
    metrics = corpus_metrics(results)
    assert metrics["total"] == 5
    assert set(metrics["by_family"]).issubset(
        {c.family for c in cases[:5]})
