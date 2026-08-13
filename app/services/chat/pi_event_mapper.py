"""Pi AgentSessionEvent -> SSE event string pure mapper (architecture-review F3).

Pure functions mapping Pi events to SSE-formatted strings. Has zero state
and zero knowledge of the ADR-0022 dispatch result cache (the cache lookup
is injected as a callable by ``PiBridge.stream_prompt``).
"""
from __future__ import annotations

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


def _extract_text_from_event(event: dict) -> str:
    """Extract text content from an AgentSessionEvent."""
    event_type = event.get("type", "")

    if event_type == "message_update":
        msg = event.get("message", {})
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            return "".join(
                seg.get("text", "") for seg in content if isinstance(seg, dict)
            )

    elif event_type == "agent_end":
        msg = event.get("message", {})
        content = msg.get("content", "")
        if isinstance(content, str):
            return content

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


def _handle_message_update(event: dict, session_id: str, cache_lookup: Optional[Callable]) -> Optional[str]:
    assistant_event = event.get("assistantMessageEvent", {})
    event_kind = assistant_event.get("type", "")

    if "text" in event_kind or "thinking" in event_kind:
        content = assistant_event.get("content", "")
        is_reasoning = "thinking" in event_kind
        if content:
            return sse_event("token", {
                "content": content,
                "is_reasoning": is_reasoning,
                "session_id": session_id,
            })
    elif "tool_call" in event_kind or "toolcall" in event_kind:
        return sse_event("tool_call", {
            "name": assistant_event.get("name", ""),
            "arguments": assistant_event.get("arguments", ""),
            "session_id": session_id,
        })
    return None


def _handle_tool_execution_start(event: dict, session_id: str, cache_lookup: Optional[Callable]) -> Optional[str]:
    step_idx = event.get("stepIndex") or event.get("step_index") or 0
    return sse_event("step_start", _base_step_payload(event, session_id, {
        "step_index": step_idx,
        "tool": event.get("toolName", ""),
    }))


def _handle_tool_execution_end(event: dict, session_id: str, cache_lookup: Optional[Callable]) -> Optional[str]:
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


def _handle_agent_end(event: dict, session_id: str, cache_lookup: Optional[Callable]) -> Optional[str]:
    return sse_event("task_complete", _base_step_payload(event, session_id, {
        "step_count": 0,
        "summary": "",
    }))


def _handle_compaction_start(event: dict, session_id: str, cache_lookup: Optional[Callable]) -> Optional[str]:
    return sse_event("content", {
        "content": COMPACTION_START_MSG,
        "session_id": session_id,
    })


def _handle_compaction_end(event: dict, session_id: str, cache_lookup: Optional[Callable]) -> Optional[str]:
    return sse_event("content", {
        "content": COMPACTION_END_MSG,
        "session_id": session_id,
    })


_EVENT_HANDLERS: dict[str, Callable[[dict, str, Optional[Callable]], Optional[str]]] = {
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

    Returns:
        SSE-formatted string or None if unhandled
    """
    event_type = event.get("type", "")
    handler = _EVENT_HANDLERS.get(event_type)
    if handler is None:
        return None
    return handler(event, session_id, cache_lookup)
