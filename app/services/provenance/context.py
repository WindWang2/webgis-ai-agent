"""Tool execution context propagation (spec §21).

The workflow engine executes tools on behalf of an authenticated caller. This
module provides a contextvar channel so any tool (or lineage/metrics code) can
discover the *caller identity* + *project scope* + *run* that the current
dispatch belongs to — without threading kwargs through every ``dispatch`` call
site (which the registry signature does not accept).

Why a contextvar and not a dispatch kwarg: the registry's ``dispatch`` is called
from many places (agent bridge, workflow engine, explorer); changing its
signature would be a wide-blast-radius change for a channel most tools don't
read. A contextvar is set once per workflow execution and auto-propagates across
``await`` boundaries within the same ``asyncio.Task`` (the same pattern the
durable-job runtime uses for ``current_origin``).

This makes the engine's security claim *truthful*: ``user_id`` / ``org_id`` /
``project_id`` / ``run_id`` really do reach the tool layer, and the workflow-
level guards (project ownership, tenant scope) are enforced for run / replay /
resume (INV-AUTH1).
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolExecutionContext:
    user_id: Optional[str] = None
    org_id: Optional[int] = None
    project_id: Optional[str] = None
    run_id: Optional[str] = None
    session_id: Optional[str] = None


_unset: ContextVar[Optional[ToolExecutionContext]] = ContextVar(
    "tool_execution_context", default=None
)


def set_tool_execution_context(ctx: ToolExecutionContext):
    """Bind the execution context for the current asyncio.Task. Returns a token."""
    return _unset.set(ctx)


def reset_tool_execution_context(token) -> None:
    _unset.reset(token)


def get_tool_execution_context() -> Optional[ToolExecutionContext]:
    """Read the current execution context (None outside a workflow execution)."""
    return _unset.get()
