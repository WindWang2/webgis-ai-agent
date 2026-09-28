"""Turn journal sink：kernel 事件 seam → durable 账本的旁路写入（H04）。

纪律（ADR-0216）：
- **fail-open**：账本任何故障（DB 不可用/降级）绝不阻塞 chat 热路径——
  丢的是崩溃取证能力，不是正确性。降级以 ``hk_metrics`` 计数 +
  限频 warning 暴露；与 envelope 保存的 fail-closed 语义（session lock
  降级即拒绝）**刻意不同**：envelope 是权威，账本是投影。
- **有界**：队列 maxlen 默认 1024，满即 drop + 计数（不排队积压、
  不无界内存）。append 经 ``asyncio.to_thread``，不占事件循环。
- **不取锁**：sink 不碰 session lock、不读 envelope——与任何既有锁
  顺序零交集。
- env 门控：``GIS_TURN_JOURNAL=off`` 时换 NullSink（默认 on）。

幂等由 ledger 层保证（event_id UNIQUE）；sink 层重复 record 同一
event_id 是廉价 no-op（duplicate），无需本地去重状态。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from typing import Optional

from app.services.turn_journal.contracts import TurnEventRecord

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_MAX = 1024
#: 同原因限频日志窗口（秒）——DB 长时间不可用时防日志风暴。
_LOG_SUPPRESS_S = 60.0


def journal_enabled() -> bool:
    return os.environ.get("GIS_TURN_JOURNAL", "on").strip().lower() != "off"


class TurnJournalSink:
    """异步旁路写入器（每进程一个；``get_turn_journal_sink`` 单例）。"""

    def __init__(
        self,
        ledger: Optional[Any] = None,
        *,
        queue_max: Optional[int] = None,
    ) -> None:
        self._ledger = ledger  # lazy default：首次 flush 才 import DB 栈
        self._queue: deque = deque(maxlen=queue_max or DEFAULT_QUEUE_MAX)
        self._drain_lock = asyncio.Lock()
        self._last_log_at: dict = {}

    def _metric(self, name: str, **kw: object) -> None:
        try:
            from app.services.harness_kernel import hk_metrics

            hk_metrics.record(name, **kw)
        except Exception:  # noqa: BLE001 — 计数器绝不反噬
            pass

    def _log_suppressed(self, reason: str, msg: str, *args: object) -> None:
        now = time.monotonic()
        if now - self._last_log_at.get(reason, 0.0) < _LOG_SUPPRESS_S:
            return
        self._last_log_at[reason] = now
        logger.warning(msg, *args)

    def record(self, event: TurnEventRecord) -> bool:
        """入队（同步、零阻塞）。满 → drop + 计数，返回 False。"""
        if not journal_enabled():
            return False
        # 脏行（缺 session/kind）无论有无 event_id 一律拒绝——落库即
        # NOT NULL 违例，不如入口拒绝。
        if not event.session_id or not event.kind:
            return False
        if len(self._queue) >= self._queue.maxlen:  # type: ignore[arg-type]
            self._metric("journal_sink_drop", kind=event.kind[:64])
            return False
        self._queue.append(event)
        # 排空是幂等的：并发/重入由锁串行化，锁内循环到队列为空。
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return True  # 无 loop（同步测试/启动期）——下次 record 排空
        loop.create_task(self._drain())
        return True

    async def _drain(self) -> None:
        async with self._drain_lock:
            if self._ledger is None:
                from app.services.turn_journal.ledger import TurnEventLedger

                self._ledger = TurnEventLedger()
            while self._queue:
                event = self._queue[0]
                try:
                    result = await self._ledger.append(event)
                except Exception:  # noqa: BLE001 — fail-open：账本故障不反噬
                    self._metric("journal_append_failed", kind=event.kind[:64])
                    self._log_suppressed(
                        "append_failed",
                        "[TurnJournal] append failed kind=%s turn=%s — "
                        "dropping event (fail-open)",
                        event.kind, event.turn_id,
                    )
                    self._queue.popleft()
                    continue
                if result.status == "duplicate":
                    self._metric("journal_append_duplicate", kind=event.kind[:64])
                self._queue.popleft()

    @property
    def pending(self) -> int:
        return len(self._queue)

    async def flush(self) -> int:
        """测试/优雅停机用：排空队列，返回剩余（0 = 全部落账）。"""
        await self._drain()
        return len(self._queue)


class NullTurnJournalSink:
    """env 关闭时的空实现（零开销）。"""

    def record(self, event: TurnEventRecord) -> bool:  # noqa: ARG002
        return False

    async def flush(self) -> int:
        return 0

    @property
    def pending(self) -> int:
        return 0


_sink: Optional[TurnJournalSink] = None


def get_turn_journal_sink() -> TurnJournalSink | NullTurnJournalSink:
    """进程级单例（env ``GIS_TURN_JOURNAL=off`` → NullSink）。"""
    global _sink
    if _sink is None:
        if journal_enabled():
            _sink = TurnJournalSink()
        else:
            return NullTurnJournalSink()
    return _sink


def reset_turn_journal_sink() -> None:
    """测试钩子：重置单例（env 变更后生效）。"""
    global _sink
    _sink = None
