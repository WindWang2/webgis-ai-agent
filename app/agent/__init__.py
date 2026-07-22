"""New Agent system — public surface.

Imports are lazy/guarded so this package can be imported even if some
submodules are not yet implemented.
"""
from __future__ import annotations

from app.agent._agent import Agent
from app.agent._event import EventBus, create_emit_fn
from app.agent._loop import run_agent_loop, run_agent_loop_continue
from app.agent._runtime import AgentRuntime
from app.agent._stream import build_stream_fn_from_client, create_stream_fn
from app.agent._types import (
    AgentContext,
    AgentEvent,
    AgentLoopConfig,
    AgentState,
    ModelInfo,
    StreamOptions,
    ToolExecutionMode,
)

# Subpackages
from app.agent.chat import ChatAgent
from app.agent.subagent import Subagent
from app.agent.harness import compaction, session, skills, system_prompt

__all__ = [
    # Core
    "Agent",
    "AgentRuntime",
    "AgentContext",
    "AgentEvent",
    "AgentLoopConfig",
    "AgentState",
    "ModelInfo",
    "StreamOptions",
    "ToolExecutionMode",
    # Loop
    "run_agent_loop",
    "run_agent_loop_continue",
    # Event
    "EventBus",
    "create_emit_fn",
    # Stream
    "create_stream_fn",
    "build_stream_fn_from_client",
    # Subclasses
    "ChatAgent",
    "Subagent",
    # Harness
    "compaction",
    "session",
    "skills",
    "system_prompt",
]
