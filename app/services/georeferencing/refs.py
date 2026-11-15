"""Cross-session BlobStore reference counting and session purge for G48 assets (R28)."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from app.services.durable_blob_store import BlobKeyError, get_filesystem_blob_store
from app.services.gis_harness.visual_observation.blob_lock import blob_ref_lock

logger = logging.getLogger(__name__)

_IMG_PREFIX = "georefbin-"
_REF_PREFIX = "georefref-"
_MAX_SESSIONS_PER_BLOB = 128


def blob_key_for_sha(sha256: str) -> str:
    return f"{_IMG_PREFIX}{sha256}"


def ref_key_for_sha(sha256: str) -> str:
    return f"{_REF_PREFIX}{sha256}"


def _refs_path_for(sha256: str) -> Path:
    from app.core.config import settings

    prefix = (sha256[:4] or "0000").lower()
    return Path(settings.DATA_DIR) / "georeferencing" / "_refs" / prefix / f"{sha256}.json"


def _store():
    return get_filesystem_blob_store()


def _load_refs(sha256: str) -> Dict[str, int]:
    p = _refs_path_for(sha256)
    raw_bytes: bytes | None = None
    if p.is_file():
        try:
            raw_bytes = p.read_bytes()
        except OSError:
            return {}
    else:
        try:
            raw_bytes = _store().get_blob(ref_key_for_sha(sha256))
        except BlobKeyError:
            return {}
    if not raw_bytes:
        return {}
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
        refs = data.get("refs") if isinstance(data, dict) else None
        if not isinstance(refs, dict):
            return {}
        return {str(k)[:128]: max(0, int(v)) for k, v in list(refs.items())[:_MAX_SESSIONS_PER_BLOB] if int(v) > 0}
    except Exception:  # noqa: BLE001
        return {}


def _save_refs(sha256: str, refs: Dict[str, int]) -> bool:
    p = _refs_path_for(sha256)
    if not refs:
        p.unlink(missing_ok=True)
        return True
    payload = json.dumps({"v": 1, "refs": refs}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp-{os.getpid()}")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, p)
        return True
    except Exception:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        logger.warning("[georef-refs] save refs failed sha=%s", sha256[:12], exc_info=True)
        return False


def pin_blob_bytes(sha256: str, session_id: str, data: bytes) -> str:
    """Store binary bytes under content-addressed key and increment session refcount."""
    store = _store()
    bkey = blob_key_for_sha(sha256)
    with blob_ref_lock(store, f"g48:{sha256}"):
        if not store.exists(bkey):
            store.put_blob(bkey, data, "binary")
        refs = _load_refs(sha256)
        refs[session_id] = refs.get(session_id, 0) + 1
        _save_refs(sha256, refs)
    return bkey


def pin_blob_from_path(sha256: str, session_id: str, src_path: Path) -> str:
    """Store file from disk into BlobStore and increment session refcount."""
    store = _store()
    bkey = blob_key_for_sha(sha256)
    with blob_ref_lock(store, f"g48:{sha256}"):
        if not store.exists(bkey):
            store.put_blob_from_path(bkey, src_path, "binary")
        refs = _load_refs(sha256)
        refs[session_id] = refs.get(session_id, 0) + 1
        _save_refs(sha256, refs)
    return bkey


def release_blob_ref(sha256: str, session_id: str) -> bool:
    """Decrement session refcount for ``sha256``; delete underlying blob only when global refs reach 0."""
    if not sha256:
        return False
    store = _store()
    with blob_ref_lock(store, f"g48:{sha256}"):
        refs = _load_refs(sha256)
        if session_id not in refs:
            return False
        rem = refs[session_id] - 1
        if rem > 0:
            refs[session_id] = rem
            _save_refs(sha256, refs)
            return False
        refs.pop(session_id, None)
        if refs:
            _save_refs(sha256, refs)
            return False
        _save_refs(sha256, {})
        legacy_key = ref_key_for_sha(sha256)
        if store.exists(legacy_key):
            store.delete_blob(legacy_key)
        return bool(store.delete_blob(blob_key_for_sha(sha256)))


def blob_has_live_ref(sha256: str, session_id: str) -> bool:
    """Check whether ``session_id`` holds an active pin for ``sha256``."""
    refs = _load_refs(sha256)
    return bool(refs.get(session_id, 0) > 0)


def active_georef_artifact_ids(session_id: str) -> List[str]:
    """Return active georeferencing raster_refs and artifact_ids to protect from GC."""
    try:
        from app.services.georeferencing.store import list_sources, list_plans

        active: List[str] = []
        for plan in list_plans(session_id):
            for art in [plan.latest_artifact, *plan.superseded_artifacts]:
                if art and art.raster_ref:
                    active.append(art.raster_ref)
                    active.append(art.artifact_id)
        for src in list_sources(session_id):
            active.append(src.source_id)
        return active
    except Exception:  # noqa: BLE001
        return []


def purge_session_georef_state(session_id: str) -> int:
    """Release all G48 blobs, artifact refs, and local session files when a session clears (R28)."""
    released = 0
    try:
        from app.services.georeferencing.store import purge_session_files

        released = purge_session_files(session_id)
    except Exception:  # noqa: BLE001
        logger.warning("[georef-refs] purge failed session=%s", session_id, exc_info=True)
    return released


def restore_blob_to_path(sha256: str, dest_path: Path) -> bool:
    """Materialize blob from BlobStore to ``dest_path`` atomically if local copy disappeared."""
    raw = _store().get_blob(blob_key_for_sha(sha256))
    if raw is None:
        return False
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest_path.with_name(f".{dest_path.name}.tmp")
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, dest_path)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def compute_file_sha256(path: Path) -> str:
    """Stream-compute SHA256 hex digest of ``path`` in 1 MiB chunks without loading into RAM."""
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_file_sha256(path: Path, expected_sha256: str) -> str:
    """Compute SHA256 digest of ``path`` and verify exact match with ``expected_sha256``."""
    actual = compute_file_sha256(path)
    if actual != expected_sha256:
        raise ValueError(f"Digest mismatch: expected {expected_sha256}, got {actual}")
    return actual


def compute_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
