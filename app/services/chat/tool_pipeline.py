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
from app.services.task_tracker import TaskTracker
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
    ) -> ToolExecutionResult:
        """
        Execute a single tool call dictionary from LLM assistant message.

        Args:
            tc: Tool call dictionary (e.g. {"id": "call_123", "function": {"name": "foo", "arguments": "{...}"}})
            session_id: Active conversation session ID
            task_id: Active TaskTracker task ID (optional)
            executed_tools: Set of (tool_name, args_str) sentinels for duplicate loop protection

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

        # 3. TaskTracker step registration
        step_id: Optional[str] = None
        if task_id and self.tracker:
            try:
                step = self.tracker.start_step(task_id, tool_name, args_dict)
                step_id = step.id
            except Exception as e:
                logger.warning(f"[ToolPipeline] TaskTracker start_step failed: {e}")

        # 4. Execute tool dispatch via dispatch_fn or ToolDispatchService
        outcome: ToolDispatchResult
        try:
            if self.dispatch_fn is not None:
                outcome = await self.dispatch_fn(tc, session_id, sentinels)
            else:
                outcome = await self.dispatch_service.dispatch(tc, session_id, sentinels)
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

        # 5. Complete TaskTracker step
        if task_id and step_id and self.tracker:
            try:
                if is_error:
                    self.tracker.fail_step(task_id, step_id, str(outcome.raw_result))
                else:
                    self.tracker.complete_step(task_id, step_id, outcome.raw_result)
            except Exception as e:
                logger.warning(f"[ToolPipeline] TaskTracker step update failed: {e}")

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

