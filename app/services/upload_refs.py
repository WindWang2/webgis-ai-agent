"""Server-owned upload/ref bindings and deletion reconciliation."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from collections.abc import Mapping


def _read_binding(path: Path, session_id: str) -> set[str]:
    meta_path = path.parent / "meta.json"
    if not meta_path.exists():
        return set()
    if meta_path.stat().st_size > 1024 * 1024:
        raise ValueError("upload metadata exceeds deletion budget")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    binding = meta.get("registered_session_ref")
    if binding is None:
        return set()
    if not isinstance(binding, dict) or binding.get("session_id") != session_id:
        raise ValueError("upload ref binding scope mismatch")
    ref = binding.get("ref_id")
    if not isinstance(ref, str) or not ref.startswith("ref:") or len(ref) > 160:
        raise ValueError("invalid upload ref binding")
    return {ref}


async def persist_upload_ref_binding(
    path: Path, meta: Mapping[str, object], session_id: str, ref_id: str,
) -> None:
    from app.services.data_parser import save_meta

    meta = dict(meta)
    meta["registered_session_ref"] = {"session_id": session_id, "ref_id": ref_id}
    await asyncio.to_thread(save_meta, path.parent, meta)


def _legacy_vector_fingerprint(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("upload reconciliation exceeds vector size budget")
    import ijson
    from app.services.data_ingest.pipeline import compute_payload_fingerprint

    with path.open("rb") as source:
        features = list(ijson.items(source, "features.item", use_float=True))
    return compute_payload_fingerprint({"type": "FeatureCollection", "features": features})


async def clear_upload_refs(session_id: str, path: Path, file_type: str) -> set[str]:
    """Clear refs before file deletion; failure keeps the upload retriable.

    Legacy live uploads reconcile through their ingestion fingerprint or their
    terrain source path. Shared deduplicated refs are invalidated together: the
    remaining upload can be registered again rather than retaining a stale ref.
    """
    from app.core.config import settings
    from app.services.session_data import session_data_manager as manager
    from app.services.artifact_registry import get_artifact, list_artifacts, mark_status

    root = (Path(settings.DATA_DIR) / "uploads").resolve()
    path = path.resolve()
    if root not in path.parents:
        raise ValueError("upload file is outside upload storage")
    ping = getattr(manager, "ping", None)
    if ping is not None:
        await ping()
    refs = await asyncio.to_thread(_read_binding, path, session_id)
    if file_type == "vector":
        fingerprint = await asyncio.to_thread(_legacy_vector_fingerprint, path)
        if fingerprint:
            for record in await list_artifacts(session_id):
                metadata = record.metadata or {}
                if metadata.get("ingest_content_sha256") == fingerprint:
                    refs.add(record.artifact_id)
    elif file_type == "raster":
        candidates = [ref for ref in await manager.list_refs(session_id) if ref.startswith("ref:terrain-")]
        if len(candidates) > 1024:
            raise ValueError("terrain reconciliation exceeds ref budget")
        for ref in candidates:
            payload = await manager.get(session_id, ref)
            if isinstance(payload, dict) and payload.get("file_path"):
                source = await asyncio.to_thread(Path(str(payload["file_path"])).resolve)
                if source == path or path.parent in source.parents:
                    refs.add(ref)
    for ref in refs:
        existed = await manager.ref_exists(session_id, ref)
        deleted = await manager.delete_ref(session_id, ref)
        if existed and not deleted:
            raise RuntimeError("upload ref deletion failed")
        artifact = await get_artifact(session_id, ref)
        if artifact is not None and not await mark_status(session_id, ref, "expired"):
            raise RuntimeError("upload artifact invalidation failed")
    if ping is not None:
        await ping()
    return refs
