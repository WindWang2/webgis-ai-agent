"""Data Quality V9 —— 规则评估观测指标（prometheus_client 约定）。

先例 ``app/core/auth_metrics.py``：Counter 注册进默认 REGISTRY，随
instrumentator /metrics 暴露；fire-and-forget（指标绝不打断业务）。

cardinality 说明：``rule`` label 取值是封闭词表（rules.RULE_TYPES，16 类）
∪ 前缀化 rule_id（dq.*），有界不开放 —— 与 auth_metrics 的「无 label 防
cardinality」不冲突（那边 label 值不可枚举，这里值封闭）。
"""
from __future__ import annotations

import logging

from prometheus_client import Counter

logger = logging.getLogger(__name__)

#: 规则评估次数（label: rule_id × 判定结果，均封闭词表）。
DQ_RULE_EVALUATIONS = Counter(
    "data_quality_rule_evaluations_total",
    "Quality rule evaluations by rule and outcome.",
    ["rule", "outcome"],
)

#: 规则评估耗时（秒；有界 label）。
DQ_RULE_SECONDS = Counter(
    "data_quality_rule_evaluations_seconds_total",
    "Time spent evaluating quality rules (bounded closed-vocabulary labels).",
    ["rule"],
)

#: durable job 路径的报告复用（幂等命中）。
DQ_REPORT_REUSE = Counter(
    "data_quality_report_reuse_total",
    "Durable quality evaluations short-circuited by an existing completed report.",
)

#: durable job 路径完成的报告（按 overall 结论）。
DQ_JOB_REPORTS = Counter(
    "data_quality_job_reports_total",
    "Completed durable quality evaluations by overall status.",
    ["overall_status"],
)


def observe_rule(rule_id: str, outcome: str, duration_ms: int) -> None:
    """单规则观测（fire-and-forget；指标绝不打断评估）。"""
    try:
        rule = str(rule_id)[:64]
        DQ_RULE_EVALUATIONS.labels(rule=rule, outcome=str(outcome)[:16]).inc()
        DQ_RULE_SECONDS.labels(rule=rule).inc(max(0, duration_ms) / 1000.0)
    except Exception:  # noqa: BLE001
        logger.debug("data_quality metrics inc failed", exc_info=True)


def observe_reuse() -> None:
    try:
        DQ_REPORT_REUSE.inc()
    except Exception:  # noqa: BLE001
        logger.debug("data_quality metrics inc failed", exc_info=True)


def observe_job_completed(overall_status: str) -> None:
    try:
        DQ_JOB_REPORTS.labels(overall_status=str(overall_status)[:16]).inc()
    except Exception:  # noqa: BLE001
        logger.debug("data_quality metrics inc failed", exc_info=True)


__all__ = [
    "observe_rule",
    "observe_reuse",
    "observe_job_completed",
]
