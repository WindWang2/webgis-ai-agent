"""Pi AgentSessionEvent -> SSE event string pure mapper (architecture-review F3).

Pure functions mapping Pi events to SSE-formatted strings. Has zero state
and zero knowledge of the ADR-0022 dispatch result cache (the cache lookup
is injected as a callable by ``PiBridge.stream_prompt``).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from app.utils.sse import sse_event
from app.utils.security import sanitize_error_msg, redact_paths

logger = logging.getLogger(__name__)

# SSE content strings for compaction events.
COMPACTION_START_MSG = "[压缩上下文...]\n"
COMPACTION_END_MSG = "[上下文压缩完成]\n"


def _sanitize_for_client(text: str) -> str:
    """Sanitize an error string for safe emission to clients/SSE."""
    try:
        text = sanitize_error_msg(text)
    except Exception:  # noqa: BLE001
        pass
    text = redact_paths(text)
    if len(text) > 500:
        text = text[:500] + "...(truncated)"
    return text


def _extract_error_text(result: Any) -> str:
    """Extract error text from a tool result, sanitized for SSE output."""
    if isinstance(result, dict):
        content = result.get("content", [])
        if isinstance(content, list) and content:
            raw = content[0].get("text", str(result))
        else:
            raw = result.get("message", str(result))
    else:
        raw = str(result)
    return _sanitize_for_client(raw)


def _text_from_content_blocks(content: Any) -> str:
    """Join text out of an AssistantMessage ``content`` value (string or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for seg in content:
            if isinstance(seg, dict) and "text" in seg:
                parts.append(str(seg.get("text", "")))
        return "".join(parts)
    return ""


def _extract_text_from_event(event: dict) -> str:
    """Extract the AUTHORITATIVE latest assistant text for a turn (audit #816).

    Vendor protocol (vendor/pi/packages/agent/src/agent-loop.ts:326-343,
    agent/src/types.ts): ``message_update.message`` is the ACCUMULATED partial
    snapshot (appending it per event duplicated content O(n²)); ``agent_end``
    carries the final ``messages`` list. Both entry points feed
    ``PiBridge.prompt``'s drain, which keeps only the latest value instead of
    concatenating snapshots.
    """
    event_type = event.get("type", "")

    if event_type == "message_update":
        msg = event.get("message", {})
        if isinstance(msg, dict):
            return _text_from_content_blocks(msg.get("content", ""))
        return ""

    if event_type == "agent_end":
        messages = event.get("messages")
        if isinstance(messages, list):
            # Final answer = last assistant message carrying text.
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "assistant":
                    text = _text_from_content_blocks(m.get("content", ""))
                    if text:
                        return text
            return ""
        # Legacy/no-messages shape: fall back to a single message member.
        msg = event.get("message", {})
        if isinstance(msg, dict):
            return _text_from_content_blocks(msg.get("content", ""))

    return ""


def _base_step_payload(event: dict, session_id: str, extra: dict) -> dict:
    """Build base SSE payload with common fields shared across step events."""
    base = {
        "task_id": session_id,
        "step_id": event.get("toolCallId", ""),
        "session_id": session_id,
    }
    base.update(extra)
    return base


def _handle_message_update(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    """Map a ``message_update`` per the vendor AssistantMessageEvent protocol.

    audit #816: the vendor's incremental events carry text in ``delta``
    (types.ts:471-475) — ``content`` exists only on ``*_end`` events, and
    toolcall events carry ``toolCall`` (types.ts:478), never name/arguments
    at the top level. The previous shape never matched a real event.
    """
    assistant_event = event.get("assistantMessageEvent", {})
    event_kind = assistant_event.get("type", "")

    if event_kind in ("text_delta", "thinking_delta"):
        delta = assistant_event.get("delta", "")
        if delta:
            return sse_event("token", {
                "content": delta,
                "is_reasoning": event_kind == "thinking_delta",
                "session_id": session_id,
            })
        return None

    if event_kind in ("text_end", "thinking_end"):
        # The deltas already streamed this block; re-emitting the terminal
        # ``content`` would duplicate every token.
        return None

    if event_kind in ("toolcall_start", "toolcall_delta"):
        # Arguments are incomplete until toolcall_end — no SSE yet.
        return None

    if event_kind == "toolcall_end":
        tool_call = assistant_event.get("toolCall", {})
        if isinstance(tool_call, dict) and tool_call.get("name"):
            args = tool_call.get("arguments")
            if isinstance(args, dict):
                try:
                    args = json.dumps(args, ensure_ascii=False)
                except Exception:  # noqa: BLE001
                    args = "{}"
            elif not isinstance(args, str):
                args = ""
            return sse_event("tool_call", {
                "name": tool_call.get("name", ""),
                "arguments": args,
                "session_id": session_id,
            })
        return None

    return None


def _handle_tool_execution_start(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    step_idx = event.get("stepIndex") or event.get("step_index") or 0
    return sse_event("step_start", _base_step_payload(event, session_id, {
        "step_index": step_idx,
        "tool": event.get("toolName", ""),
    }))


def _handle_tool_execution_end(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    """SSE 适配器：读缓存的 dispatch 结果发 step_result / step_error.

    ``cache_lookup`` closes over the verified turn session and accepts the
    event's tool_call_id. ``session_id`` also stamps the SSE payload.
    """
    tool_name = event.get("toolName", "")
    tool_call_id = event.get("toolCallId", "")
    cached = cache_lookup(tool_call_id) if cache_lookup else None

    if cached is not None:
        if getattr(cached, "status", None) == "error":
            return sse_event("step_error", _base_step_payload(event, session_id, {
                "tool": tool_name,
                "error": getattr(cached, "error_msg", "") or "",
            }))
        # ok / repeated：用服务端 slim_event + geojson_ref
        payload = _base_step_payload(event, session_id, {
            "tool": tool_name,
            "result": getattr(cached, "slim_event", {}),
        })
        ref = getattr(cached, "geojson_ref", None)
        if ref:
            payload["geojson_ref"] = ref
            # V3 Performance: descriptor was pre-computed by dispatch() and
            # carried on ToolDispatchResult.ref_descriptor — no async call needed.
            descriptor = getattr(cached, "ref_descriptor", None)
            if descriptor:
                payload["ref_descriptor"] = descriptor
        return sse_event("step_result", payload)

    # 缓存未命中（Pi 重复回传 / dispatch 未走 service 路径）：退化到旧行为
    result = event.get("result", {})
    is_error = event.get("isError", False)
    logger.warning(
        f"[PiEventMapper] dispatch cache miss for toolCallId={tool_call_id} "
        f"(session={session_id}); falling back to Pi-echoed result without geojson_ref"
    )
    if is_error:
        error_msg = _extract_error_text(result)
        return sse_event("step_error", _base_step_payload(event, session_id, {
            "tool": tool_name,
            "error": error_msg,
        }))
    try:
        from app.services.tool_dispatch_service import slim_event_result
        slim = slim_event_result(result)
    except Exception:
        slim = result
    return sse_event("step_result", _base_step_payload(event, session_id, {
        "tool": tool_name,
        "result": slim,
    }))


def _handle_agent_end(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    """task_complete with truthful counters (audit #820).

    ``turn_stats`` (injected by PiBridge) reports the turn's observed tool-step
    count and final text so the payload matches the legacy engine's
    len(task.steps)/content[:100] semantics instead of hardcoded zeros.
    """
    stats = turn_stats() if callable(turn_stats) else {}
    summary_src = stats.get("final_text") or _extract_text_from_event(event)
    return sse_event("task_complete", _base_step_payload(event, session_id, {
        "step_count": int(stats.get("tool_step_count", 0) or 0),
        "summary": str(summary_src)[:100],
    }))


def _handle_compaction_start(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    return sse_event("content", {
        "content": COMPACTION_START_MSG,
        "session_id": session_id,
    })


def _handle_compaction_end(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    return sse_event("content", {
        "content": COMPACTION_END_MSG,
        "session_id": session_id,
    })


_EVENT_HANDLERS: dict[str, Callable[..., Optional[str]]] = {
    "message_update": _handle_message_update,
    "tool_execution_start": _handle_tool_execution_start,
    "tool_execution_end": _handle_tool_execution_end,
    "agent_end": _handle_agent_end,
    "compaction_start": _handle_compaction_start,
    "compaction_end": _handle_compaction_end,
}


def map_event_to_sse(
    event: dict,
    session_id: str = "",
    cache_lookup: Optional[Callable[[str], Optional[Any]]] = None,
    turn_stats: Optional[Callable[[], dict]] = None,
) -> Optional[str]:
    """Map a Pi AgentSessionEvent to an SSE-formatted string.

    Args:
        event: Pi event dictionary
        session_id: Session ID — used to stamp the SSE payload's session_id field.
                    Caller must pass the turn-scoped id (not a stale bridge field),
                    since Pi events carry no session of their own.
        cache_lookup: Optional callable (tool_call_id,) -> ToolDispatchResult
                      injected by PiBridge (ADR-0022 rendezvous), with the
                      verified turn session captured by the caller.
        turn_stats: Optional zero-arg callable returning the turn's observed
                    counters ({"tool_step_count": int, "final_text": str}),
                    consumed by the agent_end handler (audit #820).

    Returns:
        SSE-formatted string or None if unhandled
    """
    event_type = event.get("type", "")
    handler = _EVENT_HANDLERS.get(event_type)
    if handler is None:
        return None
    return handler(event, session_id, cache_lookup, turn_stats=turn_stats)
