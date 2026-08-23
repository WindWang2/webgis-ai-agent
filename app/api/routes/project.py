"""
Project Workspace, Persistent Workflow, Spatial Data Quality & Lineage API Endpoints
"""
import asyncio
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.core.auth import actor_ids, get_current_user, get_current_user_optional
from app.services.project_service import ProjectService
from app.services.workflow_engine import WorkflowEngine
from app.services.lineage_service import LineageService
from app.services.spatial_quality_service import SpatialQualityEngine
from app.services.spatial_repair_pipeline import SpatialRepairPipeline
from app.agent_pi_bridge import get_tool_registry
from app.schemas.project_schema import (
    ProjectCreate, ProjectUpdate, ProjectResponse,
    ProjectSummary, ProjectDatasetSummary, WorkflowSummary,
    WorkflowRunSummary, ArtifactSummary,
    DatasetAttach, ProjectDatasetResponse,
    WorkflowCreate, WorkflowResponse,
    WorkflowRunRequest, WorkflowRunResponse,
    RunComparisonResponse,
    WorkflowRevisionResponse, WorkflowRevisionSummary,
    RunReplayRequest, RunResumeRequest,
)
from app.schemas.pagination import Page, clamp_pagination

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Project Workspace"])


async def _run_workflow_engine(engine_method, **kwargs) -> Any:
    """把 WorkflowEngine 协程整体 offload 到 worker 线程执行（#386）。

    引擎每步都做同步 SQLAlchemy I/O（db.execute / flush / commit），直接 await
    在 async 路由上会阻塞整个事件循环 —— 卡住所有并发 SSE 流。WorkflowEngine
    方法是 async def（内部 await 工具 dispatch），因此在 worker 线程里用
    asyncio.run 起独立事件循环执行。

    并发安全：sync Session 非线程安全，绝不跨线程共享 —— 在 worker 线程内
    新建 Session、同一线程内使用并关闭。
    """
    def _worker() -> Any:
        with SessionLocal() as thread_db:
            return asyncio.run(engine_method(thread_db, **kwargs))

    return await asyncio.to_thread(_worker)


async def _get_project_with_auth_offloaded(
    project_id: str,
    user_id: Optional[str] = None,
    org_id: Optional[int] = None,
) -> Any:
    """#565: offload the pre-engine ownership lookup (sync SQLAlchemy) into a
    worker thread with its own SessionLocal().

    The engine body itself is already offloaded via ``_run_workflow_engine``
    (#386), but its ``get_project_with_auth`` precondition ran on the event
    loop first — a sync pool acquire there can stall the loop up to
    pool_timeout=30s during DB contention (the same failure mode #386/#421/
    #425 eliminated for the engine body). Only the returned row's truthiness
    is consumed by callers, so the worker-closed session leaves no lazy-load
    hazard.
    """
    def _worker() -> Any:
        with SessionLocal() as thread_db:
            return ProjectService.get_project_with_auth(
                db=thread_db, project_id=project_id,
                user_id=user_id, org_id=org_id,
            )

    return await asyncio.to_thread(_worker)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    data: ProjectCreate,
    db: Session = Depends(get_db),
    # 写路径须认证 (data_fabric.py '状态变更须认证' 原则): 匿名不可建
    # ownerless 项目（否则全体可写）。
    user: Dict[str, Any] = Depends(get_current_user),
):
    user_id, org_id = actor_ids(user)
    project = ProjectService.create_project(
        db=db,
        name=data.name,
        description=data.description,
        metadata_json=data.metadata_json,
        owner_id=user_id,
        org_id=org_id,
    )
    return project


@router.get("", response_model=Page[ProjectSummary])
def list_projects(
    limit: Optional[int] = Query(None, ge=1, le=200, description="Page size (default 50, max 200)"),
    offset: int = Query(0, ge=0, description="Row offset"),
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """Paginated list of projects, slim summary payload.

    Backward-compatible: the response wraps items in a Page envelope with
    total/limit/offset/has_more. Clients that only read ``items`` continue
    to work — the page is the only shape returned.
    """
    user_id, org_id = actor_ids(user)
    limit, offset = clamp_pagination(limit, offset)
    rows, total = ProjectService.list_projects(
        db=db, user_id=user_id, org_id=org_id,
        limit=limit, offset=offset,
    )
    items = [ProjectSummary.model_validate(r) for r in rows]
    return Page(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=(offset + limit) < total,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id, org_id = actor_ids(user)
    project = ProjectService.get_project_with_auth(db=db, project_id=project_id, user_id=user_id, org_id=org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found or permission denied")
    return project


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    data: ProjectUpdate,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    user_id, org_id = actor_ids(user)
    project = ProjectService.update_project(db=db, project_id=project_id, data=data, user_id=user_id, org_id=org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found or permission denied")
    return project


@router.post("/{project_id}/datasets", response_model=ProjectDatasetResponse)
def attach_dataset(
    project_id: str,
    data: DatasetAttach,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    user_id, org_id = actor_ids(user)
    dataset = ProjectService.attach_dataset(db=db, project_id=project_id, attach_data=data, user_id=user_id, org_id=org_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Project not found or permission denied")
    return dataset


@router.delete("/{project_id}/datasets/{dataset_id}")
def detach_dataset(
    project_id: str,
    dataset_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    user_id, org_id = actor_ids(user)
    success = ProjectService.detach_dataset(db=db, project_id=project_id, dataset_id=dataset_id, user_id=user_id, org_id=org_id)
    if not success:
        raise HTTPException(status_code=404, detail="Dataset or Project not found")
    return {"status": "success", "message": f"Dataset {dataset_id} detached"}


@router.get("/{project_id}/datasets", response_model=Page[ProjectDatasetSummary])
def list_datasets(
    project_id: str,
    limit: Optional[int] = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id, org_id = actor_ids(user)
    limit, offset = clamp_pagination(limit, offset)
    rows, total = ProjectService.list_project_datasets(
        db=db, project_id=project_id, user_id=user_id, org_id=org_id,
        limit=limit, offset=offset,
    )
    items = [ProjectDatasetSummary.model_validate(r) for r in rows]
    return Page(items=items, total=total, limit=limit, offset=offset,
                has_more=(offset + limit) < total)


@router.get("/{project_id}/artifacts", response_model=Page[ArtifactSummary])
def list_artifacts(
    project_id: str,
    limit: Optional[int] = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id, org_id = actor_ids(user)
    limit, offset = clamp_pagination(limit, offset)
    rows, total = ProjectService.list_project_artifacts(
        db=db, project_id=project_id, user_id=user_id, org_id=org_id,
        limit=limit, offset=offset,
    )
    items = [ArtifactSummary.model_validate(r) for r in rows]
    return Page(items=items, total=total, limit=limit, offset=offset,
                has_more=(offset + limit) < total)


@router.post("/{project_id}/workflows", response_model=WorkflowResponse)
def save_workflow(
    project_id: str,
    data: WorkflowCreate,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    user_id, org_id = actor_ids(user)
    try:
        WorkflowEngine.validate_dag(data.graph_spec.steps)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    workflow = ProjectService.save_workflow(db=db, project_id=project_id, workflow_data=data, user_id=user_id, org_id=org_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Project not found")
    return workflow


@router.get("/{project_id}/workflows", response_model=Page[WorkflowSummary])
def list_workflows(
    project_id: str,
    limit: Optional[int] = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id, org_id = actor_ids(user)
    limit, offset = clamp_pagination(limit, offset)
    rows, total = ProjectService.list_project_workflows(
        db=db, project_id=project_id, user_id=user_id, org_id=org_id,
        limit=limit, offset=offset,
    )
    items = [
        WorkflowSummary(
            id=r.id,
            project_id=r.project_id,
            name=r.name,
            description=r.description,
            version=r.version,
            step_count=len((r.graph_spec or {}).get("steps", [])) if r.graph_spec else 0,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset,
                has_more=(offset + limit) < total)


@router.post("/{project_id}/workflows/{workflow_id}/run", response_model=WorkflowRunResponse)
async def run_workflow(
    project_id: str,
    workflow_id: str,
    req: WorkflowRunRequest,
    db: Session = Depends(get_db),
    # SEC-F1: workflow execution dispatches registered tools synchronously —
    # not an anonymous surface (resource exhaustion + tool-echo).
    user: Dict[str, Any] = Depends(get_current_user),
):
    user_id, org_id = actor_ids(user)
    project = await _get_project_with_auth_offloaded(
        project_id=project_id, user_id=user_id, org_id=org_id
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Use the shared, fully-initialized registry rather than a fresh empty one
    # (a fresh ToolRegistry() registers no tools, so dispatch would always 404).
    tool_registry = get_tool_registry()

    try:
        run = await _run_workflow_engine(
            WorkflowEngine.execute_workflow_run,
            workflow_id=workflow_id,
            tool_registry=tool_registry,
            input_bindings=req.input_bindings,
            start_from_step=req.start_from_step,
            user_id=user_id,
            org_id=org_id,
            expected_project_id=project_id,
        )
        return run
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("Workflow execution failed: %s", e)
        raise HTTPException(status_code=500, detail="Workflow execution failed")


@router.get("/{project_id}/runs", response_model=Page[WorkflowRunSummary])
def list_runs(
    project_id: str,
    workflow_id: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id, org_id = actor_ids(user)
    limit, offset = clamp_pagination(limit, offset)
    rows, total = ProjectService.list_workflow_runs(
        db=db, project_id=project_id, workflow_id=workflow_id,
        user_id=user_id, org_id=org_id,
        limit=limit, offset=offset,
    )
    items = [WorkflowRunSummary.model_validate(r) for r in rows]
    return Page(items=items, total=total, limit=limit, offset=offset,
                has_more=(offset + limit) < total)


@router.post("/{project_id}/runs/compare", response_model=RunComparisonResponse)
def compare_runs(
    project_id: str,
    run_a_id: str,
    run_b_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id, org_id = actor_ids(user)
    project = ProjectService.get_project_with_auth(db=db, project_id=project_id, user_id=user_id, org_id=org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    # Targeted 2-row lookup instead of full scan + Python next() (DATA-12).
    from sqlalchemy import select as _sel
    from app.models.project import WorkflowRun as _WR
    stmt = _sel(_WR).where(
        _WR.project_id == project_id,
        _WR.id.in_([run_a_id, run_b_id]),
    )
    runs = list(db.execute(stmt).scalars().all())
    by_id = {r.id: r for r in runs}
    run_a = by_id.get(run_a_id)
    run_b = by_id.get(run_b_id)
    if not run_a or not run_b:
        raise HTTPException(status_code=404, detail="One or both WorkflowRuns not found")

    res = WorkflowEngine.compare_runs(db=db, run_a=run_a, run_b=run_b)
    return res


@router.get(
    "/{project_id}/workflows/{workflow_id}/revisions",
    response_model=Page[WorkflowRevisionSummary],
)
def list_workflow_revisions(
    project_id: str,
    workflow_id: str,
    limit: Optional[int] = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """List immutable workflow revisions (tenant-scoped)."""
    user_id, org_id = actor_ids(user)
    limit, offset = clamp_pagination(limit, offset)
    revisions = ProjectService.list_workflow_revisions(
        db=db, project_id=project_id, workflow_id=workflow_id, user_id=user_id, org_id=org_id
    )
    if revisions is None:
        raise HTTPException(status_code=404, detail="Project not found")
    total = len(revisions)
    page = revisions[offset:offset + limit]
    items = [WorkflowRevisionSummary.model_validate(r) for r in page]
    return Page(items=items, total=total, limit=limit, offset=offset,
                has_more=(offset + limit) < total)


@router.get("/{project_id}/workflows/{workflow_id}/revisions/{revision_id}",
            response_model=WorkflowRevisionResponse)
def get_workflow_revision(
    project_id: str,
    workflow_id: str,
    revision_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id, org_id = actor_ids(user)
    revisions = ProjectService.list_workflow_revisions(
        db=db, project_id=project_id, workflow_id=workflow_id, user_id=user_id, org_id=org_id
    )
    if revisions is None:
        raise HTTPException(status_code=404, detail="Project not found")
    for r in revisions:
        if r.id == revision_id:
            return r
    raise HTTPException(status_code=404, detail="Revision not found")


@router.get("/{project_id}/runs/{run_id}", response_model=WorkflowRunResponse)
def get_run_detail(
    project_id: str,
    run_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """Run detail incl. the reproducibility manifest + fingerprint."""
    user_id, org_id = actor_ids(user)
    run = ProjectService.get_workflow_run(
        db=db, project_id=project_id, run_id=run_id, user_id=user_id, org_id=org_id
    )
    if not run:
        raise HTTPException(status_code=404, detail="Run not found or permission denied")
    return run


@router.post("/{project_id}/runs/{run_id}/replay", response_model=WorkflowRunResponse)
async def replay_run(
    project_id: str,
    run_id: str,
    req: RunReplayRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Re-execute a prior run. ``exact`` (default) reuses the frozen graph +
    inputs; ``latest`` runs the current revision with the prior inputs.

    Re-authorizes project ownership (INV-AUTH1): replay cannot bypass access.
    """
    user_id, org_id = actor_ids(user)
    project = await _get_project_with_auth_offloaded(
        project_id=project_id, user_id=user_id, org_id=org_id
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    tool_registry = get_tool_registry()
    try:
        return await _run_workflow_engine(
            WorkflowEngine.replay_run,
            prior_run_id=run_id,
            tool_registry=tool_registry,
            mode=req.mode,
            user_id=user_id,
            org_id=org_id,
            expected_project_id=project_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e) else 409, detail=str(e))


@router.post("/{project_id}/runs/{run_id}/resume", response_model=WorkflowRunResponse)
async def resume_run(
    project_id: str,
    run_id: str,
    req: RunResumeRequest,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Continue a failed/partial prior run from where it stopped.

    Re-authorizes project ownership (INV-AUTH1). Rejects (409) when resume
    preconditions fail unless ``allow_rerun=True``.
    """
    user_id, org_id = actor_ids(user)
    project = await _get_project_with_auth_offloaded(
        project_id=project_id, user_id=user_id, org_id=org_id
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    tool_registry = get_tool_registry()
    try:
        return await _run_workflow_engine(
            WorkflowEngine.resume_run,
            prior_run_id=run_id,
            tool_registry=tool_registry,
            user_id=user_id,
            org_id=org_id,
            expected_project_id=project_id,
            allow_rerun=req.allow_rerun,
        )
    except ValueError as e:
        raise HTTPException(status_code=404 if "not found" in str(e) else 409, detail=str(e))


@router.post("/{project_id}/quality-audit")
def audit_spatial_quality(
    project_id: str,
    payload: Dict[str, Any],
    crs: str = "EPSG:4326",
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id, org_id = actor_ids(user)
    project = ProjectService.get_project_with_auth(db=db, project_id=project_id, user_id=user_id, org_id=org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    geojson_data = payload.get("geojson")
    if not geojson_data:
        raise HTTPException(status_code=400, detail="Missing 'geojson' in payload")

    report = SpatialQualityEngine.audit_dataset(geojson_data=geojson_data, dataset_id=project_id, crs=crs)
    return report.to_dict()


@router.post("/{project_id}/repair")
def repair_spatial_dataset(
    project_id: str,
    payload: Dict[str, Any],
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id, org_id = actor_ids(user)
    project = ProjectService.get_project_with_auth(db=db, project_id=project_id, user_id=user_id, org_id=org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    geojson_data = payload.get("geojson")
    operations = payload.get("operations", ["make_valid", "remove_empty"])
    if not geojson_data:
        raise HTTPException(status_code=400, detail="Missing 'geojson' in payload")

    repaired_geojson, logs = SpatialRepairPipeline.repair_dataset(geojson_data, operations)
    feature_count = (
        len(repaired_geojson.get("features", []))
        if isinstance(repaired_geojson, dict) else 0
    )
    # Fetch-on-Demand: trim the repaired geometry out of the inline response.
    from app.tools._utils import trim_features
    return {
        "project_id": project_id,
        "operations_applied": operations,
        "repair_logs": logs,
        "feature_count": feature_count,
        "repaired_geojson_preview": trim_features(repaired_geojson, max_features=50),
    }


@router.get("/artifacts/{artifact_id}/lineage")
def get_artifact_lineage(
    artifact_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id, org_id = actor_ids(user)
    from app.models.project import Artifact
    from sqlalchemy import select
    artifact = db.execute(select(Artifact).where(Artifact.id == artifact_id)).scalar_one_or_none()
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")

    project = ProjectService.get_project_with_auth(db=db, project_id=artifact.project_id, user_id=user_id, org_id=org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Artifact or project not found")

    # DATA-01: pass project.id so the traversal filters cross-tenant neighbors.
    graph = LineageService.get_lineage_graph(db=db, artifact_id=artifact_id, project_id=project.id)
    return graph


# ── 项目制图记忆管理（ADR-0069 / spec 开放问题 2）─────────────────────────
# 记忆是先验不是证据；这里只提供人工治理入口：查看、撤销（retire）、
# 显式（重）激活（conflicted/stale 的裁决）。不存在任何"凭记忆改评审"
# 的写入口——那在 ADR-0069 决策 2 下被禁止。


def _carto_fact_row(fact) -> Dict[str, Any]:
    return {
        "id": fact.id,
        "kind": fact.kind,
        "subject": fact.subject,
        "payload": fact.payload if isinstance(fact.payload, dict) else {},
        "fingerprint": fact.fingerprint,
        "validity_tier": fact.validity_tier,
        "status": fact.status,
        "created_at": fact.created_at.isoformat() if fact.created_at else None,
        "last_verified_at": (
            fact.last_verified_at.isoformat() if fact.last_verified_at else None
        ),
    }


@router.get("/{project_id}/carto-memory")
def list_carto_memory(
    project_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    """项目的全部制图事实（含 stale/conflicted/retired，管理视图）。"""
    user_id, org_id = actor_ids(user)
    project = ProjectService.get_project_with_auth(
        db=db, project_id=project_id, user_id=user_id, org_id=org_id,
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found or permission denied")
    from app.services.cartography.project_memory import list_project_facts

    facts = list_project_facts(db, project_id)
    return {
        "project_id": project_id,
        "counts": {
            status_name: sum(1 for f in facts if f.status == status_name)
            for status_name in ("active", "stale", "conflicted", "retired")
        },
        "facts": [_carto_fact_row(f) for f in facts],
    }


@router.delete("/{project_id}/carto-memory/{fact_id}")
def retire_carto_fact(
    project_id: str,
    fact_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """撤销一条事实（软删 retired）——用户改主意 / 方案弃用。"""
    user_id, org_id = actor_ids(user)
    project = ProjectService.get_project_with_auth(
        db=db, project_id=project_id, user_id=user_id, org_id=org_id,
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found or permission denied")
    from app.services.cartography.project_memory import retire_fact

    fact = retire_fact(db, project_id, fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="Fact not found in this project")
    db.commit()
    return {"status": "retired", "fact": _carto_fact_row(fact)}


@router.post("/{project_id}/carto-memory/{fact_id}/activate")
def activate_carto_fact(
    project_id: str,
    fact_id: str,
    db: Session = Depends(get_db),
    user: Dict[str, Any] = Depends(get_current_user),
):
    """显式（重）激活一条事实——conflicted/stale 的人工裁决入口。

    ADR-0069 决策 3：共享方案的升级/确认只能经显式动作，这里就是那个
    动作。激活清除分歧与环境事件标记并刷新验证时间。
    """
    user_id, org_id = actor_ids(user)
    project = ProjectService.get_project_with_auth(
        db=db, project_id=project_id, user_id=user_id, org_id=org_id,
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found or permission denied")
    from app.services.cartography.project_memory import activate_fact

    fact = activate_fact(db, project_id, fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="Fact not found in this project")
    db.commit()
    return {"status": "active", "fact": _carto_fact_row(fact)}
