"""ProposalStore —— feature proposals 的 per-session 持久层（G32）。

与 review/store.py（ADR-0203）同款模式：`BASE_STORAGE_DIR/<sid>/feature_proposals/
proposals.json` 原子 JSON 写（同目录 temp + fsync + os.replace）+ per-session
asyncio.Lock + 分布式锁（session_lock_registry，降级 fail-closed）。文件即
审计载体；跨重启/跨 worker 持久。

合并互斥：`status == merging` 与 merge 租约附表（`merge_leases`）同一次原子
写落盘；进程在提交桥窗口内崩溃后，租约过期由读取/变迁路径自动回收 ——
但**回收不重置 merging**：提交桥的恢复判据（bridge.py）先于任何重试运行，
merging 在租约过期后仍停留 recovery_required 判定，绝不因超时直接回到待执行。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import ValidationError

from app.schemas.feature_proposal_schema import FeatureProposalRecord
from app.services.feature_proposals.contracts import (
    MERGE_LEASE_TTL_SECONDS,
    FeatureProposalConflict,
    FeatureProposalError,
)

logger = logging.getLogger(__name__)

MAX_PROPOSALS_PER_SESSION = 50
_STORE_VERSION = 1
_FILE_NAME = "proposals.json"
_LEASES_KEY = "merge_leases"


class ProposalStoreError(FeatureProposalError):
    pass


class ProposalStoreFull(ProposalStoreError):
    pass


class ProposalStoreCorrupt(ProposalStoreError):
    """存储文件损坏 —— fail-closed，不静默清零。"""


class ProposalRevisionMismatch(FeatureProposalConflict):
    """提案 revision CAS 失败（旧客户端/并发写）—— 409 语义。"""


def _default_base_dir() -> Path:
    from app.services.mapspec.store import BASE_STORAGE_DIR

    return BASE_STORAGE_DIR


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _lease_expired(stamp: str, now: Optional[float] = None) -> bool:
    try:
        leased_at = float(stamp)
    except (TypeError, ValueError):
        return True
    return (_now() if now is None else now) - leased_at > MERGE_LEASE_TTL_SECONDS


def _reclaim_expired_leases(
    proposals: List[FeatureProposalRecord], leases: Dict[str, str],
) -> Dict[str, str]:
    """回收过期合并租约（读取路径自愈）。

    租约过期只解锁「互斥」（允许恢复判据运行），**不改变 proposal 状态**：
    merging → recovery_required 的判定在 bridge.recover_or_raise 里做，那里
    才有权威证据（journal/receipt）。坏时间戳按过期处理（fail-open 于恢复，
    fail-closed 于治理 —— 同 review #1722 纪律）。
    """
    reclaimed: List[str] = []
    for p in proposals:
        if p.status != "merging":
            continue
        stamp = leases.get(p.proposal_id)
        if stamp is not None and not _lease_expired(stamp):
            continue
        leases.pop(p.proposal_id, None)
        reclaimed.append(p.proposal_id)
    if reclaimed:
        logger.info(
            "[feature_proposals] expired merge lease(s) unlocked for recovery: %s",
            ",".join(reclaimed),
        )
    live_ids = {p.proposal_id for p in proposals if p.status == "merging"}
    return {pid: ts for pid, ts in leases.items() if pid in live_ids}


class ProposalStore:
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else _default_base_dir()
        self._locks: Dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    # ── 路径（与 review store 同款防线）──────────────────────────────────
    def _proposals_dir(self, session_id: str) -> Path:
        raw = str(session_id)
        safe = "".join(
            ch for ch in raw if ch not in ('\\', '/', ':', '*', '?', '"', '<', '>', '|', '\x00')
        )
        if not safe or safe in (".", ".."):
            raise ProposalStoreError(f"invalid session id: {session_id!r}")
        d = (self.base_dir / safe / "feature_proposals").resolve()
        base_resolved = self.base_dir.resolve()
        if base_resolved not in d.parents:
            raise ProposalStoreError(f"invalid session id: {session_id!r}")
        return d

    def _store_path(self, session_id: str) -> Path:
        return self._proposals_dir(session_id) / _FILE_NAME

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    def _durable_lock(self, session_id: str):
        from app.services.distributed_lock import session_lock_registry

        return session_lock_registry.lock(
            session_id, fail_on_degraded=True, fail_on_lost=True,
        )

    # ── 读写 ────────────────────────────────────────────────────────────
    def _load_payload_sync(
        self, session_id: str,
    ) -> Tuple[List[FeatureProposalRecord], Dict[str, str]]:
        path = self._store_path(session_id)
        if not path.exists():
            return [], {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ProposalStoreCorrupt(f"proposal store corrupt for {session_id}: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != _STORE_VERSION:
            raise ProposalStoreCorrupt(f"proposal store version mismatch for {session_id}")
        items = raw.get("proposals")
        if not isinstance(items, list):
            raise ProposalStoreCorrupt(f"proposal store proposals missing for {session_id}")
        try:
            proposals = [FeatureProposalRecord.model_validate(item) for item in items]
        except ValidationError as exc:
            raise ProposalStoreCorrupt(f"proposal store schema invalid for {session_id}: {exc}") from exc
        raw_leases = raw.get(_LEASES_KEY)
        leases = (
            {str(k): str(v) for k, v in raw_leases.items()}
            if isinstance(raw_leases, dict) else {}
        )
        return proposals, _reclaim_expired_leases(proposals, leases)

    def _save_all_sync(
        self,
        session_id: str,
        proposals: List[FeatureProposalRecord],
        leases: Optional[Dict[str, str]] = None,
    ) -> None:
        rdir = self._proposals_dir(session_id)
        rdir.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {
            "version": _STORE_VERSION,
            "proposals": [p.model_dump(mode="json") for p in proposals],
        }
        if leases:
            payload[_LEASES_KEY] = dict(leases)
        path = rdir / _FILE_NAME
        fd, tmp_name = tempfile.mkstemp(dir=str(rdir), prefix=".proposals-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # ── 公开 API ────────────────────────────────────────────────────────
    async def list_proposals(self, session_id: str) -> List[FeatureProposalRecord]:
        proposals, _leases = await asyncio.to_thread(self._load_payload_sync, session_id)
        return proposals

    async def get_proposal(
        self, session_id: str, proposal_id: str,
    ) -> Optional[FeatureProposalRecord]:
        for p in await self.list_proposals(session_id):
            if p.proposal_id == proposal_id:
                return p
        return None

    async def save_new_proposal(
        self, session_id: str, proposal: FeatureProposalRecord,
    ) -> FeatureProposalRecord:
        """新建（容量闸 + 同 id 幂等拒绝）。"""
        async with self._durable_lock(session_id):
            async with self._lock_for(session_id):
                current, leases = await asyncio.to_thread(self._load_payload_sync, session_id)
                if any(p.proposal_id == proposal.proposal_id for p in current):
                    raise ProposalStoreError("proposal id already exists")
                if len(current) >= MAX_PROPOSALS_PER_SESSION:
                    raise ProposalStoreFull(
                        f"session {session_id} already holds {MAX_PROPOSALS_PER_SESSION} proposals"
                    )
                current.append(proposal)
                await asyncio.to_thread(self._save_all_sync, session_id, current, leases)
                return proposal

    async def mutate_proposal(
        self,
        session_id: str,
        proposal_id: str,
        fn: Callable[[FeatureProposalRecord], Any],
        *,
        expected_revision: Optional[int] = None,
    ) -> Optional[FeatureProposalRecord]:
        """锁内 read-modify-write（同 review 模式）。

        expected_revision 非空时做 CAS：fn 运行前的当前 revision 必须相等
        （旧客户端不得覆盖新草稿；revision 增量由调用方在 fn 内完成）。
        fn 返回 None = 放弃变更；返回 record = 落盘。
        """
        async with self._durable_lock(session_id):
            async with self._lock_for(session_id):
                proposals, leases = await asyncio.to_thread(
                    self._load_payload_sync, session_id,
                )
                target: Optional[FeatureProposalRecord] = None
                for p in proposals:
                    if p.proposal_id == proposal_id:
                        target = p
                        break
                if target is None:
                    return None
                if expected_revision is not None and target.revision != expected_revision:
                    raise ProposalRevisionMismatch(
                        "proposal_revision_conflict",
                        "proposal was updated concurrently; reload and retry",
                        {"expected": expected_revision, "current": target.revision},
                    )
                updated = fn(target)
                if asyncio.iscoroutine(updated):
                    updated = await updated
                if updated is None:
                    return target
                if not isinstance(updated, FeatureProposalRecord):
                    raise ProposalStoreError("mutate fn must return FeatureProposalRecord")
                merged = [
                    updated if p.proposal_id == proposal_id else p for p in proposals
                ]
                # merging 租约同生共死。
                if updated.status == "merging":
                    leases.setdefault(proposal_id, str(_now()))
                else:
                    leases.pop(proposal_id, None)
                await asyncio.to_thread(self._save_all_sync, session_id, merged, leases)
                return updated

    async def list_snapshot_keys(self, session_id: str) -> Dict[str, str]:
        """会话内活跃提案占用的 base 快照 key（引用释放检查用）。"""
        out: Dict[str, str] = {}
        for p in await self.list_proposals(session_id):
            if not p.snapshot_released:
                out.setdefault(p.base.snapshot_key, p.proposal_id)
        return out


#: 进程级单例。
proposal_store = ProposalStore()
