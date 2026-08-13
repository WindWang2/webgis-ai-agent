"""Tool Execution Pipeline — Deep module for AI tool execution & task tracking (ADR-0024).

Encapsulates tool dispatch, parameter JSON parsing, TaskTracker step lifecycle,
sentinel duplicate-loop protection, and LLM payload slimming.
"""
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Callable

from app.tools.registry import ToolRegistry
from app.services.jobs.cancellation import OperationCancelled, use_token
from app.services.jobs.context import JobOrigin, use_origin
from app.lib.runtime.context import current_runtime_context
from app.services.task_tracker import TaskTracker, TaskStep
from app.services.tool_dispatch_service import (
    ToolDispatchResult,
    ToolDispatchService,
    normalize_tool_name,
)

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionResult:
    tool_name: str
    tool_call_id: str
    raw_result: Any
    llm_payload: str
    is_error: bool = False
    execution_time_ms: float = 0.0
    outcome: Optional[ToolDispatchResult] = None
    #: ADR-0052：本次工具执行期间创建的 durable job id。执行引擎把它放进
    #: step_result SSE，前端据此把后台 GIS job 挂到该 tool step 下。
    background_job_ids: list[str] = field(default_factory=list)
    #: 工具是否因取消而中止（区别于普通错误 —— 取消绝不触发 retry，规范 §17）
    cancelled: bool = False


class ToolExecutionPipeline:
    """Deep module for executing tool calls during chat loops."""

    def __init__(
        self,
        registry: ToolRegistry,
        tracker: Optional[TaskTracker] = None,
        dispatch_service: Optional[ToolDispatchService] = None,
        dispatch_fn: Optional[Callable] = None,
    ):
        self.registry = registry
        self.tracker = tracker or TaskTracker()
        self.dispatch_service = dispatch_service or ToolDispatchService(registry=registry)
        self.dispatch_fn = dispatch_fn

    async def execute_tool_call(
        self,
        tc: dict,
        session_id: str,
        task_id: Optional[str] = None,
        executed_tools: Optional[set[tuple[str, str]]] = None,
        pre_created_step: Optional[TaskStep] = None,
        owner_id: Optional[str] = None,
        owner_token: Optional[str] = None,
    ) -> ToolExecutionResult:
        """
        Execute a single tool call dictionary from LLM assistant message.

        Args:
            tc: Tool call dictionary (e.g. {"id": "call_123", "function": {"name": "foo", "arguments": "{...}"}})
            session_id: Active conversation session ID
            task_id: Active TaskTracker task ID (optional)
            executed_tools: Set of (tool_name, args_str) sentinels for duplicate loop protection
            pre_created_step: Step already opened by the caller (chat_stream emits
                step_start SSE before dispatch). When provided, the pipeline does
                NOT open a second track_step — RUN-02 (deep-audit round 2) fixed
                double step tracking that inflated task.steps 2x and desynced
                step_id between SSE events and the tracker.
            owner_id: 已认证用户 id（ADR-0052：工具派生的 durable job 归属）
            owner_token: 匿名会话归属令牌（同上，匿名路径）

        Returns:
            ToolExecutionResult with raw_result (for frontend/events) and llm_payload (for LLM history)
        """
        start_time = time.time()
        tool_call_id = tc.get("id") or ""
        func_info = tc.get("function", {})
        tool_name = func_info.get("name") or "unknown_tool"
        raw_args = func_info.get("arguments", {})

        # 1. Parse arguments dictionary
        args_dict: dict = {}
        if isinstance(raw_args, dict):
            args_dict = raw_args
        elif isinstance(raw_args, str):
            try:
                args_dict = json.loads(raw_args) if raw_args.strip() else {}
            except Exception as e:
                logger.warning(f"[ToolPipeline] JSON parse failed for {tool_name}: {e}")
                args_dict = {}

        # 2. Sentinel check / fallback
        sentinels = executed_tools if executed_tools is not None else set()

        # 3. Execute tool dispatch inside TaskTracker step context.
        # RUN-02: when the caller already opened a step (and emitted step_start),
        # use it directly instead of opening a second track_step.
        outcome: ToolDispatchResult
        is_error = False
        cancelled = False

        # ADR-0052: 取消 token 与 job 来源都通过 contextvar 传递。asyncio.to_thread
        # （registry 执行同步工具的路径）会复制 context，所以 token 自动穿到工具
        # 线程里，几十个 GIS 工具签名不用改。origin 让工具内部创建的 durable job
        # 自动带上 session/owner/agent step 关联。
        cancel_token = self.tracker.cancel_token_for(task_id) if task_id else None
        # Runtime observability (W5): inherit run_id/turn_id from the active
        # RuntimeContext so durable jobs spawned by this tool are linkable to the
        # owning turn (retires the always-NULL run_id/turn_id on job rows — the
        # new_run_id/new_turn_id helpers in jobs.context had zero callers).
        _rt = current_runtime_context()
        origin = JobOrigin(
            session_id=session_id or None,
            owner_id=owner_id,
            owner_token=owner_token,
            run_id=(_rt.run_id if _rt else None),
            turn_id=(_rt.turn_id if _rt else None),
            agent_task_id=task_id,
            agent_step_id=(pre_created_step.id if pre_created_step is not None else None),
            tool_call_id=tool_call_id or None,
            tool_name=tool_name,
        )

        async def _dispatch() -> ToolDispatchResult:
            with use_token(cancel_token), use_origin(origin):
                if self.dispatch_fn is not None:
                    return await self.dispatch_fn(tc, session_id, sentinels)
                return await self.dispatch_service.dispatch(tc, session_id, sentinels)

        def _error_outcome(exc: BaseException) -> ToolDispatchResult:
            err_msg = f"工具执行异常 ({type(exc).__name__}): {exc}"
            return ToolDispatchResult(
                status="error",
                llm_payload=err_msg,
                slim_event={"error": err_msg},
                geojson_ref=None,
                raw_result={"error": err_msg},
                error_msg=err_msg,
            )

        def _cancelled_outcome() -> ToolDispatchResult:
            # 取消不是「工具坏了」：给 LLM 一个明确的取消说明，且不产生 traceback
            msg = "工具执行已被用户取消"
            return ToolDispatchResult(
                status="error",
                llm_payload=msg,
                slim_event={"cancelled": True, "message": msg},
                geojson_ref=None,
                raw_result={"cancelled": True, "message": msg},
                error_msg=msg,
            )

        if pre_created_step is not None:
            try:
                outcome = await _dispatch()
            except OperationCancelled:
                # 协作式取消在 GIS 循环的 checkpoint 处抛出 —— 这是预期路径，
                # 不打 error 日志、不当作工具故障
                logger.info(f"[ToolPipeline] {tool_name} cancelled by user")
                cancelled = True
                outcome = _cancelled_outcome()
            except Exception as e:
                logger.error(f"[ToolPipeline] Dispatch error for {tool_name}: {e}", exc_info=True)
                outcome = _error_outcome(e)
            is_error = (outcome.status == "error")
            pre_created_step.result = outcome.raw_result
            if cancelled:
                # F8: 取消 ≠ 失败 —— step 记 cancelled。终态不可变，与引擎侧
                # （chat_stream 持有 pre_created step 的收尾权）谁先谁生效。
                if task_id:
                    try:
                        self.tracker.cancel_step(task_id, pre_created_step.id)
                    except Exception:
                        pass
            elif is_error:
                pre_created_step.error = str(outcome.raw_result)
            pre_created_step.background_job_ids = list(origin.created_job_ids)
        else:
            async with self.tracker.track_step(task_id, tool_name, args_dict) as step:
                if step is not None:
                    origin.agent_step_id = step.id
                try:
                    outcome = await _dispatch()
                except OperationCancelled:
                    logger.info(f"[ToolPipeline] {tool_name} cancelled by user")
                    cancelled = True
                    outcome = _cancelled_outcome()
                except Exception as e:
                    logger.error(f"[ToolPipeline] Dispatch error for {tool_name}: {e}", exc_info=True)
                    outcome = _error_outcome(e)

                is_error = (outcome.status == "error")
                if step is not None:
                    step.result = outcome.raw_result
                    if cancelled:
                        # F8: 取消先记 cancelled；track_step 的 __aexit__ 随后
                        # complete_step 会因终态不可变而空操作。
                        if task_id:
                            try:
                                self.tracker.cancel_step(task_id, step.id)
                            except Exception:
                                pass
                    elif is_error:
                        step.error = str(outcome.raw_result)
                    step.background_job_ids = list(origin.created_job_ids)

        elapsed_ms = (time.time() - start_time) * 1000
        if (
            outcome.status == "ok"
            and normalize_tool_name(tool_name) == "webgis_layer_upsert"
        ):
            # Both the legacy and Pi agents converge into the same existing
            # harness. Dynamic import avoids coupling the generic pipeline at
            # module import time to the optional Pi transport.
            from app.agent_pi_bridge import record_cartographic_dispatch_evidence

            await record_cartographic_dispatch_evidence(
                session_id,
                tool_call_id,
                "webgis_layer_upsert",
                args_dict,
                outcome,
                int(elapsed_ms),
            )
        return ToolExecutionResult(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            raw_result=outcome.raw_result,
            llm_payload=outcome.llm_payload,
            is_error=is_error,
            execution_time_ms=elapsed_ms,
            outcome=outcome,
            background_job_ids=list(origin.created_job_ids),
            cancelled=cancelled,
        )
