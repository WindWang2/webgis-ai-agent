"""G10 刷新事务持久化（项目运行时体系内的增量账本）。

复用 ``app.services.project_runtime.store.runtime_snapshots_dir`` 的**路径
安全校验**与目录约定（不复制一份身份校验），但使用独立 ``data-refresh/``
子目录，避免与 runtime snapshot 清单互相干扰。

- 原子写：同目录 tmp + fsync + os.replace（要么完整、要么不存在）；
- lease：kernel fcntl 非阻塞锁，进程崩溃自动释放，用于 execute/adopt 互斥；
- cap：每项目 ≤25 份 proposal（与 runtime snapshot 同纪律），tmp 超时清理；
- 候选 MapSpec 独立文件 = GC pin 标记；discard 后 unlink 才释放引用与配额
  （不另起垃圾回收，blob 保护仍归既有 artifact lifecycle 引用计数）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.project_runtime.store import runtime_snapshots_dir

from .records import RefreshCandidate, RefreshProposal

logger = logging.getLogger(__name__)

MAX_PROPOSALS_PER_PROJECT = 25
PROPOSAL_PREFIX = "g10p-"
CANDIDATE_PREFIX = "g10c-"

#: R1-P2：所有拼进文件路径的 id 必须过白名单（复用 project_runtime.store
#: 的同款 charset 纪律），禁止 ``../`` / 绝对路径 / 空串。
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")


def _safe_id(value: str) -> bool:
    return bool(_ID_RE.match(value or ""))


def _dir(project_id: str) -> Optional[Path]:
    base = runtime_snapshots_dir(project_id)
    return base.parent / "data-refresh" if base is not None else None


def _atomic_write_json_sync(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json_sync(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


class RefreshStore:
    """proposal / candidate 的落盘、读取、清单、lease 与 pin 释放原语。"""

    def __init__(self) -> None:
        self._lease_fds: Dict[str, int] = {}
        self._lease_guard = threading.Lock()

    # ── proposal ────────────────────────────────────────────────────
    async def save_proposal(self, record: RefreshProposal) -> bool:
        if not _safe_id(record.proposal_id):
            return False
        d = _dir(record.project_id)
        if d is None:
            return False
        path = d / f"{PROPOSAL_PREFIX}{record.proposal_id}.json"
        try:
            await asyncio.to_thread(
                _atomic_write_json_sync, path, record.model_dump(mode="json")
            )
        except OSError as exc:
            logger.warning("[G10] proposal write failed project=%s: %s",
                           record.project_id, exc)
            return False
        await self._enforce_cap(record.project_id)
        return True

    def load_proposal(self, project_id: str, proposal_id: str) -> Optional[RefreshProposal]:
        if not _safe_id(proposal_id):
            return None
        d = _dir(project_id)
        if d is None:
            return None
        raw = _read_json_sync(d / f"{PROPOSAL_PREFIX}{proposal_id}.json")
        if raw is None:
            return None
        try:
            return RefreshProposal.model_validate(raw)
        except Exception:  # noqa: BLE001 — 损坏/字段漂移按不存在处理
            logger.warning("[G10] invalid proposal manifest %s/%s", project_id, proposal_id)
            return None

    async def list_proposals(self, project_id: str) -> List[Dict[str, Any]]:
        d = _dir(project_id)
        if d is None or not d.is_dir():
            return []

        def _list() -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for f in sorted(d.glob(f"{PROPOSAL_PREFIX}*.json"),
                            key=lambda p: p.stat().st_mtime, reverse=True):
                raw = _read_json_sync(f)
                if not isinstance(raw, dict):
                    continue
                out.append({
                    "proposal_id": str(raw.get("proposal_id") or f.stem),
                    "owner_id": str(raw.get("owner_id") or ""),
                    "status": str(raw.get("status") or "preview"),
                    "created_at": raw.get("created_at"),
                    "updated_at": raw.get("updated_at"),
                    "proposal_fingerprint": str(raw.get("proposal_fingerprint") or ""),
                    "mapspec_revision": int(raw.get("mapspec_revision") or 0),
                    "sources": len((raw.get("inventory") or {}).get("items") or []),
                    "recompute_nodes": len((raw.get("plan") or {}).get("recompute") or []),
                    "adopted_revision": raw.get("adopted_revision"),
                })
                if len(out) >= MAX_PROPOSALS_PER_PROJECT:
                    break
            return out

        try:
            return await asyncio.to_thread(_list)
        except OSError:
            return []

    # ── candidate ───────────────────────────────────────────────────
    async def save_candidate(self, record: RefreshCandidate) -> bool:
        if not _safe_id(record.candidate_id):
            return False
        d = _dir(record.project_id)
        if d is None:
            return False
        try:
            await asyncio.to_thread(
                _atomic_write_json_sync,
                d / f"{CANDIDATE_PREFIX}{record.candidate_id}.json",
                record.model_dump(mode="json"),
            )
        except OSError as exc:
            logger.warning("[G10] candidate write failed: %s", exc)
            return False
        return True

    def load_candidate(self, project_id: str, candidate_id: str) -> Optional[RefreshCandidate]:
        if not _safe_id(candidate_id):
            return None
        d = _dir(project_id)
        if d is None:
            return None
        raw = _read_json_sync(d / f"{CANDIDATE_PREFIX}{candidate_id}.json")
        if raw is None:
            return None
        try:
            return RefreshCandidate.model_validate(raw)
        except Exception:  # noqa: BLE001
            return None

    async def save_candidate_doc(
        self, project_id: str, candidate_id: str, doc: Dict[str, Any]
    ) -> str:
        """候选 MapSpec 写隔离命名空间（文件存在 = GC pin 生效）。"""
        if not _safe_id(candidate_id):
            return ""
        d = _dir(project_id)
        if d is None:
            return ""
        path = d / f"{CANDIDATE_PREFIX}{candidate_id}.doc.json"
        await asyncio.to_thread(_atomic_write_json_sync, path, doc)
        return str(path)

    def load_candidate_doc(self, project_id: str, candidate_id: str) -> Optional[Dict[str, Any]]:
        if not _safe_id(candidate_id):
            return None
        d = _dir(project_id)
        if d is None:
            return None
        return _read_json_sync(d / f"{CANDIDATE_PREFIX}{candidate_id}.doc.json")

    async def release_candidate_pin(self, project_id: str, candidate_id: str) -> bool:
        """放弃候选后释放引用/配额（只删本任务自己创建的候选 doc/manifest）。"""
        if not _safe_id(candidate_id):
            return False
        d = _dir(project_id)
        if d is None:
            return False
        removed = False
        for name in (f"{CANDIDATE_PREFIX}{candidate_id}.doc.json",
                     f"{CANDIDATE_PREFIX}{candidate_id}.json"):
            p = d / name
            if not p.is_file():
                continue
            try:
                await asyncio.to_thread(p.unlink)
                removed = True
            except OSError:
                continue
        return removed

    async def save_doc(self, project_id: str, name: str, doc: Dict[str, Any]) -> str:
        """隔离命名空间内的任意有界文档（候选 / 采纳前基线）。"""
        d = _dir(project_id)
        if d is None or not _safe_id(name):
            return ""
        path = d / f"{name}.doc.json"
        await asyncio.to_thread(_atomic_write_json_sync, path, doc)
        return str(path)

    def load_doc(self, project_id: str, name: str) -> Optional[Dict[str, Any]]:
        d = _dir(project_id)
        if d is None or not name.replace("-", "").replace("_", "").isalnum():
            return None
        return _read_json_sync(d / f"{name}.doc.json")

    # ── 基线（首次检查建立；采纳后原子推进） ────────────────────────
    async def save_baselines(self, project_id: str, payload: Dict[str, Any]) -> bool:
        """写入项目来源基线快照（版本/字段/要素证据），供后续只读比较。"""
        d = _dir(project_id)
        if d is None:
            return False
        try:
            await asyncio.to_thread(
                _atomic_write_json_sync, d / "baselines.json", payload
            )
        except OSError:
            return False
        return True

    def load_baselines(self, project_id: str) -> Optional[Dict[str, Any]]:
        d = _dir(project_id)
        if d is None:
            return None
        return _read_json_sync(d / "baselines.json")

    # ── lease（execute / adopt 互斥；进程死亡自动释放） ───────────────
    def acquire_lease(self, project_id: str, key: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        if not _safe_id(key):
            return False, {"reason": "invalid lease key"}
        d = _dir(project_id)
        if d is None:
            return False, {"reason": "invalid ids"}
        try:
            import fcntl
        except ImportError:
            return False, {"reason": "advisory file locks unavailable"}
        d.mkdir(parents=True, exist_ok=True)
        lease = d / f"{key}.lease"
        fd = os.open(str(lease), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(fd)
            return False, {"reason": "operation already running",
                           **(_read_json_sync(lease) or {})}
        try:
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps({"pid": os.getpid(), "at": time.time()}).encode())
            with self._lease_guard:
                self._lease_fds[str(lease)] = fd
        except BaseException:
            os.close(fd)
            raise
        return True, None

    def release_lease(self, project_id: str, key: str) -> None:
        if not _safe_id(key):
            return
        d = _dir(project_id)
        if d is None:
            return
        lease = str(d / f"{key}.lease")
        with self._lease_guard:
            fd = self._lease_fds.pop(lease, None)
        if fd is not None:
            os.close(fd)

    # ── cap ─────────────────────────────────────────────────────────
    async def _enforce_cap(self, project_id: str) -> None:
        d = _dir(project_id)
        if d is None:
            return

        def _cap() -> None:
            if not d.is_dir():
                return
            now = time.time()
            for tmp in d.glob("*.tmp-*"):
                try:
                    if now - tmp.stat().st_mtime > 3600:
                        tmp.unlink()
                except OSError:
                    continue
            # R2-P2：glob 会同时匹配隔离基线 doc（*.doc.json）。它们不是
            # proposal manifest，既不能计入配额，也不能被当作可回收项删除。
            files = sorted(
                (f for f in d.glob(f"{PROPOSAL_PREFIX}*.json")
                 if not f.name.endswith(".doc.json")),
                key=lambda p: p.stat().st_mtime,
            )
            overflow = len(files) - MAX_PROPOSALS_PER_PROJECT
            for f in files[: max(0, overflow)]:
                # 已采纳/有候选 pin 的 proposal 不回收（交付固定版本不可失效）
                raw = _read_json_sync(f) or {}
                if raw.get("adopted_revision") is not None:
                    continue
                # R1-P2：仍有隔离候选文件 = GC pin 生效，不得回收 proposal
                stem = f.stem[len(PROPOSAL_PREFIX):]
                if (d / f"{CANDIDATE_PREFIX}g10c-{stem}.doc.json").is_file():
                    continue
                try:
                    f.unlink()
                except OSError:
                    continue

        try:
            await asyncio.to_thread(_cap)
        except OSError:
            logger.debug("[G10] cap sweep failed", exc_info=True)


_store: Optional[RefreshStore] = None


def get_refresh_store() -> RefreshStore:
    global _store
    if _store is None:
        _store = RefreshStore()
    return _store


def reset_refresh_store() -> None:
    global _store
    _store = None


__all__ = ["RefreshStore", "get_refresh_store", "reset_refresh_store",
           "MAX_PROPOSALS_PER_PROJECT"]
