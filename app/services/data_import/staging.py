"""G17 导入工作台 —— staging 存储（文件系统事实源）。

目录布局（DATA_DIR/import_batches/<batch_id>/）：
    owner.json    —— 归属（session_id + 用户指纹 + 创建/过期）
    batch.json    —— ImportBatchState（原子写，唯一事实源）
    sources/      —— 原始字节（写入后不可变；文件名 sanitize）
    outputs/      —— 成员提交产物（<member_key 安全化>.geojson / .table.json）
    diag/         —— 拒绝行明细（<member_key 安全化>-rejected.jsonl）

纪律：
- 原子写沿用仓库范式（同目录 tmp + fsync + os.replace）。
- 过期清理只删除自己 batch_id 目录（惰性：list/get 时触发）。
- 归属每次访问校验（session_id 必须与 owner.json 一致；会话本身的真实
  归属由 API/工具层的 verify_session_owner 保证，这里防串号）。
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.services.data_import import budgets
from app.services.data_import.models import (
    ImportBatchState,
    SourceFile,
    valid_batch_id,
)

logger = logging.getLogger(__name__)

NOTES_MAX = 64
_MEMBER_SAFE_RE = re.compile(r"[^A-Za-z0-9._\-]")


class StagingError(Exception):
    """staging 操作失败（typed；code 供 API 层映射）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = str(detail)[:300]


def _atomic_write_json(path: Path, payload: Any) -> None:
    """JSON 原子写（仓库范式：同目录 tmp + fsync + os.replace）。"""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def batches_root(data_dir: Optional[str] = None) -> Path:
    root = Path(data_dir or settings.DATA_DIR) / "import_batches"
    root.mkdir(parents=True, exist_ok=True)
    return root


def batch_root(batch_id: str, data_dir: Optional[str] = None) -> Path:
    if not valid_batch_id(batch_id):
        raise StagingError("INVALID_BATCH_ID", batch_id)
    return batches_root(data_dir) / batch_id


def member_safe_name(member_key: str) -> str:
    return _MEMBER_SAFE_RE.sub("_", member_key)[:150] or "member"


def compute_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def is_expired(state: ImportBatchState, now: Optional[datetime] = None) -> bool:
    if not state.expires_at:
        return False
    try:
        exp = datetime.fromisoformat(state.expires_at)
    except ValueError:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) >= exp


def stage_files(
    session_id: str,
    user_fingerprint: str,
    files: List[Tuple[str, bytes]],
    *,
    data_dir: Optional[str] = None,
    ttl_hours: int = budgets.BATCH_TTL_HOURS,
) -> ImportBatchState:
    """把一批上传字节写入 staging 并初始化状态（成员枚举随后进行）。

    预算：文件数、单文件大小、批次合计。任一超限 → 全部拒绝（不落部分）。
    """
    from app.services.data_import.models import new_batch_id, sanitize_stored_name

    if not files:
        raise StagingError("EMPTY_BATCH", "批次必须至少包含一个文件")
    if len(files) > budgets.MAX_FILES_PER_BATCH:
        raise StagingError(
            "TOO_MANY_FILES",
            f"单批最多 {budgets.MAX_FILES_PER_BATCH} 个文件，收到 {len(files)}",
        )
    from app.services.data_parser import MAX_VECTOR_SIZE

    total = 0
    for name, payload in files:
        if not isinstance(payload, (bytes, bytearray)):
            raise StagingError("INVALID_FILE", f"{name}: 非字节内容")
        size = len(payload)
        if size == 0:
            raise StagingError("EMPTY_FILE", f"{name}: 空文件")
        if size > MAX_VECTOR_SIZE:
            raise StagingError(
                "FILE_TOO_LARGE", f"{name}: {size} 字节超过单文件上限 {MAX_VECTOR_SIZE}"
            )
        total += size
    if total > budgets.MAX_BATCH_RAW_BYTES:
        raise StagingError("BATCH_TOO_LARGE", f"批次合计 {total} 字节超过上限")

    batch_id = new_batch_id()
    root = batch_root(batch_id, data_dir)
    src_dir = root / "sources"
    src_dir.mkdir(parents=True, exist_ok=True)

    sources: List[SourceFile] = []
    seen_stored: set = set()
    for idx, (name, payload) in enumerate(files):
        original = (name or f"file-{idx + 1}").strip() or f"file-{idx + 1}"
        ext = Path(original).suffix.lower()
        if ext == ".json":
            ext = ".geojson"  # 与旧上传白名单一致地把 json 视作 geojson 容器
        stored = f"src-{idx + 1}__{sanitize_stored_name(original)}"
        while stored in seen_stored:
            stored = f"src-{idx + 1}__{idx}-{sanitize_stored_name(original)}"
        seen_stored.add(stored)
        path = src_dir / stored
        if path.exists():  # pragma: no cover - 防御
            raise StagingError("STORED_NAME_COLLISION", stored)
        path.write_bytes(payload)
        sources.append(
            SourceFile(
                source_id=f"src-{idx + 1}",
                original_name=original,
                stored_name=stored,
                sha256=compute_sha256(payload),
                size=len(payload),
                ext=ext,
                upload_order=idx,
            )
        )

    now = datetime.now(timezone.utc)
    state = ImportBatchState(
        batch_id=batch_id,
        session_id=session_id,
        user_fingerprint=user_fingerprint[:120],
        created_at=now.isoformat(timespec="seconds"),
        updated_at=now.isoformat(timespec="seconds"),
        expires_at=(now + timedelta(hours=ttl_hours)).isoformat(timespec="seconds"),
        sources=sources,
    )
    _atomic_write_json(
        root / "owner.json",
        {
            "batch_id": batch_id,
            "session_id": session_id,
            "user_fingerprint": user_fingerprint[:120],
            "created_at": state.created_at,
            "expires_at": state.expires_at,
        },
    )
    (root / "outputs").mkdir(exist_ok=True)
    (root / "diag").mkdir(exist_ok=True)
    save_state(state, data_dir=data_dir)
    return state


def save_state(state: ImportBatchState, *, data_dir: Optional[str] = None) -> None:
    from app.services.data_import.models import utc_now_iso

    state.updated_at = utc_now_iso()
    # notes 唯一截断点（评审 P2-5）：任意路径 append 都收敛于此，防无界增长
    state.notes = list(state.notes)[-NOTES_MAX:]
    state.rev += 1
    root = batch_root(state.batch_id, data_dir)
    _atomic_write_json(root / "batch.json", json.loads(state.model_dump_json()))


def load_state(
    batch_id: str, *, data_dir: Optional[str] = None
) -> Optional[ImportBatchState]:
    if not valid_batch_id(batch_id):
        return None
    path = batch_root(batch_id, data_dir) / "batch.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return ImportBatchState.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 — 损坏状态按不存在处理并留痕
        logger.warning("import batch %s state corrupted: %s", batch_id, exc)
        return None


def source_path(state: ImportBatchState, source_id: str, *, data_dir: Optional[str] = None) -> Path:
    for s in state.sources:
        if s.source_id == source_id:
            return batch_root(state.batch_id, data_dir) / "sources" / s.stored_name
    raise StagingError("UNKNOWN_SOURCE", source_id)


def outputs_dir(state: ImportBatchState, *, data_dir: Optional[str] = None) -> Path:
    d = batch_root(state.batch_id, data_dir) / "outputs"
    d.mkdir(exist_ok=True)
    return d


def diag_dir(state: ImportBatchState, *, data_dir: Optional[str] = None) -> Path:
    d = batch_root(state.batch_id, data_dir) / "diag"
    d.mkdir(exist_ok=True)
    return d


def assert_owner(state: ImportBatchState, session_id: Optional[str]) -> ImportBatchState:
    """staging 归属校验：session 缺失或不匹配 → 统一 NOT_FOUND（不泄露存在性）。"""
    if not session_id or state.session_id != session_id:
        raise StagingError("NOT_FOUND", "导入批次不存在")
    return state


def list_batch_states(
    session_id: str, *, data_dir: Optional[str] = None
) -> List[ImportBatchState]:
    """列出会话的批次（惰性清理过期目录）。"""
    out: List[ImportBatchState] = []
    root = batches_root(data_dir)
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        state = load_state(child.name, data_dir=data_dir)
        if state is None or state.session_id != session_id:
            continue
        if is_expired(state):
            expire_batch(state.batch_id, data_dir=data_dir)
            continue
        out.append(state)
    out.sort(key=lambda s: s.created_at, reverse=True)
    _enforce_session_batch_cap(session_id, out, data_dir=data_dir)
    return out


def _enforce_session_batch_cap(
    session_id: str, states: List[ImportBatchState], *, data_dir: Optional[str] = None
) -> None:
    """会话内未终态批次超上限时，最旧的先过期（只清自己目录）。"""
    from app.services.data_import.models import BATCH_TERMINAL

    active = [s for s in states if s.status not in BATCH_TERMINAL]
    overflow = len(active) - budgets.MAX_BATCHES_PER_SESSION
    for state in sorted(active, key=lambda s: s.created_at)[: max(0, overflow)]:
        expire_batch(state.batch_id, data_dir=data_dir)


def delete_batch(batch_id: str, *, data_dir: Optional[str] = None) -> bool:
    """删除 staging（未提交文件清理；已提交产物 ref 归 SessionStore，不在此时删）。"""
    if not valid_batch_id(batch_id):
        return False
    root = batch_root(batch_id, data_dir)
    if not root.exists():
        return False
    shutil.rmtree(root, ignore_errors=True)
    return True


def expire_batch(batch_id: str, *, data_dir: Optional[str] = None) -> None:
    """过期 = 清理 staging 目录并把内存态语义留给调用方（目录没了即 expired）。"""
    delete_batch(batch_id, data_dir=data_dir)


def batch_summary(state: ImportBatchState) -> Dict[str, Any]:
    """列表用有界摘要（不携带成员明细）。"""
    from app.services.data_import.models import BATCH_TERMINAL

    return {
        "batch_id": state.batch_id,
        "status": state.status.value,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "expires_at": state.expires_at,
        "file_count": len(state.sources),
        "member_count": len(state.members),
        "selected_count": sum(1 for m in state.members if m.selected),
        "completed_count": sum(
            1 for r in state.receipts.values() if r.status == "completed"
        ),
        "plan_version": state.plan_version,
        "terminal": state.status in BATCH_TERMINAL,
        "notes": state.notes[-8:],
    }
