"""Subagent — isolated Agent for delegated tasks.

Design (aligned with Claude Code's Agent tool pattern):
- Subagent(Agent) inherits Agent, with a specialized system prompt and tool subset
- Used by ChatAgent to delegate complex sub-tasks
- Integrates with existing SubagentDispatcher from app/services/subagent.py
- Returns SubagentResult with summary + refs for parent agent consumption

Usage:
    result = await Subagent.create(
        parent_agent=chat_agent,
        task="分析这个GeoJSON数据",
        tools_subset=["geojson_analyze", "spatial_stats"],
        max_rounds=10,
    )
    # result.summary, result.refs, result.success
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from app.agent._agent import Agent
from app.agent._event import EventBus
from app.agent._types import (
    AgentContext,
    AgentLoopConfig,
    AgentMessage,
    AgentState,
    ModelInfo,
    StreamOptions,
)
from app.services.subagent import SubagentResult, select_tools_for_subagent

logger = logging.getLogger(__name__)

# Subagent system prompt (from app/services/subagent.py)
SUB_SYSTEM_PROMPT = (
    "你是一个**子代理**（subagent），被主 Agent 委派执行一个具体子任务。\n"
    "纪律：\n"
    "1. 只用允许给你的工具子集；遇到能力之外的需求直接在结论里说明而不是绕路；\n"
    "2. 不要做超出子任务边界的分析；\n"
    "3. 最终回复请用纯文本，1-3 段，描述：(a) 你做了什么；(b) 关键 ref_id；"
    "(c) 给主代理的建议下一步。"
)


class Subagent(Agent):
    """Isolated subagent for delegated tasks.

    Subagent inherits Agent lifecycle but overrides:
    - _build_system_prompt: uses subagent-specific prompt
    - _select_tools: uses provided tool subset only
    - stream_function: provided by factory method

    The subagent runs in the same session (shares refs/map_state)
    but has isolated message history and tool scope.
    """

    def __init__(
        self,
        state: AgentState,
        event_bus: EventBus,
        stream_function: Callable[
            [Any, AgentContext, Optional[StreamOptions]],
            Awaitable[None],
        ],
        task: str,
        tool_names: list[str],
    ) -> None:
        """Initialize Subagent.

        Args:
            state: AgentState with system prompt, model, tools, messages
            event_bus: EventBus for lifecycle events
            stream_function: StreamFn for LLM calls
            task: The delegated task description
            tool_names: List of tool names available to this subagent
        """
        super().__init__(
            state=state,
            event_bus=event_bus,
            stream_function=stream_function,
        )
        self._task = task
        self._tool_names = tool_names

    # ── Override Agent lifecycle ────────────────────────────────

    def _build_system_prompt(self) -> str:
        """Build subagent-specific system prompt with task description."""
        return f"{SUB_SYSTEM_PROMPT}\n\n# 子任务\n{self._task}"

    async def _select_tools(self, context: AgentContext) -> list[dict]:
        """Return the pre-configured tool subset (no catalog lookup)."""
        # Tools are already set in state by the factory method
        return list(self._state.tools)

    # ── Core API ────────────────────────────────────────────────

    @classmethod
    async def create(
        cls,
        parent: Agent,
        task: str,
        domains: Optional[list[str]] = None,
        extra_tools: Optional[list[str]] = None,
        max_rounds: int = 10,
    ) -> "Subagent":
        """Create a Subagent from a parent Agent.

        Args:
            parent: Parent Agent (ChatAgent) providing registry, model, session
            task: Natural language description of the sub-task
            domains: Optional domain list for tool filtering
            extra_tools: Optional explicit tool whitelist
            max_rounds: Maximum rounds for subagent loop

        Returns:
            Configured Subagent instance (not yet run)
        """
        # Get registry from parent
        registry = getattr(parent, "_registry", None)
        if registry is None:
            raise ValueError("Parent agent must have _registry attribute")

        # Select tool subset using existing SubagentDispatcher logic
        tool_subset = select_tools_for_subagent(
            registry,
            domains=domains,
            extra_tools=extra_tools,
            exclude_tier3=True,
        )
        tool_names = [s["function"]["name"] for s in tool_subset]

        # Build state with subagent system prompt
        session_id = getattr(parent, "_session_id", "")
        system_prompt = f"{SUB_SYSTEM_PROMPT}\n\n# 子任务\n{task}"

        state = AgentState(
            systemPrompt=system_prompt,
            model=parent._state.model,
            tools=tool_subset,
            messages=[],  # Start fresh, subagent has isolated history
        )

        # Create event bus for this subagent
        event_bus = EventBus()

        # Build stream function from parent's stream function
        # The subagent shares the same LLM client
        stream_fn = parent._stream_function

        subagent = cls(
            state=state,
            event_bus=event_bus,
            stream_function=stream_fn,
            task=task,
            tool_names=tool_names,
        )
        subagent._session_id = session_id
        subagent._max_rounds = max_rounds

        return subagent

    async def run(self) -> SubagentResult:
        """Run the subagent task and return results.

        Returns:
            SubagentResult with summary, refs, success status
        """
        from app.services.session_data import session_data_manager

        # Record refs before execution
        try:
            refs_before = set((await session_data_manager.list_refs(self._session_id)).keys())
        except Exception:
            refs_before = set()

        # Run the agent loop
        try:
            await self.prompt(self._task)
            success = True
            # Extract summary from last assistant message
            summary = ""
            if self._state.messages:
                for msg in reversed(self._state.messages):
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        summary = msg.get("content", "").strip()
                        break
            if not summary:
                summary = "子代理执行完成，但未生成文本摘要。"
        except Exception as e:
            logger.error(f"[Subagent] execution failed: {e}")
            success = False
            summary = f"子代理执行失败: {e}"

        # Record refs after execution
        try:
            refs_after = set((await session_data_manager.list_refs(self._session_id)).keys())
        except Exception:
            refs_after = set()
        new_refs = sorted(refs_after - refs_before)

        return SubagentResult(
            success=success,
            summary=summary,
            refs=new_refs,
        )
