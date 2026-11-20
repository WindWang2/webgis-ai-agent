"""G39 计划/收据存储 — per-session JSONL + 原子替换 + 稳定键（WP02）。

先例：``data_preparation/field_compute/receipt.py``。布局::

    DATA_DIR/.webgis-agent/<session_id>/raster_sampling/
        plans.jsonl           # 计划（bounded ring）
        receipts.jsonl        # 收据（bounded ring）
        by_key/<digest>.json  # 稳定键收据（幂等短路事实源）

CAS：计划修订以 ``(plan_id, revision)`` 比对后整文件原子替换；写路径经
``threading.Lock`` 串行化（``asyncio.to_thread`` 把 mutator 分发到线程池，
「单进程 asyncio 串行写」的旧假设不成立 —— 并发 CAS 曾出现 5 全胜 0 冲突），
跨进程同卷 rename 原子性兜底（与先例一致）。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.raster_sampling.contracts import (
    ExecutionReceipt,
    RasterSamplingError,
    SamplingPlan,
)

_MAX_RECORDS = 200
_PLANS_FILE = "plans.jsonl"
_RECEIPTS_FILE = "receipts.jsonl"
_BY_KEY_DIR = "by_key"

# #2221：读-改-写 mutator 的全部临界区（load→mutate→atomic_write）共用
# 一把进程内锁。asyncio.to_thread 把调用分发到默认线程池，同一事件循环上
# 并发的 save_plan_cas / append_receipt 会在线程池里真并行 —— 无锁时 CAS
# 的「读修订→比对→写回」整体竞态，5 并发全成功、append 互相覆盖。锁把
# CAS 比对+写回变成原子段（锁内不 await，无线程内重入风险）。
_STORE_LOCK = threading.Lock()


def _domain_root(session_id: str) -> Path:
    from app.services.mapspec.store import BASE_STORAGE_DIR

    root = BASE_STORAGE_DIR.resolve()
    d = (root / str(session_id)).resolve()
    if d.parent != root or d.name != str(session_id):
        # 边界逃逸与 `x/../y` 型归一化别名都拒绝（会话命名空间精确匹配）
        raise RasterSamplingError(
            "INVALID_PLAN", "invalid session id for raster sampling storage",
            field="session_id")
    return d / "raster_sampling"


def _atomic_write_jsonl(path: Path, records: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001 — 坏行跳过（诚实缺席）
                    continue
    except Exception:  # noqa: BLE001
        return []
    return out[-_MAX_RECORDS:]


async def save_plan(plan: SamplingPlan) -> bool:
    try:
        await asyncio.to_thread(_save_plan_sync, plan)
        return True
    except Exception:  # noqa: BLE001
        return False


def _save_plan_sync(plan: SamplingPlan) -> None:
    with _STORE_LOCK:
        path = _domain_root(plan.session_id) / _PLANS_FILE
        records = [r for r in _load_jsonl(path) if r.get("plan_id") != plan.plan_id]
        records.append(plan.model_dump())
        _atomic_write_jsonl(path, records[-_MAX_RECORDS:])


async def save_plan_cas(
    plan: SamplingPlan, *, expected_revision: int
) -> SamplingPlan:
    """CAS 修订：旧 revision 不匹配 → PLAN_REVISION_CONFLICT。"""
    return await asyncio.to_thread(_save_plan_cas_sync, plan, expected_revision)


def _save_plan_cas_sync(plan: SamplingPlan, expected_revision: int) -> SamplingPlan:
    with _STORE_LOCK:
        path = _domain_root(plan.session_id) / _PLANS_FILE
        records = _load_jsonl(path)
        current = next((r for r in records if r.get("plan_id") == plan.plan_id), None)
        if current is None:
            raise RasterSamplingError(
                "PLAN_NOT_FOUND",
                f"plan {plan.plan_id} not found for revision update",
                field="plan_id", status_code=404)
        if int(current.get("revision") or 0) != expected_revision:
            raise RasterSamplingError(
                "PLAN_REVISION_CONFLICT",
                f"plan {plan.plan_id} is at revision "
                f"{current.get('revision')}, expected {expected_revision}",
                field="expected_revision", status_code=409,
                correction_hint="Reload the plan and retry with its current "
                                "revision.")
        records = [r for r in records if r.get("plan_id") != plan.plan_id]
        records.append(plan.model_dump())
        _atomic_write_jsonl(path, records[-_MAX_RECORDS:])
        return plan


async def get_plan(session_id: str, plan_id: str) -> Optional[SamplingPlan]:
    try:
        records = await asyncio.to_thread(
            _load_jsonl, _domain_root(session_id) / _PLANS_FILE)
    except Exception:  # noqa: BLE001
        return None
    raw = next((r for r in records if r.get("plan_id") == plan_id), None)
    if raw is None:
        return None
    try:
        return SamplingPlan.model_validate(raw)
    except Exception:  # noqa: BLE001 — 损坏行按缺席
        return None


async def list_plans(session_id: str) -> List[SamplingPlan]:
    try:
        records = await asyncio.to_thread(
            _load_jsonl, _domain_root(session_id) / _PLANS_FILE)
    except Exception:  # noqa: BLE001
        return []
    out: List[SamplingPlan] = []
    for raw in records:
        try:
            out.append(SamplingPlan.model_validate(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


async def delete_plan(session_id: str, plan_id: str) -> bool:
    try:
        return await asyncio.to_thread(_delete_plan_sync, session_id, plan_id)
    except Exception:  # noqa: BLE001
        return False


def _delete_plan_sync(session_id: str, plan_id: str) -> bool:
    with _STORE_LOCK:
        path = _domain_root(session_id) / _PLANS_FILE
        records = _load_jsonl(path)
        kept = [r for r in records if r.get("plan_id") != plan_id]
        if len(kept) == len(records):
            return False
        _atomic_write_jsonl(path, kept[-_MAX_RECORDS:])
        return True


# ── 收据（幂等短路 + 稳定键） ────────────────────────────────────────

def receipt_stable_key(params_digest: str) -> str:
    return params_digest


async def append_receipt(receipt: ExecutionReceipt, *, published: bool) -> bool:
    try:
        return await asyncio.to_thread(_append_receipt_sync, receipt, published)
    except Exception:  # noqa: BLE001
        return False


def _append_receipt_sync(receipt: ExecutionReceipt, published: bool) -> bool:
    with _STORE_LOCK:
        root = _domain_root(receipt.session_id)
        ring_path = root / _RECEIPTS_FILE
        records = [r for r in _load_jsonl(ring_path)
                   if r.get("execution_id") != receipt.execution_id]
        records.append(receipt.model_dump())
        _atomic_write_jsonl(ring_path, records[-_MAX_RECORDS:])
        if published:
            key_path = root / _BY_KEY_DIR / f"{receipt_stable_key(receipt.params_digest)}.json"
            key_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_jsonl(key_path, [receipt.model_dump()])
        return True


async def find_receipt(
    session_id: str, plan_id: str, params_digest: str
) -> Optional[ExecutionReceipt]:
    """幂等短路：稳定键 → ring → None（plan_id 匹配才认）。"""
    def _find() -> Optional[ExecutionReceipt]:
        root = _domain_root(session_id)
        key_path = root / _BY_KEY_DIR / f"{receipt_stable_key(params_digest)}.json"
        for path in (key_path,):
            for raw in _load_jsonl(path):
                if raw.get("plan_id") == plan_id:
                    try:
                        return ExecutionReceipt.model_validate(raw)
                    except Exception:  # noqa: BLE001
                        continue
        for raw in _load_jsonl(root / _RECEIPTS_FILE):
            if (raw.get("plan_id") == plan_id
                    and raw.get("params_digest") == params_digest):
                try:
                    return ExecutionReceipt.model_validate(raw)
                except Exception:  # noqa: BLE001
                    continue
        return None

    try:
        return await asyncio.to_thread(_find)
    except Exception:  # noqa: BLE001
        return None


async def list_receipts(session_id: str) -> List[ExecutionReceipt]:
    try:
        records = await asyncio.to_thread(
            _load_jsonl, _domain_root(session_id) / _RECEIPTS_FILE)
    except Exception:  # noqa: BLE001
        return []
    out: List[ExecutionReceipt] = []
    for raw in records:
        try:
            out.append(ExecutionReceipt.model_validate(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


async def get_receipt(
    session_id: str, execution_id: str
) -> Optional[ExecutionReceipt]:
    try:
        records = await asyncio.to_thread(
            _load_jsonl, _domain_root(session_id) / _RECEIPTS_FILE)
    except Exception:  # noqa: BLE001
        return None
    raw = next((r for r in records if r.get("execution_id") == execution_id), None)
    if raw is None:
        return None
    try:
        return ExecutionReceipt.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None


def now_ts() -> float:
    return time.time()
