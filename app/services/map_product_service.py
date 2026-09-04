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

Provenance is always COMPUTED server-side from run manifests — the REST
schema deliberately offers no client-supplied fingerprint/diff fields (a
forged diff_summary would let an LLM assert false provenance into the
durable ledger).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project import MapProductVersion, WorkflowRun
from app.services.provenance.fingerprint import canonical_dumps

logger = logging.getLogger(__name__)

#: Bounded per-step compute-plan projection (steps capped, args trimmed by the
#: manifest builder before landing here).
_MAX_PLAN_STEPS = 64


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _project_steps(
    manifest: Dict[str, Any], *, include_args: bool = False, sort: bool = False
) -> List[Dict[str, Any]]:
    """Single per-step manifest projection (fingerprint / compute-plan / diff
    all share this — four hand-rolled projections used to drift apart)."""
    plan: List[Dict[str, Any]] = []
    for s in (manifest.get("steps") or [])[:_MAX_PLAN_STEPS]:
        if not isinstance(s, dict):
            continue
        row: Dict[str, Any] = {
            "step_id": s.get("step_id"),
            "capability": s.get("capability"),
            "algorithm": s.get("algorithm"),
            "tool_name": s.get("tool_name"),
        }
        if include_args:
            row["args"] = s.get("args") or {}
        plan.append(row)
    if sort:
        plan.sort(key=lambda r: str(r.get("step_id") or ""))
    return plan


def compute_product_fingerprint(
    *,
    input_dataset_fingerprints: Optional[Dict[str, str]],
    run_manifest: Optional[Dict[str, Any]],
    mapspec_fingerprint: Optional[str],
    artifact_fingerprints: List[str],
) -> str:
    """Deterministic product identity over its substantive inputs/outputs."""
    payload = {
        "inputs": dict(sorted((input_dataset_fingerprints or {}).items())),
        "compute_plan": _project_steps(run_manifest or {}, sort=True),
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
        run_manifest: Optional[Dict[str, Any]] = None,
    ) -> MapProductVersion:
        """Append one product version (per-project monotonic version_no).

        Fingerprint and diff are ALWAYS computed here from the run manifest +
        previous version (no client-supplied provenance). An identical
        fingerprint to the previous version is still recorded as a new row
        (the timeline is evidence); the diff simply reports no changes.
        ``run_manifest`` may be supplied directly for versions not bound to a
        stored run; otherwise it is read from the referenced run row.
        """
        run: Optional[WorkflowRun] = None
        if workflow_run_id:
            # Project-scoped lookup (fail-closed, mirrors _load_and_authorize_run):
            # a foreign project's run must never feed this project's ledger.
            run = db.execute(
                select(WorkflowRun).where(
                    WorkflowRun.id == workflow_run_id,
                    WorkflowRun.project_id == project_id,
                )
            ).scalar_one_or_none()
            if run is None:
                raise ValueError(f"WorkflowRun {workflow_run_id} not found in project {project_id}")

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

        product_fingerprint = compute_product_fingerprint(
            input_dataset_fingerprints=input_fps,
            run_manifest=manifest,
            mapspec_fingerprint=mapspec_fingerprint,
            artifact_fingerprints=fingerprints,
        )
        diff_summary = MapProductService.diff_versions(previous, {
            "input_dataset_fingerprints": input_fps,
            "run_manifest": manifest,
            "mapspec_fingerprint": mapspec_fingerprint,
            "artifact_fingerprints": fingerprints,
        })

        version_no = (previous.version_no if previous else 0) + 1
        row = MapProductVersion(
            project_id=project_id,
            version_no=version_no,
            product_fingerprint=product_fingerprint,
            input_dataset_fingerprints=input_fps,
            compute_plan=_project_steps(manifest, include_args=True),
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
        return _project_steps(manifest, include_args=True)

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
        # current one from the (possibly inline) manifest. Both use the
        # shared projection so the diff compares like with like.
        prev_manifest: Dict[str, Any] = {
            "steps": list(getattr(previous, "compute_plan", None) or [])
        }
        curr_manifest: Dict[str, Any] = current.get("run_manifest") or {}

        def _index(manifest: Dict[str, Any], key: str) -> Dict[str, Any]:
            return {
                str(s.get("step_id")): (s.get(key) or "")
                for s in (manifest.get("steps") or [])
                if isinstance(s, dict) and s.get("step_id")
            }

        prev_algos, curr_algos = _index(prev_manifest, "algorithm"), _index(curr_manifest, "algorithm")
        prev_args, curr_args = _index(prev_manifest, "args"), _index(curr_manifest, "args")

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
    def list_versions_paginated(
        db: Session, project_id: str, *, limit: int = 50, offset: int = 0
    ) -> tuple[List[MapProductVersion], int]:
        """(rows, total) for the paginated ledger endpoint — newest first."""
        from sqlalchemy import func

        rows = list(
            db.execute(
                select(MapProductVersion)
                .where(MapProductVersion.project_id == project_id)
                .order_by(MapProductVersion.version_no.desc())
                .offset(offset)
                .limit(limit)
            ).scalars().all()
        )
        total = int(
            db.execute(
                select(func.count(MapProductVersion.id)).where(
                    MapProductVersion.project_id == project_id
                )
            ).scalar()
            or 0
        )
        return rows, total

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

    @staticmethod
    def _stored_version_diff_input(row: MapProductVersion) -> Dict[str, Any]:
        """Project a STORED version row into the dict shape ``diff_versions``
        expects for ``current`` (mirrors the columns the row was recorded
        with, so a pairwise diff compares like with like)."""
        return {
            "input_dataset_fingerprints": dict(row.input_dataset_fingerprints or {}),
            "run_manifest": {"steps": list(row.compute_plan or [])},
            "artifact_fingerprints": list(row.output_fingerprints or []),
            "mapspec_fingerprint": row.mapspec_fingerprint,
        }

    @staticmethod
    def diff_versions_pairwise(
        db: Session, project_id: str, from_version_no: int, to_version_no: int
    ) -> Dict[str, Any]:
        """Five-dimension diff between ANY two stored versions (ADR-0092 A6
        + version-workspace UI): reuses ``diff_versions`` on the stored
        projections, then attaches the drill-down details the UI renders
        (before/after fingerprints, changed parameter keys, artifact
        membership changes). Raises ValueError when either version is
        missing (route maps to 404)."""
        prev = MapProductService.get_version(db, project_id, from_version_no)
        curr = MapProductService.get_version(db, project_id, to_version_no)
        if prev is None or curr is None:
            missing = from_version_no if prev is None else to_version_no
            raise ValueError(f"map product version not found: {missing}")

        diff = MapProductService.diff_versions(
            prev, MapProductService._stored_version_diff_input(curr)
        )
        diff["from_version_no"] = from_version_no
        diff["to_version_no"] = to_version_no

        # ── drill-down (bounded, structured summaries — not raw dumps) ──
        prev_inputs = dict(prev.input_dataset_fingerprints or {})
        curr_inputs = dict(curr.input_dataset_fingerprints or {})

        def _index_args(row: MapProductVersion) -> Dict[str, Any]:
            return {
                str(s.get("step_id")): (s.get("args") or "")
                for s in (row.compute_plan or [])
                if isinstance(s, dict) and s.get("step_id")
            }

        prev_args, curr_args = _index_args(prev), _index_args(curr)
        changed_param_steps = [
            {
                "step_id": sid,
                "from": prev_args.get(sid),
                "to": curr_args.get(sid),
            }
            for sid in sorted(set(prev_args) | set(curr_args))
            if prev_args.get(sid) != curr_args.get(sid)
        ]

        def _index_algo(row: MapProductVersion) -> Dict[str, str]:
            return {
                str(s.get("step_id")): str(s.get("algorithm") or "")
                for s in (row.compute_plan or [])
                if isinstance(s, dict) and s.get("step_id")
            }

        prev_algos, curr_algos = _index_algo(prev), _index_algo(curr)
        changed_algo_steps = [
            {
                "step_id": sid,
                "from": prev_algos.get(sid) or None,
                "to": curr_algos.get(sid) or None,
            }
            for sid in sorted(set(prev_algos) | set(curr_algos))
            if prev_algos.get(sid) != curr_algos.get(sid)
        ]

        # Membership uses output fingerprints — artifact_ids are only
        # extracted when a WorkflowRun backs the version (inline-manifest
        # versions record fingerprints alone).
        prev_outs = set(str(f) for f in (prev.output_fingerprints or []) if f)
        curr_outs = set(str(f) for f in (curr.output_fingerprints or []) if f)
        diff["details"] = {
            "input_dataset_fingerprints": {
                "from": prev_inputs,
                "to": curr_inputs,
                "changed_keys": sorted(
                    set(prev_inputs) ^ set(curr_inputs)
                    | {k for k in set(prev_inputs) & set(curr_inputs)
                       if prev_inputs[k] != curr_inputs[k]}
                ),
            },
            "algorithm_steps": changed_algo_steps,
            "parameter_steps": changed_param_steps,
            "mapspec_fingerprint": {
                "from": prev.mapspec_fingerprint,
                "to": curr.mapspec_fingerprint,
            },
            "artifacts": {
                "added": sorted(curr_outs - prev_outs),
                "removed": sorted(prev_outs - curr_outs),
                "unchanged_count": len(prev_outs & curr_outs),
            },
            "workflow_runs": {
                "from": prev.workflow_run_id,
                "to": curr.workflow_run_id,
            },
        }
        return diff
