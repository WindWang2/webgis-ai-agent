"""GeoCompute 执行平面 REST（ADR-0096，additive）。

计划校验/指纹、执行、run 状态查询。执行是同步核心，统一 to_thread
卸载（与 project workflow 路由同一模式），绝不阻塞事件循环。
计划执行证据是**有界摘要**；节点载荷只经 ref/产物通道出平面。

安全语义（SEC 评审）：
- ``/plans/execute``、``/runs/*``、``/plans/drift-check`` 强制认证
  （无/坏 Bearer → 401）；``/plans/validate`` 保持可选认证 —— 纯 CPU
  校验，只暴露指纹/波次等派生信息，不触达数据与目录。
- 执行请求的 ``session_id`` 若已存在 Conversation 行，必须属于当前
  调用者（与 data_fabric 物化路由同一 ``authorize_session_write`` 判定；
  陌生会话 → 404，不泄漏存在性）。
- caller 身份贯穿执行器：目录项准入（与 data_fabric 同一租户谓词）、
  节点复用键 owner 域、run 读隔离（他人 run → 404）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import (
    get_current_user,
    get_current_user_optional,
    get_owner_token,
    require_admin,
)
from app.services.geocompute import BudgetExceededError, GeoComputeError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/geocompute", tags=["GeoCompute / 执行平面"])


class ExecutionNodeIn(BaseModel):
    node_id: str
    category: str
    operation: str = ""
    inputs: list[str] = Field(default_factory=list)
    dataset_fingerprints: Dict[str, str] = Field(default_factory=dict)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    crs: Optional[Dict[str, Any]] = None
    estimate: Optional[Dict[str, Any]] = None
    policy: str = "in_process"
    reuse: str = "allow"
    retry: Dict[str, Any] = Field(default_factory=dict)
    deadline_s: Optional[float] = None
    cancellable: bool = True
    locality_hint: Optional[str] = None
    description: Optional[str] = None
    # Wave-11（audit 08 §6.2.4）：到既有 Artifact/DatasetVersion 身份的
    # lineage 边（{ref_id, kind}，≤16）—— 与 api.build_plan_from_json 同一
    # 契约；缺省时构建侧从参数里的可证源身份诚实派生（或为空）。
    lineage_inputs: list[Dict[str, str]] = Field(default_factory=list)


class ExecutionPlanIn(BaseModel):
    plan_id: str
    nodes: list[ExecutionNodeIn] = Field(default_factory=list)
    budget: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class ExecutePlanRequest(BaseModel):
    plan: ExecutionPlanIn
    session_id: Optional[str] = None
    # V6（cluster submit，additive）：优先级（0/5/10）与项目归属（公平/账本键）
    priority: int = 5
    project_id: Optional[str] = None


def _plan_from_request(data: ExecutionPlanIn):
    from app.services.geocompute.plan import (
        CrsExpectation,
        ExecutionNode,
        ExecutionPlan,
        NodeCategory,
        ExecutionPolicyKind,
        NodeReusePolicy,
        ResourceBudget,
        ResourceEstimate,
        RetryPolicy,
    )

    nodes = []
    for n in data.nodes:
        nodes.append(
            ExecutionNode(
                node_id=n.node_id,
                category=NodeCategory(n.category),
                operation=n.operation,
                inputs=n.inputs,
                dataset_fingerprints=n.dataset_fingerprints,
                parameters=n.parameters,
                crs=CrsExpectation(**n.crs) if n.crs else None,
                estimate=ResourceEstimate(**n.estimate) if n.estimate else None,
                policy=ExecutionPolicyKind(n.policy),
                reuse=NodeReusePolicy(n.reuse),
                retry=RetryPolicy(**n.retry) if n.retry else RetryPolicy(),
                deadline_s=n.deadline_s,
                cancellable=n.cancellable,
                locality_hint=n.locality_hint,
                description=n.description,
                lineage_inputs=n.lineage_inputs[:16],
            )
        )
    return ExecutionPlan(
        plan_id=data.plan_id,
        nodes=nodes,
        budget=ResourceBudget(**data.budget) if data.budget else ResourceBudget(),
        description=data.description,
    )


def _plan_fingerprint(data: ExecutionPlanIn) -> str:
    return _plan_from_request(data).graph_fingerprint()


def _run_response(run: Any, owner_scope: Optional[str]) -> Dict[str, Any]:
    """run 摘要 + Wave-11 附加证据（additive）。

    ``lineage``：无载荷 lineage 投影（节点身份/指纹/摘要，绝无
    features/geojson/geometry）；``reproducibility``：可复现判定块。两者
    fail-open：内存注册表与终态证据快照都缺席时省略（诚实缺省）。
    """
    payload = run.model_dump()
    try:
        from app.services.geocompute.executor import engine

        extras = engine.get_run_extras(run.run_id, owner_scope=owner_scope)
    except Exception:  # noqa: BLE001 - 附加证据读取绝不影响主应答
        extras = {}
    if extras:
        if isinstance(extras.get("lineage"), list):
            payload["lineage"] = extras["lineage"]
        if isinstance(extras.get("reproducibility"), dict):
            payload["reproducibility"] = extras["reproducibility"]
    return payload


def _authorize_session_write_sync(
    session_id: str,
    user: Dict[str, Any],
    owner_token: Optional[str],
) -> None:
    """执行前校验 session_id 的写归属（data_fabric._require_existing_session_owner
    的镜像；在 to_thread 工作线程内跑同步 ORM）。

    Conversation 行不存在 → 允许（首写创建，与首条聊天消息同一语义）；
    存在则必须 user_id 匹配（匿名行须 X-Session-Token 匹配，legacy
    NULL/NULL fail closed）→ 否则 404（不区分「不存在」与「他人会话」）。
    """
    from app.core.auth import actor_ids, authorize_session_write
    from app.core.database import SessionLocal
    from app.models.db_model import Conversation

    uid, _ = actor_ids(user)
    with SessionLocal() as db:
        conv = db.query(Conversation).filter(Conversation.id == session_id).first()
        if not authorize_session_write(conv, uid, owner_token):
            raise HTTPException(status_code=404, detail="Session not found")


@router.post("/plans/validate", tags=["GeoCompute / 执行平面"])
async def validate_execution_plan(
    plan_in: ExecutionPlanIn,
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """校验执行图并返回确定性指纹（不执行）。

    安全说明（SEC 评审）：保持**可选认证** —— 纯 CPU 校验，无目录/数据
    访问，响应只含指纹、波次与已接线类别等派生信息；未认证调用不构成
    信息泄漏面。
    """
    from app.services.geocompute import graph

    plan = _plan_from_request(plan_in)
    try:
        graph.validate_plan(plan)
    except GeoComputeError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())
    return {
        "plan_id": plan.plan_id,
        "graph_fingerprint": plan.graph_fingerprint(),
        "node_fingerprints": {n.node_id: n.semantic_fingerprint() for n in plan.nodes},
        "waves": graph.topo_wave_order(plan),
        "wired_categories": __import__(
            "app.services.geocompute.ops", fromlist=["wired_categories"]
        ).wired_categories(),
    }


@router.post("/plans/execute", tags=["GeoCompute / 执行平面"])
async def execute_execution_plan(
    body: ExecutePlanRequest,
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """执行执行图（同步卸载到工作线程；预算/取消/deadline 全程生效）。

    强制认证（无/坏 Bearer → 401）。``session_id`` 归属校验与执行同一
    工作线程顺序执行（校验先于任何节点运行）。

    DIST（round1）：REST 执行与工具路径共用 ``run_plan_sync`` —— 服务端
    ``GOVERNOR`` 层级准入（tenant/session 作用域 + 全局上限）对两条入口
    一视同仁；此前本路由直连 ``engine.execute_plan`` 完全绕过治理。
    """
    from app.services.geocompute import graph
    from app.services.geocompute.api import run_plan_sync

    plan = _plan_from_request(body.plan)
    try:
        graph.validate_plan(plan)
    except GeoComputeError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())

    def _run():
        if body.session_id:
            _authorize_session_write_sync(body.session_id, user, owner_token)
        return run_plan_sync(
            plan, session_id=body.session_id, caller=dict(user)
        )

    try:
        run = await asyncio.to_thread(_run)
    except BudgetExceededError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())
    except GeoComputeError as exc:
        raise HTTPException(status_code=500, detail=exc.to_dict())
    from app.services.geocompute.executor import owner_scope_for

    return _run_response(run, owner_scope_for(dict(user)))


@router.post("/plans/runs", status_code=202, tags=["GeoCompute / Cluster Runtime V6"])
async def submit_execution_plan(
    body: ExecutePlanRequest,
    user: Dict[str, Any] = Depends(get_current_user),
    owner_token: Optional[str] = Depends(get_owner_token),
):
    """V6 cluster 提交：plan → 持久 run 行（queued）→ 202 立即返回。

    与同步 ``/plans/execute`` 同一 authz 纪律（强制认证 + session 写归属
    校验）；plan 快照落 ``geocompute_runs``（≤256KB，超限 413），由
    cluster coordinator 持久调度执行（lease/心跳/抢占/恢复）。priority ∈
    {0,5,10}（可选）；project_id（可选）参与租户/项目级公平与账本。

    立即返回 202 + run_id；后续经 ``GET /runs/{id}`` 轮询（活投影来自
    持久行，任意进程可读）。背压：租户/全局 queued 超限 → 429。
    """
    from app.services.geocompute import graph
    from app.services.geocompute.cluster.contracts import RunPriority
    from app.services.geocompute.cluster.errors import (
        ClusterBackpressureError,
        PlanSnapshotTooLargeError,
    )
    from app.services.geocompute.cluster.store import ClusterRunStore

    try:
        plan = _plan_from_request(body.plan)
    except ValueError as exc:
        # 未知类别/策略词表 → typed 422（execute 端点既有行为是 500，
        # V6 submit 起按契约诚实映射；不改 execute 避免行为漂移）
        raise HTTPException(status_code=422, detail={
            "code": "PLAN_INVALID", "message": str(exc),
        })
    try:
        graph.validate_plan(plan)
    except GeoComputeError as exc:
        raise HTTPException(status_code=422, detail=exc.to_dict())

    def _submit():
        if body.session_id:
            _authorize_session_write_sync(body.session_id, user, owner_token)
        from app.core.auth import actor_ids
        from app.services.geocompute.durable import queue_for_node
        from app.services.geocompute.executor import owner_scope_for

        uid, org_id = actor_ids(user)
        # durable 节点的必需 profile 通道（能力匹配依据；in_process 无要求）
        profiles = sorted({
            queue_for_node(n).removesuffix("_queue")
            for n in plan.nodes if n.policy.value == "durable_job"
        })
        store = ClusterRunStore()
        run_id = store.create_run(
            plan_snapshot=plan.model_dump(mode="json"),
            plan_fingerprint=plan.graph_fingerprint(),
            owner_scope=owner_scope_for(dict(user), body.session_id),
            session_id=body.session_id,
            creator_id=str(uid) if uid else None,
            org_id=str(org_id) if org_id else None,
            tenant_raw=str(org_id) if org_id else None,
            project_raw=body.project_id,
            priority=RunPriority.coerce(body.priority),
            required_profiles=profiles,
        )
        return store.get_run(run_id)

    try:
        row = await asyncio.to_thread(_submit)
    except PlanSnapshotTooLargeError as exc:
        raise HTTPException(status_code=413, detail=exc.to_dict())
    except ClusterBackpressureError as exc:
        raise HTTPException(status_code=429, detail=exc.to_dict())
    except HTTPException:
        raise
    except GeoComputeError as exc:
        raise HTTPException(status_code=500, detail=exc.to_dict())
    row = row or {}
    return {
        "run_id": row.get("run_id"),
        "status": row.get("status"),
        "plan_fingerprint": row.get("plan_fingerprint"),
        "required_profiles": row.get("required_profiles") or [],
        "source": "cluster",
    }


@router.get("/runs", tags=["GeoCompute / Cluster Runtime V6"])
async def list_execution_runs(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """V6：列出**我的** runs（强制认证 + owner 域隔离）。

    合并两个真相域：cluster run 行（任意状态）∪ 终态证据快照（进程内
    同步执行的持久痕迹）—— 每域独立有界（各 ≤100 条），不复制载荷。
    """
    from app.services.geocompute.cluster.store import ClusterRunStore
    from app.services.geocompute.executor import owner_scope_for

    owner_scope = owner_scope_for(user)
    statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None

    def _list():
        try:
            store = ClusterRunStore()
            items = store.list_runs(owner_scope, statuses=statuses,
                                    limit=limit, offset=offset)
        except Exception:  # noqa: BLE001 - cluster 域不可用 → 空域（诚实降级）
            logger.warning("[geocompute] cluster list store unavailable")
            items = []
        listed_ids = {item["run_id"] for item in items}
        snapshots: list[Dict[str, Any]] = []
        try:
            from app.services.geocompute import run_evidence

            snapshots = run_evidence.list_snapshots(
                owner_scope, limit=limit, exclude_ids=listed_ids
            )
        except Exception:  # noqa: BLE001 - 证据域缺席不阻塞列表
            snapshots = []
        return items, snapshots

    items, snapshots = await asyncio.to_thread(_list)
    return {
        "runs": items,
        "terminal_snapshots": snapshots,
        "limit": max(1, min(int(limit), 100)),
        "offset": max(0, int(offset)),
    }


@router.get("/cluster/metrics", tags=["GeoCompute / Cluster Runtime V6"])
async def cluster_metrics(
    user: Dict[str, Any] = Depends(require_admin),
):
    """V6 集群快照（**require_admin**：暴露队列/账本/worker 全局视图）。

    有界基数：status/priority/role/profile 封闭词表；账本投影 ≤20 scope；
    取消延迟样本 ≤128（分位数暴露）。无任何 per-run/per-user 维度。
    """
    from app.services.geocompute.cluster.metrics import ClusterMetrics

    def _snap():
        return ClusterMetrics().snapshot()

    try:
        return await asyncio.to_thread(_snap)
    except Exception as exc:  # noqa: BLE001 - 集群域不可用 → typed 503
        raise HTTPException(status_code=503, detail={
            "code": "CLUSTER_METRICS_UNAVAILABLE", "message": str(exc)[:200],
        })


@router.get("/runs/{run_id}", tags=["GeoCompute / 执行平面"])
async def get_execution_run(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """查询 run（强制认证 + 读隔离：他人 run 一律 404，避免存在性预言机）。

    V5：内存未命中时回读终态证据快照（owner 域校验在引擎读取侧）——
    进程重启后读取不再 404（快照来源以 ``source="snapshot"`` 诚实标注）。
    Wave-11：应答附加 ``lineage``（无载荷投影）与 ``reproducibility`` 判定
    （快照回放路径同样携带 —— 读自快照 folded JSON）。
    V6：快照也未命中时回读 cluster run 行的**活投影**（queued/leased/
    running 等执行中状态跨进程可见 —— ``source="cluster"`` 诚实标注）。
    """
    from app.services.geocompute.executor import engine, owner_scope_for

    owner_scope = owner_scope_for(user)
    run = engine.get_run(run_id, owner_scope=owner_scope)
    if run is not None:
        return _run_response(run, owner_scope)

    def _cluster_row():
        from app.services.geocompute.cluster.store import ClusterRunStore

        return ClusterRunStore().get_run_owned(run_id, owner_scope)

    try:
        row = await asyncio.to_thread(_cluster_row)
    except Exception:  # noqa: BLE001 - cluster 域不可用 → 与未命中同语义
        logger.warning("[geocompute] cluster run store unavailable: %s", run_id)
        row = None
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})
    return {
        "run_id": row["run_id"],
        "plan_fingerprint": row["plan_fingerprint"],
        "status": row["status"],
        "source": "cluster",
        "priority": row["priority"],
        "attempts": row["attempts"],
        "preempts": row["preempts"],
        "error_code": row["error_code"],
        "required_profiles": row["required_profiles"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "terminal_at": row["terminal_at"],
    }


@router.post("/plans/runs/{run_id}/cancel", tags=["GeoCompute / 执行平面"])
async def cancel_execution_run(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """请求取消一个 run（V6 起跨进程生效）。

    与 run 读端点同一 authz 纪律：强制认证 + owner 域读隔离（未知 run 与
    他人 run 一律 404，不泄漏存在性）。

    取消链路（两级，幂等）：
    1. 本进程内存 token（``engine.cancel_run``）：在飞节点经协作 checkpoint
       收敛；durable 分支级联写 job 行取消（既有机制）。
    2. 持久取消旗标（``geocompute_runs.cancel_requested_at``）：V6 新增 ——
       任意进程可写；执行侧 coordinator 心跳（≤0.5s）点燃本地 token；
       排队中的 run 由 coordinator cancel sweep 直接收敛终态。

    幂等：已终态 / 快照回放的 run 返回 200 且 ``cancelled=false`` 并附当前
    终态 —— 与 durable job 取消的幂等语义一致。
    """
    from app.services.geocompute.executor import engine, owner_scope_for

    owner_scope = owner_scope_for(user)
    run = engine.get_run(run_id, owner_scope=owner_scope)
    if run is not None:
        cancelled = engine.cancel_run(run_id, reason="cancelled via API")
        return {
            "run_id": run_id,
            "cancelled": bool(cancelled),
            "status": run.status.value,
            "source": run.source,
        }
    # 本进程内存未命中（多副本在飞 run / cluster 排队 run）→ 持久旗标路径。
    # owner 域校验在 store 侧（他人/未知一律 None → 404，不泄漏存在性）。
    def _cancel_persistent():
        from app.services.geocompute.cluster.store import ClusterRunStore

        store = ClusterRunStore()
        row = store.get_run_owned(run_id, owner_scope)
        if row is None:
            return None, None
        changed, observed = store.request_cancel(run_id)
        return changed, observed

    try:
        changed, observed = await asyncio.to_thread(_cancel_persistent)
    except Exception:  # noqa: BLE001 - cluster 域不可用（未迁移库）→ V5 语义
        logger.warning("[geocompute] cluster cancel store unavailable: %s", run_id)
        changed, observed = None, None
    if observed is None:
        # cluster 行未命中 → 快照回放域校验（终态 run 幂等 no-op 语义），
        # 全未命中才 404。get_run 二次调用此刻走快照回读。
        snap = engine.get_run(run_id, owner_scope=owner_scope)
        if snap is None:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})
        return {
            "run_id": run_id,
            "cancelled": False,
            "status": snap.status.value,
            "source": snap.source,
        }
    return {
        "run_id": run_id,
        "cancelled": bool(changed),
        "requested": bool(changed),
        "status": observed,
        "source": "cluster",
    }


@router.get("/runs/{run_id}/summary", tags=["GeoCompute / 执行平面"])
async def get_execution_run_summary(
    run_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """查询 run 摘要（强制认证 + 与 GET /runs 相同的读隔离）。"""
    from app.services.geocompute.executor import engine, owner_scope_for

    run = engine.get_run(run_id, owner_scope=owner_scope_for(user))
    if run is None:
        raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})
    return {"lines": run.summary_lines()}


@router.post("/plans/drift-check", tags=["GeoCompute / 执行平面"])
async def drift_check(
    body: Dict[str, Any],
    user: Dict[str, Any] = Depends(get_current_user),
):
    """对持久化计划记录做语义漂移判定（ADR-0096 D7）。

    body: {"stored": {...持久记录...}, "plan": {ExecutionPlanIn（可选）}}
    返回 DriftVerdict（current/stale_runtime/degraded_plan/unknown）。
    强制认证：stored 记录可能携带计划元数据，不向匿名暴露。
    """
    from app.services.geocompute.drift import check_plan_drift

    stored = body.get("stored")
    plan_in = body.get("plan")
    plan = _plan_from_request(ExecutionPlanIn(**plan_in)) if plan_in else None
    verdict = check_plan_drift(stored, plan=plan)
    return verdict.to_dict()
