"""SpatialEventLedger — durable 事件账本 + 游标 + watch 存储（同步 SQLAlchemy）。

可靠性模型（DECISIONS D2–D4）：
- append-only 账本，自增 ``id`` = 因果序；``event_id`` 唯一 ⇒ 重复投递 ack
  duplicate、零副作用。
- worker 以条件更新（``status='pending'`` → 'processing'）抢批 = CAS；
  崩溃在 processing 中间 → 恢复清扫按 claimed_at 陈旧度复位 pending，
  at-least-once 重投 + 副作用幂等（fire 表 UQ / mission 幂等键）⇒ 恰好一次。
- 全部查询按 org_id 域过滤（租户隔离红线）；org 由受信上下文盖章。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select, text, update

from app.models.spatial_events import (
    SpatialEventCursorRow,
    SpatialEventRow,
    SpatialWatchFireRow,
    SpatialWatchRow,
)
from app.services.spatial_events import flags
from app.services.spatial_events.contracts import (
    SpatialEventEnvelope,
    SpatialWatch,
    WatchFireRecord,
    WatchState,
)

logger = logging.getLogger(__name__)

_PRIORITY_SQL = text(
    "CASE priority WHEN 'interactive' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END"
)


class LedgerError(RuntimeError):
    """事件账本拒绝操作（flag 关闭 / 租户盖章不符）。"""


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _to_aware_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _row_to_event(row: SpatialEventRow) -> Dict[str, Any]:
    return {
        "id": row.id,
        "event_id": row.event_id,
        "org_id": row.org_id,
        "kind": row.kind,
        "source": row.source,
        "subject_type": row.subject_type,
        "subject_key": row.subject_key,
        "session_id": row.session_id,
        "project_id": row.project_id,
        "occurred_at": _to_aware_iso(row.occurred_at),
        "ingested_at": _to_aware_iso(row.ingested_at),
        "priority": row.priority,
        "dedupe_key": row.dedupe_key,
        "correlation_id": row.correlation_id,
        "payload": dict(row.payload or {}),
        "payload_ref": row.payload_ref,
        "status": row.status,
        "attempts": row.attempts,
        "collapsed_count": row.collapsed_count,
        "claimed_by": row.claimed_by,
        "next_attempt_at": _to_aware_iso(row.next_attempt_at),
        "error_code": row.error_code,
        "processed_at": _to_aware_iso(row.processed_at),
    }


class AppendResult:
    __slots__ = ("event_id", "row_id", "status")

    def __init__(self, event_id: str, row_id: int, status: str) -> None:
        self.event_id = event_id
        self.row_id = row_id
        self.status = status  # appended | duplicate


class SpatialEventLedger:
    """durable ledger + cursor + watch store（每调用独立会话，仿 MissionStore）。"""

    def __init__(self, factory=None) -> None:
        if factory is None:
            from app.core.database import SessionLocal

            factory = SessionLocal
        self._factory = factory

    # ── append / read ────────────────────────────────────────────────

    def append(
        self,
        envelope: SpatialEventEnvelope,
        *,
        org_id: Optional[str] = None,
    ) -> AppendResult:
        """入账（幂等）。``org_id`` 为受信上下文印章；缺省信封自带 org。"""
        if not flags.runtime_enabled():
            raise LedgerError("SPATIAL_EVENT_RUNTIME_DISABLED")
        if org_id is not None and org_id != envelope.org_id:
            raise LedgerError(
                f"ORG_STAMP_MISMATCH: envelope={envelope.org_id} ctx={org_id}"
            )
        now = _utcnow_naive()
        with self._factory() as db:
            existing = db.execute(
                select(SpatialEventRow).where(
                    SpatialEventRow.event_id == envelope.event_id
                )
            ).scalar_one_or_none()
            if existing is not None:
                return AppendResult(existing.event_id, existing.id, "duplicate")
            row = SpatialEventRow(
                event_id=envelope.event_id,
                org_id=envelope.org_id,
                kind=envelope.kind.value,
                source=envelope.source,
                subject_type=envelope.subject_type.value,
                subject_key=envelope.subject_key,
                session_id=envelope.session_id,
                project_id=envelope.project_id,
                occurred_at=_to_naive(envelope.occurred_at),
                ingested_at=now,
                priority=envelope.priority.value,
                dedupe_key=envelope.dedupe_key,
                correlation_id=envelope.correlation_id,
                payload=dict(envelope.payload or {}),
                payload_ref=envelope.payload_ref,
                status="pending",
            )
            db.add(row)
            try:
                db.commit()
            except Exception:  # noqa: BLE001 — 唯一约束竞争 = 对方先落地
                db.rollback()
                winner = db.execute(
                    select(SpatialEventRow).where(
                        SpatialEventRow.event_id == envelope.event_id
                    )
                ).scalar_one_or_none()
                if winner is not None:
                    return AppendResult(winner.event_id, winner.id, "duplicate")
                raise
            return AppendResult(row.event_id, row.id, "appended")

    def get_event(
        self, row_id: int, *, org_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._factory() as db:
            row = db.get(SpatialEventRow, row_id)
            if row is None or row.org_id != org_id:
                return None
            return _row_to_event(row)

    def get_event_by_event_id(
        self, event_id: str, *, org_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        with self._factory() as db:
            stmt = select(SpatialEventRow).where(
                SpatialEventRow.event_id == event_id
            )
            if org_id is not None:
                stmt = stmt.where(SpatialEventRow.org_id == org_id)
            row = db.execute(stmt).scalar_one_or_none()
            return _row_to_event(row) if row is not None else None

    def list_events(
        self,
        *,
        org_id: str,
        after_id: int = 0,
        limit: int = 50,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        subject: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with self._factory() as db:
            stmt = (
                select(SpatialEventRow)
                .where(
                    SpatialEventRow.org_id == org_id,
                    SpatialEventRow.id > after_id,
                )
                .order_by(SpatialEventRow.id.asc())
                .limit(limit)
            )
            if status is not None:
                stmt = stmt.where(SpatialEventRow.status == status)
            if kind is not None:
                stmt = stmt.where(SpatialEventRow.kind == kind)
            if subject is not None:
                stmt = stmt.where(SpatialEventRow.subject_key == subject)
            return [_row_to_event(r) for r in db.execute(stmt).scalars()]

    def count_events(self, *, org_id: Optional[str] = None) -> int:
        with self._factory() as db:
            stmt = select(func.count(SpatialEventRow.id))
            if org_id is not None:
                stmt = stmt.where(SpatialEventRow.org_id == org_id)
            return int(db.execute(stmt).scalar() or 0)

    def stats(self, *, org_id: Optional[str] = None) -> Dict[str, int]:
        with self._factory() as db:
            stmt = select(
                SpatialEventRow.status, func.count(SpatialEventRow.id)
            ).group_by(SpatialEventRow.status)
            if org_id is not None:
                stmt = stmt.where(SpatialEventRow.org_id == org_id)
            out: Dict[str, int] = {}
            for status, cnt in db.execute(stmt):
                out[str(status)] = int(cnt)
            return out

    # ── worker 抢批 / 结算 ───────────────────────────────────────────

    def claim_batch(self, worker_id: str, *, limit: int) -> List[Dict[str, Any]]:
        """条件更新抢批：pending（退避已到）→ processing。priority 先、id 后。

        只把**本批要处理的**候选行推进 processing——多推不处理会让行卡死在
        processing 等恢复清扫（正确但迟钝）；这里直接一次到位。
        """
        limit = max(1, min(limit, 256))
        now = _utcnow_naive()
        with self._factory() as db:
            cand = list(
                db.execute(
                    select(SpatialEventRow.id)
                    .where(
                        SpatialEventRow.status == "pending",
                        (
                            SpatialEventRow.next_attempt_at.is_(None)
                            | (SpatialEventRow.next_attempt_at <= now)
                        ),
                    )
                    .order_by(_PRIORITY_SQL, SpatialEventRow.id.asc())
                    .limit(limit)
                )
                .scalars()
            )
            if not cand:
                return []
            db.execute(
                update(SpatialEventRow)
                .where(
                    SpatialEventRow.id.in_(cand),
                    SpatialEventRow.status == "pending",
                )
                .values(
                    status="processing",
                    claimed_by=worker_id,
                    claimed_at=now,
                    attempts=SpatialEventRow.attempts + 1,
                )
            )
            rows = (
                db.execute(
                    select(SpatialEventRow)
                    .where(
                        SpatialEventRow.id.in_(cand),
                        SpatialEventRow.claimed_by == worker_id,
                        SpatialEventRow.status == "processing",
                    )
                    .order_by(_PRIORITY_SQL, SpatialEventRow.id.asc())
                )
                .scalars()
                .all()
            )
            db.commit()
            return [_row_to_event(r) for r in rows]

    def mark_processed(self, row_id: int) -> bool:
        with self._factory() as db:
            res = db.execute(
                update(SpatialEventRow)
                .where(SpatialEventRow.id == row_id)
                .values(
                    status="processed",
                    processed_at=_utcnow_naive(),
                    claimed_by=None,
                    error_code=None,
                )
            )
            db.commit()
            return bool(res.rowcount)

    def mark_skipped(self, row_id: int, *, reason: str) -> bool:
        with self._factory() as db:
            res = db.execute(
                update(SpatialEventRow)
                .where(SpatialEventRow.id == row_id)
                .values(
                    status="skipped",
                    processed_at=_utcnow_naive(),
                    claimed_by=None,
                    error_code=reason[:128],
                )
            )
            db.commit()
            return bool(res.rowcount)

    def mark_failed(
        self, row_id: int, *, error_code: str, backoff_s: float = 30.0
    ) -> bool:
        """失败：未达上限回 pending（带退避）；达上限落终态 failed（不丢行）。"""
        with self._factory() as db:
            row = db.get(SpatialEventRow, row_id)
            if row is None or row.status not in ("processing", "pending"):
                return False
            terminal = row.attempts >= flags.max_attempts()
            next_at = (
                _utcnow_naive() + timedelta(seconds=max(0.0, backoff_s))
                if not terminal
                else None
            )
            db.execute(
                update(SpatialEventRow)
                .where(SpatialEventRow.id == row_id)
                .values(
                    status="failed" if terminal else "pending",
                    error_code=error_code[:128],
                    claimed_by=None,
                    next_attempt_at=next_at,
                )
            )
            db.commit()
            return True

    def requeue_stale_processing(self, *, older_than_s: float) -> int:
        """崩溃恢复：claimed_at 超过阈值的 processing 行复位 pending。"""
        cutoff = _utcnow_naive() - timedelta(seconds=max(0.0, older_than_s))
        with self._factory() as db:
            res = db.execute(
                update(SpatialEventRow)
                .where(
                    SpatialEventRow.status == "processing",
                    SpatialEventRow.claimed_at <= cutoff,
                )
                .values(
                    status="pending",
                    claimed_by=None,
                    next_attempt_at=None,
                )
            )
            db.commit()
            return int(res.rowcount or 0)

    # ── coalesce（burst 背压第一级）─────────────────────────────────

    def coalesce_pending(self, *, org_id: Optional[str] = None) -> Dict[str, int]:
        """同 (kind, subject_key) 的 pending 只留最新 occurred_at，其余 coalesced。

        只合并同租户同 subject 的可重投事实；processing/退避中行不动。
        """
        now = _utcnow_naive()
        with self._factory() as db:
            stmt = (
                select(SpatialEventRow)
                .where(
                    SpatialEventRow.status == "pending",
                    (
                        SpatialEventRow.next_attempt_at.is_(None)
                        | (SpatialEventRow.next_attempt_at <= now)
                    ),
                )
                .order_by(SpatialEventRow.id.asc())
            )
            if org_id is not None:
                stmt = stmt.where(SpatialEventRow.org_id == org_id)
            rows = db.execute(stmt).scalars().all()
        groups: Dict[Tuple[str, str, str], List[SpatialEventRow]] = {}
        for r in rows:
            groups.setdefault((r.org_id, r.kind, r.subject_key), []).append(r)
        coalesced = 0
        survivors = 0
        with self._factory() as db:
            for members in groups.values():
                if len(members) < 2:
                    continue
                ordered = sorted(
                    members,
                    key=lambda r: (r.occurred_at, r.id),
                )
                keeper = ordered[-1]
                losers = ordered[:-1]
                for loser in losers:
                    db.execute(
                        update(SpatialEventRow)
                        .where(
                            SpatialEventRow.id == loser.id,
                            SpatialEventRow.status == "pending",
                        )
                        .values(status="coalesced")
                    )
                db.execute(
                    update(SpatialEventRow)
                    .where(SpatialEventRow.id == keeper.id)
                    .values(
                        collapsed_count=(
                            keeper.collapsed_count + len(losers)
                        )
                    )
                )
                coalesced += len(losers)
                survivors += 1
            db.commit()
        return {"coalesced": coalesced, "groups": survivors}

    def requeue_event(self, row_id: int) -> bool:
        """重放入队：pending/failed/coalesced → pending（清退避）；终态不动。"""
        with self._factory() as db:
            res = db.execute(
                update(SpatialEventRow)
                .where(
                    SpatialEventRow.id == row_id,
                    SpatialEventRow.status.in_(("pending", "failed", "coalesced")),
                )
                .values(status="pending", next_attempt_at=None, claimed_by=None)
            )
            db.commit()
            return bool(res.rowcount)

    # ── durable cursor ───────────────────────────────────────────────

    def get_cursor(self, name: str) -> int:
        with self._factory() as db:
            row = db.get(SpatialEventCursorRow, name)
            return int(row.last_event_id) if row is not None else 0

    def advance_cursor(self, name: str, last_event_id: int) -> bool:
        """只前进。倒退/回绕拒收（重启恢复的红线）。"""
        with self._factory() as db:
            row = db.get(SpatialEventCursorRow, name)
            if row is None:
                db.add(
                    SpatialEventCursorRow(
                        name=name,
                        last_event_id=int(last_event_id),
                        updated_at=_utcnow_naive(),
                    )
                )
                db.commit()
                return True
            if int(last_event_id) < int(row.last_event_id):
                db.rollback()
                return False
            row.last_event_id = int(last_event_id)
            row.updated_at = _utcnow_naive()
            db.commit()
            return True

    # ── watch store ──────────────────────────────────────────────────

    def upsert_watch(self, watch: SpatialWatch) -> None:
        now = _utcnow_naive()
        with self._factory() as db:
            row = db.get(SpatialWatchRow, watch.watch_id)
            if row is None:
                row = SpatialWatchRow(watch_id=watch.watch_id, created_at=now)
                db.add(row)
            row.org_id = watch.org_id
            row.name = watch.name
            row.enabled = 1 if watch.enabled else 0
            row.kinds = list(watch.kinds)
            row.subject_key_prefix = watch.subject_key_prefix
            row.session_id = watch.session_id
            row.project_id = watch.project_id
            row.condition = watch.condition.model_dump()
            row.actions = list(watch.actions)
            row.cooldown_s = float(watch.cooldown_s)
            row.mission_goal_template = watch.mission_goal_template
            row.mission_project_id = watch.mission_project_id
            row.updated_at = now
            db.commit()

    def _row_to_watch(self, row: SpatialWatchRow) -> SpatialWatch:
        from app.services.spatial_events.contracts import WatchCondition

        return SpatialWatch(
            watch_id=row.watch_id,
            org_id=row.org_id,
            name=row.name,
            enabled=bool(row.enabled),
            kinds=list(row.kinds or []),
            subject_key_prefix=row.subject_key_prefix,
            session_id=row.session_id,
            project_id=row.project_id,
            condition=WatchCondition(**(row.condition or {})),
            actions=list(row.actions or []),
            # 0.0 是合法 cooldown（falsy 陷阱：不能用 `or 60.0`）
            cooldown_s=float(row.cooldown_s) if row.cooldown_s is not None else 60.0,
            mission_goal_template=row.mission_goal_template,
            mission_project_id=row.mission_project_id,
        )

    def get_watch(
        self, watch_id: str, *, org_id: str
    ) -> Optional[SpatialWatch]:
        with self._factory() as db:
            row = db.get(SpatialWatchRow, watch_id)
            if row is None or row.org_id != org_id:
                return None
            return self._row_to_watch(row)

    def list_watches(
        self, *, org_id: str, enabled_only: bool = False
    ) -> List[str]:
        with self._factory() as db:
            stmt = select(SpatialWatchRow.watch_id).where(
                SpatialWatchRow.org_id == org_id
            )
            if enabled_only:
                stmt = stmt.where(SpatialWatchRow.enabled == 1)
            return sorted(db.execute(stmt).scalars())

    def delete_watch(self, watch_id: str, *, org_id: str) -> bool:
        with self._factory() as db:
            row = db.get(SpatialWatchRow, watch_id)
            if row is None or row.org_id != org_id:
                db.rollback()
                return False
            db.delete(row)
            db.commit()
            return True

    def get_watch_state(
        self, watch_id: str, *, org_id: str
    ) -> Optional[WatchState]:
        with self._factory() as db:
            row = db.get(SpatialWatchRow, watch_id)
            if row is None or row.org_id != org_id:
                return None
            return WatchState(**(row.state or {}))

    def update_watch_state(
        self, watch_id: str, state: WatchState, *, org_id: str
    ) -> bool:
        with self._factory() as db:
            row = db.get(SpatialWatchRow, watch_id)
            if row is None or row.org_id != org_id:
                db.rollback()
                return False
            row.state = state.model_dump(mode="json")
            row.updated_at = _utcnow_naive()
            db.commit()
            return True

    # ── fire 账本 ────────────────────────────────────────────────────

    def record_fire(
        self, record: WatchFireRecord
    ) -> Tuple[WatchFireRecord, bool]:
        """记录触发（(watch_id,event_id) UQ ⇒ 重复触发 ack duplicate）。"""
        with self._factory() as db:
            existing = db.execute(
                select(SpatialWatchFireRow).where(
                    SpatialWatchFireRow.watch_id == record.watch_id,
                    SpatialWatchFireRow.event_id == record.event_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return (record, False)
            db.add(
                SpatialWatchFireRow(
                    watch_id=record.watch_id,
                    org_id=record.org_id,
                    event_id=record.event_id,
                    action=record.action,
                    outcome=record.outcome,
                    fired_at=_to_naive(record.fired_at),
                    detail=dict(record.detail or {}),
                )
            )
            try:
                db.commit()
            except Exception:  # noqa: BLE001 — 并发竞争 = 对方先记
                db.rollback()
                return (record, False)
            return (record, True)

    def list_fires(
        self,
        *,
        org_id: str,
        watch_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with self._factory() as db:
            stmt = (
                select(SpatialWatchFireRow)
                .where(SpatialWatchFireRow.org_id == org_id)
                .order_by(SpatialWatchFireRow.id.desc())
                .limit(limit)
            )
            if watch_id is not None:
                stmt = stmt.where(SpatialWatchFireRow.watch_id == watch_id)
            out = []
            for r in db.execute(stmt).scalars():
                out.append(
                    {
                        "id": r.id,
                        "watch_id": r.watch_id,
                        "org_id": r.org_id,
                        "event_id": r.event_id,
                        "action": r.action,
                        "outcome": r.outcome,
                        "fired_at": _to_aware_iso(r.fired_at),
                        "detail": dict(r.detail or {}),
                    }
                )
            return out

    def get_fire(self, watch_id: str, event_id: str) -> Optional[Dict[str, Any]]:
        with self._factory() as db:
            row = db.execute(
                select(SpatialWatchFireRow).where(
                    SpatialWatchFireRow.watch_id == watch_id,
                    SpatialWatchFireRow.event_id == event_id,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return {
                "id": row.id,
                "watch_id": row.watch_id,
                "org_id": row.org_id,
                "event_id": row.event_id,
                "action": row.action,
                "outcome": row.outcome,
                "fired_at": _to_aware_iso(row.fired_at),
                "detail": dict(row.detail or {}),
            }

    #: 触发结果更新允许的 outcome 词表（防伪造任意串）
    _FIRE_OUTCOMES = frozenset(
        {
            "pending", "notified", "suppressed", "duplicate", "rejected",
            "deferred", "mission_created", "mission_revised", "mission_resumed",
        }
    )

    def update_fire_outcome(
        self,
        watch_id: str,
        event_id: str,
        outcome: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if outcome not in self._FIRE_OUTCOMES:
            return False
        with self._factory() as db:
            row = db.execute(
                select(SpatialWatchFireRow).where(
                    SpatialWatchFireRow.watch_id == watch_id,
                    SpatialWatchFireRow.event_id == event_id,
                )
            ).scalar_one_or_none()
            if row is None:
                db.rollback()
                return False
            row.outcome = outcome
            if detail:
                merged = dict(row.detail or {})
                merged.update(
                    {
                        k: (v if isinstance(v, (int, float, bool)) else str(v)[:160])
                        for k, v in list(detail.items())[:8]
                    }
                )
                row.detail = merged
            db.commit()
            return True

    # ── cursor 水位 ──────────────────────────────────────────────────

    def cursor_high_watermark(self) -> int:
        """可安全推进到的最大 id：min(非终态) - 1；全终态则 max(id)。

        语义：游标以下的每个 id 都已到达终态（processed/failed/coalesced/
        skipped）——重启恢复据此对账（正在处理的行自然把水位压在它下面）。
        """
        with self._factory() as db:
            min_open = db.execute(
                select(func.min(SpatialEventRow.id)).where(
                    SpatialEventRow.status.in_(("pending", "processing"))
                )
            ).scalar()
            if min_open is not None:
                return max(0, int(min_open) - 1)
            max_id = db.execute(
                select(func.max(SpatialEventRow.id))
            ).scalar()
            return int(max_id or 0)
