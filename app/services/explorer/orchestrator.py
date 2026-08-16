"""探索任务编排器"""
import logging
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import AsyncGenerator, Optional, Any
from celery import chain
from app.services.explorer.models import SearchContext, ExplorerPerceptionEvent
from app.services.explorer.intent_detector import IntentDetector, ExploreDecision
from app.services.task_queue import TaskQueueService
from app.utils.sse import sse_event

logger = logging.getLogger(__name__)

# ─── Issue #481: whole-chain run tracking ──────────────────────────────────
#
# Celery chain semantics (verified against Celery 5.6.3):
# ``chain(a, b, c).apply_async()`` returns the LAST task's result — the
# durable whole-chain handle (SUCCESS only when the whole chain finished) —
# and ``result.parent`` walks backward toward the FIRST task. The previous
# code inverted this: it walked to the root (the discover stage, done in
# seconds) and polled/revoked that id, so the SSE stream faked "completed"
# while stages 2-5 ran dark, late-stage failures never surfaced (downstream
# ids stay PENDING forever when an upstream stage fails), and abort was a
# no-op once discover succeeded.
#
# The orchestrator therefore registers, per exploration, every stage task id
# ordered [first..last] under the final id it hands to the client. Status is
# aggregated across all stage ids (any stage failure ⇒ run failed; all
# SUCCESS ⇒ run completed; otherwise PROGRESS on the first not-yet-SUCCESS
# stage), and abort revokes every stage id.

EXPLORER_STAGES = ("discover", "fetch", "parse", "geocode", "validate")

# Bound the registry with the same LRU discipline as TaskQueueService's
# owner map (F29): entries are only useful while a run is live/queried.
_CHAIN_RUNS_MAX_ENTRIES = 20_000


@dataclass
class ExplorerChainRun:
    """All Celery stage task ids of one exploration, ordered first→last.

    ``stage_ids[-1]`` is the whole-chain handle returned to the client; the
    ids before it are the predecessors walked via ``AsyncResult.parent``.
    """

    stage_ids: list[str]


_chain_runs: "OrderedDict[str, ExplorerChainRun]" = OrderedDict()


def register_chain_run(final_task_id: str, stage_ids: list[str]) -> ExplorerChainRun:
    """Record the stage task ids of a submitted exploration under its final
    (whole-chain) id, so status/abort/stream can act on the whole chain."""
    run = ExplorerChainRun(stage_ids=list(stage_ids))
    _chain_runs[final_task_id] = run
    _chain_runs.move_to_end(final_task_id)
    while len(_chain_runs) > _CHAIN_RUNS_MAX_ENTRIES:
        _chain_runs.popitem(last=False)
    return run


def get_chain_run(final_task_id: str) -> Optional[ExplorerChainRun]:
    """Look up the chain-run record for a whole-chain (final) task id."""
    run = _chain_runs.get(final_task_id)
    if run is not None:
        # Refresh recency so a live run is never LRU-evicted mid-flight.
        _chain_runs.move_to_end(final_task_id)
    return run


# ─── Issue #518: session → active explorer tasks (post-turn chat-stream bridge) ─
#
# Anonymous sessions (no owner registration, S42) can never reach the
# owner-verified /explorer/stream/{task_id} endpoint. Their only progress
# channel is the chat SSE stream, which is session-isolated by construction —
# the post-turn bridge (bridge_session_explorer_progress) streams these
# owner-less tasks there. The registry is keyed by session_id and LRU-bounded
# like _chain_runs; entries are pruned once the bridge sees them terminal.
_session_tasks: "OrderedDict[str, list[tuple[str, str]]]" = OrderedDict()
_SESSION_TASKS_MAX_ENTRIES = 20_000

# Bounded wall-clock cap for the post-turn bridge: never hold the chat SSE
# open forever for a stuck task. On expiry the bridge emits an explicit
# failed terminal event for still-unfinished tasks (no silent hangs).
_EXPLORER_BRIDGE_MAX_SECONDS = 600


def register_session_task(session_id: str, task_id: str, user_id: str) -> None:
    """Record an explorer task under its session for the post-turn bridge."""
    if not session_id or not task_id:
        return
    entry = _session_tasks.get(session_id)
    if entry is None:
        _session_tasks[session_id] = []
    if not any(tid == task_id for tid, _ in _session_tasks[session_id]):
        _session_tasks[session_id].append((task_id, user_id))
    _session_tasks.move_to_end(session_id)
    while len(_session_tasks) > _SESSION_TASKS_MAX_ENTRIES:
        _session_tasks.popitem(last=False)


def get_session_tasks(session_id: str) -> list[tuple[str, str]]:
    """Active (task_id, user_id) pairs registered under a session."""
    if not session_id:
        return []
    entry = _session_tasks.get(session_id)
    if entry:
        _session_tasks.move_to_end(session_id)
    return list(entry or [])


def remove_session_task(session_id: str, task_id: str) -> None:
    """Drop a task from the session registry (called once it reaches terminal)."""
    entry = _session_tasks.get(session_id)
    if entry is None:
        return
    remaining = [(t, u) for t, u in entry if t != task_id]
    if remaining:
        _session_tasks[session_id] = remaining
    else:
        del _session_tasks[session_id]


def collect_stage_ids(result: Any) -> list[str]:
    """Collect every stage task id of a chain result, ordered first→last.

    ``result`` is what ``chain(...).apply_async()`` returned: the LAST task's
    AsyncResult/EagerResult, whose ``.parent`` chain walks back to the first
    task. Verified against Celery 5.6.3 (both real and eager application).
    """
    ids: list[str] = []
    node: Any = result
    while node is not None:
        ids.append(node.id)
        node = getattr(node, "parent", None)
    ids.reverse()
    return ids


class ExplorerOrchestrator:
    """探索任务编排器"""

    def __init__(self):
        self.intent_detector = IntentDetector()
        self.task_queue = TaskQueueService()

    async def evaluate_intent(
        self,
        query: str,
        current_layers: list[dict],
        session_history: list[dict],
    ) -> ExploreDecision:
        """评估是否需要深度搜索"""
        return self.intent_detector.detect(query, current_layers, session_history)

    async def start_exploration(
        self,
        query: str,
        context: SearchContext,
        session_id: str = "",
        user_id: str = "",
        mode: str = "celery",
        adapter: Optional[Any] = None,
    ) -> str:
        """启动探索任务，返回 task_id"""
        task_id = f"exp_{session_id}_{asyncio.get_running_loop().time():.0f}"

        if mode == "in_process":
            from app.services.explorer.pipeline import ExplorerPipeline
            pipeline = ExplorerPipeline()
            res = await pipeline.run_in_process(task_id, query, context, adapter=adapter)
            logger.info(f"[Explorer] Finished in-process task {task_id} (success={res.success})")
            return task_id

        # 构建 Celery 任务链 (default "celery" mode)
        from app.tasks.explorer.task_chain import (
            explorer_discover_task,
            explorer_fetch_task,
            explorer_parse_task,
            explorer_geocode_task,
            explorer_validate_task,
        )

        task_chain = chain(
            explorer_discover_task.s(task_id, query, context.model_dump()),
            explorer_fetch_task.s(),
            explorer_parse_task.s(),
            explorer_geocode_task.s(),
            explorer_validate_task.s(),
        )

        # 提交任务 — 计算隔离不变式 1：apply_async 是 broker socket I/O，
        # offload 到 worker 线程（#386），避免阻塞事件循环上的 SSE 流。
        def _submit_chain() -> list[str]:
            result = task_chain.apply_async()
            # Issue #481: apply_async returns the LAST task's result — the
            # durable whole-chain handle. Walk .parent backward to collect
            # every stage's task id (first→last) for status aggregation and
            # abort. The FIRST task's id (the old return value) goes SUCCESS
            # seconds in and must never be used as the run's handle.
            return collect_stage_ids(result)

        stage_ids = await asyncio.to_thread(_submit_chain)
        celery_task_id = stage_ids[-1]
        register_chain_run(celery_task_id, stage_ids)

        # #518: 会话 → 任务登记（post-turn chat-stream bridge 用）。匿名
        # 会话没有 owner registration，独立流端点不可达，靠聊天流桥接。
        if session_id:
            register_session_task(session_id, celery_task_id, user_id)

        # 审计 S42：记录任务所有权 — on the whole-chain id the client polls.
        if user_id:
            TaskQueueService.register_owner(celery_task_id, user_id)

        logger.info(f"[Explorer] Started task {task_id} (celery_id={celery_task_id}) for user {user_id}")

        return celery_task_id

    def _aggregate_chain_status(self, run: ExplorerChainRun) -> dict:
        """Fold the per-stage Celery states into one honest run status.

        Rules (see the issue-#481 note at module top):
        - any stage FAILURE/REVOKED ⇒ run failed, naming the failed stage
          (downstream ids stay PENDING forever after an upstream failure, so
          the final id alone can never report this);
        - all stages SUCCESS ⇒ run completed;
        - otherwise ⇒ PROGRESS on the first not-yet-SUCCESS stage (a later
          stage is PENDING while its predecessors run — that is chain order,
          not completion).
        """
        final_id = run.stage_ids[-1]
        per_stage = [
            self.task_queue.get_task_status(task_id)
            for task_id in run.stage_ids
        ]
        statuses = [d.get("status") for d in per_stage]

        if statuses and all(s == "UNKNOWN" for s in statuses):
            # Result backend unavailable: degrade honestly (ADR-0052), never
            # report a fake completion.
            return {"task_id": final_id, "status": "UNKNOWN", "result": None, "progress": 0}

        for name, stage in zip(EXPLORER_STAGES, per_stage):
            if stage.get("status") in ("FAILURE", "REVOKED"):
                return {
                    "task_id": final_id,
                    "status": stage["status"],
                    "stage": name,
                    "progress": stage.get("progress", 0),
                    "result": None,
                }

        if statuses and all(s == "SUCCESS" for s in statuses):
            last = per_stage[-1]
            return {
                "task_id": final_id,
                "status": "SUCCESS",
                "stage": EXPLORER_STAGES[-1],
                "progress": 100,
                "result": last.get("result"),
            }

        for name, stage in zip(EXPLORER_STAGES, per_stage):
            if stage.get("status") != "SUCCESS":
                return {
                    "task_id": final_id,
                    "status": "PROGRESS",
                    "stage": name,
                    "progress": stage.get("progress", 0),
                    "result": None,
                }

        # Unreachable (a non-terminal aggregate must hit the loop above), but
        # keep a safe default rather than returning None.
        return {"task_id": final_id, "status": "UNKNOWN", "result": None, "progress": 0}

    async def get_task_status(self, task_id: str) -> dict:
        """查询任务状态 — AsyncResult 读 result backend 是 socket I/O，
        SSE stream_progress 每秒轮询，必须 offload（#386）。

        For a registered chain run this aggregates ALL stage ids (issue #481):
        polling the final id alone cannot see mid-chain failures (they leave
        it PENDING forever), and polling the first id fakes SUCCESS early.
        """
        run = get_chain_run(task_id)
        if run is not None:
            return await asyncio.to_thread(self._aggregate_chain_status, run)
        return await asyncio.to_thread(self.task_queue.get_task_status, task_id)

    async def abort_task(self, task_id: str) -> bool:
        """中止任务 — control.revoke 是 broker socket I/O，offload（#386）。

        For a chain run every stage id is revoked (issue #481): revoking only
        the first id is a no-op once discover succeeded, and revoking only the
        final id is a no-op while earlier stages run.
        """
        run = get_chain_run(task_id)
        if run is None:
            return await asyncio.to_thread(self.task_queue.revoke_task, task_id)

        def _revoke_all() -> bool:
            outcomes = []
            for stage_task_id in run.stage_ids:
                outcomes.append(self.task_queue.revoke_task(stage_task_id))
            failed = [
                tid for tid, ok in zip(run.stage_ids, outcomes) if not ok
            ]
            if failed:
                logger.warning(
                    f"[Explorer] Partial abort for {task_id}: revoke failed for {failed}"
                )
            return all(outcomes)

        return await asyncio.to_thread(_revoke_all)

    async def stream_progress(
        self,
        task_id: str,
    ) -> AsyncGenerator[str, None]:
        """SSE 进度流生成器

        Issue #481: ``task_id`` is the whole-chain handle; status comes from
        the per-stage aggregate, so the stream stays open through validate,
        reports real stage events (including late-stage failures), and only
        terminates on whole-chain completion/failure/revocation.
        """
        import time

        last_state = None
        last_stage = None
        heartbeat_interval = 15  # seconds
        last_heartbeat = time.time()

        while True:
            status = await self.get_task_status(task_id)
            current_state = status.get("status")
            current_stage = status.get("stage") or "pending"

            # 结束条件
            if current_state in ("SUCCESS", "FAILURE", "REVOKED"):
                # 发送最终事件 — 成功即整链完成（validate）；失败时点名失败
                # 的阶段，而不是硬编码 validate。终态只发这一条事件（上方
                # 的常规发射跳过终态，避免重复的 completed/failed）。
                final_event = ExplorerPerceptionEvent(
                    stage="validate" if current_state == "SUCCESS" else current_stage,
                    task_id=task_id,
                    status="completed" if current_state == "SUCCESS" else "failed",
                    context={"final_status": current_state},
                )
                yield sse_event("explorer_progress", final_event)
                break

            # 发送进度事件（阶段切换或 PROGRESS 更新时；终态走上方最终事件）
            if (
                current_state != last_state
                or current_stage != last_stage
                or current_state == "PROGRESS"
            ):
                info = status.get("result")
                meta = info.get("meta", {}) if isinstance(info, dict) else {}
                progress = status.get("progress") or meta.get("progress") or 0

                event = ExplorerPerceptionEvent(
                    stage=current_stage,
                    task_id=task_id,
                    status="progress" if current_state == "PROGRESS" else "started",
                    context={"progress": progress},
                )

                yield sse_event("explorer_progress", event)
                last_state = current_state
                last_stage = current_stage

            # 心跳
            now = time.time()
            if now - last_heartbeat >= heartbeat_interval:
                yield sse_event("heartbeat", {"ts": now})
                last_heartbeat = now

            await asyncio.sleep(1)

    async def stream_session_progress(
        self,
        task_ids: list[str],
    ) -> AsyncGenerator[str, None]:
        """Merge per-task progress streams (post-turn chat-stream bridge).

        Completes when every task reached a terminal state. A bounded wall-
        clock cap (`_EXPLORER_BRIDGE_MAX_SECONDS`) force-terminates stragglers
        with an explicit `failed` terminal event so the chat SSE never hangs
        open silently. Every event carries its task_id, so the frontend tracks
        each task independently.
        """
        if not task_ids:
            return
        import time as _time

        queue: "asyncio.Queue[object]" = asyncio.Queue()
        sentinel = object()

        async def _push(task_id: str) -> None:
            try:
                async for event in self.stream_progress(task_id):
                    await queue.put(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 单任务进度流异常 → 显式失败终态（不静默丢弃）。
                await queue.put(
                    sse_event("explorer_progress", ExplorerPerceptionEvent(
                        stage="validate",
                        task_id=task_id,
                        status="failed",
                        context={"final_status": "FAILURE", "error": "progress stream error"},
                    ))
                )
            finally:
                await queue.put(sentinel)

        workers = [asyncio.create_task(_push(tid)) for tid in task_ids]
        finished = 0
        try:
            deadline = _time.monotonic() + _EXPLORER_BRIDGE_MAX_SECONDS
            while finished < len(workers):
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if item is sentinel:
                    finished += 1
                    continue
                yield item
            # 有界兜底：超时后为仍未终态的任务发显式 failed 终态。
            if finished < len(workers):
                for task_id in task_ids:
                    try:
                        status = await self.get_task_status(task_id)
                    except Exception:
                        status = {}
                    if status.get("status") not in ("SUCCESS", "FAILURE", "REVOKED"):
                        yield sse_event("explorer_progress", ExplorerPerceptionEvent(
                            stage=status.get("stage") or "validate",
                            task_id=task_id,
                            status="failed",
                            context={
                                "final_status": "FAILURE",
                                "error": "bridge timeout",
                            },
                        ))
        finally:
            # 取消在飞 worker 并等待其结束：只 cancel() 不 await 会让
            # 事件循环在 generator 关闭后仍持有 pending task →
            # "Task was destroyed but it is pending" 警告。gather 吞掉
            # CancelledError（return_exceptions=True）。
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)


async def bridge_session_explorer_progress(
    session_id: str,
    user_id: Optional[str],
) -> AsyncGenerator[str, None]:
    """#518 post-turn chat-stream bridge for owner-less explorer tasks.

    Anonymous sessions (no owner registration, S42) can never reach the
    owner-verified /explorer/stream endpoint; the chat SSE stream is
    session-isolated by construction, so their progress is bridged here.
    Tasks with a registered owner keep the independent stream and are
    skipped. Completed tasks are pruned from the session registry.
    """
    tasks = get_session_tasks(session_id)
    if not tasks:
        return
    ownerless = [
        task_id for task_id, owner in tasks
        if not TaskQueueService.verify_owner(task_id, user_id or "")
    ]
    if not ownerless:
        return
    async for event in _bridge_orchestrator.stream_session_progress(ownerless):
        yield event
    for task_id in ownerless:
        remove_session_task(session_id, task_id)


_bridge_orchestrator = ExplorerOrchestrator()
