"""QueryEvidence 组装（ADR-0094 §1/§43）。

证据随 QueryResult.metadata 携带（不建第二 lineage store），供
Map Product / Workflow lineage（ADR-0092）与 agent 决策消费。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

from app.services.data_fabric.query.models import (
    DatasetVersion,
    QueryEvidence,
    QueryPlan,
    QuerySpecV2,
    ResultMode,
)
from app.services.data_fabric.query.planner import dataset_version_from_descriptor


def new_query_id() -> str:
    return f"q_{uuid.uuid4().hex[:12]}"


def build_evidence(
    plan: QueryPlan,
    *,
    started_at: Optional[float] = None,
    result_count: Optional[int] = None,
    total_matching: Optional[int] = None,
    truncated: bool = False,
    rows_fetched: Optional[int] = None,
    rows_returned: Optional[int] = None,
    http_requests: Optional[int] = None,
    db_queries: Optional[int] = None,
    cache_hit: Optional[bool] = None,
    retry_count: Optional[int] = None,
    extra_fallbacks: Optional[list] = None,
    dataset_version: Optional[DatasetVersion] = None,
) -> QueryEvidence:
    duration = (time.monotonic() - started_at) if started_at is not None else None
    fallbacks = list(extra_fallbacks or [])
    if plan.fallback_reason:
        fallbacks.append(plan.fallback_reason)
    return QueryEvidence(
        query_id=new_query_id(),
        dataset_id=plan.dataset_id,
        source_id=plan.source_id,
        dataset_fingerprint=plan.dataset_fingerprint,
        query_fingerprint=plan.query_fingerprint,
        normalized_query=plan.normalized_query,
        pushdowns={
            "bbox": plan.pushed_spatial,
            "filter": bool(plan.pushed_filters),
            "projection": plan.pushed_projection,
            "aggregation": plan.pushed_aggregation,
            "sort": plan.pushed_sort,
        },
        local_operations=plan.local_filters,
        result_count=result_count,
        total_matching=total_matching,
        truncated=truncated,
        execution_duration_s=round(duration, 4) if duration is not None else None,
        fallbacks=fallbacks,
        warnings=plan.warnings,
        dataset_version=dataset_version,
        rows_fetched=rows_fetched,
        rows_returned=rows_returned,
        http_requests=http_requests,
        db_queries=db_queries,
        cache_hit=cache_hit,
        retry_count=retry_count,
    )


def evidence_for_descriptor(descriptor: Any, fingerprint: Optional[str]) -> DatasetVersion:
    return dataset_version_from_descriptor(descriptor, fingerprint)


def pushdown_ratio(evidence: QueryEvidence) -> Optional[float]:
    """pushdown_ratio = rows_returned / rows_fetched（远端传输利用率）。

    优秀执行：数据库 1M 行、返回 20 个区县计数 → ratio ≈ 0.00002。
    """
    if evidence.rows_fetched and evidence.rows_returned is not None:
        if evidence.rows_fetched <= 0:
            return None
        return round(evidence.rows_returned / evidence.rows_fetched, 6)
    return None


__all__ = [
    "new_query_id",
    "build_evidence",
    "evidence_for_descriptor",
    "pushdown_ratio",
]
