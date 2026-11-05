"""一次 legacy 流式 turn 的跨阶段显式状态（H03 阶段化拆分）。

独立模块化（S3/Wave G 债务棘轮）：``execution_engine`` 已是 god-module
（#1544），阶段状态契约定驻于此，引擎经组合消费 —— 字段分组即阶段
输入输出契约（详见类 docstring）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable, Iterator, Optional

from app.lib.cancellation import CancellationToken
from app.services.chat.sse_contracts import TurnEventEmitter

if TYPE_CHECKING:
    # 仅类型注解需要（避免与 execution_engine 的导入环；运行时经 engine
    # 参数组合注入，无顶层依赖）。
    from app.services.chat.execution_engine import ChatExecutionEngine

logger = logging.getLogger(__name__)

class StreamTurnContext:
    """一次 legacy 流式 turn 的跨阶段显式状态（H03 阶段化拆分）。

    ``chat_stream`` 曾是 964 行单生成器：阶段间变量靠闭包捕获传递，
    断连路径甚至用 ``locals().get("_wave_flushed", True)`` 探测**未定义
    变量**。拆分后所有 ``_stream_*`` 阶段方法读写这一显式状态对象 ——
    下方字段分组即阶段输入输出契约：

    - admission/请求入参：session_id / message / user_id / project_id /
      skill_name / map_state
    - context setup 产出：messages（锁下加载的会话历史，RUN-13）
    - turn 身份（_stream_bind_turn_identity）：task / turn_id / run_id /
      rt_ev / rt_cm / tev_cm / emitter / owner_token / hk_lock_token
    - planner（_stream_planner_phase）：plan / plan_existed_this_turn
    - 主循环（_stream_rounds/_stream_single_round）：turn_start_user_message
      / turn_id_for_catalog / turn_deadline / no_progress_streak /
      executed_tools / turn_finished
    - 工具波（_stream_tool_wave，断连路径 F9-parity 消费）：wave_flushed /
      pending_tools / completion_results / standard_calls / tool_result_msgs

    ``turn_finished`` 是阶段向编排层回传「本 turn 已发过终态、立即收尾」
    的唯一信号（对应拆分前各分支的裸 ``return``）。
    ``plan_finalized_event`` / ``aborted_step_events`` 是拆分前两个嵌套
    闭包的方法化。
    """

    def __init__(
        self,
        *,
        engine: "ChatExecutionEngine",
        session_id: str,
        message: str,
        user_id: Optional[str] = None,
        project_id: Optional[str] = None,
        skill_name: Optional[str] = None,
        map_state: Optional[dict] = None,
        engine_lock: Optional[Any] = None,
    ) -> None:
        # 请求入参（不可变段）
        self.engine = engine
        self.session_id = session_id
        self.message = message
        self.user_id = user_id
        self.project_id = project_id
        self.skill_name = skill_name
        self.map_state = map_state
        # RUN-03 全 turn 持锁的锁对象（ADR-0180 legacy_bind_engine_lock 需要）
        self.lock = engine_lock
        # context setup 产出（RUN-13：锁下加载；默认空表保证 trim 全态）
        self.messages: list = []
        # turn 身份
        self.task: Any = None
        self.turn_id: Optional[str] = None
        self.run_id: Optional[str] = None
        self.rt_ev: Any = None
        self.rt_cm: Any = None
        self.tev_cm: Any = None
        self.emitter: Optional[TurnEventEmitter] = None
        self.owner_token: Optional[str] = None
        self.hk_lock_token: Any = None
        # planner
        self.plan: Any = None
        self.plan_existed_this_turn: bool = False
        # 主循环
        self.turn_start_user_message: str = ""
        self.turn_id_for_catalog: Optional[str] = None
        self.turn_deadline: float = 0.0
        self.no_progress_streak: int = 0
        self.executed_tools: set = set()
        # 工具波（断连路径按此重建 F9-parity 落库）
        self.wave_flushed: bool = True
        self.pending_tools: list = []
        self.completion_results: dict = {}
        self.standard_calls: list = []
        self.tool_result_msgs: list = []
        # 编排信号
        self.turn_finished: bool = False

    def plan_finalized_event(self) -> Optional[str]:
        """design-v3：只有本轮确实存在/产生过非终态计划才发 plan_finalized，
        避免无计划轮次或已恢复出终态计划时发出 spurious finalize。"""
        if not self.plan_existed_this_turn:
            return None
        try:
            from app.services.chat import planner as _planner
            plan_obj = _planner.get_plan(self.session_id)
            if plan_obj is None:
                return None
            skipped = [s.n for s in plan_obj.steps if not s.done]
            return self.emitter.plan_finalized(self.task.id, skipped)
        except Exception as e:
            logger.warning(f"[chat_execution_engine] plan_finalized 构造失败: {e}")
            return None

    def cancelled_events(
        self, settle: Callable[[], None], *, include_done: bool = False,
    ) -> Iterator[str]:
        """Yield the shared cancellation tail without changing its pause points.

        Settlement follows the optional plan event; turn_finished is only set
        once the caller resumes past the final terminal event. Tool-wave and
        token-stream callers retain their existing done-event difference.
        """
        finalized = self.plan_finalized_event()
        if finalized:
            yield finalized
        settle()  # cooperative cancel ≠ success
        yield self.emitter.task_cancelled(self.task.id)
        if include_done:
            yield self.emitter.done()
        self.turn_finished = True

    def aborted_step_events(self) -> list:
        """F7/F8: 取消/断连路径上把未完成的 step 记 cancelled
        （绝不留 running），并生成对应的 step_cancelled SSE 事件。"""
        evts: list = []
        for p in self.pending_tools:
            if p["step"].id in self.completion_results:
                continue
            try:
                self.engine.tracker.cancel_step(self.task.id, p["step"].id)
            except Exception:
                continue
            evts.append(
                self.emitter.step_cancelled(self.task.id, p["step"].id, p["tool_name"])
            )
        return evts


# ── #409/#554 流式管道原语（S3/Wave G 债务棘轮自 execution_engine 拆驻）──────
# 以下两个原语是 chat_stream 的流式管道 plumbing，与本模块的阶段状态契约
# 同域，落驻于此以缓解 execution_engine 的 god-module 棘轮（#1544）。

#: #409：provider 流静默多久后向 SSE 侧发一次 keep_alive（泵任务不取消
#: provider 流，仅对队列等待计时）。
_LLM_TOKEN_KEEPALIVE_S = 15.0
_KEEPALIVE_EVENT: tuple[str, dict] = ("keep_alive", {"message": "ping"})
_QUEUE_END = object()  # pump-completed sentinel


class _AcquiredLock:
    """Adapter presenting an already-acquired asyncio.Lock to ``async with``.

    Used by :meth:`ChatEngine.chat_stream` (#554 defect 1, legacy sibling):
    the per-session turn lock is acquired by a keepalive polling loop so bytes
    flow on the wire while a same-session concurrent second request waits for
    the previous turn to release it. The adapter then presents the lock to the
    existing ``async with lock:`` block unchanged, preserving its
    release-on-any-exit semantics (including ``GeneratorExit`` on client
    disconnect) without re-indenting the ~650-line turn body.
    """

    def __init__(self, lock: Any) -> None:
        self._lock = lock
        self._released = False

    async def __aenter__(self) -> "_AcquiredLock":
        return self

    def release(self) -> None:
        if not self._released:
            self._released = True
            if hasattr(self._lock, "locked"):
                if self._lock.locked():
                    self._lock.release()
            elif hasattr(self._lock, "release"):
                self._lock.release()

    async def __aexit__(self, exc_type: object = None, exc_val: object = None, exc_tb: object = None) -> None:
        if not self._released:
            self._released = True
            if hasattr(self._lock, "__aexit__"):
                await self._lock.__aexit__(exc_type, exc_val, exc_tb)
            elif hasattr(self._lock, "release"):
                try:
                    self._lock.release()
                except RuntimeError:
                    # 双重释放防护（#1218 A-7 语义）：锁已释放时静默幂等。
                    logger.debug("[stream_turn_context] lock already released")


async def _stream_with_token_keepalive(
    stream: AsyncGenerator[tuple[str, dict], None],
    timeout_s: float = _LLM_TOKEN_KEEPALIVE_S,
    cancel_token: CancellationToken | None = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Adapt an LLM provider event stream: when no item arrives within
    ``timeout_s`` seconds, a keep_alive tuple is yielded instead (the caller
    forwards it as an SSE ``keep_alive`` event) and the wait resumes. Provider
    items are delivered in order, unchanged; provider exceptions are re-raised
    in the consumer. The pump task owns the provider generator, so a timeout
    never cancels the underlying HTTP read; the pump is cancelled in the
    ``finally`` when the consumer exits early (disconnect). An explicit task
    cancellation interrupts the provider immediately, including before its
    first token, and takes precedence over an already-queued late completion.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def _pump() -> None:
        try:
            async for item in stream:
                queue.put_nowait(item)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 — re-raised in the consumer
            queue.put_nowait(exc)
        else:
            queue.put_nowait(_QUEUE_END)

    if cancel_token is not None:
        cancel_token.raise_if_cancelled()
    pump = asyncio.create_task(_pump())
    cancel_watch = asyncio.create_task(cancel_token.wait()) if cancel_token is not None else None
    next_item: asyncio.Task | None = None
    try:
        while True:
            next_item = asyncio.create_task(queue.get())
            waiters = {next_item}
            if cancel_watch is not None:
                waiters.add(cancel_watch)
            done, _ = await asyncio.wait(waiters, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
            if cancel_token is not None:
                # Cancellation wins if a provider token/completion and the
                # cancellation signal become ready in the same loop turn.
                cancel_token.raise_if_cancelled()
            if next_item not in done:
                next_item.cancel()
                await asyncio.gather(next_item, return_exceptions=True)
                if pump.done() and queue.empty():
                    return
                yield _KEEPALIVE_EVENT
                continue
            item = next_item.result()
            if item is _QUEUE_END:
                return
            if isinstance(item, BaseException):
                raise item  # type: ignore[misc]  # noqa: B904
            yield item
    finally:
        pending = [task for task in (pump, next_item, cancel_watch) if task is not None]
        for task in pending:
            if not task.done():
                task.cancel()
        # Reap both the provider read and cancellation/queue waiters before
        # the caller emits terminal cancellation or releases the session lock.
        await asyncio.gather(*pending, return_exceptions=True)
