"""Deterministic fingerprints for datasets, workflow graphs, and tool outputs.

All functions are pure & deterministic (INV-FP1/FP3): identical inputs always
produce identical hashes; they never read the clock or a RNG. Hashing follows the
same canonical-JSON + sha256 pattern as the existing data-fabric fingerprint
service (``app.services.data_fabric.fingerprint``) so the two are interoperable
in spirit.

Choice of evidence (per the V2 spec §8): we cannot rely on filename / created_at
/ a random UUID. We hash the *identity* of the data:
  * dataset   → source_type + source_ref + crs + canonical(schema_profile)
  * graph     → canonical(graph_spec)
  * tool out  → canonical(result descriptor: ref_id + feature_count + bbox, or a
                stable hash of the result envelope when those keys are absent)

When ``source_ref`` already points at content-addressed storage (a ``ref:`` id or
a layer id) the dataset fingerprint transitively content-addresses the bytes —
the same immutable dataset always yields the same fingerprint, and any content
change flows through to a different ``source_ref`` / schema and therefore a
different fingerprint (INV-FP2).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional


def _canonical_default(obj: Any) -> Any:
    """Deterministic fallback for non-JSON-native leaves.

    Sets/frozensets are rendered as ``sorted(list(...))`` so the hash does not
    depend on iteration order (which varies under PYTHONHASHSEED). Tuples become
    lists. Anything else is stringified — matching the prior ``default=str``
    behavior for datetimes etc.
    """
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=lambda x: str(x))
    if isinstance(obj, tuple):
        return list(obj)
    return str(obj)


def canonical_dumps(obj: Any) -> str:
    """Deterministic JSON serialization.

    Sorted keys + compact separators + ``ensure_ascii=False`` so the serialized
    form depends only on the *contents* of ``obj``, never on insertion order or
    unicode escaping. Non-JSON-native leaf values (datetime/tuple/set) are
    canonicalized via :func:`_canonical_default` — sets are sorted so the result
    is stable across processes (INV-FP3).
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=_canonical_default,
    )


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_dataset_fingerprint(
    source_type: str,
    source_ref: Optional[str],
    crs: Optional[str],
    schema_profile: Optional[Dict[str, Any]] = None,
) -> str:
    """Deterministic dataset identity fingerprint (INV-FP1/FP2/FP3).

    Hashes the immutable identity evidence of a dataset. ``name`` is
    intentionally excluded: it is a human label, not content identity — two
    datasets pointing at the same content should share a fingerprint.
    """
    evidence = {
        "source_type": source_type or "",
        "source_ref": source_ref or "",
        "crs": crs or "",
        "schema_profile": schema_profile or {},
    }
    return _sha256(canonical_dumps(evidence))


def compute_graph_fingerprint(graph_spec: Optional[Dict[str, Any]]) -> str:
    """Deterministic fingerprint of a workflow graph (INV-REV3).

    Two graphs produce the same fingerprint iff their canonical serialization is
    identical — used to dedupe revisions and to compare runs.
    """
    return _sha256(canonical_dumps(graph_spec or {}))


def extract_artifact_metadata(tool_name: str, tool_result: Any) -> Dict[str, Optional[str]]:
    """Derive TRUTHFUL artifact metadata from a tool result (INV-ART1/ART2).

    Never fabricates: when the result carries no CRS / format signal the field is
    returned as ``None`` (the caller persists ``None`` / ``"unknown"``, never a
    made-up ``EPSG:4326``). Recognised evidence:

    * ``ref_id`` / ``layer_id`` → ``storage_ref`` (canonical storage reference)
    * ``raster_source`` present → raster output (format ``raster``)
    * Fetch-on-Demand descriptor (``ref_id`` + ``feature_count``) → vector
      (format ``geojson``)
    * ``crs`` / ``srs`` key → the dataset CRS, if the tool echoes one

    Returns a dict with keys: ``storage_ref``, ``artifact_type``, ``format``,
    ``crs``, ``content_fingerprint``.
    """
    res = tool_result if isinstance(tool_result, dict) else {}
    storage_ref = str(
        res.get("ref_id") or res.get("layer_id") or res.get("result_layer_id") or ""
    ) or None

    has_raster = isinstance(res.get("raster_source"), dict) or bool(res.get("raster_source"))
    feature_count = res.get("feature_count")

    if has_raster:
        artifact_type: Optional[str] = "raster"
        fmt: Optional[str] = "raster"
    elif storage_ref and isinstance(feature_count, int):
        # Fetch-on-Demand vector descriptor.
        artifact_type = "vector"
        fmt = "geojson"
    elif storage_ref:
        artifact_type = "analysis"
        fmt = None
    else:
        artifact_type = "analysis"
        fmt = None

    # CRS: only trust a key the tool actually echoes. Most tools do NOT, so this
    # is None (unknown) for computed outputs — recorded truthfully, not assumed.
    crs = res.get("crs") or res.get("srs") or None

    content_fingerprint = compute_content_fingerprint(tool_name, tool_result)
    return {
        "storage_ref": storage_ref,
        "artifact_type": artifact_type,
        "format": fmt,
        "crs": crs,
        "content_fingerprint": content_fingerprint,
    }


def compute_content_fingerprint(tool_name: str, tool_result: Any) -> Optional[str]:
    """Best-effort deterministic fingerprint of a tool *output*.

    Derived from content-derived evidence only — ``tool`` + ``feature_count`` +
    ``bbox`` (+ a stable hash of the trimmed result envelope when those are
    absent). The per-run random ``ref_id`` is deliberately **excluded** so that
    the same logical output yields the same fingerprint across runs (cheap
    duplicate detection along lineage edges); two outputs that differ only in
    their random storage ref do not collide-differ. Returns ``None`` only when
    the result carries nothing hashable.
    """
    res = tool_result if isinstance(tool_result, dict) else None
    if res is None:
        return None

    feature_count = res.get("feature_count")
    bbox = res.get("bbox")
    geometry_type = res.get("geometry_type")

    if feature_count is not None or bbox is not None or geometry_type is not None:
        descriptor = {
            "tool": tool_name or "",
            "feature_count": feature_count if isinstance(feature_count, int) else "",
            "bbox": bbox if bbox is not None else "",
            "geometry_type": geometry_type or "",
        }
        return _sha256(canonical_dumps(descriptor))

    # Strip volatile/large fields before hashing the whole envelope.
    trimmed = {
        k: v
        for k, v in res.items()
        if k not in {"data", "features", "raster_source", "summary", "ref_id", "layer_id"}
    }
    envelope = {"tool": tool_name or "", "result": trimmed}
    return _sha256(canonical_dumps(envelope))
