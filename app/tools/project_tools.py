"""
Project Workspace, Workflow & Spatial Quality Agent Tools
"""
import logging
from typing import Dict, Any, List, Optional

from app.tools.registry import ToolRegistry
from app.core.database import SessionLocal
from app.services.project_service import ProjectService
from app.services.workflow_engine import WorkflowEngine
from app.services.spatial_quality_service import SpatialQualityEngine
from app.services.spatial_repair_pipeline import SpatialRepairPipeline
from app.schemas.project_schema import WorkflowCreate, WorkflowGraphSpec, WorkflowStepSpec

logger = logging.getLogger(__name__)


def register_project_tools(registry: ToolRegistry) -> None:

    @registry.register(
        name="save_plan_as_workflow",
        description="Save a successful execution plan or steps into a persistent, reusable Project Workflow.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Target Project ID"},
                "workflow_name": {"type": "string", "description": "Workflow name"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_id": {"type": "string"},
                            "tool_name": {"type": "string"},
                            "args_template": {"type": "object"},
                            "dependencies": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["step_id", "tool_name"],
                    },
                    "description": "List of DAG workflow step specifications",
                },
                "description": {"type": "string", "description": "Workflow description"},
            },
            "required": ["project_id", "workflow_name", "steps"],
        },
        tier=1,
        tags=["project", "workflow"],
    )
    def save_plan_as_workflow(
        project_id: str,
        workflow_name: str,
        steps: List[Dict[str, Any]],
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        step_specs = [
            WorkflowStepSpec(
                step_id=s["step_id"],
                tool_name=s["tool_name"],
                args_template=s.get("args_template", {}),
                dependencies=s.get("dependencies", []),
            )
            for s in steps
        ]
        wf_create = WorkflowCreate(
            name=workflow_name,
            description=description,
            graph_spec=WorkflowGraphSpec(steps=step_specs),
        )
        with SessionLocal() as db:
            wf = ProjectService.save_workflow(db, project_id, wf_create)
            if not wf:
                return {"status": "error", "message": f"Project {project_id} not found"}
            return {
                "status": "success",
                "workflow_id": wf.id,
                "version": wf.version,
                "message": f"Saved workflow '{workflow_name}' to project {project_id}",
            }

    @registry.register(
        name="rerun_workflow",
        description="Re-run a persistent workflow with updated inputs, AOI, or parameters.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Target Project ID"},
                "workflow_id": {"type": "string", "description": "Target Workflow ID"},
                "input_bindings": {"type": "object", "description": "Overridden inputs or parameters"},
                "start_from_step": {"type": "string", "description": "Optional step to run from"},
            },
            "required": ["project_id", "workflow_id"],
        },
        tier=1,
        tags=["project", "workflow"],
    )
    def rerun_workflow(
        project_id: str,
        workflow_id: str,
        input_bindings: Optional[Dict[str, Any]] = None,
        start_from_step: Optional[str] = None,
    ) -> Dict[str, Any]:
        with SessionLocal() as db:
            run = WorkflowEngine.execute_workflow_run(
                db=db,
                workflow_id=workflow_id,
                tool_registry=registry,
                input_bindings=input_bindings or {},
                start_from_step=start_from_step,
            )
            return {
                "status": run.status,
                "run_id": run.id,
                "outputs": run.outputs,
                "error_message": run.error_message,
            }

    @registry.register(
        name="audit_spatial_quality",
        description="Audit spatial dataset quality across Geometry, Topology, CRS, Attributes, and Sanity dimensions.",
        parameters={
            "type": "object",
            "properties": {
                "geojson": {"type": "object", "description": "GeoJSON FeatureCollection data"},
                "dataset_id": {"type": "string", "description": "Dataset identifier"},
                "crs": {"type": "string", "description": "Coordinate reference system"},
            },
            "required": ["geojson"],
        },
        tier=1,
        tags=["spatial", "quality"],
    )
    def audit_spatial_quality(
        geojson: Dict[str, Any],
        dataset_id: str = "ds_agent",
        crs: str = "EPSG:4326",
    ) -> Dict[str, Any]:
        report = SpatialQualityEngine.audit_dataset(geojson, dataset_id, crs)
        return report.to_dict()

    @registry.register(
        name="repair_spatial_dataset",
        description="Perform non-destructive safe remediation on a spatial dataset and create a clean derived output.",
        parameters={
            "type": "object",
            "properties": {
                "geojson": {"type": "object", "description": "GeoJSON FeatureCollection data"},
                "operations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of repair operations: make_valid, remove_empty, normalize_geometry_type, deduplicate, snap_within_tolerance",
                },
            },
            "required": ["geojson"],
        },
        tier=1,
        tags=["spatial", "quality"],
    )
    async def repair_spatial_dataset(
        geojson: Dict[str, Any],
        operations: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        ops = operations or ["make_valid", "remove_empty", "deduplicate"]
        repaired, logs = await SpatialRepairPipeline.repair_dataset(geojson, ops)
        return {
            "status": "success",
            "operations_applied": ops,
            "logs_count": len(logs),
            "repaired_geojson": repaired,
        }
