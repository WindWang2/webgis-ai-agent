"""计划/收据持久化（G19 / WP08）—— 与 preparation 同纪律的有界 JSONL ring。

- per-writer ``mkstemp + os.replace`` 原子替换；坏行跳过；超限丢最旧；
- 文件名不含冒号（规避 ``docs/adr`` 之外既有 descriptor store 在 Windows 的
  ``dsd-v1:<sha>.json`` WinError 123 基线缺陷）；
- 幂等键 = ``compute_fingerprint + input_ref + input_content_hash``：
  同计划重复提交返回既有成果，不同内容 revision **绝不**复用。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, List, Optional

from app.services.data_preparation.field_compute.contracts import (
    ComputePlan, ComputeReceipt,
)

_PLANS_FILE = "field_compute_plans.jsonl"
_RECEIPTS_FILE = "field_compute_receipts.jsonl"
_MAX_RECORDS = 64

logger = logging.getLogger(__name__)


def _storage_root() -> Path:
    from app.services.mapspec.store import BASE_STORAGE_DIR
    return BASE_STORAGE_DIR.resolve()


def _storage_path(session_id: str, filename: str) -> Path:
    root = _storage_root()
    session_dir = (root / str(session_id)).resolve()
    if session_dir.parent != root:
        raise ValueError("invalid session id for field compute storage")
    return session_dir / "field_compute" / filename


def _load_sync(path: Path, model: Any) -> List[Any]:
    if not path.exists():
        return []
    out: List[Any] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                item = (ComputePlan.from_dict(raw) if model is ComputePlan
                        else _receipt_from(raw))
                if item is not None:
                    out.append(item)
    except Exception:  # noqa: BLE001 — 读失败按空（诚实缺席）
        return []
    return out[-_MAX_RECORDS:]


def _receipt_from(raw: Any) -> Optional[ComputeReceipt]:
    try:
        return ComputeReceipt.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None


def _append_sync(path: Path, records: List[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in records[-_MAX_RECORDS:]:
                blob = (r.to_bounded_dict() if isinstance(r, ComputePlan)
                        else r.to_bounded_dict())
                f.write(json.dumps(blob, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            logger.warning("[field_compute] temp cleanup failed", exc_info=True)
        raise


async def save_plan(plan: ComputePlan) -> bool:
    try:
        await asyncio.to_thread(_save_plan_sync, plan)
        return True
    except Exception:  # noqa: BLE001
        return False


def _save_plan_sync(plan: ComputePlan) -> None:
    path = _storage_path(plan.session_id, _PLANS_FILE)
    records = [r for r in _load_sync(path, ComputePlan)
               if r.plan_id != plan.plan_id]
    records.append(plan)
    _append_sync(path, records)


async def get_plan(session_id: str, plan_id: str) -> Optional[ComputePlan]:
    try:
        records = await asyncio.to_thread(
            _load_sync, _storage_path(session_id, _PLANS_FILE), ComputePlan)
    except Exception:  # noqa: BLE001
        return None
    return next((r for r in records if r.plan_id == plan_id), None)


async def list_plans(session_id: str) -> List[ComputePlan]:
    try:
        return await asyncio.to_thread(
            _load_sync, _storage_path(session_id, _PLANS_FILE), ComputePlan)
    except Exception:  # noqa: BLE001
        return []


async def find_receipt(session_id: str, compute_fingerprint: str,
                       input_ref: str, content_hash: str) -> Optional[ComputeReceipt]:
    """幂等查找；内容锚不一致 = 另一个 revision，绝不复用。"""
    try:
        records = await asyncio.to_thread(
            _load_sync, _storage_path(session_id, _RECEIPTS_FILE), ComputeReceipt)
    except Exception:  # noqa: BLE001
        return None
    for r in reversed(records):
        if (r.compute_fingerprint == compute_fingerprint
                and r.input_ref == input_ref
                and r.input_content_hash == content_hash):
            return r
    return None


async def append_receipt(session_id: str, receipt: ComputeReceipt) -> bool:
    try:
        await asyncio.to_thread(_append_receipt_sync, session_id, receipt)
        return True
    except Exception:  # noqa: BLE001
        return False


def _append_receipt_sync(session_id: str, receipt: ComputeReceipt) -> None:
    path = _storage_path(session_id, _RECEIPTS_FILE)
    records = [r for r in _load_sync(path, ComputeReceipt)
               if not (r.compute_fingerprint == receipt.compute_fingerprint
                       and r.input_ref == receipt.input_ref
                       and r.input_content_hash == receipt.input_content_hash)]
    records.append(receipt)
    _append_sync(path, records)


__all__ = ["save_plan", "get_plan", "list_plans", "find_receipt", "append_receipt"]
