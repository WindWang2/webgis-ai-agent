"""ReviewStore —— proposal 治理对象的 per-session 持久层（ADR-0201）。

存储决策（DECISIONS #1）：proposal 与其所治理的 mapspec 同生命周期
（mapspec 本身就是 Redis+盘、无 DB 表），因此沿用 checkpoint manifest 的
同款模式：`BASE_STORAGE_DIR/<sid>/review/proposals.json` 原子 JSON 写 +
per-session asyncio.Lock 串行化变迁。文件即审计载体（可整包导出）。

fail-closed：损坏的存储文件抛 ReviewStoreCorrupt（不静默清零历史）；
容量上限 ReviewStoreFull（更新既有 proposal 不受限）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import ValidationError

from app.schemas.review_schema import ReviewProposal

logger = logging.getLogger(__name__)

#: 每会话 proposal 容量（治理对象低频；超限是滥用信号，明确拒绝）。
MAX_PROPOSALS_PER_SESSION = 100
_STORE_VERSION = 1
_FILE_NAME = "proposals.json"


class ReviewStoreError(Exception):
    """review 存储层错误基类。"""


class ReviewStoreFull(ReviewStoreError):
    """会话 proposal 数超容量上限。"""


class ReviewStoreCorrupt(ReviewStoreError):
    """存储文件损坏 —— fail-closed，不做静默清零。"""


def _default_base_dir() -> Path:
    from app.services.mapspec.store import BASE_STORAGE_DIR

    return BASE_STORAGE_DIR


class ReviewStore:
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else _default_base_dir()
        self._locks: Dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    # ── 路径 ────────────────────────────────────────────────────────────
    def _review_dir(self, session_id: str) -> Path:
        # 与 MapSpecStore._session_dir_path 同款防线：会话 id 不得逃出 base。
        raw = str(session_id)
        safe = "".join(
            ch for ch in raw if ch not in ('\\', '/', ':', '*', '?', '"', '<', '>', '|', '\x00')
        )
        # 空/纯点 id 会落到共享目录（review R1-P3 防御纵深）—— 显式拒绝。
        if not safe or safe in (".", ".."):
            raise ReviewStoreError(f"invalid session id: {session_id!r}")
        d = (self.base_dir / safe / "review").resolve()
        base_resolved = self.base_dir.resolve()
        if base_resolved not in d.parents:
            raise ReviewStoreError(f"invalid session id: {session_id!r}")
        return d

    def _store_path(self, session_id: str) -> Path:
        return self._review_dir(session_id) / _FILE_NAME

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        # 锁表按会话惰性建；会话数量级 = 活跃协作会话（有界），不专门 GC。
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    # ── 读写 ────────────────────────────────────────────────────────────
    async def _load_all(self, session_id: str) -> List[ReviewProposal]:
        return await asyncio.to_thread(self._load_all_sync, session_id)

    def _load_all_sync(self, session_id: str) -> List[ReviewProposal]:
        path = self._store_path(session_id)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ReviewStoreCorrupt(f"review store corrupt for {session_id}: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != _STORE_VERSION:
            raise ReviewStoreCorrupt(
                f"review store version mismatch for {session_id}: {type(raw).__name__}"
            )
        items = raw.get("proposals")
        if not isinstance(items, list):
            raise ReviewStoreCorrupt(f"review store proposals missing for {session_id}")
        try:
            return [ReviewProposal.model_validate(item) for item in items]
        except ValidationError as exc:
            raise ReviewStoreCorrupt(
                f"review store schema invalid for {session_id}: {exc}"
            ) from exc

    def _save_all_sync(self, session_id: str, proposals: List[ReviewProposal]) -> None:
        rdir = self._review_dir(session_id)
        rdir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _STORE_VERSION,
            "proposals": [p.model_dump(mode="json") for p in proposals],
        }
        path = rdir / _FILE_NAME
        # 原子写（同目录 temp + replace；与 mapspec/checkpoint 同款纪律）。
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
    async def list_proposals(self, session_id: str) -> List[ReviewProposal]:
        return await self._load_all(session_id)

    async def get_proposal(
        self, session_id: str, proposal_id: str,
    ) -> Optional[ReviewProposal]:
        for p in await self._load_all(session_id):
            if p.proposal_id == proposal_id:
                return p
        return None

    # ── 分布式锁（review R1-P1：跨进程串行化）────────────────────────────
    def _durable_lock(self, session_id: str):
        """与 mutation 平面同源的跨进程互斥（Redis 生产 / 进程内降级）。

        多 worker 部署下 proposals.json 是整文件读改写 —— 只有进程内
        asyncio.Lock 会出现 last-writer-wins（审批被并发评论抹掉、终态
        回退）。锁降级/丢失 fail-closed：治理平面宁可拒绝也不丢更新。
        """
        from app.services.distributed_lock import session_lock_registry

        return session_lock_registry.lock(
            session_id, fail_on_degraded=True, fail_on_lost=True,
        )

    async def save_proposal(self, session_id: str, proposal: ReviewProposal) -> None:
        """整文件原子重写；新增超容量拒绝（更新既有不受限）。"""
        async with self._durable_lock(session_id):
            async with self._lock_for(session_id):
                await asyncio.to_thread(self._save_checked_sync, session_id, proposal)

    def _save_checked_sync(self, session_id: str, proposal: ReviewProposal) -> None:
        current = self._load_all_sync(session_id)
        if all(p.proposal_id != proposal.proposal_id for p in current):
            if len(current) >= MAX_PROPOSALS_PER_SESSION:
                raise ReviewStoreFull(
                    f"session {session_id} already holds {MAX_PROPOSALS_PER_SESSION} proposals"
                )
        merged = [p for p in current if p.proposal_id != proposal.proposal_id]
        merged.append(proposal)
        self._save_all_sync(session_id, merged)

    async def mutate_proposal(
        self,
        session_id: str,
        proposal_id: str,
        fn: Callable[[ReviewProposal], Any],
    ) -> Optional[ReviewProposal]:
        """锁内 read-modify-write：fn(proposal) -> proposal（可异步）。

        fn 返回原对象（无变更）也安全；返回 None 表示放弃变更（保持原样）。
        """
        async with self._durable_lock(session_id):
            async with self._lock_for(session_id):
                proposals = await self._load_all(session_id)
                target: Optional[ReviewProposal] = None
                for i, p in enumerate(proposals):
                    if p.proposal_id == proposal_id:
                        target = p
                        break
                if target is None:
                    return None
                updated = fn(target)
                if asyncio.iscoroutine(updated):
                    updated = await updated
                if updated is None:
                    return target
                if not isinstance(updated, ReviewProposal):  # 防御：fn 契约错误
                    raise ReviewStoreError("mutate fn must return ReviewProposal")
                merged = [
                    updated if p.proposal_id == proposal_id else p for p in proposals
                ]
                await asyncio.to_thread(self._save_all_sync, session_id, merged)
                return updated


#: 进程级单例（路由/service 共用）。
review_store = ReviewStore()
