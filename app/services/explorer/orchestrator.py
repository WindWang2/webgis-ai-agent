"""探索任务编排器"""
import logging
import asyncio
from typing import AsyncGenerator
from celery import chain
from app.services.explorer.models import SearchContext, ExplorerPerceptionEvent
from app.services.explorer.intent_detector import IntentDetector, ExploreDecision
from app.services.task_queue import TaskQueueService
from app.utils.sse import sse_event

logger = logging.getLogger(__name__)


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
    ) -> str:
        """启动探索任务，返回 task_id"""
        task_id = f"exp_{session_id}_{asyncio.get_event_loop().time():.0f}"

        # 构建 Celery 任务链
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

        # 提交任务
        result = task_chain.apply_async()
        celery_task_id = result.id

        logger.info(f"[Explorer] Started task {task_id} (celery_id={celery_task_id})")

        return celery_task_id

    async def get_task_status(self, task_id: str) -> dict:
        """查询任务状态"""
        return self.task_queue.get_task_status(task_id)

    async def abort_task(self, task_id: str) -> bool:
        """中止任务"""
        return self.task_queue.revoke_task(task_id)

    async def stream_progress(
        self,
        task_id: str,
    ) -> AsyncGenerator[str, None]:
        """SSE 进度流生成器"""
        import time

        last_state = None
        heartbeat_interval = 15  # seconds
        last_heartbeat = time.time()

        while True:
            status = await self.get_task_status(task_id)
            current_state = status.get("status")

            # 发送进度事件
            if current_state != last_state or current_state == "PROGRESS":
                info = status.get("result") or {}
                meta = info.get("meta", {}) if isinstance(info, dict) else {}

                event = ExplorerPerceptionEvent(
                    stage=meta.get("stage", "unknown"),
                    task_id=task_id,
                    status="progress" if current_state == "PROGRESS" else (
                        "completed" if current_state == "SUCCESS" else (
                            "failed" if current_state == "FAILURE" else "started"
                        )
                    ),
                    context={"progress": meta.get("progress", 0)},
                )

                yield sse_event("explorer_progress", event)
                last_state = current_state

            # 心跳
            now = time.time()
            if now - last_heartbeat >= heartbeat_interval:
                yield sse_event("heartbeat", {"ts": now})
                last_heartbeat = now

            # 结束条件
            if current_state in ("SUCCESS", "FAILURE", "REVOKED"):
                # 发送最终事件
                final_event = ExplorerPerceptionEvent(
                    stage="validate",
                    task_id=task_id,
                    status="completed" if current_state == "SUCCESS" else "failed",
                    context={"final_status": current_state},
                )
                yield sse_event("explorer_progress", final_event)
                break

            await asyncio.sleep(1)
