"""Harness Turn Journal ORM models（H04 会话级 durable turn 事件账本）。

表：
- ``turn_events``：append-only 事实账本。自增 ``id`` = 因果序；``event_id``
  唯一 ⇒ append 判重（canonical 行的幂等键与 envelope 完全一致：
  ``kind:turn_id:causal_id``）。**这不是状态机，也不是消息队列**——
  envelope（SessionPlan）仍是 live 权威；本表只承载崩溃后取证（resume
  分类）、因果查询与 retention 投影，绝不驱动 phase 转移。

载荷纪律（与 spatial_events 同款）：``detail`` 为有界 JSON（≤2KB
canonical，写入侧截断并计数）；大内容只存调用方显式给出的 ``payload_ref``
——journal 永不放大 payload（GeoJSON/截图本体不入账）。

时间纪律：列口径 naive-UTC（全库既定 DB 边界）；读输出经
``app.lib.runtime.clock.from_db_utc`` 抬回 aware（历史 naive 行按 UTC 兼容）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TurnEventRow(Base):
    """turn 事件事实行（append-only；存储态见 status，非 lifecycle）。"""

    __tablename__ = "turn_events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"),
                primary_key=True, autoincrement=True)
    #: 幂等键：canonical 行 = envelope 的 ``kind:turn_id:causal_id``；
    #: legacy _journal 行 = ``legacy:{kind}:{turn_id}:{seq}``（无稳定幂等
    #: 语义，at-least-once —— 已知边界，见 ADR-0216）。
    event_id = Column(String(160), nullable=False)
    session_id = Column(String(64), nullable=False)
    turn_id = Column(String(80), nullable=False, default="")
    run_id = Column(String(64), nullable=False, default="")
    step_id = Column(String(80), nullable=False, default="")
    kind = Column(String(64), nullable=False)
    host = Column(String(32), nullable=False, default="unknown")
    #: envelope-monotonic 序号（legacy 行 0）。
    seq = Column(Integer, nullable=False, default=0)
    #: 产出身份（tool_call_id / mutation_id / trigger id），与 envelope 行一致。
    causal_id = Column(String(160), nullable=False, default="")
    note = Column(String(300), nullable=False, default="")
    detail = Column(JSON, nullable=False, default=dict)
    payload_ref = Column(String(160), nullable=True)
    #: map_mutated 行携带（诊断"最后一致 revision"）。
    mutation_revision = Column(Integer, nullable=True)
    occurred_at = Column(DateTime, nullable=False)
    ingested_at = Column(DateTime, default=_utcnow, nullable=False)
    #: recorded → compacted（压缩摘要行）。存储生命周期，非业务状态机。
    status = Column(String(16), nullable=False, default="recorded")

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_turn_event_id"),
        CheckConstraint(
            "status IN ('recorded','compacted')",
            name="ck_turn_event_status",
        ),
        Index("idx_turn_event_session", "session_id", "id"),
        Index("idx_turn_event_turn", "turn_id", "id"),
        Index("idx_turn_event_sweep", "status", "occurred_at"),
        Index("idx_turn_event_kind", "session_id", "kind", "id"),
    )
