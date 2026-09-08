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

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

from app.services.jobs.redaction import SENSITIVE_KEY_PARTS
from app.services.provenance.fingerprint import canonical_dumps, _sha256

# Keys whose values are typically large inline payloads — excluded from the
# fingerprint projection (and trimmed in stored summaries) so the manifest stays
# compact and the fingerprint stable & cheap to compute.
_LARGE_KEYS = frozenset(
    {"geojson", "data", "features", "geometry", "coordinates", "raster_source", "summary"}
)
_MAX_LEAF_LEN = 200

# ── Wave 11 (audit 08 §2.4 / §6.2.5): write-time provenance redaction ────────
# Provenance rows are links + bounded facts, never payload or credential stores.
# Until now execution_trace[].args / ArtifactLineage.parameters / manifest step
# args were only size-trimmed — secret values (a fabric tool's ``password`` arg)
# and inline GeoJSON landed verbatim in DB rows. The redactor below runs at the
# persistence boundary: keys are kept, values are redacted.
# Round-1 review (SEC MINOR-1): the marker list is the UNION with the durable
# jobs redactor's ``SENSITIVE_KEY_PARTS`` (app/services/jobs/redaction.py) —
# ``s3_access_key`` / ``signed_url`` / ``private_key`` style args were not
# covered by the narrower local tuple. Import direction is verified safe:
# ``jobs.redaction`` is a pure-stdlib leaf module and the ``jobs`` package
# never imports ``provenance`` (no cycle).
_SECRET_KEY_MARKERS: tuple = SENSITIVE_KEY_PARTS
#: string leaves longer than this are replaced by a sha256 digest + size note
_MAX_PERSISTED_LEAF_CHARS = 512
#: hard budget for the fully-redacted params JSON (audit 08 §6.2.5)
_MAX_REDACTED_JSON_CHARS = 4096
_REDACTED = "[REDACTED]"


def _is_secret_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def _digest_note(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"[digest:sha256:{digest} bytes={len(value)}]"


def _bound_redacted(value: Any) -> Any:
    """Keep the redacted dict when it fits the JSON budget; else fall back to a
    deterministically truncated canonical JSON string (decision_log precedent).
    The fallback is honest about the truncation and stays within the budget in
    its PERSISTED (JSON-escaped) form — the DB column stores json.dumps of the
    value, so that is the length that must fit."""
    try:
        dumped = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        dumped = str(value)
    if len(dumped) <= _MAX_REDACTED_JSON_CHARS:
        return value
    marker = "...[truncated]"
    keep = _MAX_REDACTED_JSON_CHARS
    fallback = dumped[:keep] + marker
    while (
        keep > 0
        and len(json.dumps(fallback, ensure_ascii=False)) > _MAX_REDACTED_JSON_CHARS
    ):
        keep = max(0, keep - 256)
        fallback = dumped[:keep] + marker
    return fallback


def redact_provenance_args(value: Any, depth: int = 0) -> Any:
    """Redact + bound tool args before they reach provenance persistence.

    - large payload keys (``_LARGE_KEYS``: features/geojson/geometry/...) are
      dropped entirely — links stay links, lineage never becomes a shadow
      payload store (audit 08 §5.2 last row);
    - secret-looking keys (password/secret/token/api_key/...) keep the key,
      the VALUE is replaced with ``"[REDACTED]"``;
    - oversized string leaves (>512 chars) keep the key and shrink to a
      ``sha256[:16]`` digest + byte-size note (existence verifiable, content
      not recoverable);
    - the result is bounded: redacted JSON exceeding 4KB is deterministically
      truncated (with a visible marker).

    Deterministic and idempotent (already-redacted values pass through), so
    engine-side and service-side application compose safely.
    """
    if depth > 8:
        return None
    if isinstance(value, dict):
        out: Any = {}
        for k, v in value.items():
            if isinstance(k, str) and k in _LARGE_KEYS:
                continue
            if _is_secret_key(k):
                out[k] = _REDACTED
            else:
                out[k] = redact_provenance_args(v, depth + 1)
        return _bound_redacted(out)
    if isinstance(value, list):
        return [redact_provenance_args(v, depth + 1) for v in value[:64]]
    if isinstance(value, str) and len(value) > _MAX_PERSISTED_LEAF_CHARS:
        return _digest_note(value)
    return value


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
    runtime_manifest_fingerprint: Optional[str] = None,
    mapspec_fingerprint: Optional[str] = None,
    product_facets: Optional[List[Dict[str, Any]]] = None,
    qa_summary: Optional[Dict[str, Any]] = None,
    finalization_summary: Optional[Dict[str, Any]] = None,
    reproducibility: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the canonical run manifest (the full, storable document).

    ``steps`` items: {step_id, tool_name, tool_version, status, args} (args are
    secret-redacted + trimmed for size). ``artifacts`` items carry the truthful
    per-artifact metadata + ids (ids are NOT part of the fingerprint).

    ADR-0092 A2 executable-snapshot extensions (all bounded projections):
    ``runtime_manifest_fingerprint`` (registry generation the run executed
    under), ``mapspec_fingerprint`` (desired map state at run end),
    ``product_facets`` / ``qa_summary`` / ``finalization_summary`` (product
    outcome evidence), and ``reproducibility`` (Wave-11, audit 08 §6.2.1:
    {classification, basis[]} honest verdict about whether re-running would
    reproduce the outputs). The outcome blocks are deliberately OUTSIDE the run
    fingerprint projection (see _stable_projection): they describe results,
    not the compute plan, and two replays may legitimately differ in render/QA
    timing without being different runs.
    """
    steps_list: List[Dict[str, Any]] = []
    for s in steps:
        steps_list.append(
            {
                "step_id": s.get("step_id"),
                "tool_name": s.get("tool_name"),
                "tool_version": s.get("tool_version"),
                "status": s.get("status"),
                "capability": s.get("capability"),
                "algorithm": s.get("algorithm"),
                "args": _trim(redact_provenance_args(s.get("args") or {})),
            }
        )

    manifest = {
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
    # Bounded outcome evidence (present only when the caller supplied it —
    # legacy engine paths keep their exact manifest shape).
    if runtime_manifest_fingerprint:
        manifest["runtime_manifest_fingerprint"] = runtime_manifest_fingerprint
    if mapspec_fingerprint:
        manifest["mapspec_fingerprint"] = mapspec_fingerprint
    if product_facets:
        manifest["product_facets"] = product_facets[:32]
    if qa_summary:
        manifest["qa_summary"] = qa_summary
    if finalization_summary:
        manifest["finalization_summary"] = finalization_summary
    if reproducibility:
        manifest["reproducibility"] = reproducibility
    return manifest


def _stable_projection(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Project the manifest down to the deterministic, reproducible identity.

    Excludes: artifact ids + storage refs (random per run); per-step **resolved
    args** (which carry random upstream ``ref_id``s when a step binds to a prior
    step's output — folding those in would make two replays of the same workflow
    hash differently, violating INV-MAN2); and any volatile field.

    Keeps: graph identity, declared inputs, input dataset fingerprints, and the
    per-step (tool + tool_version + status + capability/algorithm) compute plan.
    Static per-step args are already captured in ``graph_fingerprint``; bound
    inputs in ``inputs``. Per-step capability/algorithm ARE folded in (they are
    deterministic functions of graph + registry generation) — a rerun that
    re-resolves a capability to a different algorithm is a *different* compute
    plan and must fingerprint differently (ADR-0092 A2).
    """
    steps = [
        {
            "step_id": s.get("step_id"),
            "tool_name": s.get("tool_name"),
            "tool_version": s.get("tool_version"),
            "status": s.get("status"),
            "capability": s.get("capability"),
            "algorithm": s.get("algorithm"),
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
        self._runtime_manifest_fp: Optional[str] = None
        self._mapspec_fp: Optional[str] = None
        self._product_facets: Optional[List[Dict[str, Any]]] = None
        self._qa_summary: Optional[Dict[str, Any]] = None
        self._finalization_summary: Optional[Dict[str, Any]] = None
        self._reproducibility: Optional[Dict[str, Any]] = None

    def set_outcome_context(
        self,
        *,
        runtime_manifest_fingerprint: Optional[str] = None,
        mapspec_fingerprint: Optional[str] = None,
        product_facets: Optional[List[Dict[str, Any]]] = None,
        qa_summary: Optional[Dict[str, Any]] = None,
        finalization_summary: Optional[Dict[str, Any]] = None,
        reproducibility: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Attach bounded product-outcome evidence (ADR-0092 A2). Called once
        before :meth:`build`; every field is optional and omitted fields keep
        the legacy manifest shape."""
        if runtime_manifest_fingerprint:
            self._runtime_manifest_fp = runtime_manifest_fingerprint
        if mapspec_fingerprint:
            self._mapspec_fp = mapspec_fingerprint
        if product_facets:
            self._product_facets = product_facets
        if qa_summary:
            self._qa_summary = qa_summary
        if finalization_summary:
            self._finalization_summary = finalization_summary
        if reproducibility:
            self._reproducibility = reproducibility

    def add_step(
        self,
        *,
        step_id: str,
        tool_name: str,
        tool_version: str,
        status: str,
        args: Optional[Dict[str, Any]] = None,
        capability: Optional[str] = None,
        algorithm: Optional[str] = None,
    ) -> None:
        self._steps.append(
            {
                "step_id": step_id,
                "tool_name": tool_name,
                "tool_version": tool_version,
                "status": status,
                "capability": capability,
                "algorithm": algorithm,
                    "args": _trim(redact_provenance_args(args or {})),
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
            runtime_manifest_fingerprint=self._runtime_manifest_fp,
            mapspec_fingerprint=self._mapspec_fp,
            product_facets=self._product_facets,
            qa_summary=self._qa_summary,
            finalization_summary=self._finalization_summary,
            reproducibility=self._reproducibility,
        )
