"""
Project Workspace, Workflow & Spatial Quality Agent Tools
"""
import logging
from typing import Dict, Any, List, Optional

from app.tools.registry import ToolRegistry, tool
from app.tools._utils import trim_features
from app.core.database import SessionLocal
from app.services.project_service import ProjectService
from app.services.workflow_engine import WorkflowEngine
from app.services.spatial_quality_service import SpatialQualityEngine
from app.services.spatial_repair_pipeline import SpatialRepairPipeline
from app.schemas.project_schema import WorkflowCreate, WorkflowGraphSpec, WorkflowStepSpec

logger = logging.getLogger(__name__)


def register_project_tools(registry: ToolRegistry) -> None:

    @tool(registry, 
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
        tier=2, domains=["report"],
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

    @tool(registry, 
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
        tier=2, domains=["report"],
        tags=["project", "workflow"],
    )
    async def rerun_workflow(
        project_id: str,
        workflow_id: str,
        input_bindings: Optional[Dict[str, Any]] = None,
        start_from_step: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Project authz: refuse to run a workflow that isn't owned by this project,
        # and re-authorize each step via Tool Execution Policy inside dispatch.
        with SessionLocal() as db:
            project = ProjectService.get_project_with_auth(db=db, project_id=project_id)
            if not project:
                return {"status": "error", "message": f"Project {project_id} not found or permission denied"}
            run = await WorkflowEngine.execute_workflow_run(
                db=db,
                workflow_id=workflow_id,
                tool_registry=registry,
                input_bindings=input_bindings or {},
                start_from_step=start_from_step,
                expected_project_id=project_id,
            )
            return {
                "status": run.status,
                "run_id": run.id,
                "outputs": run.outputs,
                "error_message": run.error_message,
            }

    @tool(registry, 
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
        tier=2, domains=["report"],
        tags=["spatial", "quality"],
    )
    def audit_spatial_quality(
        geojson: Dict[str, Any],
        dataset_id: str = "ds_agent",
        crs: str = "EPSG:4326",
    ) -> Dict[str, Any]:
        # audit_dataset signature is (geojson_data, crs, dataset_id); earlier this
        # call passed args positionally as (geojson, dataset_id, crs), which fed
        # the CRS check a dataset id and labelled the report with the CRS string.
        report = SpatialQualityEngine.audit_dataset(
            geojson_data=geojson, crs=crs, dataset_id=dataset_id
        )
        return report.to_dict()

    @tool(registry, 
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
        tier=2, domains=["report"],
        tags=["spatial", "quality"],
    )
    async def repair_spatial_dataset(
        geojson: Dict[str, Any],
        operations: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        # repair_dataset is a synchronous, non-destructive CPU op; run it off the
        # event loop. Earlier this awaited a sync function, which raised TypeError.
        ops = operations or ["make_valid", "remove_empty", "deduplicate"]
        import asyncio
        repaired, logs = await asyncio.to_thread(
            SpatialRepairPipeline.repair_dataset, geojson, ops
        )
        feature_count = len(repaired.get("features", []) if isinstance(repaired, dict) else [])
        # Fetch-on-Demand: never inline a full repaired FeatureCollection into the
        # tool_result (persisted to DB + fed to the LLM). Trim heavy geometry.
        return {
            "status": "success",
            "operations_applied": ops,
            "logs_count": len(logs),
            "feature_count": feature_count,
            "repaired_geojson_preview": trim_features(repaired, max_features=50),
        }
