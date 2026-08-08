"""
Project Workspace, Persistent Workflow, Spatial Data Quality & Lineage API Endpoints
"""
import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user_optional
from app.services.project_service import ProjectService
from app.services.workflow_engine import WorkflowEngine
from app.services.lineage_service import LineageService
from app.services.spatial_quality_service import SpatialQualityEngine
from app.services.spatial_repair_pipeline import SpatialRepairPipeline
from app.tools.registry import ToolRegistry
from app.schemas.project_schema import (
    ProjectCreate, ProjectUpdate, ProjectResponse,
    DatasetAttach, ProjectDatasetResponse,
    WorkflowCreate, WorkflowResponse,
    WorkflowRunRequest, WorkflowRunResponse,
    ArtifactResponse, ArtifactLineageResponse,
    RunComparisonResponse
)

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


@router.get("", response_model=List[ProjectResponse])
def list_projects(
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
    return ProjectService.list_projects(db=db, user_id=user_id, org_id=org_id)


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


@router.get("/{project_id}/datasets", response_model=List[ProjectDatasetResponse])
def list_datasets(
    project_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
    return ProjectService.list_project_datasets(db=db, project_id=project_id, user_id=user_id, org_id=org_id)


@router.get("/{project_id}/artifacts", response_model=List[ArtifactResponse])
def list_artifacts(
    project_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
    return ProjectService.list_project_artifacts(db=db, project_id=project_id, user_id=user_id, org_id=org_id)


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


@router.get("/{project_id}/workflows", response_model=List[WorkflowResponse])
def list_workflows(
    project_id: str,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
    return ProjectService.list_project_workflows(db=db, project_id=project_id, user_id=user_id, org_id=org_id)


@router.post("/{project_id}/workflows/{workflow_id}/run", response_model=WorkflowRunResponse)
def run_workflow(
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

    tool_registry = ToolRegistry()

    try:
        run = WorkflowEngine.execute_workflow_run(
            db=db,
            workflow_id=workflow_id,
            tool_registry=tool_registry,
            input_bindings=req.input_bindings,
            start_from_step=req.start_from_step,
            user_id=user_id,
            org_id=org_id,
        )
        return run
    except Exception as e:
        logger.error(f"Workflow execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}/runs", response_model=List[WorkflowRunResponse])
def list_runs(
    project_id: str,
    workflow_id: Optional[str] = None,
    db: Session = Depends(get_db),
    user: Optional[Dict[str, Any]] = Depends(get_current_user_optional),
):
    user_id = user.get("id") if user else None
    org_id = user.get("org_id") if user else None
    return ProjectService.list_workflow_runs(db=db, project_id=project_id, workflow_id=workflow_id, user_id=user_id, org_id=org_id)


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
    runs = ProjectService.list_workflow_runs(db=db, project_id=project_id, user_id=user_id, org_id=org_id)
    run_a = next((r for r in runs if r.id == run_a_id), None)
    run_b = next((r for r in runs if r.id == run_b_id), None)
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
    return {
        "project_id": project_id,
        "operations_applied": operations,
        "repair_logs": logs,
        "repaired_geojson": repaired_geojson,
    }


@router.get("/artifacts/{artifact_id}/lineage")
def get_artifact_lineage(artifact_id: str, db: Session = Depends(get_db)):
    graph = LineageService.get_lineage_graph(db=db, artifact_id=artifact_id)
    return graph
