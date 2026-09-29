"""Harness Turn Journal（H04 / ADR-0216）：会话级 durable turn 事件账本。

authority 声明：envelope（SessionPlan / harness_kernel）仍是 live 权威；
本包只是其 append-only 事实投影（崩溃取证 + 因果查询 + retention），
绝不驱动 lifecycle 决策。公开面：

- ``TurnEventRecord`` / ``sanitize_detail``：账本行契约 + 载荷纪律；
- ``TurnEventLedger``：幂等 append / 有界读 / compaction + retention；
- ``get_turn_journal_sink``：kernel 事件 seam 的旁路写入器（fail-open）；
- ``build_recovery_plan_sync`` / ``session_journal_report_sync``：只读
  恢复分类与诊断报告。
"""
from app.services.turn_journal.contracts import (
    MAX_DETAIL_BYTES,
    TurnEventRecord,
    sanitize_detail,
)
from app.services.turn_journal.diagnostics import (
    build_causal_tree_sync,
    session_journal_report_sync,
)
from app.services.turn_journal.ledger import (
    MAX_JOURNAL_QUERY,
    TERMINAL_TURN_KIND,
    AppendResult,
    TurnEventLedger,
)
from app.services.turn_journal.resume import (
    CLASS_NEEDS_REPLAN,
    CLASS_RECEIPT_CHECK,
    CLASS_RECEIPT_PRESENT,
    CLASS_SAFE_REPLAY,
    build_recovery_plan_sync,
    classify_step,
)
from app.services.turn_journal.sink import (
    NullTurnJournalSink,
    TurnJournalSink,
    get_turn_journal_sink,
    journal_enabled,
    reset_turn_journal_sink,
)

__all__ = [
    "MAX_DETAIL_BYTES",
    "MAX_JOURNAL_QUERY",
    "TERMINAL_TURN_KIND",
    "AppendResult",
    "CLASS_NEEDS_REPLAN",
    "CLASS_RECEIPT_CHECK",
    "CLASS_RECEIPT_PRESENT",
    "CLASS_SAFE_REPLAY",
    "NullTurnJournalSink",
    "TurnEventLedger",
    "TurnEventRecord",
    "TurnJournalSink",
    "build_causal_tree_sync",
    "build_recovery_plan_sync",
    "classify_step",
    "get_turn_journal_sink",
    "journal_enabled",
    "reset_turn_journal_sink",
    "sanitize_detail",
    "session_journal_report_sync",
]
