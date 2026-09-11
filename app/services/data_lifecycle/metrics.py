"""Data Lifecycle V9 —— 生命周期观测指标（prometheus_client 约定）。

label 取值均为封闭词表（5 类对象 kind / 动作结果），无开放 cardinality。
"""
from __future__ import annotations

import logging

from prometheus_client import Counter

logger = logging.getLogger(__name__)

#: assess 枚举登记（kind 封闭 ×5）。
LC_ASSESS_OBJECTS = Counter(
    "data_lifecycle_assess_objects_total",
    "Lifecycle objects enumerated per assess run.",
    ["kind"],
)

#: 候选回收量（只统计、不等于已删；删除另有 gc_plan 结果指标）。
LC_ASSESS_CANDIDATES = Counter(
    "data_lifecycle_assess_candidates_total",
    "Reclamation candidates identified per assess run (observation only).",
    ["kind"],
)

#: 复活（引用即预热）次数。
LC_REVIVE = Counter(
    "data_lifecycle_revive_total",
    "Objects revived (last-used refreshed on reference).",
    ["kind"],
)

#: GC 计划创建/执行/回滚（无 label：计划维度不进指标，防 cardinality）。
LC_PLANS_CREATED = Counter(
    "data_lifecycle_gc_plans_created_total",
    "GC plans created (entered pending_approval).",
)
LC_GC_EXECUTED_OBJECTS = Counter(
    "data_lifecycle_gc_executed_objects_total",
    "Objects moved to staging by executed GC plans.",
)
LC_GC_EXECUTED_BYTES = Counter(
    "data_lifecycle_gc_executed_bytes_total",
    "Bytes attributed to executed GC plans (estimated).",
)
LC_GC_ROLLED_BACK = Counter(
    "data_lifecycle_gc_rolled_back_objects_total",
    "Objects restored from staging by rollbacks.",
)


def observe_assess(kind: str, total: int, total_bytes: int, candidates: int) -> None:
    try:
        k = str(kind)[:32]
        LC_ASSESS_OBJECTS.labels(kind=k).inc(max(0, int(total)))
        LC_ASSESS_CANDIDATES.labels(kind=k).inc(max(0, int(candidates)))
    except Exception:  # noqa: BLE001 — 指标绝不打断评估
        logger.debug("data_lifecycle metrics inc failed", exc_info=True)


def observe_revive(kind: str) -> None:
    try:
        LC_REVIVE.labels(kind=str(kind)[:32]).inc()
    except Exception:  # noqa: BLE001
        logger.debug("data_lifecycle metrics inc failed", exc_info=True)


def observe_plan_created(candidates: int, candidate_bytes: int) -> None:
    try:
        LC_PLANS_CREATED.inc()
        _ = candidates, candidate_bytes
    except Exception:  # noqa: BLE001
        logger.debug("data_lifecycle metrics inc failed", exc_info=True)


def observe_executed(candidates: int, candidate_bytes: int) -> None:
    try:
        LC_GC_EXECUTED_OBJECTS.inc(max(0, int(candidates)))
        LC_GC_EXECUTED_BYTES.inc(max(0, int(candidate_bytes)))
    except Exception:  # noqa: BLE001
        logger.debug("data_lifecycle metrics inc failed", exc_info=True)


def observe_rolled_back(restored: int) -> None:
    try:
        LC_GC_ROLLED_BACK.inc(max(0, int(restored)))
    except Exception:  # noqa: BLE001
        logger.debug("data_lifecycle metrics inc failed", exc_info=True)


__all__ = [
    "observe_assess",
    "observe_revive",
    "observe_plan_created",
    "observe_executed",
    "observe_rolled_back",
]
