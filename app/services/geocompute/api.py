"""GeoCompute 工具/API facade（Agent Plane 消费入口，ADR-0096 D1）。

把 ExecutionPlan 的 JSON 构造与同步执行收敛到一个稳定入口：工具、REST
与（未来）workflow 编译共用同一份真值，避免各调用方自行拼装契约。
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.geocompute.errors import GeoComputeError
from app.services.geocompute.plan import (
    CrsExpectation,
    ExecutionNode,
    ExecutionPlan,
    ExecutionPolicyKind,
    NodeCategory,
    NodeReusePolicy,
    ResourceBudget,
    ResourceEstimate,
    RetryPolicy,
)

_WIRED_CATEGORIES = {
    "source_scan", "query", "filter", "aggregate", "spatial_join",
    "attribute_join", "vector_operation", "raster_window_operation",
    "interpolation", "materialize", "artifact_register",
}


def build_plan_from_json(data: dict[str, Any]) -> ExecutionPlan:
    """JSON dict → ExecutionPlan（严格校验；未知字段/类别 → typed 错误）。"""
    if not isinstance(data, dict):
        raise GeoComputeError("plan payload must be an object")
    nodes: list[ExecutionNode] = []
    for raw in data.get("nodes") or []:
        if not isinstance(raw, dict):
            raise GeoComputeError("each node must be an object")
        category = str(raw.get("category", ""))
        if category not in _WIRED_CATEGORIES:
            raise GeoComputeError(
                f"unknown node category '{category}'",
                details={"wired": sorted(_WIRED_CATEGORIES)},
            )
        retry = raw.get("retry") or {}
        nodes.append(
            ExecutionNode(
                node_id=str(raw["node_id"]),
                category=NodeCategory(category),
                operation=str(raw.get("operation", "")),
                inputs=[str(s) for s in raw.get("inputs") or []],
                dataset_fingerprints={
                    str(k): str(v) for k, v in (raw.get("dataset_fingerprints") or {}).items()
                },
                parameters=dict(raw.get("parameters") or {}),
                crs=CrsExpectation(**raw["crs"]) if raw.get("crs") else None,
                estimate=ResourceEstimate(**raw["estimate"]) if raw.get("estimate") else None,
                policy=ExecutionPolicyKind(raw.get("policy", "in_process")),
                reuse=NodeReusePolicy(raw.get("reuse", "allow")),
                retry=RetryPolicy(**retry) if retry else RetryPolicy(),
                deadline_s=raw.get("deadline_s"),
                cancellable=bool(raw.get("cancellable", True)),
                locality_hint=raw.get("locality_hint"),
                description=raw.get("description"),
            )
        )
    budget_raw = dict(data.get("budget") or {})
    return ExecutionPlan(
        plan_id=str(data.get("plan_id", "plan")),
        nodes=nodes,
        budget=ResourceBudget(**budget_raw) if budget_raw else ResourceBudget(),
        description=data.get("description"),
    )


def run_plan_sync(
    plan: ExecutionPlan,
    *,
    session_id: Optional[str] = None,
    cancel_token: Optional[Any] = None,
):
    """同步执行入口（工具/线程上下文用；REST 走 to_thread 同一函数）。"""
    from app.services.geocompute.executor import engine

    return engine.execute_plan(plan, session_id=session_id, cancel_token=cancel_token)
