"""Tool Execution Pipeline — Deep module for AI tool execution & task tracking (ADR-0024).

Encapsulates tool dispatch, parameter JSON parsing, TaskTracker step lifecycle,
sentinel duplicate-loop protection, and LLM payload slimming.
"""
import json
import time
import logging
from dataclasses import dataclass
from typing import Any, Optional, Callable

from app.tools.registry import ToolRegistry
from app.services.task_tracker import TaskTracker, TaskStep
from app.services.tool_dispatch_service import ToolDispatchService, ToolDispatchResult

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

        async def _dispatch() -> ToolDispatchResult:
            if self.dispatch_fn is not None:
                return await self.dispatch_fn(tc, session_id, sentinels)
            return await self.dispatch_service.dispatch(tc, session_id, sentinels)

        if pre_created_step is not None:
            try:
                outcome = await _dispatch()
            except Exception as e:
                logger.error(f"[ToolPipeline] Dispatch error for {tool_name}: {e}", exc_info=True)
                err_msg = f"工具执行异常 ({type(e).__name__}): {e}"
                outcome = ToolDispatchResult(
                    status="error",
                    llm_payload=err_msg,
                    slim_event={"error": err_msg},
                    geojson_ref=None,
                    raw_result={"error": err_msg},
                    error_msg=err_msg,
                )
            is_error = (outcome.status == "error")
            pre_created_step.result = outcome.raw_result
            if is_error:
                pre_created_step.error = str(outcome.raw_result)
        else:
            async with self.tracker.track_step(task_id, tool_name, args_dict) as step:
                try:
                    outcome = await _dispatch()
                except Exception as e:
                    logger.error(f"[ToolPipeline] Dispatch error for {tool_name}: {e}", exc_info=True)
                    err_msg = f"工具执行异常 ({type(e).__name__}): {e}"
                    outcome = ToolDispatchResult(
                        status="error",
                        llm_payload=err_msg,
                        slim_event={"error": err_msg},
                        geojson_ref=None,
                        raw_result={"error": err_msg},
                        error_msg=err_msg,
                    )

                is_error = (outcome.status == "error")
                if step is not None:
                    step.result = outcome.raw_result
                    if is_error:
                        step.error = str(outcome.raw_result)

        elapsed_ms = (time.time() - start_time) * 1000
        return ToolExecutionResult(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            raw_result=outcome.raw_result,
            llm_payload=outcome.llm_payload,
            is_error=is_error,
            execution_time_ms=elapsed_ms,
            outcome=outcome,
        )

