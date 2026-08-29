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
import shutil
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


# #687：自动 checkpoint（ckpt_<ms>）保留上限。显式命名 checkpoint 不受此限
#（调用方按名 rollback）。镜像 store.MAPSPEC_REV_RETENTION 的裁剪模式。
MAPSPEC_CKPT_RETENTION = 20

_AUTO_CKPT_RE = re.compile(r"^ckpt_\d+$")


def _prune_and_write_manifest_sync(session_dir: Path, manifest: Dict[str, Any]) -> None:
    """同步（#687 线程内）：清痕 → 原子写 manifest → 裁剪目录。

    顺序即安全性（评审修正）：先把将被裁掉的目录从 manifest 条目中移除并
    落盘，再 rmtree —— rmtree 失败时清单已不含被裁条目（安全）；manifest
    写失败时目录仍在且清单仍指向它们（安全）。反序会在 manifest 写失败
    时留下指向空洞的清单。
    """
    pruned_names = _select_prune_targets(session_dir)
    if pruned_names:
        entries = manifest.get("entries", {})
        manifest["entries"] = {
            h: cid for h, cid in entries.items() if cid not in pruned_names
        }
    _atomic_write_json_sync(_manifest_path(session_dir), manifest)
    if pruned_names:
        _rmtree_prune_targets(session_dir, pruned_names)


def _select_prune_targets(session_dir: Path) -> set:
    """同步：按保留上限选出将被裁剪的自动 checkpoint 目录名集合。"""
    root = _checkpoints_root(session_dir)
    if not root.exists():
        return set()
    auto_dirs = sorted(
        d for d in root.iterdir() if d.is_dir() and _AUTO_CKPT_RE.match(d.name)
    )
    if len(auto_dirs) <= MAPSPEC_CKPT_RETENTION:
        return set()
    return {d.name for d in auto_dirs[: len(auto_dirs) - MAPSPEC_CKPT_RETENTION]}


def _rmtree_prune_targets(session_dir: Path, names: set) -> None:
    root = _checkpoints_root(session_dir)
    for name in names:
        shutil.rmtree(root / name, ignore_errors=True)


def _prune_auto_checkpoints_sync(session_dir: Path, manifest: Dict[str, Any]) -> None:
    """同步（#687 线程内）：裁剪目录 + 就地更新 manifest 视图（不写盘）。

    独立调用入口（测试/维护用）；snapshot 路径走 _prune_and_write_manifest_sync
    的安全顺序。只动 ckpt_<ms> 形态的目录。
    """
    pruned_names = _select_prune_targets(session_dir)
    if not pruned_names:
        return
    _rmtree_prune_targets(session_dir, pruned_names)
    entries = manifest.get("entries", {})
    manifest["entries"] = {
        h: cid for h, cid in entries.items() if cid not in pruned_names
    }


def _checkpoints_root(session_dir: Path) -> Path:
    return session_dir / "checkpoints"


def _blob_dir(session_dir: Path) -> Path:
    return _checkpoints_root(session_dir) / "blobs"


def _referenced_session_refs(mapspec: Dict[str, Any]) -> list[str]:
    """Return stable session-owned refs without touching their payloads."""
    refs: list[str] = []
    for source in mapspec.get("sources", {}).values():
        if not isinstance(source, dict):
            continue
        candidate = source_ref(source)
        if (
            isinstance(candidate, str)
            and candidate.startswith("ref:")
            and candidate not in refs
        ):
            refs.append(candidate)
    return refs


async def discard_checkpoint(session_dir: Path, checkpoint_id: str) -> None:
    """#1074(F-14): 丢弃一个未提交成功却已落盘的 auto-checkpoint。

    apply_mutation 在提交前创建 checkpoint；提交失败回滚 spec/layers，
    但此前不清理该 checkpoint —— 孤儿目录描述一个从未 commit 的候选
    世代，后续 rollback(checkpoint_id) 会"恢复"到未提交状态，且占用
    20 槽保留额。从 manifest 与目录一并移除（best-effort）。
    """
    if not checkpoint_id:
        return
    import shutil as _shutil

    def _remove() -> None:
        ckpt_dir = _checkpoints_root(session_dir) / checkpoint_id
        try:
            _shutil.rmtree(ckpt_dir)
        except FileNotFoundError:
            pass
        manifest = _load_manifest(session_dir)
        entries = manifest.get("entries") or {}
        stale = [k for k, v in entries.items() if v == checkpoint_id]
        for k in stale:
            entries.pop(k, None)
        _prune_and_write_manifest_sync(session_dir, manifest)

    await asyncio.to_thread(_remove)


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


def _write_blobs_and_hash_sync(
    blob_dir: Path, materialized_refs: Dict[str, Any], mapspec: Dict[str, Any]
) -> tuple:
    """Offloaded: write ref blobs AND compute the whole-checkpoint content hash.

    Both are O(payload size); running them on the event loop would block for a
    large inline-GeoJSON mapspec. Combined into one thread call to avoid two
    separate offloads over the same data.
    """
    ref_blob_map = _write_ref_blobs_sync(blob_dir, materialized_refs)
    content_h = _content_hash(mapspec, ref_blob_map)
    return ref_blob_map, content_h


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
    referenced_refs = _referenced_session_refs(mapspec)
    # Automatic mutation checkpoints protect presentation state. Session refs
    # are immutable identities and cartographic repairs never mutate their
    # datasets, so downloading a large ref on every style update is needless
    # write amplification. Explicit named checkpoints remain self-contained.
    materialized_refs = (
        await _materialize_refs(mapspec, session_id_for_refs, session_data_manager)
        if checkpoint_id is not None
        else {}
    )

    # 2. ref blob 去重写入 + 内容哈希（整体卸载到线程）。
    blob_dir = _blob_dir(session_dir)
    ref_blob_map, content_h = await asyncio.to_thread(
        _write_blobs_and_hash_sync, blob_dir, materialized_refs, mapspec
    )

    # 3. 整 checkpoint 内容哈希。
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
        # Automatic mutation checkpoints are intentionally metadata-only: they
        # protect presentation changes without copying a 100k-feature result on
        # every style edit. They are rollbackable only while these immutable
        # live refs still exist; rollback verifies that condition explicitly.
        "mode": "self_contained" if checkpoint_id is not None else "presentation_only",
        "referenced_refs": referenced_refs,
    }

    def _write_checkpoint_sync() -> None:
        _atomic_write_json_sync(ckpt_dir / "mapspec.json", mapspec)
        _atomic_write_json_sync(ckpt_dir / "descriptor.json", descriptor)

    await asyncio.to_thread(_write_checkpoint_sync)

    # 5. 更新 manifest（content_hash -> checkpoint_id），原子写；
    #    顺带裁剪自动 checkpoint 保留量（#687：无上限时每次 layer 变更
    #    新增一个目录直至会话清扫，磁盘无界增长）。
    manifest["entries"][content_h] = ckpt_id
    await asyncio.to_thread(
        _prune_and_write_manifest_sync, session_dir, manifest
    )

    return {
        "success": True,
        "checkpoint_id": ckpt_id,
        "checkpoint_dir": str(ckpt_dir),
        "ref_count": len(ref_blob_map),
        "checkpoint_mode": descriptor["mode"],
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
        referenced_refs = [
            ref_id
            for ref_id in descriptor.get("referenced_refs", [])
            if isinstance(ref_id, str) and ref_id.startswith("ref:")
        ]
        mode = descriptor.get("mode")
        if mode == "presentation_only":
            # Metadata-first existence check. Vector refs use their bounded
            # session index; raster refs are immutable session files. No
            # descriptor fallback or feature collection is loaded merely to
            # restore presentation state.
            from app.services.raster_store import resolve_png_path

            available_refs = set(
                (await session_data_manager.list_refs(session_id_for_refs)).keys()
            )
            missing_refs: list[str] = []
            for ref_id in referenced_refs:
                if ref_id.startswith("ref:raster/"):
                    if resolve_png_path(session_dir, ref_id) is None:
                        missing_refs.append(ref_id)
                    continue
                if ref_id not in available_refs:
                    missing_refs.append(ref_id)
            if missing_refs:
                return {
                    "success": False,
                    "message": (
                        f"Checkpoint '{checkpoint_id}' is presentation-only and "
                        f"its live refs are unavailable: {missing_refs}"
                    ),
                    "checkpoint_mode": mode,
                    "missing_refs": missing_refs,
                }
            ref_count = 0
            refs_reused = len(referenced_refs)
        else:
            refs_reused = 0
            unmaterialized_refs = sorted(
                set(referenced_refs) - set(descriptor["refs"])
            )
            if unmaterialized_refs:
                return {
                    "success": False,
                    "message": (
                        f"Checkpoint '{checkpoint_id}' is not self-contained; "
                        f"refs were not materialized: {unmaterialized_refs}"
                    ),
                    "checkpoint_mode": mode or "legacy",
                    "missing_refs": unmaterialized_refs,
                }
        blob_dir = _blob_dir(session_dir)
        # #746: verify ALL blobs exist BEFORE the first overwrite — the old
        # loop overwrote refs one-by-one and reported missing blobs only
        # after N-1 refs were already restored, leaving the session matching
        # neither the checkpoint nor the pre-rollback state.
        planned = {
            ref_id: blob_hash
            for ref_id, blob_hash in (
                {} if mode == "presentation_only" else descriptor["refs"]
            ).items()
            if isinstance(blob_hash, str)
        }
        missing_blobs = [
            ref_id
            for ref_id, blob_hash in planned.items()
            if not (blob_dir / _blob_filename(blob_hash)).is_file()
        ]
        if missing_blobs:
            return {
                "success": False,
                "message": (
                    f"Checkpoint '{checkpoint_id}' references missing blobs: "
                    f"{missing_blobs}"
                ),
            }
        restored = 0
        unrestorable: list[str] = []
        for ref_id, blob_hash in planned.items():
            blob_path = blob_dir / _blob_filename(blob_hash)
            payload = await asyncio.to_thread(_read_json_sync, blob_path)
            if payload is None:
                # TOCTOU guard: blob vanished between the existence check and
                # the read — abort; refs restored so far stay a prefix of the
                # checkpoint state, and the caller sees an explicit failure.
                return {
                    "success": False,
                    "message": (
                        f"Checkpoint '{checkpoint_id}' blob for ref '{ref_id}' "
                        "disappeared mid-restore"
                    ),
                }
            # #1072: overwrite 返回值此前未检查 —— Redis 抖动（RedisError →
            # False）或内存后端 ref 已被 LRU 逐出（键不存在 → False，与
            # Redis SET 会重建键不同）时，回滚静默"成功"，恢复出的 spec
            # sources 指向无负载 ref（瓦片/要素解析为空）。计数比对失败
            # 即响亮失败。
            if not await session_data_manager.overwrite(session_id_for_refs, ref_id, payload):
                unrestorable.append(ref_id)
            else:
                restored += 1
        if unrestorable:
            return {
                "success": False,
                "message": (
                    f"Checkpoint '{checkpoint_id}' could not restore "
                    f"{len(unrestorable)} ref(s): {unrestorable[:8]}"
                    "（ref 键缺失或存储不可用；已恢复的 ref 构成 checkpoint "
                    "状态的前缀）"
                ),
            }
        if mode != "presentation_only":
            ref_count = restored
    else:
        # 旧格式：materialized_refs.json 内联 payload。
        refs_file = ckpt_dir / "materialized_refs.json"
        refs_data = await asyncio.to_thread(_read_json_sync, refs_file)
        refs_data = refs_data if isinstance(refs_data, dict) else {}
        ref_count = 0
        refs_reused = 0
        _legacy_unrestorable: list[str] = []
        for ref_id, payload in refs_data.items():
            # #1072: 旧格式路径同样检查 overwrite 返回值。
            if not await session_data_manager.overwrite(session_id_for_refs, ref_id, payload):
                _legacy_unrestorable.append(ref_id)
                continue
            ref_count += 1
        if _legacy_unrestorable:
            return {
                "success": False,
                "message": (
                    f"Checkpoint '{checkpoint_id}' (legacy) could not restore "
                    f"{len(_legacy_unrestorable)} ref(s): {_legacy_unrestorable[:8]}"
                ),
            }

    return {
        "success": True,
        "checkpoint_id": checkpoint_id,
        "mapspec": mapspec,
        "ref_count": ref_count,
        "refs_reused": refs_reused,
        "checkpoint_mode": (
            descriptor.get("mode") if isinstance(descriptor, dict) else "legacy"
        ),
        "summary": (
            f"Recovered checkpoint '{checkpoint_id}' "
            f"({refs_reused} immutable live refs verified)"
        ),
    }
