"""Project Artifact Promotion — session artifact → durable project artifact
(ADR-0092 A3).

A workflow run's outputs live in the session store (Redis 4h TTL / memory
LRU) and the DB ``artifacts`` row only holds a ``storage_ref`` pointer —
after the session expires the pointer dangles and the run is no longer
explainable or re-runnable. Promotion closes that gap:

    Session artifact (ref, TTL-bound)
        ↓ promote (content-addressed materialization)
    Project Artifact (durable content + full semantic metadata)

Promotion writes the artifact payload to a content-addressed store keyed by
the artifact's ``content_fingerprint`` and records on the DB row:
content location, CRS, bbox, schema/feature summary, the producing
capability/algorithm/tool triple, parents (via existing lineage), and the
run identity. Re-opening the project weeks later therefore never depends on
the original SessionStore.

Truthfulness rules (INV-ART1, unchanged): metadata comes only from the real
payload/descriptor; when the session has already expired the promotion is
recorded honestly as ``content_status: "session_expired"`` instead of
fabricating a summary.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from app.models.project import Artifact, WorkflowRun
from app.services.provenance.fingerprint import canonical_dumps

logger = logging.getLogger(__name__)

#: Content-addressed store root (relative paths resolve under DATA_DIR).
_DEFAULT_STORE_DIRNAME = "project_artifacts"

#: Bounded per-artifact projection: promotion metadata must stay compact.
_MAX_SCHEMA_FIELDS = 32

#: Promotion content-status vocabulary (machine contract, single source).
#: promoted          — content materialized under content_location
#: already_promoted  — idempotent re-entry on a promoted row
#: no_session_context— caller had no session to probe (content may be alive)
#: session_expired   — probed the session; payload gone
#: store_unavailable — content write failed (disk full / IO error), disclosed
ContentStatus = Literal[
    "promoted",
    "already_promoted",
    "no_session_context",
    "session_expired",
    "store_unavailable",
]

_ROOT_CACHE: "Optional[Path]" = None


def content_store_root() -> Path:
    """Content-addressed store root (resolved + mkdir'd once per process)."""
    global _ROOT_CACHE
    if _ROOT_CACHE is None:
        from app.core.config import settings

        configured = str(getattr(settings, "PROJECT_ARTIFACT_CONTENT_DIR", "") or "").strip()
        if configured:
            root = Path(configured)
        else:
            root = Path(str(getattr(settings, "DATA_DIR", "./data") or "./data")) / _DEFAULT_STORE_DIRNAME
        root.mkdir(parents=True, exist_ok=True)
        _ROOT_CACHE = root
    return _ROOT_CACHE


def reset_content_store_root_cache() -> None:
    """Test hook: settings monkeypatching must be able to re-resolve the root."""
    global _ROOT_CACHE
    _ROOT_CACHE = None


def _content_path(content_fingerprint: str) -> Optional[Path]:
    """Content-addressed file path for a fingerprint (sha2-4 prefix shards)."""
    fp = str(content_fingerprint or "").strip()
    if not fp or any(c in fp for c in ("/", chr(92), "..")):
        return None
    shard = fp[:4]
    return content_store_root() / shard / f"{fp}.json"


def _sha256_of_blob(blob: str) -> str:
    import hashlib

    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _schema_summary(payload: Any) -> Dict[str, Any]:
    """Bounded structural summary of an artifact payload."""
    summary: Dict[str, Any] = {}
    if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        features = payload.get("features") or []
        summary["feature_count"] = len(features)
        props: Dict[str, str] = {}
        geom_types: List[str] = []
        for f in features[:200]:
            if not isinstance(f, dict):
                continue
            geom = f.get("geometry") or {}
            gt = geom.get("type")
            if gt and gt not in geom_types:
                geom_types.append(gt)
            for k, v in (f.get("properties") or {}).items():
                if k not in props and len(props) < _MAX_SCHEMA_FIELDS:
                    props[k] = type(v).__name__
        summary["geometry_types"] = geom_types[:8]
        summary["property_types"] = props
        if features and isinstance(features[0], dict):
            crs = payload.get("crs")
            if crs:
                summary["declared_crs"] = crs
        bbox = payload.get("bbox") or _bbox_of(features)
        if bbox:
            summary["bbox"] = bbox
    else:
        summary["payload_kind"] = type(payload).__name__
        try:
            summary["payload_bytes"] = len(canonical_dumps(payload))
        except Exception:  # noqa: BLE001 — summary is best-effort
            pass
    return summary


def _bbox_of(features: List[Any]) -> Optional[List[float]]:
    xs: List[float] = []
    ys: List[float] = []

    def _walk(coords: Any) -> None:
        if isinstance(coords, (list, tuple)) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
            xs.append(float(coords[0]))
            ys.append(float(coords[1]))
            return
        if isinstance(coords, (list, tuple)):
            for c in coords:
                _walk(c)

    for f in features[:2000]:
        if not isinstance(f, dict):
            continue
        _walk((f.get("geometry") or {}).get("coordinates"))
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def materialize_blob(content_fingerprint: str, blob: str) -> Optional[str]:
    """Write a pre-serialized canonical blob to the content-addressed store.

    Single-serialization contract: the caller serializes the payload once and
    reuses the same blob for the sha256 digest and the write, so stored bytes
    always match the recorded digest. Returns the relative location, or None
    when persistence failed (caller discloses).
    """
    path = _content_path(content_fingerprint)
    if path is None:
        return None
    if path.exists():
        return str(path.relative_to(content_store_root()))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(blob, encoding="utf-8")
        os.replace(tmp, path)
        return str(path.relative_to(content_store_root()))
    except Exception as e:  # noqa: BLE001 — promotion must never break a run
        logger.warning("[artifact_promotion] content write failed for %s: %s", content_fingerprint, e)
        return None


def materialize_content(content_fingerprint: str, payload: Any) -> Optional[str]:
    """Convenience wrapper: canonical-serialize then materialize the blob."""
    try:
        return materialize_blob(content_fingerprint, canonical_dumps(payload))
    except Exception:  # noqa: BLE001 — unserializable payload → disclosed
        return None


def read_content(content_location: str, expected_sha256: str = "") -> Optional[Any]:
    """Read back promoted content; verify the recorded digest when given.

    A missing/modified file returns None (caller discloses) instead of
    silently serving substituted content.
    """
    if not content_location:
        return None
    root = content_store_root()
    path = (root / content_location).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        if expected_sha256:
            import hashlib

            actual = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if actual != expected_sha256:
                logger.warning(
                    "[artifact_promotion] content digest mismatch for %s "
                    "(expected %s, got %s)", content_location, expected_sha256, actual,
                )
                return None
        return json.loads(raw)
    except Exception as e:  # noqa: BLE001 — unreadable content is disclosed
        logger.warning(
            "[artifact_promotion] content read failed for %s: %s",
            content_location, e,
        )
        return None


async def promote_run_artifacts(
    db,
    run: WorkflowRun,
    *,
    session_id: Optional[str],
    project_id: str,
) -> List[Dict[str, Any]]:
    """Promote every artifact of a completed run into durable project artifacts.

    Updates the existing ``artifacts`` rows in place (created by the engine
    during execution) — promotion does NOT mint a parallel artifact identity.
    Returns a bounded report list, one entry per artifact.
    """
    from sqlalchemy import select

    from app.services.session_data import session_data_manager

    # Authoritative binding: this run's manifest artifact ids (O(1) lookups).
    manifest = run.run_manifest or {}
    manifest_ids = {
        str(rec.get("id"))
        for rec in (manifest.get("artifacts") or [])
        if rec.get("id")
    }
    if manifest_ids:
        rows = db.execute(
            select(Artifact).where(Artifact.id.in_(manifest_ids))
        ).scalars().all()
        # Trust but verify: a manifest id from another project would be a
        # corrupt manifest; the project filter keeps promotion tenant-scoped.
        rows = [a for a in rows if a.project_id == project_id]
    else:
        # Legacy rows (pre-manifest runs): bind via lineage edges scoped to
        # THIS run — never project-wide step_id matching (cross-run
        # contamination; same deliberate choice as the engine's
        # _reconstruct_prior).
        from app.models.project import ArtifactLineage

        lineage_ids = list(
            db.execute(
                select(ArtifactLineage.artifact_id).where(
                    ArtifactLineage.workflow_run_id == run.id
                )
            ).scalars().all()
        )
        if not lineage_ids:
            return []
        rows = db.execute(
            select(Artifact).where(
                Artifact.project_id == project_id,
                Artifact.id.in_(lineage_ids),
            )
        ).scalars().all()
    report: List[Dict[str, Any]] = []
    for art in rows:
        meta = dict(art.metadata_json or {})
        if meta.get("content_status") == "promoted" and meta.get("content_location"):
            report.append({"artifact_id": art.id, "status": "already_promoted"})
            continue
        entry: Dict[str, Any] = {"artifact_id": art.id}
        payload: Optional[Any] = None
        if session_id and art.storage_ref:
            try:
                payload = await session_data_manager.get(session_id, art.storage_ref)
            except Exception as e:  # noqa: BLE001 — store outage is disclosed, not fatal
                logger.warning(
                    "[artifact_promotion] session store read failed for %s: %s",
                    art.storage_ref, e,
                )
                payload = None
        if payload is None:
            # Truthful status: "no_session_context" (caller had no session to
            # probe — content MAY still be alive) differs from
            # "session_expired" (probed and gone). Never conflate the two.
            meta["content_status"] = (
                "no_session_context" if not session_id else "session_expired"
            )
            art.metadata_json = meta
            entry["status"] = meta["content_status"]
            report.append(entry)
            continue
        # Durable-content identity: the parked payload is materialized content,
        # while the run-time ``content_fingerprint`` was derived from the tool's
        # result descriptor (different format, same logical output). Record an
        # independent, verifiable digest of the durable bytes instead of
        # pretending the two are equal (truthful provenance, INV-ART1).
        # Serialize ONCE (canonical blob); the same bytes back the digest, the
        # write, and later read-back verification. Hash + file IO run off the
        # event loop (this coroutine is awaited from the async engine path).
        try:
            blob = canonical_dumps(payload)
        except Exception as e:  # noqa: BLE001 — unserializable → disclosed
            logger.warning(
                "[artifact_promotion] payload serialization failed for %s: %s",
                art.id, e,
            )
            blob = ""
        payload_digest = ""
        if blob:
            try:
                payload_digest = await asyncio.to_thread(_sha256_of_blob, blob)
                meta["content_payload_sha256"] = payload_digest
            except Exception as e:  # noqa: BLE001 — digest is best-effort
                logger.warning(
                    "[artifact_promotion] digest failed for %s: %s", art.id, e
                )
        # Content-addressed key: run-time fingerprint when present, else the
        # durable payload digest (an artifact with no descriptor evidence still
        # materializes under its own content identity).
        location = (
            await asyncio.to_thread(
                materialize_blob, art.content_fingerprint or payload_digest, blob
            )
            if blob
            else None
        )
        summary = await asyncio.to_thread(_schema_summary, payload)
        if location:
            meta["content_status"] = "promoted"
            meta["content_location"] = location
        else:
            meta["content_status"] = "store_unavailable"
        meta["content_summary"] = summary
        art.metadata_json = meta
        entry["status"] = meta["content_status"]
        entry["content_location"] = location
        report.append(entry)
    db.commit()
    return report


