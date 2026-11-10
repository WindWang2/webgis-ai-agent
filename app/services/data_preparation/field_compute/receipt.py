"""计划/收据持久化（G19 / WP08）—— 有界 JSONL ring + **稳定键文件**。

- per-writer ``mkstemp + os.replace`` 原子替换；坏行跳过；ring 超限丢最旧；
- 文件名不含冒号（规避既有 descriptor store 在 Windows 的 WinError 123）；
- **幂等不依赖 ring**（独立评审 P2-11）：每条已发布收据同时按
  ``(compute_fingerprint, input_ref, input_content_hash)`` 写入稳定键文件；
  ``find_receipt`` 优先读该文件，因此 64 条 ring 溢出后幂等仍成立；
- 已发布收据**先写稳定键、再写 ring**，ring 失败时删除刚写的稳定键，确保
  「任一次写入失败 ⇒ 两个面都不存在已发布副本」（独立复审 P1）；
- ``probe_publish()`` 在执行前探测 ring 与 ``by_key`` 两个写入面都可落盘；
- ``published=False`` 的草稿收据在 store/登记之前写入，作为「收据可落盘」
  的前置闸门，且**不参与幂等、不对外可见**（独立评审 P1-3）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def stable_key(fingerprint: str, input_ref: str, content_hash: str) -> str:
    return hashlib.sha256(
        f"{fingerprint}|{input_ref}|{content_hash}".encode("utf-8")
    ).hexdigest()[:32]


def _stable_receipt_path(session_id: str, key: str) -> Path:
    return _storage_path(session_id, _RECEIPTS_FILE).parent / "by_key" / f"{key}.json"


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


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True,
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


def _append_sync(path: Path, records: List[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in records[-_MAX_RECORDS:]:
                f.write(json.dumps(r.to_bounded_dict(), ensure_ascii=False,
                                   sort_keys=True, separators=(",", ":"),
                                   default=str) + "\n")
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
    """幂等查找：稳定键文件优先（不受 ring 截断影响）；只返回已发布收据。"""
    key = stable_key(compute_fingerprint, input_ref, content_hash)
    stable = _stable_receipt_path(session_id, key)
    if stable.exists():
        try:
            raw = json.loads(stable.read_text(encoding="utf-8"))
            item = _receipt_from(raw)
            if item is not None and item.published:
                return item
        except Exception:  # noqa: BLE001
            pass
    try:
        records = await asyncio.to_thread(
            _load_sync, _storage_path(session_id, _RECEIPTS_FILE), ComputeReceipt)
    except Exception:  # noqa: BLE001
        return None
    for r in reversed(records):
        if (r.published and r.compute_fingerprint == compute_fingerprint
                and r.input_ref == input_ref
                and r.input_content_hash == content_hash):
            return r
    return None


async def probe_publish(session_id: str) -> bool:
    """探测 ring 与稳定键两个写入面都可落盘（草稿闸门的一部分）。

    修独立复审 P1：草稿闸门只验证 ring 可写时，最终 upsert 仍可能在
    ``by_key`` 目录创建/fsync 失败；先探测两个目录，把该失败面提前到
    store 与任何登记之前。
    """
    try:
        await asyncio.to_thread(_probe_publish_sync, session_id)
        return True
    except Exception:  # noqa: BLE001
        return False


def _probe_publish_sync(session_id: str) -> None:
    base = _storage_path(session_id, _RECEIPTS_FILE).parent
    for directory in (base, base / "by_key"):
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(directory), suffix=".probe")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("{}")
                f.flush()
                os.fsync(f.fileno())
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                logger.warning("[field_compute] probe cleanup failed",
                               exc_info=True)


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
    # 稳定键先写、ring 后写：ring 失败时删掉刚写的稳定键，保证「任一次写入
    # 失败 ⇒ 两个面都不存在已发布副本」（修独立复审 P1：不能出现 ring 里
    # published=True 而稳定键缺失、被 fallback 幂等命中的组合）。
    key_path: Optional[Path] = None
    if receipt.published:
        key = stable_key(receipt.compute_fingerprint, receipt.input_ref,
                         receipt.input_content_hash)
        key_path = _stable_receipt_path(session_id, key)
        _atomic_write_json(key_path, receipt.to_bounded_dict())
    try:
        _append_sync(path, records)
    except Exception:
        if key_path is not None:
            try:
                key_path.unlink()
            except OSError:
                logger.warning("[field_compute] stable key rollback failed",
                               exc_info=True)
        raise


__all__ = [
    "save_plan", "get_plan", "list_plans", "find_receipt", "append_receipt",
    "probe_publish", "stable_key",
]
