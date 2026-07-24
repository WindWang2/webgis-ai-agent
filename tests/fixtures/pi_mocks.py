"""Shared Pi bridge test fixtures and mock helpers.

Import these in any test file that needs to mock Pi subprocess I/O.
"""
from __future__ import annotations

from typing import Any, List
from unittest.mock import MagicMock


# ─── readline helper ────────────────────────────────────────────────

def make_readline(lines: list[str]) -> Any:
    """Return a sync callable that yields lines then '' (EOF)."""
    it = iter(lines)
    def _reader(*args: Any, **kwargs: Any) -> str:
        try:
            return next(it)
        except StopIteration:
            return ""
    return _reader


# ─── mock process factory ───────────────────────────────────────────

def make_mock_process(lines: list[str] | None = None, latency_ms: float = 0) -> MagicMock:
    """Create a MagicMock subprocess with mocked stdin/stdout/stderr.

    Args:
        lines: Response lines for readline to yield (ignored if latency_ms > 0).
        latency_ms: If > 0, add a sleep(ms) inside readline to simulate
            Pi processing latency (useful for latency benchmarks).
    """
    import time

    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.poll.return_value = None

    if latency_ms > 0:
        def _readline(*args: Any, **kwargs: Any) -> str:
            time.sleep(latency_ms / 1000.0)
            return '{"type":"response","id":"1","success":true}\n'
        proc.stdout.readline.side_effect = _readline
    else:
        proc.stdout.readline.side_effect = make_readline(lines or [])

    return proc


# ─── Pi event factories ─────────────────────────────────────────────

def make_token_event(content: str) -> dict:
    return {
        "type": "message_update",
        "message": {"role": "assistant", "content": [{"type": "text", "text": content}]},
        "assistantMessageEvent": {"type": "text_delta", "content": content},
    }


def make_tool_call_event(name: str, arguments: str) -> dict:
    return {
        "type": "message_update",
        "message": {"role": "assistant", "content": [{"type": "tool_call", "name": name, "arguments": arguments}]},
        "assistantMessageEvent": {"type": "tool_call", "name": name, "arguments": arguments},
    }


def make_tool_execution_start(tool_call_id: str, tool_name: str) -> dict:
    return {
        "type": "tool_execution_start",
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "args": {},
    }


def make_tool_execution_end(tool_call_id: str, tool_name: str, is_error: bool = False) -> dict:
    result = {"content": [{"type": "text", "text": "Tool error"}]} if is_error else {"features": 10, "area": 1500.0}
    return {
        "type": "tool_execution_end",
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "result": result,
        "isError": is_error,
    }


def make_agent_end() -> dict:
    return {"type": "agent_end"}
