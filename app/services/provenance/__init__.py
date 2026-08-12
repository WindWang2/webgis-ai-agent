"""Provenance primitives: deterministic fingerprints and reproducibility manifests.

This package is the single source of truth for the canonical-serialization /
hashing rules that make workflows reproducible (INV-FP / INV-REV / INV-MAN).
Everything here is **pure and deterministic**: no clock, no RNG, no I/O.
"""
from app.services.provenance.fingerprint import (
    canonical_dumps,
    compute_content_fingerprint,
    compute_dataset_fingerprint,
    compute_graph_fingerprint,
    extract_artifact_metadata,
)
from app.services.provenance.manifest import (
    RunManifestBuilder,
    build_run_manifest,
    compute_run_fingerprint,
)
from app.services.provenance.context import (
    ToolExecutionContext,
    get_tool_execution_context,
    reset_tool_execution_context,
    set_tool_execution_context,
)

__all__ = [
    "canonical_dumps",
    "compute_dataset_fingerprint",
    "compute_graph_fingerprint",
    "compute_content_fingerprint",
    "extract_artifact_metadata",
    "RunManifestBuilder",
    "build_run_manifest",
    "compute_run_fingerprint",
    "ToolExecutionContext",
    "get_tool_execution_context",
    "reset_tool_execution_context",
    "set_tool_execution_context",
]
