"""
Persistent Workflow & DAG Re-run Execution Platform.

Provenance contract (see .scratch/workflow-lineage-v2/invariants.md):
  * A run snapshots the exact graph + input dataset fingerprints it executed
    with, so it stays interpretable after the live Workflow row is edited
    (INV-SNAP1/2).
  * Each successful step's artifact + lineage + run-trace commit as ONE atomic
    boundary; a failed step never corrupts prior steps (INV-PART1/2/3, INV-TX1).
  * Artifact metadata is derived from the real tool result — never fabricated
    (INV-ART1/2).
  * replay(exact) reuses the frozen graph + inputs; resume reuses prior completed
    steps only when their outputs are reconstructable AND input fingerprints are
    unchanged (INV-REPLAY1/RESUME1).
  * run / replay / resume all re-authorize project ownership + tenant scope
    (INV-AUTH1); the caller identity is propagated to the tool layer via the
    ToolExecutionContext channel (spec §21).
"""
import uuid
import json
import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from collections import defaultdict, deque
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.project import (
    Workflow, WorkflowRevision, WorkflowRun, Artifact, ProjectDataset,
)
from app.services.lineage_service import LineageService
from app.services.provenance import (
    RunManifestBuilder,
    compute_dataset_fingerprint,
    compute_graph_fingerprint,
    compute_run_fingerprint,
    extract_artifact_metadata,
)
from app.services.provenance.context import (
    ToolExecutionContext,
    reset_tool_execution_context,
    set_tool_execution_context,
)
from app.schemas.project_schema import WorkflowStepSpec

logger = logging.getLogger(__name__)



async def _dispatch_step_via_service(
    tool_registry,
    tool_name: str,
    tool_args: dict,
    session_id: Optional[str],
    service,
    executed_tools: set,
) -> Dict[str, Any]:
    """#694：workflow 步骤经 ToolDispatchService 执行（不再直调 registry）。

    此前直调绕过了 #589 错误形态折叠与 Redis-unavailable ref 哨兵检测
    （ADR-0014 只豁免 plan_mode/admin 端点）。service 缺省时惰性构建；
    error 判别式结果上抛为 RuntimeError（workflow step 失败语义）。
    """
    from app.services.tool_dispatch_service import ToolDispatchService

    if service is None:
        service = ToolDispatchService(registry=tool_registry)
    _tc = {
        "id": "wf-step",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(tool_args, ensure_ascii=False, default=str),
        },
    }
    _res = await service.dispatch(_tc, session_id=session_id, executed_tools=executed_tools)
    _status = getattr(_res.status, "value", _res.status)
    if _status == "error":
        raise RuntimeError(f"tool '{tool_name}' failed: {_res.error_msg}")
    out = _res.raw_result
    return out if isinstance(out, dict) else {"success": True, "data": out}


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

        queue = deque(sorted(step_id for step_id, deg in in_degree.items() if deg == 0))

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

    # ── capability re-resolution (ADR-0092 A5) ────────────────────────────

    @staticmethod
    def resolve_step_tool(
        step_spec: WorkflowStepSpec,
        tool_registry,
        available: Optional[set] = None,
    ) -> Tuple[str, Optional[str], Optional[str], Dict[str, Any]]:
        """Resolve the tool a step executes, honoring capability semantics.

        A capability-bearing step is re-resolved through AlgorithmResolver
        (CapabilityRegistry → AlgorithmRegistry → ToolRegistry) at execution
        time — the recorded ``tool_name`` is evidence, not a hard binding. When
        re-resolution cannot produce a registered tool (registry view unknown,
        capability retired), the recorded tool id is used and the fallback is
        disclosed in the returned evidence so reruns stay explainable.

        ``available``: a precomputed set of registered tool names — callers
        that resolve many steps build it once instead of paying
        O(steps × registry_size) rebuilding it per step.

        Returns (tool_name, capability, algorithm, resolution_evidence).
        """
        capability = step_spec.capability
        if not capability:
            return step_spec.tool_name, None, step_spec.algorithm_preference, {}
        try:
            from app.lib.gis.algorithm_resolver import get_algorithm_resolver

            if available is None:
                try:
                    available = set(tool_registry.list_tools())
                except Exception:  # noqa: BLE001 — registry view unknown → resolver default
                    available = None
            resolution = get_algorithm_resolver().resolve(
                capability, available_tools=available
            )
            evidence: Dict[str, Any] = {
                "resolver_status": resolution.status,
                "resolver_reason": resolution.reason,
                "recorded_tool": step_spec.tool_name,
            }
            if resolution.status == "resolved" and resolution.tool:
                if step_spec.algorithm_preference and resolution.algorithm != step_spec.algorithm_preference:
                    evidence["algorithm_changed_from"] = step_spec.algorithm_preference
                return (
                    resolution.tool,
                    capability,
                    resolution.algorithm,
                    evidence,
                )
            # Honest fallback: recorded tool id may be stale; disclose it.
            evidence["used_recorded_tool"] = True
            return step_spec.tool_name, capability, step_spec.algorithm_preference, evidence
        except Exception as e:  # noqa: BLE001 — resolver failure must not kill rerun
            logger.warning(
                "[WorkflowEngine] capability re-resolution failed for '%s' on step "
                "'%s': %s — using recorded tool", capability, step_spec.step_id, e,
            )
            return (
                step_spec.tool_name,
                capability,
                step_spec.algorithm_preference,
                {"resolver_error": str(e)[:200], "used_recorded_tool": True},
            )

    # ── authorization & revision helpers ───────────────────────────────────

    @staticmethod
    def _authorize(
        workflow: Workflow,
        expected_project_id: Optional[str],
        user_id: Optional[str],
        org_id: Optional[int],
    ) -> None:
        """Re-authorize workflow ownership (INV-AUTH1).

        Raises ValueError when the workflow does not belong to the caller's
        project (cross-project / IDOR) — enforced identically for run, replay,
        and resume so none of them can bypass the access check.
        """
        if expected_project_id and workflow.project_id and workflow.project_id != expected_project_id:
            logger.warning(
                "[WorkflowEngine] IDOR attempt: workflow %s (project %s) invoked "
                "under project %s by user %s",
                workflow.id, workflow.project_id, expected_project_id, user_id,
            )
            raise ValueError(
                f"Workflow {workflow.id} does not belong to project {expected_project_id}"
            )

    @staticmethod
    def _ensure_revision(db: Session, workflow: Workflow, user_id: Optional[str]) -> WorkflowRevision:
        """Return an immutable revision matching the workflow's current graph.

        Append-only (INV-REV1/2): if the latest revision's graph fingerprint
        already matches the current graph_spec, reuse it; otherwise create a new
        revision (revision_no = max + 1) and advance the workflow's pointer +
        version. Runs snapshot against the returned revision.
        """
        graph_spec = workflow.graph_spec or {"steps": []}
        graph_fp = compute_graph_fingerprint(graph_spec)

        if workflow.current_revision_id:
            existing = db.execute(
                select(WorkflowRevision).where(WorkflowRevision.id == workflow.current_revision_id)
            ).scalar_one_or_none()
            if existing and existing.graph_fingerprint == graph_fp:
                return existing

        latest_no = db.execute(
            select(WorkflowRevision.revision_no)
            .where(WorkflowRevision.workflow_id == workflow.id)
            .order_by(WorkflowRevision.revision_no.desc())
        ).scalars().first() or 0

        # Concurrent runs on the same workflow may race to insert the same
        # revision_no; the unique index prevents duplicates but the loser raises
        # IntegrityError. Retry once, reusing the winner's revision if its graph
        # fingerprint matches (the common case — same graph, two runs).
        try:
            revision = WorkflowRevision(
                id=f"wfrev_{uuid.uuid4().hex[:16]}",
                workflow_id=workflow.id,
                revision_no=latest_no + 1,
                graph_spec=graph_spec,
                inputs_schema=workflow.inputs_schema,
                graph_fingerprint=graph_fp,
                created_by=user_id,
                created_at=datetime.now(timezone.utc),
            )
            db.add(revision)
            db.flush()
            workflow.current_revision_id = revision.id
            workflow.version = max(workflow.version or 1, revision.revision_no)
            workflow.updated_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(workflow)
            db.refresh(revision)
            return revision
        except IntegrityError:
            db.rollback()
            workflow = db.merge(workflow)
            winner = db.execute(
                select(WorkflowRevision)
                .where(WorkflowRevision.workflow_id == workflow.id)
                .order_by(WorkflowRevision.revision_no.desc())
            ).scalars().first()
            if winner and winner.graph_fingerprint == graph_fp:
                workflow.current_revision_id = winner.id
                workflow.version = max(workflow.version or 1, winner.revision_no)
                db.commit()
                return winner
            return WorkflowEngine._ensure_revision(db, workflow, user_id)

    @staticmethod
    def _capture_dataset_fingerprints(db: Session, project_id: str) -> Dict[str, str]:
        """Snapshot fingerprints of all ACTIVE (non-detached) project datasets.

        Used at run start (INV-SNAP2): resume compares these against the current
        dataset fingerprints to detect input drift. Recomputed from evidence
        (not the stored column) so a content change is always detected.
        """
        rows = db.execute(
            select(ProjectDataset).where(
                ProjectDataset.project_id == project_id,
                ProjectDataset.detached_at.is_(None),
            )
        ).scalars().all()
        return {
            ds.id: compute_dataset_fingerprint(ds.source_type, ds.source_ref, ds.crs, ds.schema_profile)
            for ds in rows
        }

    @staticmethod
    def _attribute_source_dataset(
        datasets: List[ProjectDataset], tool_args: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[str]]:
        """Best-effort attribution of the input dataset seeding a root step.

        Returns (dataset_id, fingerprint) when a tool arg value matches a project
        dataset id or source_ref; otherwise (None, None) — recorded truthfully as
        unknown rather than fabricated (INV-LIN4 / INV-ART1).
        """
        id_index = {ds.id: ds for ds in datasets}
        ref_index = {ds.source_ref: ds for ds in datasets if ds.source_ref}
        for value in tool_args.values():
            # Only scalar string values can be dataset ids / refs; structured
            # values (GeoJSON lists/dicts) are unhashable and never ids.
            if not isinstance(value, str):
                continue
            ds = id_index.get(value) or ref_index.get(value)
            if ds is not None:
                fp = compute_dataset_fingerprint(ds.source_type, ds.source_ref, ds.crs, ds.schema_profile)
                return ds.id, fp
        return None, None

    # ── core execution ──────────────────────────────────────────────────────

    @staticmethod
    async def _execute(
        db: Session,
        workflow: Workflow,
        revision: WorkflowRevision,
        tool_registry,
        steps: List[WorkflowStepSpec],
        execution_order: List[str],
        input_bindings: Optional[Dict[str, Any]],
        dataset_fingerprints: Dict[str, str],
        project_datasets: List[ProjectDataset],
        user_id: Optional[str],
        org_id: Optional[int],
        session_id: Optional[str],
        run_id_hint: Optional[str] = None,
        seed_outputs: Optional[Dict[str, Dict[str, Any]]] = None,
        seed_completed: Optional[List[str]] = None,
        seed_trace: Optional[List[Dict[str, Any]]] = None,
        seed_artifact_records: Optional[List[Dict[str, Any]]] = None,
        rerun_disclosures: Optional[Dict[str, Any]] = None,
        available_tools: Optional[set] = None,
    ) -> WorkflowRun:
        """Run ``execution_order`` steps, snapshotting everything immutable.

        ``seed_*`` carry prior-run state for resume: reconstructed outputs of
        already-completed steps, their trace entries, and artifact records (so the
        resumed run's manifest reflects the whole workflow, not just the tail).
        """
        step_map = {s.step_id: s for s in steps}
        bound_inputs = input_bindings or {}
        seed_outputs = seed_outputs or {}
        seed_completed = set(seed_completed or [])
        run_id = run_id_hint or f"wfrun_{uuid.uuid4().hex[:16]}"
        ctx = ToolExecutionContext(
            user_id=user_id,
            org_id=org_id,
            project_id=workflow.project_id,
            run_id=run_id,
            session_id=session_id,
        )
        ctx_token = set_tool_execution_context(ctx)

        run = WorkflowRun(
            id=run_id,
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            project_id=workflow.project_id,
            workflow_revision_id=revision.id,
            graph_snapshot=revision.graph_spec,
            input_bindings=bound_inputs,
            input_dataset_fingerprints=dataset_fingerprints,
            status="running",
            started_at=datetime.now(timezone.utc),
            execution_trace=list(seed_trace or []),
            outputs={},
            error_message=None,
            cost_perf_summary={},
            completed_steps=list(seed_completed),
            run_manifest=None,
            run_fingerprint=None,
            durable_job_id=None,
            created_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()

        manifest_builder = RunManifestBuilder(
            workflow_revision_id=revision.id,
            graph_fingerprint=revision.graph_fingerprint,
            input_bindings=bound_inputs,
            input_dataset_fingerprints=dataset_fingerprints,
        )
        # Seed the manifest with the prior completed (reused) steps + artifacts so
        # a resumed run's manifest reflects the WHOLE workflow, not just the tail
        # (INV-MAN1). Without this, a resumed run's run_fingerprint would differ
        # from an equivalent fresh run and compare_runs would see a short step set.
        for entry in (seed_trace or []):
            manifest_builder.add_step(
                step_id=entry.get("step_id"),
                tool_name=entry.get("tool_name"),
                tool_version=entry.get("tool_version"),
                status=entry.get("status", "success"),
                args=entry.get("args"),
                capability=entry.get("capability"),
                algorithm=entry.get("algorithm"),
            )
        for rec in (seed_artifact_records or []):
            manifest_builder.add_artifact(**rec)

        step_outputs: Dict[str, Dict[str, Any]] = dict(seed_outputs)
        step_artifacts: Dict[str, str] = {}  # step_id -> artifact_id
        # Rebuild step->artifact mapping for seeded (reused) steps so downstream
        # lineage in the resumed tail links to the prior artifacts correctly.
        for rec in (seed_artifact_records or []):
            sid = rec.get("producing_step")
            aid = rec.get("id")
            if sid and aid:
                step_artifacts[sid] = aid

        completed_steps: List[str] = list(seed_completed)
        execution_trace: List[Dict[str, Any]] = list(seed_trace or [])
        artifact_records: List[Dict[str, Any]] = list(seed_artifact_records or [])

        step_dispatch_service = None  # #694: lazily built ToolDispatchService
        step_executed_tools: set = set()
        try:
            for step_id in execution_order:
                step_spec = step_map[step_id]
                # ADR-0092 A5: capability-bearing steps re-resolve through the
                # registries at execution time (never a blind tool-id replay).
                tool_name, step_capability, step_algorithm, resolution_evidence = (
                    WorkflowEngine.resolve_step_tool(
                        step_spec, tool_registry, available=available_tools
                    )
                )
                tool_version = tool_registry.tool_version(tool_name)
                step_start = datetime.now(timezone.utc)

                tool_args = WorkflowEngine._resolve_step_args(
                    step_spec, step_outputs, bound_inputs
                )

                # OBSERVABILITY/SEC-REDACT: log only structural fingerprints of the
                # args (key names + a bounded size estimate), NEVER the values —
                # tool_args can carry inline GeoJSON / coordinates / large rasters
                # passed as step inputs, which must not reach INFO logs.
                _arg_keys = sorted(tool_args.keys()) if isinstance(tool_args, dict) else []
                logger.info(
                    "[WorkflowEngine] step '%s' tool '%s' v=%s arg_keys=%s",
                    step_id, tool_name, tool_version, _arg_keys,
                )
                # #694：经 ToolDispatchService 执行（#589 错误折叠 + 哨兵检测
                # 不再被绕过）；helper 可独立测试。
                tool_result = await _dispatch_step_via_service(
                    tool_registry, tool_name, tool_args, session_id,
                    step_dispatch_service, step_executed_tools,
                )

                step_duration = (datetime.now(timezone.utc) - step_start).total_seconds()
                step_output = {"result": tool_result}
                step_outputs[step_id] = step_output

                trace_entry = {
                    "step_id": step_id,
                    "tool_name": tool_name,
                    "tool_version": tool_version,
                    "status": "success",
                    "duration_seconds": step_duration,
                    "args": tool_args,
                    "result_summary": str(tool_result)[:200],
                }
                if step_capability:
                    trace_entry["capability"] = step_capability
                    trace_entry["algorithm"] = step_algorithm
                if resolution_evidence:
                    trace_entry["resolution_evidence"] = resolution_evidence
                execution_trace.append(trace_entry)
                manifest_builder.add_step(
                    step_id=step_id,
                    tool_name=tool_name,
                    tool_version=tool_version,
                    status="success",
                    args=tool_args,
                    capability=step_capability,
                    algorithm=step_algorithm,
                )

                # Truthful artifact metadata from the real result (INV-ART1/2).
                meta = extract_artifact_metadata(tool_name, tool_result)
                artifact_id = f"art_{uuid.uuid4().hex[:16]}"
                artifact_meta: Dict[str, Any] = {
                    "step_id": step_id,
                    "tool_name": tool_name,
                }
                if step_capability:
                    artifact_meta["capability"] = step_capability
                if step_algorithm:
                    artifact_meta["algorithm"] = step_algorithm
                artifact = Artifact(
                    id=artifact_id,
                    project_id=workflow.project_id,
                    name=f"{workflow.name}_{step_id}_output",
                    artifact_type=meta["artifact_type"],
                    format=meta["format"],
                    crs=meta["crs"],
                    storage_ref=meta["storage_ref"],
                    content_fingerprint=meta["content_fingerprint"],
                    metadata_json=artifact_meta,
                    created_at=datetime.now(timezone.utc),
                )
                db.add(artifact)

                parent_artifact_ids = [
                    step_artifacts[dep] for dep in step_spec.dependencies if dep in step_artifacts
                ]
                # Attribute an input dataset only on root steps (no artifact
                # parents); best-effort, None when unknown.
                src_ds_id: Optional[str] = None
                src_ds_fp: Optional[str] = None
                if not parent_artifact_ids:
                    src_ds_id, src_ds_fp = WorkflowEngine._attribute_source_dataset(project_datasets, tool_args)

                LineageService.record_lineage(
                    db=db,
                    artifact_id=artifact_id,
                    producing_tool=tool_name,
                    tool_version=tool_version,
                    producing_capability=step_capability,
                    producing_algorithm=step_algorithm,
                    parent_artifact_ids=parent_artifact_ids,
                    workflow_run_id=run_id,
                    parameters=tool_args,
                    source_dataset_id=src_ds_id,
                    source_dataset_fingerprint=src_ds_fp,
                    content_fingerprint=meta["content_fingerprint"],
                    commit=False,
                )

                step_artifacts[step_id] = artifact_id
                completed_steps.append(step_id)
                artifact_record = {
                    "id": artifact_id,
                    "producing_step": step_id,
                    "artifact_type": meta["artifact_type"],
                    "format": meta["format"],
                    "crs": meta["crs"],
                    "content_fingerprint": meta["content_fingerprint"],
                    "storage_ref": meta["storage_ref"],
                }
                artifact_records.append(artifact_record)
                manifest_builder.add_artifact(**artifact_record)

                # INV-TX1 / INV-PART1: per-step atomic commit. artifact + lineage
                # + the run's own progress (trace + completed_steps) all land
                # together, so a later failure never leaves an indistinguishable
                # partial batch and never loses which steps already succeeded.
                run.execution_trace = list(execution_trace)
                run.completed_steps = list(completed_steps)
                run.outputs = {
                    sid: str(res.get("result", ""))[:500] if isinstance(res, dict) else str(res)[:500]
                    for sid, res in step_outputs.items()
                }
                db.commit()

            run.status = "completed"
            run.completed_at = datetime.now(timezone.utc)
            run.cost_perf_summary = {
                "total_steps": len(execution_order) + len(seed_completed),
                "total_duration_seconds": sum(t.get("duration_seconds", 0) for t in execution_trace),
            }
            if rerun_disclosures:
                run.cost_perf_summary["rerun_disclosures"] = rerun_disclosures

        except Exception as e:
            logger.error("[WorkflowEngine] step failed: %s", e, exc_info=True)
            # Roll back only the failing step's uncommitted artifact/lineage;
            # prior steps are already durable via the per-step commits above.
            try:
                db.rollback()
            except Exception:
                logger.warning("[WorkflowEngine] rollback after step failure failed", exc_info=True)
            run = db.merge(run)
            run.status = "failed"
            run.error_message = str(e)
            run.completed_at = datetime.now(timezone.utc)
            run.execution_trace = list(execution_trace)
            run.completed_steps = list(completed_steps)

        # Build the reproducibility manifest + fingerprint from whatever
        # completed (full or partial). Both fresh and resumed runs get a manifest
        # spanning every step that has a record (seed + executed).
        try:
            # ADR-0092 A2: attach bounded product-outcome evidence when this run
            # executed inside a session/map-product context. Every field is
            # best-effort — an absent session or map state simply omits the
            # block and keeps the legacy manifest shape.
            await WorkflowEngine._attach_outcome_context(
                manifest_builder,
                run=run,
                session_id=session_id,
            )
            manifest = manifest_builder.build()
            run.run_manifest = manifest
            run.run_fingerprint = compute_run_fingerprint(manifest)
            db.commit()
            try:
                db.refresh(run)
            except Exception:
                run = db.merge(run)
            # ADR-0092 A3: best-effort artifact promotion, AFTER the manifest
            # commit — promotion's internal commits must never precede the
            # run manifest landing (otherwise a crash mid-promotion leaves a
            # completed run with run_manifest=NULL). Session-expired or store
            # failures are disclosed per-artifact and never fail the run.
            if session_id and workflow.project_id:
                try:
                    from app.services.project_artifact_promotion import (
                        promote_run_artifacts,
                    )

                    promotion_report = await promote_run_artifacts(
                        db, run, session_id=session_id, project_id=workflow.project_id
                    )
                    run.cost_perf_summary = {
                        **(run.cost_perf_summary or {}),
                        "artifact_promotion": promotion_report[:32],
                    }
                    db.commit()
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "[WorkflowEngine] artifact promotion failed for run %s: %s",
                        run_id, e,
                    )
                # ADR-0099: run 完成 → 幂等自动记录一条 Map Product 版本
                # （同 run+指纹去重；snapshot best-effort —— 会话缺席/过期
                # 仍记录，只是 open 降级为 compare-only）。失败绝不影响 run。
                if workflow.project_id:
                    try:
                        from app.services.map_product_service import MapProductService

                        _snapshot = None
                        if session_id:
                            try:
                                from app.services.mapspec_store import mapspec_store

                                _snapshot = await mapspec_store.get_mapspec(session_id)
                            except Exception:  # noqa: BLE001 — 快照缺席仍记录
                                _snapshot = None
                        MapProductService.maybe_auto_record_version(
                            db, run, session_id=session_id,
                            mapspec_snapshot=_snapshot,
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "[WorkflowEngine] auto map-product record failed "
                            "for run %s: %s", run_id, e,
                        )
        finally:
            # INV-AUTH1 / §21: the ToolExecutionContext must be cleared on EVERY
            # path (success, step failure, manifest build failure) so caller
            # identity never leaks beyond this execution.
            reset_tool_execution_context(ctx_token)
        return run

    @staticmethod
    def _resolve_step_args(
        step_spec: WorkflowStepSpec,
        step_outputs: Dict[str, Dict[str, Any]],
        bound_inputs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Merge args_template with explicit input bindings / prior step outputs."""
        tool_args = dict(step_spec.args_template)
        for param_key, bind_val in step_spec.input_bindings.items():
            if isinstance(bind_val, str) and bind_val.startswith("step_"):
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
        return tool_args

    # ── public entry points ─────────────────────────────────────────────────

    @staticmethod
    async def execute_workflow_run(
        db: Session,
        workflow_id: str,
        tool_registry,
        input_bindings: Optional[Dict[str, Any]] = None,
        start_from_step: Optional[str] = None,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
        expected_project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> WorkflowRun:
        """Execute a fresh run of the workflow's current revision.

        ``expected_project_id`` enforces that the workflow belongs to the caller's
        project (IDOR guard). ``user_id``/``org_id``/``session_id``/``project_id``
        are propagated to the tool layer via the ToolExecutionContext channel
        (spec §21) — available to any tool that needs caller identity, in addition
        to the registry's own tool allow-list policy.
        """
        workflow = db.execute(select(Workflow).where(Workflow.id == workflow_id)).scalar_one_or_none()
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")
        WorkflowEngine._authorize(workflow, expected_project_id, user_id, org_id)

        revision = WorkflowEngine._ensure_revision(db, workflow, user_id)
        graph_spec = revision.graph_spec or {"steps": []}
        steps = [WorkflowStepSpec(**s) for s in graph_spec.get("steps", [])]
        topo_order = WorkflowEngine.validate_dag(steps)

        # start_from_step WITHOUT a prior run is only safe for a root step
        # (one with no unmet dependencies). Mid-graph resume must go through
        # resume_run, which reconstructs prior outputs (INV-RESUME1).
        if start_from_step:
            if start_from_step not in topo_order:
                raise ValueError(f"start_from_step '{start_from_step}' not in workflow DAG")
            step_map = {s.step_id: s for s in steps}
            # A fresh run has no prior outputs, so any non-root start is unsafe.
            if step_map[start_from_step].dependencies:
                raise ValueError(
                    f"start_from_step '{start_from_step}' has dependencies "
                    f"{step_map[start_from_step].dependencies}; use resume_run to "
                    f"continue from a prior partial run"
                )
            start_idx = topo_order.index(start_from_step)
            execution_order = topo_order[start_idx:]
        else:
            execution_order = topo_order

        dataset_fingerprints = WorkflowEngine._capture_dataset_fingerprints(db, workflow.project_id)
        project_datasets = list(db.execute(
            select(ProjectDataset).where(
                ProjectDataset.project_id == workflow.project_id,
                ProjectDataset.detached_at.is_(None),
            )
        ).scalars().all())

        return await WorkflowEngine._execute(
            db=db,
            workflow=workflow,
            revision=revision,
            tool_registry=tool_registry,
            steps=steps,
            execution_order=execution_order,
            input_bindings=input_bindings,
            dataset_fingerprints=dataset_fingerprints,
            project_datasets=project_datasets,
            user_id=user_id,
            org_id=org_id,
            session_id=session_id,
        )

    @staticmethod
    async def replay_run(
        db: Session,
        prior_run_id: str,
        tool_registry,
        mode: str = "exact",
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
        expected_project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> WorkflowRun:
        """Re-execute a prior run (INV-REPLAY1).

        ``mode="exact"`` (default): reuse the prior run's frozen graph snapshot,
        input bindings, and recorded input dataset fingerprints — it does NOT
        silently pick up later edits to the live Workflow row.

        ``mode="latest"``: run against the workflow's current revision while
        keeping the prior run's input bindings. The two modes are deliberately
        separate so callers cannot accidentally replay against a drifted graph.
        """
        prior = WorkflowEngine._load_and_authorize_run(
            db, prior_run_id, expected_project_id, user_id, org_id
        )
        workflow = db.execute(select(Workflow).where(Workflow.id == prior.workflow_id)).scalar_one_or_none()
        if not workflow:
            raise ValueError(f"Workflow {prior.workflow_id} not found")
        # Re-authorize the workflow itself (INV-AUTH1): the run's project and the
        # workflow's project must both match the caller's expected project.
        WorkflowEngine._authorize(workflow, expected_project_id, user_id, org_id)

        if mode == "exact":
            graph_spec = prior.graph_snapshot or {"steps": []}
            revision = WorkflowEngine._revision_or_snapshot(db, workflow, graph_spec, prior, user_id)
            input_bindings = prior.input_bindings
            # Record what the replay ACTUALLY consumes (recomputed current
            # fingerprints) so the run row is truthful; if inputs drifted since
            # the prior run, compare_runs(dataset_versions_changed) surfaces it
            # rather than the new run inheriting a stale baseline.
            dataset_fingerprints = WorkflowEngine._capture_dataset_fingerprints(db, workflow.project_id)
        elif mode == "latest":
            revision = WorkflowEngine._ensure_revision(db, workflow, user_id)
            graph_spec = revision.graph_spec
            input_bindings = prior.input_bindings
            dataset_fingerprints = WorkflowEngine._capture_dataset_fingerprints(db, workflow.project_id)
        else:
            raise ValueError(f"unknown replay mode '{mode}' (use 'exact' or 'latest')")

        steps = [WorkflowStepSpec(**s) for s in graph_spec.get("steps", [])]
        execution_order = WorkflowEngine.validate_dag(steps)
        project_datasets = list(db.execute(
            select(ProjectDataset).where(
                ProjectDataset.project_id == workflow.project_id,
                ProjectDataset.detached_at.is_(None),
            )
        ).scalars().all())

        return await WorkflowEngine._execute(
            db=db,
            workflow=workflow,
            revision=revision,
            tool_registry=tool_registry,
            steps=steps,
            execution_order=execution_order,
            input_bindings=input_bindings,
            dataset_fingerprints=dataset_fingerprints,
            project_datasets=project_datasets,
            user_id=user_id,
            org_id=org_id,
            session_id=session_id,
        )

    @staticmethod
    async def resume_run(
        db: Session,
        prior_run_id: str,
        tool_registry,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
        expected_project_id: Optional[str] = None,
        session_id: Optional[str] = None,
        allow_rerun: bool = False,
    ) -> WorkflowRun:
        """Continue a failed/partial prior run from where it stopped (INV-RESUME1).

        Permitted only when ALL of:
          * prior run is failed/cancelled with >=1 completed step;
          * the prior completed steps' artifacts still exist;
          * the prior completed steps' outputs are reconstructable (read back from
            the session store via their storage_ref);
          * input dataset fingerprints are unchanged since the prior run.

        Otherwise: reject with a clear reason, or — when ``allow_rerun=True`` —
        fall back to a full fresh execution (never silently reuse stale results).
        """
        prior = WorkflowEngine._load_and_authorize_run(
            db, prior_run_id, expected_project_id, user_id, org_id
        )
        workflow = db.execute(select(Workflow).where(Workflow.id == prior.workflow_id)).scalar_one_or_none()
        if not workflow:
            raise ValueError(f"Workflow {prior.workflow_id} not found")
        WorkflowEngine._authorize(workflow, expected_project_id, user_id, org_id)

        if prior.status not in ("failed", "cancelled"):
            raise ValueError(
                f"cannot resume run {prior.id} with status '{prior.status}' "
                f"(only failed/cancelled partial runs are resumable)"
            )
        completed = list(prior.completed_steps or [])
        if not completed:
            if allow_rerun:
                return await WorkflowEngine.execute_workflow_run(
                    db, workflow.id, tool_registry,
                    input_bindings=prior.input_bindings,
                    user_id=user_id, org_id=org_id,
                    expected_project_id=expected_project_id, session_id=session_id,
                )
            raise ValueError(
                f"run {prior.id} has no completed steps to resume from "
                f"(use replay/execute for a full run)"
            )

        # Verify input dataset fingerprints are unchanged.
        current_fps = WorkflowEngine._capture_dataset_fingerprints(db, workflow.project_id)
        if (prior.input_dataset_fingerprints or {}) != current_fps:
            drifted = {
                k for k in set((prior.input_dataset_fingerprints or {})) | set(current_fps)
                if (prior.input_dataset_fingerprints or {}).get(k) != current_fps.get(k)
            }
            if allow_rerun:
                logger.warning(
                    "[WorkflowEngine] input drift on resume of %s (%s); full rerun",
                    prior.id, sorted(drifted),
                )
                return await WorkflowEngine.execute_workflow_run(
                    db, workflow.id, tool_registry,
                    input_bindings=prior.input_bindings,
                    user_id=user_id, org_id=org_id,
                    expected_project_id=expected_project_id, session_id=session_id,
                )
            raise ValueError(
                f"cannot resume run {prior.id}: input dataset fingerprints changed "
                f"({sorted(drifted)}); pass allow_rerun=True for a full rerun"
            )

        # Reconstruct prior step outputs from their artifacts.
        seed_outputs, seed_trace, seed_artifacts, step_artifact_map = await WorkflowEngine._reconstruct_prior(
            db, prior, completed, session_id
        )
        if seed_outputs is None:
            if allow_rerun:
                return await WorkflowEngine.execute_workflow_run(
                    db, workflow.id, tool_registry,
                    input_bindings=prior.input_bindings,
                    user_id=user_id, org_id=org_id,
                    expected_project_id=expected_project_id, session_id=session_id,
                )
            raise ValueError(
                f"cannot resume run {prior.id}: prior step outputs are no longer "
                f"reconstructable (artifacts missing or storage refs expired); "
                f"pass allow_rerun=True for a full rerun"
            )

        graph_spec = prior.graph_snapshot or {"steps": []}
        steps = [WorkflowStepSpec(**s) for s in graph_spec.get("steps", [])]
        topo_order = WorkflowEngine.validate_dag(steps)
        completed_set = set(completed)
        execution_order = [s for s in topo_order if s not in completed_set]

        revision = WorkflowEngine._revision_or_snapshot(db, workflow, graph_spec, prior, user_id)
        project_datasets = list(db.execute(
            select(ProjectDataset).where(
                ProjectDataset.project_id == workflow.project_id,
                ProjectDataset.detached_at.is_(None),
            )
        ).scalars().all())

        return await WorkflowEngine._execute(
            db=db,
            workflow=workflow,
            revision=revision,
            tool_registry=tool_registry,
            steps=steps,
            execution_order=execution_order,
            input_bindings=prior.input_bindings,
            dataset_fingerprints=current_fps,
            project_datasets=project_datasets,
            user_id=user_id,
            org_id=org_id,
            session_id=session_id,
            seed_outputs=seed_outputs,
            seed_completed=completed,
            seed_trace=seed_trace,
            seed_artifact_records=seed_artifacts,
        )

    @staticmethod
    async def _attach_outcome_context(
        manifest_builder: RunManifestBuilder,
        *,
        run: WorkflowRun,
        session_id: Optional[str],
    ) -> None:
        """Best-effort product-outcome evidence for the run manifest (A2).

        Attaches: the runtime manifest fingerprint (registry generation this run
        executed under), and — when the run ran inside a live session — the
        MapSpec fingerprint, product facet status, QA and finalization
        summaries. Any failure simply omits the block: outcome evidence is
        additive, never load-bearing for the fingerprint.
        """
        try:
            from app.lib.gis.runtime_manifest import get_runtime_manifest

            manifest_builder.set_outcome_context(
                runtime_manifest_fingerprint=get_runtime_manifest().fingerprint
            )
        except Exception:  # noqa: BLE001
            pass
        if not session_id:
            return
        mapspec: Optional[Dict[str, Any]] = None
        try:
            from app.lib.cartography.quality_loop import cartographic_fingerprint
            from app.services.mapspec.store import mapspec_store_instance

            mapspec = await mapspec_store_instance.get_mapspec(session_id)
            if isinstance(mapspec, dict) and mapspec:
                manifest_builder.set_outcome_context(
                    mapspec_fingerprint=cartographic_fingerprint(mapspec)
                )
        except Exception:  # noqa: BLE001
            mapspec = None
        try:
            from app.services.session_plan import load_session_plan
            from app.services.gis_harness.product_graph import build_facet_completion

            plan = await load_session_plan(session_id)
            chapter = (plan.gis_chapter if plan else None) or {}
            if chapter:
                facets = build_facet_completion(chapter, mapspec)
                manifest_builder.set_outcome_context(
                    product_facets=[
                        {
                            "facet_id": f.facet_id,
                            "kind": f.kind,
                            "status": f.status,
                            "required": f.required,
                        }
                        for f in facets.facets
                    ][:32]
                )
                product = chapter.get("map_product")
                if isinstance(product, dict):
                    manifest_builder.set_outcome_context(
                        finalization_summary={
                            "status": product.get("status"),
                            "projection": str(product.get("projection") or "")[:200],
                        },
                        qa_summary={
                            "issue_summary": (product.get("qa_summary") or {}) if isinstance(product.get("qa_summary"), dict) else {},
                            "fallback_count": len(chapter.get("fallbacks") or []),
                        },
                    )
        except Exception as e:  # noqa: BLE001 — evidence loss must be visible
            logger.warning(
                "[WorkflowEngine] outcome context: product facets/QA summary "
                "unavailable for run %s (session %s): %s",
                getattr(run, "id", "?"), session_id, e,
            )

    @staticmethod
    def _descendants_of(
        steps: List[WorkflowStepSpec], seeds: set, step_map: Optional[Dict[str, WorkflowStepSpec]] = None
    ) -> set:
        """Transitive descendants of ``seeds`` over dependency edges.

        Reverse-adjacency BFS — O(S + E), single pass (the previous fixed-point
        rescans were O(S²) worst case on deep graphs).
        """
        step_map = step_map or {st.step_id: st for st in steps}
        dependents: Dict[str, List[str]] = {}
        for st in steps:
            for dep in st.dependencies:
                dependents.setdefault(dep, []).append(st.step_id)
        out = set(seeds)
        queue = deque(seeds)
        while queue:
            node = queue.popleft()
            for nxt in dependents.get(node, []):
                if nxt not in out:
                    out.add(nxt)
                    queue.append(nxt)
        return out

    @staticmethod
    def _stale_seed_steps(
        steps: List[WorkflowStepSpec],
        seed_completed: List[str],
        tool_registry,
        available: Optional[set] = None,
        step_map: Optional[Dict[str, WorkflowStepSpec]] = None,
    ) -> List[str]:
        """Seed steps whose capability re-resolves to a different algorithm.

        A reused step whose algorithm would resolve differently today is
        stale compute (ADR-0092 A5): the caller invalidates it instead of
        silently mixing outputs from two algorithm generations. Steps without
        a capability (pure tool steps) are never stale by this definition —
        tool-version drift is captured by compare_runs.
        """
        step_map = step_map or {s.step_id: s for s in steps}
        stale: List[str] = []
        for sid in seed_completed:
            spec = step_map.get(sid)
            if spec is None or not spec.capability:
                continue
            _tool, _cap, algo, evidence = WorkflowEngine.resolve_step_tool(
                spec, tool_registry, available=available
            )
            if evidence.get("resolver_status") != "resolved":
                continue  # registry unavailable → keep honest reuse
            recorded = spec.algorithm_preference or ""
            if recorded and algo and algo != recorded:
                stale.append(sid)
        return stale

    @staticmethod
    async def rerun_from_step(
        db: Session,
        prior_run_id: str,
        tool_registry,
        from_step: str,
        input_bindings: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        org_id: Optional[int] = None,
        expected_project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> WorkflowRun:
        """Incremental re-run (ADR-0092 A5): re-execute ``from_step`` and its
        descendants; keep every other already-completed step's results.

        Unlike resume_run (which reuses prior steps only for *recovery*), this
        is the deliberate invalidate-descendants entry: a changed dataset,
        parameter or AOI invalidates exactly the affected subgraph. Reused
        steps still require fingerprint equality (same guard as resume), and
        re-executed steps re-resolve capability → algorithm → tool through the
        registries — old tool calls are never replayed blindly.
        """
        prior = WorkflowEngine._load_and_authorize_run(
            db, prior_run_id, expected_project_id, user_id, org_id
        )
        workflow = db.execute(select(Workflow).where(Workflow.id == prior.workflow_id)).scalar_one_or_none()
        if not workflow:
            raise ValueError(f"Workflow {prior.workflow_id} not found")
        WorkflowEngine._authorize(workflow, expected_project_id, user_id, org_id)

        graph_spec = prior.graph_snapshot or {"steps": []}
        steps = [WorkflowStepSpec(**s) for s in graph_spec.get("steps", [])]
        step_map = {s.step_id: s for s in steps}
        if from_step not in step_map:
            raise ValueError(
                f"from_step '{from_step}' not found in workflow graph"
            )
        topo_order = WorkflowEngine.validate_dag(steps)

        # Descendants via reverse-adjacency BFS (O(S + E), single pass).
        descendants = WorkflowEngine._descendants_of(steps, {from_step}, step_map)

        completed = set(prior.completed_steps or [])
        seed_completed = [s for s in topo_order if s in (completed - descendants)]

        # Current dataset fingerprints (drift detection like resume).
        current_fps = WorkflowEngine._capture_dataset_fingerprints(db, workflow.project_id)
        drift_disclosure: Dict[str, Any] = {}
        if (prior.input_dataset_fingerprints or {}) != current_fps:
            drifted = sorted(
                k for k in set(prior.input_dataset_fingerprints or {}) | set(current_fps)
                if (prior.input_dataset_fingerprints or {}).get(k) != current_fps.get(k)
            )
            logger.info(
                "[WorkflowEngine] rerun_from_step of %s: input drift detected "
                "(%s) — invalidation set honored, fingerprints refreshed",
                prior.id, drifted,
            )
            drift_disclosure["input_drift"] = drifted

        # Registry view built once for every resolution below (stale-seed check
        # + the re-executed steps in _execute).
        try:
            available_tools = set(tool_registry.list_tools())
        except Exception:  # noqa: BLE001 — registry view unknown
            available_tools = None

        # Stale-compute guard (ADR-0092 A5): a seed step whose capability now
        # re-resolves to a DIFFERENT algorithm must not silently ride on its
        # old output — invalidate it (and its descendants) too, with a record.
        stale_steps = WorkflowEngine._stale_seed_steps(
            steps, seed_completed, tool_registry,
            available=available_tools, step_map=step_map,
        )
        if stale_steps:
            logger.info(
                "[WorkflowEngine] rerun_from_step of %s: capability re-resolution "
                "changed for seed steps %s — invalidating them too",
                prior.id, stale_steps,
            )
            expanded = WorkflowEngine._descendants_of(steps, set(stale_steps), step_map)
            seed_set = set(seed_completed) - expanded
            seed_completed = [s for s in seed_completed if s in seed_set]
            drift_disclosure["stale_algorithm_steps"] = stale_steps
        # Machine-readable invalidation record for the run row (audit trail:
        # which completed steps this rerun threw away and why).
        drift_disclosure["invalidated_steps"] = sorted(
            completed - set(seed_completed)
        )

        seed_outputs, seed_trace, seed_artifacts, _ = await WorkflowEngine._reconstruct_prior(
            db, prior, seed_completed, session_id
        )
        if seed_outputs is None and seed_completed:
            raise ValueError(
                f"cannot rerun from '{from_step}': reusable upstream steps' outputs "
                f"are no longer reconstructable (session expired); run a full rerun"
            )

        execution_order = [s for s in topo_order if s not in set(seed_completed)]
        revision = WorkflowEngine._revision_or_snapshot(
            db, workflow, graph_spec, prior, user_id
        )
        project_datasets = list(db.execute(
            select(ProjectDataset).where(
                ProjectDataset.project_id == workflow.project_id,
                ProjectDataset.detached_at.is_(None),
            )
        ).scalars().all())

        merged_bindings: Dict[str, Any] = dict(prior.input_bindings or {})
        if input_bindings:
            merged_bindings.update(input_bindings)

        return await WorkflowEngine._execute(
            db=db,
            workflow=workflow,
            revision=revision,
            tool_registry=tool_registry,
            steps=steps,
            execution_order=execution_order,
            input_bindings=merged_bindings,
            dataset_fingerprints=current_fps,
            project_datasets=project_datasets,
            user_id=user_id,
            org_id=org_id,
            session_id=session_id,
            seed_outputs=seed_outputs,
            seed_completed=seed_completed,
            seed_trace=seed_trace,
            seed_artifact_records=seed_artifacts,
            rerun_disclosures=drift_disclosure or None,
            available_tools=available_tools,
        )

    # ── run loading / reconstruction helpers ────────────────────────────────

    @staticmethod
    def _load_and_authorize_run(
        db: Session, run_id: str, expected_project_id: Optional[str], user_id: Optional[str], org_id: Optional[int]
    ) -> WorkflowRun:
        prior = db.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalar_one_or_none()
        if not prior:
            raise ValueError(f"WorkflowRun {run_id} not found")
        # Re-authorize via the run's own project scope (INV-AUTH1): replay/resume
        # cannot bypass the project ownership check. Fail CLOSED — if the caller
        # names a project, the run must belong to exactly that project (a NULL
        # project_id on a legacy/corrupt row must NOT bypass the check).
        if expected_project_id and prior.project_id != expected_project_id:
            logger.warning(
                "[WorkflowEngine] IDOR attempt: run %s (project %s) invoked under "
                "project %s by user %s", run_id, prior.project_id, expected_project_id, user_id,
            )
            raise ValueError(f"Run {run_id} does not belong to project {expected_project_id}")
        return prior

    @staticmethod
    def _revision_or_snapshot(
        db: Session, workflow: Workflow, graph_spec: Dict[str, Any], prior: WorkflowRun, user_id: Optional[str]
    ) -> WorkflowRevision:
        """Resolve the revision for a replay/resume, preferring the prior one."""
        if prior.workflow_revision_id:
            rev = db.execute(
                select(WorkflowRevision).where(WorkflowRevision.id == prior.workflow_revision_id)
            ).scalar_one_or_none()
            if rev:
                return rev
        # Prior revision missing (deleted): build a throwaway snapshot revision so
        # the run still records a concrete, immutable graph identity.
        graph_fp = compute_graph_fingerprint(graph_spec)
        latest_no = db.execute(
            select(WorkflowRevision.revision_no)
            .where(WorkflowRevision.workflow_id == workflow.id)
            .order_by(WorkflowRevision.revision_no.desc())
        ).scalars().first() or 0
        revision = WorkflowRevision(
            id=f"wfrev_{uuid.uuid4().hex[:16]}",
            workflow_id=workflow.id,
            revision_no=latest_no + 1,
            graph_spec=graph_spec,
            inputs_schema=workflow.inputs_schema,
            graph_fingerprint=graph_fp,
            created_by=user_id,
            created_at=datetime.now(timezone.utc),
        )
        db.add(revision)
        db.flush()
        return revision

    @staticmethod
    async def _reconstruct_prior(
        db: Session, prior: WorkflowRun, completed: List[str], session_id: Optional[str]
    ) -> Tuple[
        Optional[Dict[str, Dict[str, Any]]],
        List[Dict[str, Any]],
        List[Dict[str, Any]],
        Dict[str, str],
    ]:
        """Rebuild prior completed-step outputs/artifacts for resume.

        Returns (outputs, trace, artifact_records, step_artifact_map). outputs is
        None when reconstruction is impossible (a required artifact or its stored
        payload is gone) → caller decides reject vs full rerun.
        """
        # Load this run's artifacts via its lineage edges, scoped to the prior
        # run. Deliberately NO unscoped project-wide fallback: step_ids recur
        # across runs, so an unscoped lookup could attach another run's artifact
        # to a resumed step (cross-run contamination).
        from app.models.project import ArtifactLineage as _AL
        rows = db.execute(
            select(_AL).where(_AL.workflow_run_id == prior.id)
        ).scalars().all()
        artifact_ids = list({r.artifact_id for r in rows if r.artifact_id})
        artifacts: Dict[str, Artifact] = {}
        if artifact_ids:
            for art in db.execute(select(Artifact).where(Artifact.id.in_(artifact_ids))).scalars().all():
                artifacts[art.id] = art

        # Build step -> artifact by reading metadata_json.step_id.
        step_to_artifact: Dict[str, Artifact] = {}
        for art in artifacts.values():
            meta = art.metadata_json or {}
            sid = meta.get("step_id")
            if sid:
                step_to_artifact[sid] = art

        from app.services.session_data import session_data_manager

        seed_outputs: Dict[str, Dict[str, Any]] = {}
        step_artifact_map: Dict[str, str] = {}
        seed_artifacts: List[Dict[str, Any]] = []
        verified = False
        for sid in completed:
            art = step_to_artifact.get(sid)
            if art is None:
                return None, [], [], {}
            ref = art.storage_ref
            if session_id:
                # Probe the session store to verify the prior output is still
                # retrievable. Gone (or store error) → cannot faithfully resume.
                if not ref:
                    return None, [], [], {}
                try:
                    payload = await session_data_manager.get(session_id, ref)
                except Exception as e:
                    logger.warning(
                        "[WorkflowEngine] session store error reconstructing '%s' "
                        "for run %s: %s", ref, prior.id, e,
                    )
                    return None, [], [], {}
                if payload is None:
                    return None, [], [], {}
                verified = True
            # Reconstruct the SAME descriptor shape a fresh run produces
            # ({"result": {"ref_id": ...}}) so downstream tools get the structure
            # they expect, not the raw parked payload.
            seed_outputs[sid] = {"result": {"ref_id": ref}}
            step_artifact_map[sid] = art.id
            seed_artifacts.append({
                "id": art.id,
                "producing_step": sid,
                "artifact_type": art.artifact_type,
                "format": art.format,
                "crs": art.crs,
                "content_fingerprint": art.content_fingerprint,
                "storage_ref": ref,
            })

        if session_id is None:
            # API path has no session to probe against: reconstruct best-effort
            # and flag that retrievability was NOT verified (INV-RESUME1 caveat).
            logger.info(
                "[WorkflowEngine] resume of run %s reconstructed prior outputs "
                "without a session probe (best-effort)", prior.id,
            )
        _ = verified

        # Carry the prior trace entries for completed steps into the new manifest.
        seed_trace: List[Dict[str, Any]] = []
        for entry in (prior.execution_trace or []):
            if entry.get("step_id") in completed:
                seed_trace.append(entry)
        return seed_outputs, seed_trace, seed_artifacts, step_artifact_map

    # ── comparison ─────────────────────────────────────────────────────────

    @staticmethod
    def compare_runs(
        db: Optional[Session], run_a: WorkflowRun, run_b: WorkflowRun
    ) -> Dict[str, Any]:
        """Compare two runs across the full provenance surface (spec §23).

        ``db`` is accepted for signature compatibility but not required — the
        comparison reads only the (already-loaded) run rows, so it works on
        in-memory run objects too.
        """
        man_a = run_a.run_manifest or {}
        man_b = run_b.run_manifest or {}

        def _diff_dict(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> Dict[str, Any]:
            a = a or {}
            b = b or {}
            keys = set(a) | set(b)
            changed = sorted(k for k in keys if a.get(k) != b.get(k))
            return {"run_a": a, "run_b": b, "diff_keys": changed}

        inputs_changed = _diff_dict(run_a.input_bindings, run_b.input_bindings)
        dataset_changed = _diff_dict(
            run_a.input_dataset_fingerprints, run_b.input_dataset_fingerprints
        )

        tool_versions_a = man_a.get("tool_versions", {}) or {}
        tool_versions_b = man_b.get("tool_versions", {}) or {}
        tool_version_diff = {
            k: (tool_versions_a.get(k), tool_versions_b.get(k))
            for k in set(tool_versions_a) | set(tool_versions_b)
            if tool_versions_a.get(k) != tool_versions_b.get(k)
        }

        steps_a = {s.get("step_id"): s for s in (man_a.get("steps") or [])}
        steps_b = {s.get("step_id"): s for s in (man_b.get("steps") or [])}
        params_changed: Dict[str, Any] = {}
        for sid in set(steps_a) | set(steps_b):
            sa = steps_a.get(sid, {})
            sb = steps_b.get(sid, {})
            if sa.get("args") != sb.get("args") or sa.get("tool_name") != sb.get("tool_name"):
                params_changed[sid] = {
                    "tool_a": sa.get("tool_name"), "tool_b": sb.get("tool_name"),
                    "args_diff": _diff_dict(sa.get("args"), sb.get("args"))["diff_keys"],
                }

        artifacts_a = man_a.get("artifacts") or []
        artifacts_b = man_b.get("artifacts") or []
        output_artifacts_changed = {
            "run_a_status": run_a.status,
            "run_b_status": run_b.status,
            "run_a_artifact_count": len(artifacts_a),
            "run_b_artifact_count": len(artifacts_b),
            "run_a_fingerprints": sorted({a.get("content_fingerprint") for a in artifacts_a if a.get("content_fingerprint")}),
            "run_b_fingerprints": sorted({a.get("content_fingerprint") for a in artifacts_b if a.get("content_fingerprint")}),
        }

        metrics_changed = {
            "run_a_perf": run_a.cost_perf_summary,
            "run_b_perf": run_b.cost_perf_summary,
        }
        warnings_changed = {
            "run_a_error": run_a.error_message,
            "run_b_error": run_b.error_message,
            "run_a_completed_steps": run_a.completed_steps,
            "run_b_completed_steps": run_b.completed_steps,
        }

        return {
            "run_a_id": run_a.id,
            "run_b_id": run_b.id,
            "revision": {
                "run_a_revision": run_a.workflow_revision_id,
                "run_b_revision": run_b.workflow_revision_id,
                "run_a_graph_fingerprint": man_a.get("graph_fingerprint"),
                "run_b_graph_fingerprint": man_b.get("graph_fingerprint"),
                "graph_same": man_a.get("graph_fingerprint") == man_b.get("graph_fingerprint"),
            },
            "inputs_changed": inputs_changed,
            "dataset_versions_changed": dataset_changed,
            "tool_versions_changed": tool_version_diff,
            "params_changed": params_changed,
            "output_artifacts_changed": output_artifacts_changed,
            "metrics_changed": metrics_changed,
            "warnings_changed": warnings_changed,
            "run_fingerprint": {
                "run_a": run_a.run_fingerprint,
                "run_b": run_b.run_fingerprint,
                "same": run_a.run_fingerprint == run_b.run_fingerprint and run_a.run_fingerprint is not None,
            },
        }
