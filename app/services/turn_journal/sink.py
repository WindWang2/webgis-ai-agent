"""Turn journal sink：kernel 事件 seam → durable 账本的旁路写入（H04）。

纪律（ADR-0216）：
- **fail-open**：账本任何故障（DB 不可用/降级）绝不阻塞 chat 热路径——
  丢的是崩溃取证能力，不是正确性。降级以 ``hk_metrics`` 计数 +
  限频 warning 暴露；与 envelope 保存的 fail-closed 语义（session lock
  降级即拒绝）**刻意不同**：envelope 是权威，账本是投影。DB 故障时
  **整批放弃**（一次失败往返 = 清空队列）：不逐条烧连接超时。
- **有界**：队列 maxlen 默认 1024（env ``GIS_TURN_JOURNAL_QUEUE_MAX``），
  满即 drop 新事件 + 计数（不驱逐最老因果行、不无界内存）。append 经
  ``asyncio.to_thread``，不占事件循环。
- **不取锁**：sink 不碰 session lock、不读 envelope——与任何既有锁
  顺序零交集。
- **任务纪律**：排空任务持强引用（asyncio 对 task 只持弱引用，不持会
  被 GC 半途收走）并合并调度（burst 只排一个 drain task，review P2-1）。
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
from typing import Any, Optional, Set

from app.services.turn_journal.contracts import TurnEventRecord

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_MAX = 1024
#: 同原因限频日志窗口（秒）——DB 长时间不可用时防日志风暴。
_LOG_SUPPRESS_S = 60.0
#: 停机 flush 默认预算（秒）——"尽力落盘"必须有时限（review P2-4）。
DEFAULT_FLUSH_DEADLINE_S = 10.0


def journal_enabled() -> bool:
    return os.environ.get("GIS_TURN_JOURNAL", "on").strip().lower() != "off"


def _queue_max_from_env() -> int:
    try:
        value = int(os.environ.get("GIS_TURN_JOURNAL_QUEUE_MAX", "") or DEFAULT_QUEUE_MAX)
    except ValueError:
        return DEFAULT_QUEUE_MAX
    return max(1, value)


# 排空任务的强引用集（防 GC 半途收走；done 回调自清）。
_DRAIN_TASKS: Set["asyncio.Task"] = set()


class TurnJournalSink:
    """异步旁路写入器（每进程一个；``get_turn_journal_sink`` 单例）。"""

    def __init__(
        self,
        ledger: Optional[Any] = None,
        *,
        queue_max: Optional[int] = None,
    ) -> None:
        self._ledger = ledger  # lazy default：首次 flush 才 import DB 栈
        self._queue: deque = deque(maxlen=queue_max or _queue_max_from_env())
        self._drain_lock = asyncio.Lock()
        self._drain_scheduled = False
        self._scheduled_task: Optional["asyncio.Task"] = None
        #: flush 预算（monotonic 绝对时刻；0 = 无限制）。所有 drain 共享——
        #: 合并调度的遗留 task 与 flush 自己的 drain 都受它约束。
        self._stop_at = 0.0
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
        # record 时快照：envelope 的 detail dict 可能在排队期间被生产者
        # 原地改写（review P3-4）——投影必须是入账时刻的事实。
        self._queue.append(event.snapshot())
        self._schedule_drain()
        return True

    def _schedule_drain(self) -> None:
        """合并调度：已有 pending drain 则不再叠加 task（burst 只排一个）。"""
        if self._drain_scheduled:
            return
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._drain())
        except RuntimeError:
            return  # 无 loop / loop 关闭中——下次 record/flush 排空
        # 旗标在 task 创建成功后才置位（review 复核 P3-2）：closing loop
        # 上 create_task 抛错不能把调度旗标卡死。
        self._drain_scheduled = True
        self._scheduled_task = task
        _DRAIN_TASKS.add(task)

        def _done(t: "asyncio.Task") -> None:
            _DRAIN_TASKS.discard(t)
            if self._scheduled_task is t:
                self._scheduled_task = None

        task.add_done_callback(_done)

    async def _drain(self) -> None:
        async with self._drain_lock:
            self._drain_scheduled = False
            if self._ledger is None:
                from app.services.turn_journal.ledger import TurnEventLedger

                self._ledger = TurnEventLedger()
            while self._queue:
                # 逐条检查预算：单条 append 无法中断，但不会在其后再启动
                # 一条（flush 的"尽力"承诺由此成立，review P2-4）。
                stop = self._stop_at
                if stop and time.monotonic() >= stop:
                    return
                event = self._queue[0]
                try:
                    result = await self._ledger.append(event)
                except Exception:  # noqa: BLE001 — fail-open：账本故障不反噬
                    dropped = len(self._queue)
                    self._metric("journal_append_failed", kind=event.kind[:64])
                    # 逐事件计数（review 复核 P3-1）：count 不能做维度——
                    # 离散 drop 数会把 hk_metrics 的聚合打散成每值一键。
                    for _ in range(dropped):
                        self._metric("journal_sink_drop", kind=event.kind[:64])
                    self._log_suppressed(
                        "append_failed",
                        "[TurnJournal] append failed kind=%s turn=%s — "
                        "aborting batch, dropping %d events (fail-open)",
                        event.kind, event.turn_id, dropped,
                    )
                    self._queue.clear()  # 整批放弃：DB 挂时不逐条烧超时
                    return
                if result.status == "duplicate":
                    self._metric("journal_append_duplicate", kind=event.kind[:64])
                self._queue.popleft()

    @property
    def pending(self) -> int:
        return len(self._queue)

    async def flush(self, *, deadline_s: float = DEFAULT_FLUSH_DEADLINE_S) -> int:
        """测试/优雅停机用：限时排空，返回剩余（0 = 全部落账）。

        预算写到 ``_stop_at``——**所有** drain（含合并调度的遗留 task）
        逐条受检，预算耗尽不再启动下一条 append（in-flight 一条不可中
        断），停机不被慢 DB 拖进分钟级（review P2-4）。
        """
        deadline = time.monotonic() + max(0.0, float(deadline_s))
        self._stop_at = deadline
        try:
            # 接管已调度的 drain（避免它以无预算状态继续跑；它内部同样
            # 受 _stop_at 约束，await 有界）。
            task = self._scheduled_task
            if task is not None:
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — drain 自吞错；此处兜底
                    pass
            while self._queue and time.monotonic() < deadline:
                await self._drain()
        finally:
            self._stop_at = 0.0
        return len(self._queue)


class NullTurnJournalSink:
    """env 关闭时的空实现（零开销）。"""

    def record(self, event: TurnEventRecord) -> bool:  # noqa: ARG002
        return False

    async def flush(self, *, deadline_s: float = DEFAULT_FLUSH_DEADLINE_S) -> int:  # noqa: ARG002
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
