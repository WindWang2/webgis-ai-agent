"""GeoCompute 工具/API facade（Agent Plane 消费入口，ADR-0096 D1）。

把 ExecutionPlan 的 JSON 构造与同步执行收敛到一个稳定入口：工具、REST
与（未来）workflow 编译共用同一份真值，避免各调用方自行拼装契约。
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.geocompute.budgets import BudgetLimits, ResourceGovernor
from app.services.geocompute.errors import GeoComputeError
from app.services.geocompute.resource_counter import shared_counter
from app.services.geocompute.plan import (
    CrsExpectation,
    ExecutionNode,
    ExecutionPlan,
    ExecutionPolicyKind,
    LineageLink,
    NodeCategory,
    NodeReusePolicy,
    PayloadKind,
    ResourceBudget,
    ResourceClass,
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
        policy_kind = ExecutionPolicyKind(raw.get("policy", "in_process"))
        if policy_kind is ExecutionPolicyKind.DURABLE_JOB and category in (
            "raster_window_operation", "artifact_register",
        ):
            # durable 交接只承载 features/rows 载荷（session ref）；
            # raster_path 型输出 / 需在进程内解析输入的类别不支持。
            raise GeoComputeError(
                f"category '{category}' does not support durable_job policy "
                "(payload handoff is session-ref features/rows only)",
                details={"category": category,
                         "hint": "use in_process, or materialize first"},
            )
        retry = raw.get("retry") or {}
        produces_raw = raw.get("produces")
        accepts_raw = raw.get("accepts") or []
        rc_raw = raw.get("resource_class") or {}
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
                policy=policy_kind,
                reuse=NodeReusePolicy(raw.get("reuse", "allow")),
                retry=RetryPolicy(**retry) if retry else RetryPolicy(),
                deadline_s=raw.get("deadline_s"),
                cancellable=bool(raw.get("cancellable", True)),
                locality_hint=raw.get("locality_hint"),
                description=raw.get("description"),
                produces=PayloadKind(produces_raw) if produces_raw else None,
                accepts=[PayloadKind(a) for a in accepts_raw][:8],
                resource_class=ResourceClass(**rc_raw) if rc_raw else ResourceClass(),
                deterministic=bool(raw.get("deterministic", True)),
                upstream_fingerprints={
                    str(k): str(v)
                    for k, v in (raw.get("upstream_fingerprints") or {}).items()
                },
                lineage_inputs=[
                    LineageLink(**link) for link in (raw.get("lineage_inputs") or [])
                ][:16],
                evidence_schema={
                    str(k): str(v)
                    for k, v in (raw.get("evidence_schema") or {}).items()
                },
            )
        )
    budget_raw = dict(data.get("budget") or {})
    return ExecutionPlan(
        plan_id=str(data.get("plan_id", "plan")),
        nodes=nodes,
        budget=ResourceBudget(**budget_raw) if budget_raw else ResourceBudget(),
        description=data.get("description"),
    )


#: 生产 governor（评审 S-F7/A-F7：服务端准入不再依赖调用方自报预算）。
#: 层级作用域按既有身份真相挂载（ADR-0101 D10）：tenant ← caller.org_id、
#: project ← 显式 project_id、session ← 稳定哈希派生；执行作用域由
#: executor 创建/摘除。
GOVERNOR = ResourceGovernor(
    global_limits=BudgetLimits(max_rows=5_000_000, max_bytes=2 * 1024 * 1024 * 1024),
    # Wave 8 R2：可选跨进程 advisory 计数器。默认关闭（无 REDIS_URL +
    # WEBGIS_CROSS_PROCESS_GOVERNOR=1 时零 Redis 交互、语义与纯进程内
    # 完全一致）；开启后也只做尽力准入建议 —— L1 树始终是权威真相。
    cross_process=shared_counter(),
)

#: 层级并发槽位上界（有界、服务端红线；不做计费系统）。
GOVERNOR_TENANT_MAX_CONCURRENCY = 8
GOVERNOR_PROJECT_MAX_CONCURRENCY = 4
GOVERNOR_SESSION_MAX_CONCURRENCY = 4


def _caller_org_id(caller: Optional[dict[str, Any]]) -> Optional[str]:
    """caller → org_id（auth 真相；匿名哨兵折叠为 None）。"""
    if not caller:
        return None
    try:
        from app.core.auth import actor_ids

        _, org_id = actor_ids(caller)
        return str(org_id) if org_id else None
    except Exception:  # noqa: BLE001 - 身份解析失败按匿名处理
        return None


def run_plan_sync(
    plan: ExecutionPlan,
    *,
    session_id: Optional[str] = None,
    cancel_token: Optional[Any] = None,
    governor: Optional[ResourceGovernor] = None,
    caller: Optional[dict[str, Any]] = None,
    project_id: Optional[str] = None,
):
    """同步执行入口（工具/线程上下文用；REST 走 to_thread 同一函数）。

    默认接入进程级 ``GOVERNOR``：global 上限 + 层级作用域链
    ``tenant:{org} → project:{pid} → session:{sid} → execution``（ADR-0101
    D10 —— tenant/project 仅在既有身份真相存在时挂载；稳定哈希派生，
    幂等，execution 作用域在 run 内创建并在 finally 摘除）。

    ``caller``（auth user dict 或 None）原样穿透到执行器：目录项准入、
    复用键 owner 域与 run 归属都以它为准（SEC：数据平面内 authz 与
    跨用户复用隔离的身份来源）。
    """
    import hashlib

    from app.services.geocompute.budgets import ScopeKind

    gov = governor or GOVERNOR
    parent = "global:root"
    org_id = _caller_org_id(caller)
    if org_id:
        parent = gov.ensure_scope(
            parent, ScopeKind.TENANT, hashlib.sha1(
                org_id.encode(), usedforsecurity=False).hexdigest()[:12],
            limits=BudgetLimits(max_concurrency=GOVERNOR_TENANT_MAX_CONCURRENCY),
        )
    if project_id:
        parent = gov.ensure_scope(
            parent, ScopeKind.PROJECT, hashlib.sha1(
                str(project_id).encode(), usedforsecurity=False).hexdigest()[:12],
            limits=BudgetLimits(max_concurrency=GOVERNOR_PROJECT_MAX_CONCURRENCY),
        )
    if session_id:
        sid = hashlib.sha1(session_id.encode(), usedforsecurity=False).hexdigest()[:12]
        parent = gov.ensure_scope(
            parent, ScopeKind.SESSION, sid,
            limits=BudgetLimits(max_concurrency=GOVERNOR_SESSION_MAX_CONCURRENCY),
        )
    from app.services.geocompute.executor import engine

    return engine.execute_plan(
        plan, session_id=session_id, caller=caller, cancel_token=cancel_token,
        governor=gov, governor_parent_path=parent,
    )
