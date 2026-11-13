"""G35 状态仓库：owner 隔离、原子写、有界帧缓存、授权发布。

复用既有事实源：``export_paths.exports_root()``（DATA_DIR/exports 唯一真相）、
``map._persist_export_file / _set_export_owner``、``core.signing.sign_path``。
本模块不建新队列/版本库/Agent loop；只保存本领域计划、阶段投影与成果引用。
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .contracts import (
    MAX_FRAME_CACHE_BYTES, MAX_FRAME_CACHE_ENTRIES, MAX_MANIFEST_CHARS,
    TemporalMediaError,
)

_TERMINAL = frozenset(("completed", "cancelled", "failed"))


def owner_key(principal: str) -> str:
    """owner 作用域目录（不可逆摘要，避免路径穿越与跨 owner 缓存命中）。"""
    return hashlib.sha256(str(principal).encode("utf-8")).hexdigest()[:24]


def media_root() -> Path:
    from app.services.export_paths import ensure_exports_root
    return ensure_exports_root() / "temporal_media"


def job_dir(principal: str, job_id: str) -> Path:
    safe = "".join(c for c in str(job_id) if c.isalnum() or c in "-_")[:64]
    if not safe:
        raise TemporalMediaError("INVALID_JOB_ID", "invalid job id", status_code=422)
    return media_root() / owner_key(principal) / safe


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str).encode("utf-8")
    _atomic_bytes(path, data)


def read_json(path: Path, *, max_bytes: int = MAX_MANIFEST_CHARS) -> Dict[str, Any]:
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise TemporalMediaError(
            "STATE_UNAVAILABLE", "owned media state record is missing or oversized",
            status_code=404)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TemporalMediaError("STATE_CORRUPT", "media state record is corrupt",
                                 status_code=500)
    return payload


def save_plan(principal: str, job_id: str, plan: Dict[str, Any]) -> None:
    atomic_json(job_dir(principal, job_id) / "plan.json", plan)


def load_plan(principal: str, job_id: str) -> Dict[str, Any]:
    return read_json(job_dir(principal, job_id) / "plan.json",
                     max_bytes=16 * 1024 * 1024)


def save_preview(principal: str, plan: Dict[str, Any]) -> str:
    """owner 作用域的预览计划句柄（16 位短 ref → 完整冻结计划）。"""
    ref = str(plan["plan_hash"])[:16]
    atomic_json(job_dir(principal, "preview_" + ref) / "plan.json", plan)
    return ref


def resolve_preview(principal: str, ref: str) -> Dict[str, Any]:
    if len(ref) != 16 or not all(c in "0123456789abcdef" for c in ref):
        raise TemporalMediaError("PLAN_VERSION_MISMATCH",
                                 "unknown preview plan_ref", status_code=409)
    try:
        plan = load_plan(principal, "preview_" + ref)
    except TemporalMediaError as exc:
        raise TemporalMediaError("PLAN_VERSION_MISMATCH",
                                 "unknown or expired preview plan_ref",
                                 status_code=409) from exc
    if str(plan.get("plan_hash"))[:16] != ref:
        raise TemporalMediaError("PLAN_VERSION_MISMATCH",
                                 "preview plan_ref fingerprint mismatch",
                                 status_code=409)
    return plan


def initial_state(principal: str, job_id: str, plan: Dict[str, Any],
                  durable_job_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "schema_version": "temporal_media_state.v1",
        "job_id": str(job_id),
        "owner_key": owner_key(principal),
        "durable_job_id": durable_job_id,
        "plan_hash": plan["plan_hash"],
        "status": "pending",
        "phase": "queued",
        "created_at": time.time(),
        "progress": None,
        "message": "媒体任务已登记，等待 worker 认领",
        "version": 1,
        "attempt": 0,
        "current_frame": 0,
        "total_frames": int(plan["resource_estimate"]["unique_digital_frames"]),
        "rendered_frames": 0,
        "cache_hits": 0,
        "encoded_frames": 0,
        "artifacts": {},
        "error": None,
        "cancel_requested": False,
    }


def save_state(principal: str, job_id: str, state: Dict[str, Any]) -> None:
    atomic_json(job_dir(principal, job_id) / "state.json", state)


def load_state(principal: str, job_id: str) -> Dict[str, Any]:
    state = read_json(job_dir(principal, job_id) / "state.json")
    if str(state.get("job_id") or "") != str(job_id):
        raise TemporalMediaError("STATE_CORRUPT",
                                 "media state record does not belong to this job id",
                                 status_code=500)
    return state


def update_state(principal: str, job_id: str,
                 mutator: Callable[[Dict[str, Any]], None]) -> Dict[str, Any]:
    """跨进程文件锁下的读-改-写；终态字段整体保护 + 版本单调 CAS。

    uvicorn（取消）与 Celery worker（finalize）是不同进程，纯内存 CAS 会
    出现「两者都读到 running，最后落盘者胜」的竞态。用 state.lock 的
    ``flock(LOCK_EX)`` 把读-改-写收口为一次临界区；终态一旦成立，
    迟到成功/进度/成果一律整体回退，而不是只回退 status。
    """
    directory = job_dir(principal, job_id)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "state.lock").open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            state = load_state(principal, job_id)
            before = dict(state)
            mutator(state)
            if before.get("status") in _TERMINAL:
                # 终态不可被任何迟到写入改写（含 artifacts/phase/progress）。
                for key in ("status", "phase", "progress", "artifacts",
                            "verification", "error", "message",
                            "rendered_frames", "encoded_frames",
                            "cache_hits", "current_frame", "total_frames"):
                    state[key] = before.get(key)
                state["cancel_requested"] = bool(before.get("cancel_requested"))
            state["version"] = int(before.get("version") or 0) + 1
            save_state(principal, job_id, state)
            return state
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def request_cancel(principal: str, job_id: str) -> Dict[str, Any]:
    def mutate(state: Dict[str, Any]) -> None:
        if state.get("status") in ("completed",):
            return
        state["cancel_requested"] = True
        if state.get("status") in ("pending", "running"):
            state["status"] = "cancelled"
            state["phase"] = "cancelled"
            state["message"] = "用户取消：后续帧与编码停止，迟到成功不得覆盖"
    return update_state(principal, job_id, mutate)


def is_cancelled(principal: str, job_id: str) -> bool:
    try:
        state = load_state(principal, job_id)
    except TemporalMediaError:
        return False
    return bool(state.get("cancel_requested")) or state.get("status") == "cancelled"


# ── 有界数字帧缓存（条目 + 字节双上限；只复用同指纹且可解码的帧）─────────
def cache_dir(principal: str) -> Path:
    return media_root() / "_frame_cache" / owner_key(principal)


def frame_key(plan: Dict[str, Any], segment: Dict[str, Any]) -> str:
    """owner + 计划 + 源 + frame + renderer 指纹共同决定缓存身份。"""
    fp = plan["frozen_fingerprints"]
    parts = [
        owner_key(plan["owner"]),
        plan["plan_hash"],
        fp["document"], fp["temporal"], fp["style"], fp["viewport"],
        str(fp["renderer"]), str(fp["font"]),
        str(plan["canvas"]["width"]), str(plan["canvas"]["height"]),
        segment["window"]["start"], segment["window"]["end"],
        segment["time_label"], str(segment["held_from"]), segment["kind"],
        str(plan["format"]),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:40]


def cached_frame(principal: str, key: str) -> Optional[Tuple[Path, Dict[str, Any]]]:
    """缓存命中必须可读取且 checksum 正确，否则视为未命中。"""
    from .freeze import sha256_bytes

    base = cache_dir(principal) / key
    png, meta = base.with_suffix(".png"), base.with_suffix(".json")
    if not png.is_file() or not meta.is_file():
        return None
    try:
        evidence = read_json(meta, max_bytes=256 * 1024)
        data = png.read_bytes()
    except (OSError, TemporalMediaError):
        return None
    if "sha256:" + sha256_bytes(data) != evidence.get("png_sha256"):
        return None
    from PIL import Image
    import io
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            if list(im.size) != list(evidence.get("size") or []):
                return None
    except Exception:  # noqa: BLE001 — 损坏帧一律不复用
        return None
    return png, evidence


def put_cached_frame(principal: str, key: str, png_bytes: bytes,
                     evidence: Dict[str, Any]) -> Path:
    base = cache_dir(principal) / key
    _atomic_bytes(base.with_suffix(".png"), png_bytes)
    atomic_json(base.with_suffix(".json"), evidence)
    _evict(cache_dir(principal))
    return base.with_suffix(".png")


def _evict(root: Path) -> None:
    if not root.is_dir():
        return
    def _sidecars(path: Path) -> List[Path]:
        return [path, path.with_suffix(".json"), path.with_suffix(".svg")]

    def _bytes(path: Path) -> int:
        return sum(p.stat().st_size for p in _sidecars(path) if p.is_file())

    entries = sorted((p for p in root.glob("*.png") if p.is_file()),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    total = sum(_bytes(p) for p in entries)
    for path in entries[MAX_FRAME_CACHE_ENTRIES:]:
        total -= _bytes(path)
        for side in _sidecars(path):
            side.unlink(missing_ok=True)
    if total > MAX_FRAME_CACHE_BYTES:
        for path in reversed(entries[:MAX_FRAME_CACHE_ENTRIES]):
            if total <= MAX_FRAME_CACHE_BYTES:
                break
            total -= _bytes(path)
            for side in _sidecars(path):
                side.unlink(missing_ok=True)


def publish_bytes(filename: str, data: bytes, ext: str,
                  principal: str) -> str:
    """原子发布到既有 exports 根 + 注册 .owner；返回签名下载 URL。"""
    from app.api.routes.map import _persist_export_file, _set_export_owner
    from app.core.signing import sign_path

    _persist_export_file(filename, data, ext)
    _set_export_owner(filename, principal)
    path = f"/api/v1/export/download/{filename}"
    exp, sig = sign_path(path, ttl_seconds=3600)
    return f"{path}?exp={exp}&sig={sig}"


def new_artifact_name(job_id: str, stem: str, ext: str) -> str:
    safe = "".join(c for c in str(job_id) if c.isalnum() or c in "-_")[:24]
    return f"temporal_media_{safe}_{stem}_{uuid.uuid4().hex[:10]}{ext}"


def list_jobs(principal: str) -> List[Dict[str, Any]]:
    root = media_root() / owner_key(principal)
    if not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for state_path in sorted(root.glob("*/state.json")):
        try:
            state = read_json(state_path)
        except (TemporalMediaError, OSError, ValueError):
            continue
        out.append({k: state.get(k) for k in (
            "job_id", "status", "phase", "plan_hash", "progress", "message",
            "created_at")})
    # 按创建时间而非 job_id 字典序；重开页面要恢复「最近一次」任务。
    out.sort(key=lambda item: float(item.get("created_at") or 0))
    return out[-50:]


__all__ = [
    "owner_key", "media_root", "job_dir", "atomic_json", "read_json",
    "save_plan", "load_plan", "save_preview", "resolve_preview",
    "initial_state", "save_state", "load_state",
    "update_state", "request_cancel", "is_cancelled", "frame_key",
    "cached_frame", "put_cached_frame", "publish_bytes",
    "new_artifact_name", "list_jobs",
]
