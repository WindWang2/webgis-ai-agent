"""Reproducibility manifest + run fingerprint (INV-MAN1/MAN2).

A run manifest is a canonical JSON document that fully describes *what* a run
executed, so the run is interpretable and comparable without the (mutable) live
Workflow row. The run fingerprint is sha256 over a **stable projection** of the
manifest — the projection excludes every volatile/random field (run id,
timestamps, durations, random artifact ids and their random storage ref_ids) so
that two replays of the same (workflow revision, inputs, tool versions) yield the
same fingerprint (INV-MAN2). Output content identity is intentionally NOT folded
in: tool outputs are stored under random ref_ids and their content is not hashed
in the engine path, so doing so would make the fingerprint non-deterministic.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.services.provenance.fingerprint import canonical_dumps, _sha256

# Keys whose values are typically large inline payloads — excluded from the
# fingerprint projection (and trimmed in stored summaries) so the manifest stays
# compact and the fingerprint stable & cheap to compute.
_LARGE_KEYS = frozenset(
    {"geojson", "data", "features", "geometry", "coordinates", "raster_source", "summary"}
)
_MAX_LEAF_LEN = 200


def _trim(value: Any, depth: int = 0) -> Any:
    """Recursively drop large keys and truncate long string leaves."""
    if depth > 8:
        return None
    if isinstance(value, dict):
        return {k: _trim(v, depth + 1) for k, v in value.items() if k not in _LARGE_KEYS}
    if isinstance(value, list):
        return [_trim(v, depth + 1) for v in value][:64]
    if isinstance(value, str):
        return value if len(value) <= _MAX_LEAF_LEN else value[:_MAX_LEAF_LEN] + "…"
    return value


def build_run_manifest(
    *,
    workflow_revision_id: Optional[str],
    graph_fingerprint: Optional[str],
    input_bindings: Optional[Dict[str, Any]],
    input_dataset_fingerprints: Optional[Dict[str, str]],
    steps: Iterable[Dict[str, Any]],
    tool_versions: Dict[str, str],
    artifacts: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Assemble the canonical run manifest (the full, storable document).

    ``steps`` items: {step_id, tool_name, tool_version, status, args} (args are
    trimmed for size). ``artifacts`` items carry the truthful per-artifact
    metadata + ids (ids are NOT part of the fingerprint).
    """
    steps_list: List[Dict[str, Any]] = []
    for s in steps:
        steps_list.append(
            {
                "step_id": s.get("step_id"),
                "tool_name": s.get("tool_name"),
                "tool_version": s.get("tool_version"),
                "status": s.get("status"),
                "args": _trim(s.get("args") or {}),
            }
        )

    return {
        "workflow_revision_id": workflow_revision_id,
        "graph_fingerprint": graph_fingerprint,
        "inputs": _trim(input_bindings or {}),
        "input_dataset_fingerprints": dict(input_dataset_fingerprints or {}),
        "steps": steps_list,
        "tool_versions": dict(tool_versions or {}),
        "artifacts": [
            {
                "id": a.get("id"),
                "producing_step": a.get("producing_step"),
                "artifact_type": a.get("artifact_type"),
                "format": a.get("format"),
                "crs": a.get("crs"),
                "content_fingerprint": a.get("content_fingerprint"),
                "storage_ref": a.get("storage_ref"),
            }
            for a in artifacts
        ],
    }


def _stable_projection(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Project the manifest down to the deterministic, reproducible identity.

    Excludes: artifact ids + storage refs (random per run); per-step **resolved
    args** (which carry random upstream ``ref_id``s when a step binds to a prior
    step's output — folding those in would make two replays of the same workflow
    hash differently, violating INV-MAN2); and any volatile field.

    Keeps: graph identity, declared inputs, input dataset fingerprints, and the
    per-step (tool + tool_version + status) compute plan. Static per-step args
    are already captured in ``graph_fingerprint``; bound inputs in ``inputs``.
    """
    steps = [
        {
            "step_id": s.get("step_id"),
            "tool_name": s.get("tool_name"),
            "tool_version": s.get("tool_version"),
            "status": s.get("status"),
        }
        for s in manifest.get("steps", [])
    ]
    steps.sort(key=lambda s: (s.get("step_id") or "",))
    return {
        "graph_fingerprint": manifest.get("graph_fingerprint"),
        "inputs": manifest.get("inputs"),
        "input_dataset_fingerprints": dict(
            sorted((manifest.get("input_dataset_fingerprints") or {}).items())
        ),
        "steps": steps,
        "tool_versions": dict(sorted((manifest.get("tool_versions") or {}).items())),
    }


def compute_run_fingerprint(manifest: Dict[str, Any]) -> str:
    """sha256 over the stable projection of the manifest (INV-MAN2)."""
    return _sha256(canonical_dumps(_stable_projection(manifest)))


class RunManifestBuilder:
    """Incremental builder used by the engine as steps complete.

    Avoids rebuilding the whole manifest on each step; the engine calls
    :meth:`add_step` / :meth:`add_artifact` and finally :meth:`build`.
    """

    def __init__(
        self,
        *,
        workflow_revision_id: Optional[str],
        graph_fingerprint: Optional[str],
        input_bindings: Optional[Dict[str, Any]],
        input_dataset_fingerprints: Optional[Dict[str, str]],
    ):
        self._revision_id = workflow_revision_id
        self._graph_fp = graph_fingerprint
        self._inputs = input_bindings or {}
        self._dataset_fps = input_dataset_fingerprints or {}
        self._steps: List[Dict[str, Any]] = []
        self._tool_versions: Dict[str, str] = {}
        self._artifacts: List[Dict[str, Any]] = []

    def add_step(
        self,
        *,
        step_id: str,
        tool_name: str,
        tool_version: str,
        status: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._steps.append(
            {
                "step_id": step_id,
                "tool_name": tool_name,
                "tool_version": tool_version,
                "status": status,
                "args": _trim(args or {}),
            }
        )
        if tool_name:
            self._tool_versions.setdefault(tool_name, tool_version)

    def add_artifact(self, **fields: Any) -> None:
        self._artifacts.append(fields)

    def build(self) -> Dict[str, Any]:
        return build_run_manifest(
            workflow_revision_id=self._revision_id,
            graph_fingerprint=self._graph_fp,
            input_bindings=self._inputs,
            input_dataset_fingerprints=self._dataset_fps,
            steps=self._steps,
            tool_versions=self._tool_versions,
            artifacts=self._artifacts,
        )
