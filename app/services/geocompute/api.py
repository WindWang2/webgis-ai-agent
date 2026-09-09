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

# Wave-11（audit 08 §6.2.4）：``lineage_inputs`` 被解析但从未被填充 —— 执行级
# lineage 投影因此断链。构建侧从节点参数里**可证**的源身份键派生 LineageLink
# （key 名 → 身份族）；无证据键 → 诚实为空，绝不虚构身份。
_LINEAGE_PARAM_HINTS: tuple[tuple[str, str], ...] = (
    ("dataset_id", "dataset_version"),
    ("dataset_ids", "dataset_version"),
    ("source_ref", "dataset_version"),
    ("source_refs", "dataset_version"),
    ("ref_id", "ref"),
    ("ref_ids", "ref"),
    ("artifact_id", "artifact"),
    ("artifact_ids", "artifact"),
)


def _derive_lineage_inputs(raw: dict) -> list[LineageLink]:
    """参数中的源身份 → LineageLink（≤16 条；缺证诚实为空）。"""
    params = raw.get("parameters")
    if not isinstance(params, dict):
        return []
    links: list[LineageLink] = []
    for key, kind in _LINEAGE_PARAM_HINTS:
        value = params.get(key)
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for item in items:
            if isinstance(item, str) and item.strip():
                links.append(LineageLink(ref_id=item.strip()[:256], kind=kind))
                if len(links) >= 16:
                    return links
    return links


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
                ][:16]
                # Wave-11：显式声明优先；缺省时从参数里的可证源身份派生
                #（不参与语义指纹 —— 指纹值域不变）。
                or _derive_lineage_inputs(raw),
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
    run_id: Optional[str] = None,
    yield_check: Optional[Any] = None,
    owner_scope_override: Optional[str] = None,
):
    """同步执行入口（工具/线程上下文用；REST 走 to_thread 同一函数）。

    默认接入进程级 ``GOVERNOR``：global 上限 + 层级作用域链
    ``tenant:{org} → project:{pid} → session:{sid} → execution``（ADR-0101
    D10 —— tenant/project 仅在既有身份真相存在时挂载；稳定哈希派生，
    幂等，execution 作用域在 run 内创建并在 finally 摘除）。

    ``caller``（auth user dict 或 None）原样穿透到执行器：目录项准入、
    复用键 owner 域与 run 归属都以它为准（SEC：数据平面内 authz 与
    跨用户复用隔离的身份来源）。

    V6（additive）：``run_id``/``yield_check`` 供 cluster coordinator 路径
    注入持久 run 身份与安全点探针；直跑路径不传，行为不变。
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
        run_id=run_id, yield_check=yield_check,
        owner_scope_override=owner_scope_override,
    )
