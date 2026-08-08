"""
Persistent Workflow & DAG Re-run Execution Platform
"""
import uuid
import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from collections import defaultdict, deque
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.project import Workflow, WorkflowRun, Artifact, ProjectDataset
from app.services.lineage_service import LineageService
from app.tools.registry import ToolRegistry
from app.schemas.project_schema import WorkflowCreate, WorkflowGraphSpec, WorkflowStepSpec

logger = logging.getLogger(__name__)


class WorkflowEngine:
    @staticmethod
    def validate_dag(steps: List[WorkflowStepSpec]) -> List[str]:
        """
        Validates DAG topology and returns topologically sorted step_ids.
        Raises ValueError if cycle is detected or dependency is missing.
        """
        step_map = {step.step_id: step for step in steps}
        in_degree = defaultdict(int)
        graph = defaultdict(list)

        for step in steps:
            if step.step_id not in in_degree:
                in_degree[step.step_id] = 0
            for dep in step.dependencies:
                if dep not in step_map:
                    raise ValueError(f"Workflow step '{step.step_id}' references missing dependency '{dep}'")
                graph[dep].append(step.step_id)
                in_degree[step.step_id] += 1

        queue = deque([step_id for step_id, deg in in_degree.items() if deg == 0])
        topo_order = []

        while queue:
            node = queue.popleft()
            topo_order.append(node)
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(topo_order) != len(steps):
            raise ValueError("Cycle detected in Workflow DAG specification")

        return topo_order

    @staticmethod
    def execute_workflow_run(
        db: Session,
        workflow_id: str,
        tool_registry: ToolRegistry,
        input_bindings: Optional[Dict[str, Any]] = None,
        start_from_step: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
    ) -> WorkflowRun:
        """
        Executes a workflow run, enforcing tool security policy and producing immutable run outputs & lineage.
        """
        stmt = select(Workflow).where(Workflow.id == workflow_id)
        workflow = db.execute(stmt).scalar_one_or_none()
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        graph_spec = workflow.graph_spec
        raw_steps = graph_spec.get("steps", [])
        steps = [WorkflowStepSpec(**s) for s in raw_steps]

        topo_order = WorkflowEngine.validate_dag(steps)
        step_map = {s.step_id: s for s in steps}

        if start_from_step and start_from_step in topo_order:
            start_idx = topo_order.index(start_from_step)
            execution_order = topo_order[start_idx:]
        else:
            execution_order = topo_order

        run_id = f"wfrun_{uuid.uuid4().hex[:16]}"
        run = WorkflowRun(
            id=run_id,
            workflow_id=workflow_id,
            workflow_version=workflow.version,
            input_bindings=input_bindings or {},
            status="running",
            started_at=datetime.now(timezone.utc),
            execution_trace=[],
            outputs={},
            cost_perf_summary={},
            created_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()

        step_outputs: Dict[str, Any] = {}
        step_artifacts: Dict[str, str] = {}  # step_id -> artifact_id
        execution_trace = []
        bound_inputs = input_bindings or {}

        try:
            for step_id in execution_order:
                step_spec = step_map[step_id]
                tool_name = step_spec.tool_name
                step_start_time = datetime.now(timezone.utc)

                # Prepare merged tool arguments
                tool_args = dict(step_spec.args_template)

                # Bind explicit input parameter overrides or dataset inputs
                for param_key, bind_val in step_spec.input_bindings.items():
                    if isinstance(bind_val, str) and bind_val.startswith("step_"):
                        # Value from a previous step output
                        source_step = bind_val.split(".")[0]
                        out_key = bind_val.split(".")[1] if "." in bind_val else "result"
                        if source_step in step_outputs:
                            step_res = step_outputs[source_step]
                            if isinstance(step_res, dict) and out_key in step_res:
                                tool_args[param_key] = step_res[out_key]
                            else:
                                tool_args[param_key] = step_res
                    elif param_key in bound_inputs:
                        tool_args[param_key] = bound_inputs[param_key]

                # Run tool with Security Policy re-authorization via ToolRegistry
                logger.info(f"[WorkflowEngine] Executing step '{step_id}' using tool '{tool_name}' with args {tool_args}")
                tool_result = tool_registry.dispatch(tool_name, tool_args)

                step_duration = (datetime.now(timezone.utc) - step_start_time).total_seconds()
                step_output = {"result": tool_result}
                step_outputs[step_id] = step_output

                trace_entry = {
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "status": "success",
                    "duration_seconds": step_duration,
                    "args": tool_args,
                    "result_summary": str(tool_result)[:200],
                }
                execution_trace.append(trace_entry)

                # Produce Artifact & Lineage if step produces geographical output
                res_dict = tool_result if isinstance(tool_result, dict) else {}
                storage_ref = str(res_dict.get("ref_id") or res_dict.get("layer_id") or "")

                artifact_id = f"art_{uuid.uuid4().hex[:16]}"
                artifact = Artifact(
                    id=artifact_id,
                    project_id=workflow.project_id,
                    name=f"{workflow.name}_{step_id}_output",
                    artifact_type="analysis",
                    format="geojson",
                    crs="EPSG:4326",
                    storage_ref=storage_ref,
                    metadata_json={"step_id": step_id, "tool_name": tool_name},
                    created_at=datetime.now(timezone.utc),
                )
                db.add(artifact)
                step_artifacts[step_id] = artifact_id

                # Collect parent artifact IDs from step dependencies
                parent_artifact_ids = [
                    step_artifacts[dep] for dep in step_spec.dependencies if dep in step_artifacts
                ]

                LineageService.record_lineage(
                    db=db,
                    artifact_id=artifact_id,
                    producing_tool=tool_name,
                    tool_version="1.0",
                    parent_artifact_ids=parent_artifact_ids,
                    workflow_run_id=run_id,
                    parameters=tool_args,
                )

            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            run.execution_trace = execution_trace
            run.outputs = {step_id: str(res.get("result", ""))[:500] if isinstance(res, dict) else str(res)[:500] for step_id, res in step_outputs.items()}
            run.cost_perf_summary = {
                "total_steps": len(execution_order),
                "total_duration_seconds": sum(t["duration_seconds"] for t in execution_trace),
            }

        except Exception as e:
            logger.error(f"[WorkflowEngine] Step failed during workflow execution: {e}", exc_info=True)
            run.status = "failed"
            run.error_message = str(e)
            run.completed_at = datetime.now(timezone.utc)
            run.execution_trace = execution_trace

        db.commit()
        db.refresh(run)
        return run

    @staticmethod
    def compare_runs(db: Session, run_a: WorkflowRun, run_b: WorkflowRun) -> Dict[str, Any]:
        """
        Compares two Workflow runs A and B.
        """
        keys_a = set(run_a.input_bindings.keys())
        keys_b = set(run_b.input_bindings.keys())
        diff_keys = list((keys_a ^ keys_b) | {k for k in (keys_a & keys_b) if run_a.input_bindings[k] != run_b.input_bindings[k]})
        inputs_changed = {
            "run_a": run_a.input_bindings,
            "run_b": run_b.input_bindings,
            "diff_keys": diff_keys,
        }
        metrics_changed = {
            "run_a_perf": run_a.cost_perf_summary,
            "run_b_perf": run_b.cost_perf_summary,
        }
        return {
            "run_a_id": run_a.id,
            "run_b_id": run_b.id,
            "inputs_changed": inputs_changed,
            "params_changed": {},
            "output_artifacts_changed": {
                "run_a_status": run_a.status,
                "run_b_status": run_b.status,
            },
            "metrics_changed": metrics_changed,
            "warnings_changed": {},
        }
