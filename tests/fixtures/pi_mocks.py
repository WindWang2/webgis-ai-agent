"""Shared Pi bridge test fixtures and mock helpers.

Import these in any test file that needs to mock Pi subprocess I/O.
"""
from __future__ import annotations

from typing import Any
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
# Shapes mirror the vendor AgentSessionEvent protocol exactly
# (vendor/pi/packages/agent/src/agent-loop.ts + ai/src/types.ts):
# message_update carries the accumulated partial snapshot in `message`
# plus the incremental AssistantMessageEvent in `assistantMessageEvent`;
# text increments live in `delta`, tool calls complete on toolcall_end
# with a `toolCall` object; agent_end carries a `messages` list and a
# `willRetry` flag, and is followed by `agent_settled` — the sole
# turn-final event the vendor emits unconditionally (#855).

def make_token_event(content: str) -> dict:
    return {
        "type": "message_update",
        "message": {"role": "assistant", "content": [{"type": "text", "text": content}]},
        "assistantMessageEvent": {
            "type": "text_delta",
            "contentIndex": 0,
            "delta": content,
        },
    }


def make_tool_call_event(name: str, arguments: str) -> dict:
    import json as _json
    try:
        parsed_args = _json.loads(arguments) if isinstance(arguments, str) else arguments
    except Exception:
        parsed_args = {}
    if not isinstance(parsed_args, dict):
        parsed_args = {}
    return {
        "type": "message_update",
        "message": {"role": "assistant", "content": [
            {"type": "toolCall", "id": "tc_mock", "name": name, "arguments": parsed_args}
        ]},
        "assistantMessageEvent": {
            "type": "toolcall_end",
            "contentIndex": 1,
            "toolCall": {"type": "toolCall", "id": "tc_mock", "name": name, "arguments": parsed_args},
        },
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


def make_agent_end(content: str = "", will_retry: bool = False) -> dict:
    event: dict = {"type": "agent_end", "willRetry": will_retry}
    if content:
        event["messages"] = [
            {"role": "user", "content": [{"type": "text", "text": "q"}]},
            {"role": "assistant", "content": [{"type": "text", "text": content}]},
        ]
    return event


def make_agent_settled() -> dict:
    """The vendor's unconditional turn-final event (#855)."""
    return {"type": "agent_settled"}


def make_auto_retry_start(attempt: int = 1, max_attempts: int = 3, delay_ms: int = 2000, error: str = "429 rate limited") -> dict:
    return {
        "type": "auto_retry_start",
        "attempt": attempt,
        "maxAttempts": max_attempts,
        "delayMs": delay_ms,
        "errorMessage": error,
    }


def make_auto_retry_end(success: bool, attempt: int = 1, final_error: str | None = None) -> dict:
    event: dict = {"type": "auto_retry_end", "success": success, "attempt": attempt}
    if final_error is not None:
        event["finalError"] = final_error
    return event
