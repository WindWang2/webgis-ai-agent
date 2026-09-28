"""Turn event ledger：durable append-only 账本（H04 / ADR-0216）。

同步 SQLAlchemy 会话经 ``asyncio.to_thread`` 下放（仿 SpatialEventLedger /
workflow InstanceStore）；``factory`` 可注入（测试用临时 SQLite）。

幂等：``event_id`` UNIQUE 判重——先查后插 + 唯一约束竞争兜底（对撞 =
对方先落地，返回 duplicate），canonical 行 exactly-once observable。

有界性：所有查询 limit 硬帽 ``MAX_JOURNAL_QUERY=200``；compaction 把已
终局 turn 的非凭证行折叠为一行 ``turn_compacted`` 摘要（map_mutated /
带 payload_ref 行永不压缩——mutation receipt 链是凭证，必须保留全量）；
retention 按 ``occurred_at`` 删除。因果查询在压缩后仍 turn 粒度正确。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.lib.runtime.clock import from_db_utc, to_db_utc, utc_now
from app.models.harness_journal import TurnEventRow
from app.services.turn_journal.contracts import TurnEventRecord

logger = logging.getLogger(__name__)

#: 单次查询行数硬帽（防全量扫描；诊断面分页拿全量）。
MAX_JOURNAL_QUERY = 200
#: compaction 单批 turn 数上限（周期任务分批推进，不占长事务）。
COMPACTION_BATCH_TURNS = 50
#: compaction 后保留的行 kind（凭证行：mutation receipt / 外部 payload ref）。
_PRESERVE_KINDS = frozenset({"map_mutated"})

SessionFactory = Callable[[], Session]

#: 终局 turn 的判定 kind（kernel end_turn 唯一结算行；detail.status 结构化）。
TERMINAL_TURN_KIND = "turn_ended"


class AppendResult:
    __slots__ = ("event_id", "row_id", "status")

    def __init__(self, event_id: str, row_id: int, status: str) -> None:
        self.event_id = event_id
        self.row_id = row_id
        self.status = status  # appended | duplicate


class TurnEventLedger:
    """会话级 turn 事件账本（每调用独立会话；append-only，无消费 ACK）。"""

    def __init__(self, factory: Optional[SessionFactory] = None) -> None:
        if factory is None:
            from app.core.database import SessionLocal

            factory = SessionLocal
        self._factory = factory

    # ── append ────────────────────────────────────────────────────────

    def _append_sync(self, record: TurnEventRecord) -> AppendResult:
        with self._factory() as db:
            existing = db.execute(
                select(TurnEventRow).where(
                    TurnEventRow.event_id == record.event_id)
            ).scalar_one_or_none()
            if existing is not None:
                return AppendResult(existing.event_id, existing.id, "duplicate")
            row = TurnEventRow(**record.to_row_kwargs())
            db.add(row)
            try:
                db.commit()
            except Exception:  # noqa: BLE001 — 唯一约束竞争 = 对方先落地
                db.rollback()
                winner = db.execute(
                    select(TurnEventRow).where(
                        TurnEventRow.event_id == record.event_id)
                ).scalar_one_or_none()
                if winner is not None:
                    return AppendResult(winner.event_id, winner.id, "duplicate")
                raise
            return AppendResult(row.event_id, row.id, "appended")

    async def append(self, record: TurnEventRecord) -> AppendResult:
        """入账（幂等）；调用方（sink）负责失败降级，本方法只如实抛错。"""
        import asyncio

        return await asyncio.to_thread(self._append_sync, record)

    # ── read（全部有界）───────────────────────────────────────────────

    def _row_to_dict(self, row: TurnEventRow) -> Dict[str, Any]:
        return {
            "id": row.id,
            "event_id": row.event_id,
            "session_id": row.session_id,
            "turn_id": row.turn_id,
            "run_id": row.run_id,
            "step_id": row.step_id,
            "kind": row.kind,
            "host": row.host,
            "seq": row.seq,
            "causal_id": row.causal_id,
            "note": row.note,
            "detail": dict(row.detail or {}),
            "payload_ref": row.payload_ref,
            "mutation_revision": row.mutation_revision,
            "occurred_at": from_db_utc(row.occurred_at).isoformat()
            if row.occurred_at else None,
            "ingested_at": from_db_utc(row.ingested_at).isoformat()
            if row.ingested_at else None,
            "status": row.status,
        }

    async def list_events(
        self,
        session_id: str,
        *,
        turn_id: str = "",
        kinds: Optional[List[str]] = None,
        after_id: int = 0,
        limit: int = MAX_JOURNAL_QUERY,
    ) -> List[Dict[str, Any]]:
        """按因果序读（id 升序）；条件全部可选，行数硬帽。"""
        import asyncio

        return await asyncio.to_thread(
            self._list_events_sync, session_id,
            turn_id=turn_id, kinds=kinds, after_id=after_id, limit=limit,
        )

    def _list_events_sync(
        self,
        session_id: str,
        *,
        turn_id: str = "",
        kinds: Optional[List[str]] = None,
        after_id: int = 0,
        limit: int = MAX_JOURNAL_QUERY,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), MAX_JOURNAL_QUERY))
        with self._factory() as db:
            stmt = select(TurnEventRow).where(
                TurnEventRow.session_id == session_id)
            if turn_id:
                stmt = stmt.where(TurnEventRow.turn_id == turn_id)
            if kinds:
                stmt = stmt.where(TurnEventRow.kind.in_(kinds))
            if after_id:
                stmt = stmt.where(TurnEventRow.id > int(after_id))
            stmt = stmt.order_by(TurnEventRow.id).limit(limit)
            rows = db.execute(stmt).scalars().all()
            return [self._row_to_dict(r) for r in rows]

    def _turn_summaries_sync(
        self, session_id: str, *, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """per-turn 聚合（compaction 后仍正确：摘要行参与聚合）。"""
        limit = max(1, min(int(limit), MAX_JOURNAL_QUERY))
        with self._factory() as db:
            rows = db.execute(
                select(TurnEventRow).where(
                    TurnEventRow.session_id == session_id)
                .order_by(TurnEventRow.id.desc())
                .limit(limit * 20)
            ).scalars().all()
        turns: Dict[str, Dict[str, Any]] = {}
        for row in reversed(rows):  # 旧→新
            slot = turns.setdefault(row.turn_id, {
                "turn_id": row.turn_id,
                "first_id": row.id,
                "last_id": row.id,
                "kinds_count": {},
                "event_count": 0,
                "last_seq": 0,
                "terminal": False,
                "terminal_status": "",
                "last_mutation_revision": None,
                "run_id": row.run_id or "",
            })
            slot["last_id"] = row.id
            slot["event_count"] += 1
            slot["kinds_count"][row.kind] = \
                slot["kinds_count"].get(row.kind, 0) + 1
            slot["last_seq"] = max(slot["last_seq"], int(row.seq or 0))
            slot["run_id"] = slot["run_id"] or row.run_id or ""
            if row.kind == TERMINAL_TURN_KIND:
                slot["terminal"] = True
                status = str((row.detail or {}).get("status") or "")
                if status:
                    slot["terminal_status"] = status
            if row.mutation_revision is not None:
                slot["last_mutation_revision"] = max(
                    int(slot["last_mutation_revision"] or 0),
                    int(row.mutation_revision),
                )
        return list(turns.values())[-limit:]

    async def turn_summaries(
        self, session_id: str, *, limit: int = 50
    ) -> List[Dict[str, Any]]:
        import asyncio

        return await asyncio.to_thread(
            self._turn_summaries_sync, session_id, limit=limit)

    # ── compaction / retention ────────────────────────────────────────

    def _compactable_turns_sync(
        self, *, older_than: datetime, limit: int
    ) -> List[str]:
        """已终局且足够老的 turn_id 列表（旧→新）。"""
        cutoff = to_db_utc(older_than)
        with self._factory() as db:
            last_events = (
                select(
                    TurnEventRow.turn_id,
                    func.max(TurnEventRow.id).label("last_id"),
                )
                .where(TurnEventRow.turn_id != "")
                .group_by(TurnEventRow.turn_id)
                .subquery()
            )
            stmt = (
                select(TurnEventRow.turn_id)
                .join(last_events, TurnEventRow.id == last_events.c.last_id)
                .where(
                    TurnEventRow.kind == TERMINAL_TURN_KIND,
                    TurnEventRow.occurred_at < cutoff,
                )
                .order_by(TurnEventRow.id)
                .limit(max(1, min(limit, COMPACTION_BATCH_TURNS)))
            )
            return [r for r in db.execute(stmt).scalars().all()]

    def _compact_turn_sync(self, turn_id: str) -> int:
        """把一个已终局 turn 的非凭证行折叠为一行摘要，返回压缩行数。

        保留：map_mutated（mutation receipt）、带 payload_ref 行、终局行、
        已是摘要的行。事务内"插摘要 + 删旧行"原子完成；重入安全（再次
        压缩只剩凭证行，0 行被删）。
        """
        with self._factory() as db:
            rows = db.execute(
                select(TurnEventRow)
                .where(TurnEventRow.turn_id == turn_id)
                .order_by(TurnEventRow.id)
            ).scalars().all()
            if not rows:
                return 0
            # 终局判定看"任意位置存在 turn_ended 行"，而非最后一行——
            # late callback（终局后才落账的 map_mutated）会排在终局行之后。
            if not any(r.kind == TERMINAL_TURN_KIND for r in rows):
                return 0  # 未终局（防御；调用方已过滤）
            keep, kinds_count, last_seq, session_id = [], {}, 0, rows[0].session_id
            for row in rows:
                if row.status == "compacted" or row.kind in _PRESERVE_KINDS \
                        or bool(row.payload_ref) or row.kind == TERMINAL_TURN_KIND:
                    keep.append(row)
                    continue
                kinds_count[row.kind] = kinds_count.get(row.kind, 0) + 1
                last_seq = max(last_seq, int(row.seq or 0))
            if not kinds_count:
                return 0
            revisions = [
                int(r.mutation_revision) for r in rows
                if r.mutation_revision is not None
            ]
            terminal = next(
                (r for r in reversed(rows) if r.kind == TERMINAL_TURN_KIND), None)
            summary = TurnEventRow(
                event_id=f"compacted:{turn_id}",
                session_id=session_id,
                turn_id=turn_id,
                run_id=next((r.run_id for r in rows if r.run_id), ""),
                kind="turn_compacted",
                host="ledger",
                seq=last_seq,
                note=f"compacted {sum(kinds_count.values())} rows",
                detail={
                    "kinds_count": kinds_count,
                    "terminal_status": str(
                        (terminal.detail or {}).get("status") or ""),
                    "last_mutation_revision": max(revisions) if revisions else None,
                    "preserved_event_ids": [r.event_id for r in keep][:32],
                },
                mutation_revision=max(revisions) if revisions else None,
                occurred_at=terminal.occurred_at if terminal else rows[-1].occurred_at,
                ingested_at=to_db_utc(utc_now()),
                status="compacted",
            )
            db.add(summary)
            keep_ids = {k.id for k in keep}
            for row in rows:
                if row.id not in keep_ids:
                    db.delete(row)
            try:
                db.commit()
            except Exception:  # noqa: BLE001 — 摘要键冲突 = 已压缩过
                db.rollback()
                return 0
            return sum(kinds_count.values())

    def _sweep_retention_sync(
        self, *, older_than: datetime, limit: int
    ) -> int:
        cutoff = to_db_utc(older_than)
        with self._factory() as db:
            rows = db.execute(
                select(TurnEventRow)
                .where(TurnEventRow.occurred_at < cutoff)
                .order_by(TurnEventRow.id)
                .limit(max(1, min(limit, 1000)))
            ).scalars().all()
            for row in rows:
                db.delete(row)
            db.commit()
            return len(rows)

    async def compact_and_sweep(
        self,
        *,
        now: Optional[datetime] = None,
        compaction_age_s: float = 0.0,
        retention_age_s: float = 0.0,
        retention_limit: int = 1000,
    ) -> Dict[str, int]:
        """compaction + retention 各推进一步（幂等、分批、可重入）。

        先压缩（已终局且老于 compaction_age 的 turn），再按 retention_age
        删行。年龄 ≤0 的阶段跳过；返回各阶段行数统计。
        """
        import asyncio
        from datetime import timedelta

        now = now or utc_now()

        def _sync() -> Dict[str, int]:
            stats = {"compacted_turns": 0, "compacted_rows": 0, "retention_deleted": 0}
            aware_now = from_db_utc(now) or utc_now()
            if compaction_age_s > 0:
                cutoff = aware_now - timedelta(seconds=compaction_age_s)
                turn_ids = self._compactable_turns_sync(
                    older_than=cutoff, limit=COMPACTION_BATCH_TURNS)
                for turn_id in turn_ids:
                    removed = self._compact_turn_sync(turn_id)
                    if removed:
                        stats["compacted_turns"] += 1
                        stats["compacted_rows"] += removed
            if retention_age_s > 0:
                cutoff = aware_now - timedelta(seconds=retention_age_s)
                stats["retention_deleted"] = self._sweep_retention_sync(
                    older_than=cutoff, limit=retention_limit)
            return stats

        return await asyncio.to_thread(_sync)
