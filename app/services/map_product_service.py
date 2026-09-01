"""Map Product Versioning (ADR-0092 A6).

A project's map product is versioned every time its substantive state lands:
the artifact set of a workflow run plus the MapSpec generation it finalized
against. Each version records a machine-readable diff against the previous
one across five dimensions:

    data_changed       — input dataset fingerprints moved
    algorithm_changed  — per-step resolved algorithms moved (registry re-resolution)
    parameter_changed  — step args moved
    style_changed      — MapSpec fingerprint moved while the compute plan didn't
    output_changed     — artifact content fingerprints moved

``style_changed`` without the others is the machine-readable proof of the
"style-only change ⇒ no analysis re-computation" contract; ``data_changed``
is the trigger that *must* invalidate descendants on the next rerun.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import MapProductVersion, WorkflowRun
from app.services.provenance.fingerprint import canonical_dumps, _sha256

logger = logging.getLogger(__name__)


def compute_product_fingerprint(
    *,
    input_dataset_fingerprints: Optional[Dict[str, str]],
    run_manifest: Optional[Dict[str, Any]],
    mapspec_fingerprint: Optional[str],
    artifact_fingerprints: List[str],
) -> str:
    """Deterministic product identity over its substantive inputs/outputs."""
    manifest = run_manifest or {}
    steps = sorted(
        (
            {
                "step_id": s.get("step_id"),
                "tool_name": s.get("tool_name"),
                "capability": s.get("capability"),
                "algorithm": s.get("algorithm"),
            }
            for s in (manifest.get("steps") or [])
        ),
        key=lambda s: s.get("step_id") or "",
    )
    payload = {
        "inputs": dict(sorted((input_dataset_fingerprints or {}).items())),
        "compute_plan": steps,
        "mapspec": mapspec_fingerprint or "",
        "outputs": sorted(f for f in artifact_fingerprints if f),
    }
    return _sha256(canonical_dumps(payload))


class MapProductService:
    @staticmethod
    def record_version(
        db: Session,
        project_id: str,
        *,
        workflow_run_id: Optional[str] = None,
        mapspec_fingerprint: Optional[str] = None,
        mapspec_revision: Optional[int] = None,
        recipe_id: Optional[str] = None,
        artifact_ids: Optional[List[str]] = None,
        input_dataset_fingerprints: Optional[Dict[str, str]] = None,
        product_fingerprint: Optional[str] = None,
        diff_summary: Optional[Dict[str, Any]] = None,
        run_manifest: Optional[Dict[str, Any]] = None,
    ) -> MapProductVersion:
        """Append one product version (per-project monotonic version_no).

        When ``product_fingerprint`` / ``diff_summary`` are omitted they are
        computed from the run manifest + previous version. An identical
        fingerprint to the previous version is still recorded as a new row
        (the timeline is evidence); the diff simply reports no changes.
        ``run_manifest`` may be supplied directly for versions not bound to a
        stored run; otherwise it is read from the referenced run row.
        """
        run: Optional[WorkflowRun] = None
        if workflow_run_id:
            run = db.execute(
                select(WorkflowRun).where(WorkflowRun.id == workflow_run_id)
            ).scalar_one_or_none()
            if run is None:
                raise ValueError(f"WorkflowRun {workflow_run_id} not found")

        manifest = run_manifest if run_manifest is not None else (
            (run.run_manifest if run else None) or {}
        )
        artifact_ids = list(artifact_ids or [])
        if not artifact_ids and run:
            artifact_ids = [
                str(a.get("id"))
                for a in (manifest.get("artifacts") or [])
                if a.get("id")
            ]
        fingerprints = [
            str(a.get("content_fingerprint"))
            for a in (manifest.get("artifacts") or [])
            if a.get("content_fingerprint")
        ]
        input_fps = dict(input_dataset_fingerprints or {})
        if not input_fps and run:
            input_fps = dict(run.input_dataset_fingerprints or {})

        previous = db.execute(
            select(MapProductVersion)
            .where(MapProductVersion.project_id == project_id)
            .order_by(MapProductVersion.version_no.desc())
        ).scalars().first()

        if product_fingerprint is None:
            product_fingerprint = compute_product_fingerprint(
                input_dataset_fingerprints=input_fps,
                run_manifest=manifest,
                mapspec_fingerprint=mapspec_fingerprint,
                artifact_fingerprints=fingerprints,
            )
        if diff_summary is None:
            diff_summary = MapProductService.diff_versions(previous, {
                "input_dataset_fingerprints": input_fps,
                "run_manifest": manifest,
                "mapspec_fingerprint": mapspec_fingerprint,
                "artifact_fingerprints": fingerprints,
            })

        version_no = (previous.version_no if previous else 0) + 1
        compute_plan = MapProductService.bounded_compute_plan(manifest)
        row = MapProductVersion(
            project_id=project_id,
            version_no=version_no,
            product_fingerprint=product_fingerprint,
            input_dataset_fingerprints=input_fps,
            compute_plan=compute_plan,
            output_fingerprints=sorted(f for f in fingerprints if f)[:128],
            workflow_id=str(run.workflow_id) if run else None,
            workflow_run_id=workflow_run_id,
            mapspec_fingerprint=mapspec_fingerprint,
            mapspec_revision=mapspec_revision,
            recipe_id=recipe_id,
            artifact_ids=artifact_ids[:128],
            diff_summary=diff_summary,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def bounded_compute_plan(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Bounded per-step compute-plan projection stored with each version."""
        plan: List[Dict[str, Any]] = []
        for s in (manifest.get("steps") or [])[:64]:
            if not isinstance(s, dict):
                continue
            plan.append({
                "step_id": s.get("step_id"),
                "capability": s.get("capability"),
                "algorithm": s.get("algorithm"),
                "tool_name": s.get("tool_name"),
                "args": s.get("args") or {},
            })
        return plan

    @staticmethod
    def diff_versions(
        previous: Optional[Any],
        current: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Five-dimension diff of a product state vs the previous one."""
        prev_inputs: Dict[str, str] = dict(
            getattr(previous, "input_dataset_fingerprints", None) or {}
        )
        curr_inputs: Dict[str, str] = dict(current.get("input_dataset_fingerprints") or {})

        # Previous compute plan comes from the stored snapshot column; the
        # current one from the (possibly inline) manifest.
        prev_plan: List[Dict[str, Any]] = list(
            getattr(previous, "compute_plan", None) or []
        )
        prev_manifest: Dict[str, Any] = {"steps": prev_plan}
        curr_manifest: Dict[str, Any] = current.get("run_manifest") or {}

        def _algos(manifest: Dict[str, Any]) -> Dict[str, str]:
            return {
                str(s.get("step_id")): str(s.get("algorithm") or "")
                for s in (manifest.get("steps") or [])
                if s.get("step_id")
            }

        def _args(manifest: Dict[str, Any]) -> Dict[str, Any]:
            return {
                str(s.get("step_id")): s.get("args") or {}
                for s in (manifest.get("steps") or [])
                if s.get("step_id")
            }

        prev_algos, curr_algos = _algos(prev_manifest), _algos(curr_manifest)
        prev_args, curr_args = _args(prev_manifest), _args(curr_manifest)

        prev_outputs = sorted(
            str(fp)
            for fp in (getattr(previous, "output_fingerprints", None) or [])
            if fp
        )
        curr_outputs = sorted(
            str(fp) for fp in (current.get("artifact_fingerprints") or []) if fp
        )

        prev_style = getattr(previous, "mapspec_fingerprint", None)
        curr_style = current.get("mapspec_fingerprint")
        style_changed = bool(prev_style) and bool(curr_style) and prev_style != curr_style

        data_changed = prev_inputs != curr_inputs
        algorithm_changed = prev_algos != curr_algos
        parameter_changed = prev_args != curr_args
        output_changed = prev_outputs != curr_outputs

        return {
            "vs_version_no": previous.version_no if previous is not None else None,
            "data_changed": data_changed,
            "algorithm_changed": algorithm_changed,
            "parameter_changed": parameter_changed,
            "style_changed": style_changed,
            "output_changed": output_changed,
            # Convenience flags for UI copy.
            "analysis_recomputation_expected": bool(
                data_changed or algorithm_changed or parameter_changed
            ),
        }

    @staticmethod
    def list_versions(db: Session, project_id: str) -> List[MapProductVersion]:
        return list(
            db.execute(
                select(MapProductVersion)
                .where(MapProductVersion.project_id == project_id)
                .order_by(MapProductVersion.version_no.asc())
            ).scalars().all()
        )

    @staticmethod
    def get_version(
        db: Session, project_id: str, version_no: int
    ) -> Optional[MapProductVersion]:
        return db.execute(
            select(MapProductVersion).where(
                MapProductVersion.project_id == project_id,
                MapProductVersion.version_no == version_no,
            )
        ).scalar_one_or_none()
