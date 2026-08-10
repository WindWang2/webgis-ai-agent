"""CheckpointStore (app/services/mapspec/checkpoint.py).

拥有 MapSpec 各种 Checkpoint Snapshot、Ref Materialization 与 Rollback 逻辑。

可靠性 / 写放大契约（REL-05）：
- Whole-checkpoint 内容寻址：相同 (mapspec + refs) 的重复 checkpoint 复用已有 id，
  不重写完整 payload（满足"repeated unchanged checkpoint 不重复写大 payload"）。
- Per-ref blob 去重：每个 ref 数据按内容哈希存为 ``blobs/<sha>.json``，相同 ref
  不被每个 checkpoint 重复复制（满足"相同 ref 不被每个 checkpoint 重复复制"）。
- 原子写（temp + os.replace）；manifest 损坏 / blob 缺失时回退可观察，不静默成功。
- 向后兼容：旧 checkpoint（``mapspec.json`` + ``materialized_refs.json``，无
  ``descriptor.json`` / manifest）仍可 rollback；新 checkpoint 额外写 descriptor。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.services.mapspec_source import ref as source_ref
from app.services.mapspec.store import _atomic_write_json_sync, _read_json_sync

# SEC-02: checkpoint_id is an LLM/user-supplied string that is joined directly
# into a filesystem path. A value containing ``..`` or path separators could
# read or write outside the session's ``checkpoints/`` directory (path
# traversal). Restrict to a safe charset; caller-controlled structure is never
# permitted. See docs/research/deep-audit-performance-convergence.md SEC-02.
_SAFE_CHECKPOINT_ID = re.compile(r"^[A-Za-z0-9_.-]+$")

_MANIFEST_VERSION = 1


def _validate_checkpoint_id(ckpt_id: str) -> str:
    """Reject checkpoint ids that are not safe filesystem segment names.

    Allows alphanumerics, underscore, dash, dot — enough for the default
    ``ckpt_<millis>`` ids and human-friendly labels, while forbidding path
    separators and ``..`` traversal.
    """
    if not ckpt_id or not _SAFE_CHECKPOINT_ID.match(ckpt_id) or ckpt_id in (".", ".."):
        raise ValueError(
            f"Invalid checkpoint_id '{ckpt_id}': must match {_SAFE_CHECKPOINT_ID.pattern}"
        )
    return ckpt_id


def _canonical_json(payload: Any) -> str:
    """Deterministic JSON for stable hashing (sorted keys, no extra whitespace)."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(mapspec: Dict[str, Any], ref_blob_map: Dict[str, str]) -> str:
    """SHA256 over the canonical mapspec + the ref_id→blob_hash mapping.

    The blob mapping (not the raw payload) is hashed so two checkpoints that
    reference the same ref data hash identically — that is the dedup key.
    """
    h = hashlib.sha256()
    h.update(_canonical_json(mapspec).encode("utf-8"))
    h.update(b"\x1f")  # separator between the two segments
    h.update(_canonical_json(ref_blob_map).encode("utf-8"))
    return h.hexdigest()


def _blob_filename(content_hash: str) -> str:
    return f"{content_hash}.json"


def _checkpoints_root(session_dir: Path) -> Path:
    return session_dir / "checkpoints"


def _blob_dir(session_dir: Path) -> Path:
    return _checkpoints_root(session_dir) / "blobs"


def _manifest_path(session_dir: Path) -> Path:
    return _checkpoints_root(session_dir) / "manifest.json"


def _load_manifest(session_dir: Path) -> Dict[str, Any]:
    """Load the content-hash → checkpoint_id manifest. Missing/corrupt → empty."""
    raw = _read_json_sync(_manifest_path(session_dir))
    if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
        return raw
    return {"version": _MANIFEST_VERSION, "entries": {}}


async def _materialize_refs(
    mapspec: Dict[str, Any], session_id: str, session_data_manager
) -> Dict[str, Any]:
    """Fetch every ref: payload referenced by the mapspec's sources.

    Returns {ref_id: payload}. Missing refs (get returned None) are omitted —
    a checkpoint never persists a None payload as if it existed.
    """
    materialized: Dict[str, Any] = {}
    for source in mapspec.get("sources", {}).values():
        ref_candidate = source_ref(source) or ""
        if isinstance(ref_candidate, str) and ref_candidate.startswith("ref:"):
            ref_data = await session_data_manager.get(session_id, ref_candidate)
            if ref_data is not None:
                materialized[ref_candidate] = ref_data
    return materialized


def _write_ref_blobs_sync(
    blob_dir: Path, materialized_refs: Dict[str, Any]
) -> Dict[str, str]:
    """Persist each ref payload as a content-addressed blob (dedup by hash).

    Returns {ref_id: blob_hash}. Existing blobs are not rewritten. Each blob is
    written atomically so a crash cannot leave a partial blob that later
    rollback would trust.
    """
    blob_dir.mkdir(parents=True, exist_ok=True)
    ref_blob_map: Dict[str, str] = {}
    for ref_id, payload in materialized_refs.items():
        blob_hash = hashlib.sha256(
            _canonical_json(payload).encode("utf-8")
        ).hexdigest()
        blob_path = blob_dir / _blob_filename(blob_hash)
        if not blob_path.exists():
            _atomic_write_json_sync(blob_path, payload)
        ref_blob_map[ref_id] = blob_hash
    return ref_blob_map


async def snapshot(
    mapspec: Dict[str, Any],
    session_dir: Path,
    session_data_manager,
    checkpoint_id: Optional[str] = None,
) -> Dict[str, Any]:
    """生成 MapSpec Checkpoint Snapshot 及其引用的 ref 数据物理副本。

    写放大控制：
    1. ref 数据按内容哈希存为 blobs/<sha>.json，相同 ref 复用 blob；
    2. 整个 checkpoint 的内容哈希命中 manifest 时，复用已有 checkpoint_id，
       不重写 mapspec/descriptor。
    """
    ckpt_id = checkpoint_id or f"ckpt_{int(time.time() * 1000)}"
    _validate_checkpoint_id(ckpt_id)

    session_id_for_refs = session_dir.name
    # 1. 物化所有 ref（async）。这是数据读取，去重省的是写入，不是读取。
    materialized_refs = await _materialize_refs(
        mapspec, session_id_for_refs, session_data_manager
    )

    # 2. ref blob 去重写入（卸载到线程）。返回 ref_id -> blob_hash 映射。
    blob_dir = _blob_dir(session_dir)
    ref_blob_map = await asyncio.to_thread(
        _write_ref_blobs_sync, blob_dir, materialized_refs
    )

    # 3. 整 checkpoint 内容哈希。
    content_h = _content_hash(mapspec, ref_blob_map)
    manifest = await asyncio.to_thread(_load_manifest, session_dir)

    # 去重契约：
    # - ref blob 去重永远生效（第 2 步，不影响 checkpoint 身份）。
    # - whole-checkpoint 内容去重（复用已有 id、跳过写入）**仅对自动 checkpoint**
    #   （checkpoint_id is None）生效。显式 id 由调用方指定（供按名 rollback），
    #   必须落地该 id 对应目录；否则 rollback(checkpoint_id) 会找不到目录。
    if checkpoint_id is None:
        existing = manifest["entries"].get(content_h)
        if existing and isinstance(existing, str):
            existing_dir = _checkpoints_root(session_dir) / existing
            if existing_dir.exists():
                return {
                    "success": True,
                    "checkpoint_id": existing,
                    "checkpoint_dir": str(existing_dir),
                    "ref_count": len(ref_blob_map),
                    "deduplicated": True,
                    "summary": (
                        f"Checkpoint reused as '{existing}' "
                        f"(content-identical; {len(ref_blob_map)} refs deduplicated)"
                    ),
                }
            # manifest 指向已删除目录：清理并继续写一个新 checkpoint。

    # 4. 写 checkpoint 目录。显式 id 若已存在且内容相同则跳过（幂等），否则写入。
    ckpt_dir = _checkpoints_root(session_dir) / ckpt_id
    if ckpt_dir.exists():
        prior_descriptor = await asyncio.to_thread(
            _read_json_sync, ckpt_dir / "descriptor.json"
        )
        if (
            isinstance(prior_descriptor, dict)
            and prior_descriptor.get("content_hash") == content_h
        ):
            # 同 id 同内容：已落地，跳过重写（仍记录 manifest 命中）。
            manifest["entries"][content_h] = ckpt_id
            await asyncio.to_thread(
                _atomic_write_json_sync, _manifest_path(session_dir), manifest
            )
            return {
                "success": True,
                "checkpoint_id": ckpt_id,
                "checkpoint_dir": str(ckpt_dir),
                "ref_count": len(ref_blob_map),
                "deduplicated": True,
                "summary": (
                    f"Checkpoint '{ckpt_id}' unchanged; skipped rewrite "
                    f"({len(ref_blob_map)} refs deduplicated)"
                ),
            }

    ckpt_dir.mkdir(parents=True, exist_ok=True)
    descriptor = {
        "checkpoint_id": ckpt_id,
        "content_hash": content_h,
        "timestamp": time.time(),
        "ref_count": len(ref_blob_map),
        "refs": ref_blob_map,  # ref_id -> blob_hash
    }

    def _write_checkpoint_sync() -> None:
        _atomic_write_json_sync(ckpt_dir / "mapspec.json", mapspec)
        _atomic_write_json_sync(ckpt_dir / "descriptor.json", descriptor)

    await asyncio.to_thread(_write_checkpoint_sync)

    # 5. 更新 manifest（content_hash -> checkpoint_id），原子写。
    manifest["entries"][content_h] = ckpt_id
    await asyncio.to_thread(_atomic_write_json_sync, _manifest_path(session_dir), manifest)

    return {
        "success": True,
        "checkpoint_id": ckpt_id,
        "checkpoint_dir": str(ckpt_dir),
        "ref_count": len(ref_blob_map),
        "deduplicated": False,
        "summary": f"Checkpoint '{ckpt_id}' created with {len(ref_blob_map)} materialized refs",
    }


async def rollback(
    session_dir: Path,
    checkpoint_id: str,
    session_data_manager,
) -> Dict[str, Any]:
    """回滚恢复 Checkpoint Snapshot。

    支持两种磁盘布局：
    - 新格式：``descriptor.json``（ref_id -> blob_hash）+ ``blobs/<sha>.json``；
    - 旧格式（向后兼容）：``materialized_refs.json``（ref_id -> 内联 payload）。
    """
    _validate_checkpoint_id(checkpoint_id)
    ckpt_dir = _checkpoints_root(session_dir) / checkpoint_id
    if not ckpt_dir.exists():
        return {"success": False, "message": f"Checkpoint '{checkpoint_id}' not found"}

    mapspec_file = ckpt_dir / "mapspec.json"
    mapspec = await asyncio.to_thread(_read_json_sync, mapspec_file)
    if mapspec is None:
        return {
            "success": False,
            "message": f"Checkpoint '{checkpoint_id}' mapspec.json missing or corrupt",
        }

    session_id_for_refs = session_dir.name

    # 优先新格式 descriptor（ref -> blob），回退旧格式 materialized_refs（内联）。
    descriptor = await asyncio.to_thread(_read_json_sync, ckpt_dir / "descriptor.json")
    if isinstance(descriptor, dict) and isinstance(descriptor.get("refs"), dict):
        blob_dir = _blob_dir(session_dir)
        restored = 0
        missing_blobs = []
        for ref_id, blob_hash in descriptor["refs"].items():
            if not isinstance(blob_hash, str):
                continue
            blob_path = blob_dir / _blob_filename(blob_hash)
            payload = await asyncio.to_thread(_read_json_sync, blob_path)
            if payload is None:
                missing_blobs.append(ref_id)
                continue
            await session_data_manager.overwrite(session_id_for_refs, ref_id, payload)
            restored += 1
        if missing_blobs:
            return {
                "success": False,
                "message": (
                    f"Checkpoint '{checkpoint_id}' references missing blobs: "
                    f"{missing_blobs}"
                ),
            }
        ref_count = restored
    else:
        # 旧格式：materialized_refs.json 内联 payload。
        refs_file = ckpt_dir / "materialized_refs.json"
        refs_data = await asyncio.to_thread(_read_json_sync, refs_file)
        refs_data = refs_data if isinstance(refs_data, dict) else {}
        ref_count = 0
        for ref_id, payload in refs_data.items():
            await session_data_manager.overwrite(session_id_for_refs, ref_id, payload)
            ref_count += 1

    return {
        "success": True,
        "checkpoint_id": checkpoint_id,
        "mapspec": mapspec,
        "ref_count": ref_count,
        "summary": f"Recovered checkpoint '{checkpoint_id}'",
    }
