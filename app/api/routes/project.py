"""
Project Workspace, Persistent Workflow, Spatial Data Quality & Lineage API Endpoints
"""
import logging
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user_optional
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
    RunComparisonResponse
)
from app.schemas.pagination import Page, clamp_pagination

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["Project Workspace"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    data: ProjectCreate,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
    project = ProjectService.get_project_with_auth(db=db, project_id=project_id, user_id=user_id, org_id=org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found or permission denied")
    return project


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    data: ProjectUpdate,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
    project = ProjectService.update_project(db=db, project_id=project_id, data=data, user_id=user_id, org_id=org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found or permission denied")
    return project


@router.post("/{project_id}/datasets", response_model=ProjectDatasetResponse)
def attach_dataset(
    project_id: str,
    data: DatasetAttach,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
    dataset = ProjectService.attach_dataset(db=db, project_id=project_id, attach_data=data, user_id=user_id, org_id=org_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="Project not found or permission denied")
    return dataset


@router.delete("/{project_id}/datasets/{dataset_id}")
def detach_dataset(
    project_id: str,
    dataset_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
    project = ProjectService.get_project_with_auth(db=db, project_id=project_id, user_id=user_id, org_id=org_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Use the shared, fully-initialized registry rather than a fresh empty one
    # (a fresh ToolRegistry() registers no tools, so dispatch would always 404).
    tool_registry = get_tool_registry()

    try:
        run = await WorkflowEngine.execute_workflow_run(
            db=db,
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
        logger.error(f"Workflow execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}/runs", response_model=Page[WorkflowRunSummary])
def list_runs(
    project_id: str,
    workflow_id: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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


@router.post("/{project_id}/quality-audit")
def audit_spatial_quality(
    project_id: str,
    payload: Dict[str, Any],
    crs: str = "EPSG:4326",
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
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
