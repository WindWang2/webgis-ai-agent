"""Python bridge to Pi (earendil-works/pi) RPC mode.

Spawns Pi as a subprocess and communicates via JSON-RPC over stdin/stdout.
Supports both request/response and streaming event patterns.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from app.utils.sse import sse_event

logger = logging.getLogger(__name__)

# Pi RPC entry point
PI_RPC_ENTRY = Path(__file__).parent.parent.parent / "vendor" / "pi" / "packages" / "coding-agent" / "dist" / "rpc-entry.js"

# Default session directory
DEFAULT_SESSION_DIR = Path(__file__).parent.parent.parent / ".pi" / "sessions"

# Timeout constants (seconds)
# 审计: 之前硬编码在代码中，提取为常量便于运维调参。
PI_RPC_TIMEOUT = 300.0        # _send_request 等待 Pi 响应的上限
PI_EVENT_DRAIN_TIMEOUT = 2.0  # prompt() 非流式模式下 drain event queue 的单次超时
PI_EVENT_STREAM_TIMEOUT = 30.0  # stream_prompt 等待下一个 event 的超时
PI_STARTUP_READY_TIMEOUT = 10.0  # start()  readiness check 的总超时

# SSE content strings for compaction events.
# TODO: Replace with i18n-aware strings when the frontend supports localized SSE events.
# 当前硬编码中文，因为 compaction 事件仅用于 backend 日志 + 前端 content SSE，
# 不暴露给最终用户（前端会以 content 事件渲染）。
COMPACTION_START_MSG = "[压缩上下文...]\n"
COMPACTION_END_MSG = "[上下文压缩完成]\n"


class PiRpcError(Exception):
    """Error from Pi RPC."""
    pass


class PiBridge:
    """Bridge to Pi agent via RPC mode.

    Spawns Pi as a subprocess and communicates via JSON-RPC protocol.
    Supports:
    - Request/response: get_state, set_model, get_available_models, get_messages
    - Streaming: prompt (yields SSE events as Pi processes)
    """

    def __init__(
        self,
        pi_rpc_entry: Optional[Path] = None,
        session_dir: Optional[Path] = None,
        cwd: Optional[Path] = None,
        extension_paths: Optional[list[str]] = None,
    ):
        self._pi_rpc_entry = pi_rpc_entry or PI_RPC_ENTRY
        self._session_dir = session_dir or DEFAULT_SESSION_DIR
        self._cwd = cwd or Path.cwd()
        self._extension_paths = extension_paths or []
        self._process: Optional[subprocess.Popen] = None
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._request_counter = 0
        self._reader_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._session_id: str = ""

    async def start(self) -> None:
        """Start the Pi subprocess."""
        if self._process is not None:
            return

        self._session_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PI_SESSION_DIR"] = str(self._session_dir)
        env["PI_OFFLINE"] = "1"
        env["PI_SKIP_VERSION_CHECK"] = "1"

        # Build CLI args with --extension flags for each extension path
        args = ["node", str(self._pi_rpc_entry), "--mode", "rpc", "--no-session"]
        for ext_path in self._extension_paths:
            args.extend(["--extension", str(ext_path)])

        self._process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(self._cwd),
            text=True,
        )

        # Start reader task
        self._reader_task = asyncio.create_task(self._read_responses())

        # Wait for Pi to initialize by polling get_state until it responds
        try:
            await asyncio.wait_for(self._wait_for_ready(), timeout=PI_STARTUP_READY_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(f"[PiBridge] Pi did not become ready within {PI_STARTUP_READY_TIMEOUT}s, continuing anyway")

    async def _wait_for_ready(self) -> None:
        """Poll get_state until Pi responds or the reader task ends."""
        while True:
            try:
                await self._send_request("get_state", {})
                return  # Pi is ready
            except PiRpcError:
                await asyncio.sleep(0.2)

    async def stop(self) -> None:
        """Stop the Pi subprocess."""
        if self._process is None:
            return

        try:
            self._process.terminate()
            await asyncio.sleep(0.5)
            if self._process.poll() is None:
                self._process.kill()
        finally:
            self._process = None

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

    async def _read_responses(self) -> None:
        """Read responses and events from Pi stdout."""
        while self._process and self._process.stdout:
            line = await asyncio.get_running_loop().run_in_executor(None, self._process.stdout.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                # Request response: has "type": "response" and "id"
                if obj.get("type") == "response" and obj.get("id"):
                    await self._handle_response(obj)
                # AgentSessionEvent: has "type" but no "id"
                elif "type" in obj and "id" not in obj:
                    await self._event_queue.put(obj)
            except json.JSONDecodeError:
                logger.warning(f"[PiBridge] Invalid JSON: {line[:200]}")

    async def _handle_response(self, response: dict) -> None:
        """Handle a request response from Pi."""
        request_id = response.get("id")
        if not request_id:
            return
        future = self._pending_requests.pop(request_id, None)
        if future and not future.done():
            if response.get("success"):
                future.set_result(response.get("data"))
            else:
                future.set_exception(PiRpcError(response.get("error", "Unknown error")))

    async def _send_request(self, command: str, data: Optional[dict] = None) -> Any:
        """Send a request to Pi and wait for response."""
        if self._process is None or self._process.stdin is None:
            raise PiRpcError("Pi process not started")

        self._request_counter += 1
        request_id = str(self._request_counter)

        request = {"id": request_id, "type": command}
        if data is not None:
            request["data"] = data

        future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future

        try:
            line = json.dumps(request) + "\n"
            self._process.stdin.write(line)
            self._process.stdin.flush()
            result = await asyncio.wait_for(future, timeout=PI_RPC_TIMEOUT)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise PiRpcError(f"Pi request timeout: {command}")

    # ── Public API ──────────────────────────────────────────────

    async def prompt(self, message: str, session_id: Optional[str] = None) -> dict:
        """Send a prompt to Pi agent (non-streaming).

        Args:
            message: User message
            session_id: Optional session ID

        Returns:
            Response dict with session_id and content

        Raises:
            PiRpcError: If the Pi agent returns an error or the request fails.
        """
        data: dict[str, Any] = {"message": message}
        if session_id:
            data["sessionId"] = session_id
            self._session_id = session_id

        async with self._lock:
            await self._send_request("prompt", data)

        # Drain events from the queue (non-streaming mode)
        content_parts: list[str] = []
        while True:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=PI_EVENT_DRAIN_TIMEOUT)
                text = self._extract_text_from_event(event)
                if text:
                    content_parts.append(text)
            except asyncio.TimeoutError:
                break

        return {
            "sessionId": self._session_id,
            "content": "".join(content_parts),
        }

    async def stream_prompt(
        self, message: str, session_id: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """Stream a prompt to Pi agent, yielding SSE events.

        Args:
            message: User message
            session_id: Optional session ID

        Yields:
            SSE-formatted event strings
        """
        data: dict[str, Any] = {"message": message}
        if session_id:
            data["sessionId"] = session_id
            self._session_id = session_id

        # Send prompt command
        try:
            async with self._lock:
                await self._send_request("prompt", data)
        except PiRpcError as e:
            logger.error(f"[PiBridge] stream_prompt send failed: {e}")
            yield sse_event("task_error", {
                "task_id": session_id or "",
                "session_id": self._session_id,
                "error": str(e),
            })
            yield sse_event("done", {"session_id": self._session_id})
            return

        # Stream events from Pi
        yield sse_event("task_start", {"task_id": session_id or "", "session_id": self._session_id})

        timed_out = False
        while True:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=PI_EVENT_STREAM_TIMEOUT)
                sse = self._map_event_to_sse(event)
                if sse:
                    yield sse
                if event.get("type") == "agent_end":
                    break
            except asyncio.TimeoutError:
                timed_out = True
                break

        if timed_out:
            yield sse_event("error", {
                "session_id": self._session_id,
                "error": f"Pi agent response timed out ({int(PI_EVENT_STREAM_TIMEOUT)}s). The agent may be processing or stalled.",
            })

        yield sse_event("done", {"session_id": self._session_id})

    async def get_state(self) -> dict:
        """Get current Pi agent state."""
        async with self._lock:
            result = await self._send_request("get_state")
            return result or {}

    async def get_available_models(self) -> list[dict]:
        """Get available models."""
        async with self._lock:
            result = await self._send_request("get_available_models")
            return result.get("models", []) if result else []

    async def set_model(self, provider: str, model_id: str) -> dict:
        """Set the model."""
        async with self._lock:
            result = await self._send_request("set_model", {"provider": provider, "modelId": model_id})
            return result or {}

    async def abort(self) -> dict:
        """Abort current operation."""
        async with self._lock:
            result = await self._send_request("abort")
            return result or {}

    # ── Event mapping ───────────────────────────────────────────

    # Dispatch table: Pi event type → PiBridge handler method name
    _EVENT_HANDLERS: dict[str, str] = {
        "message_update": "_handle_message_update",
        "tool_execution_start": "_handle_tool_execution_start",
        "tool_execution_end": "_handle_tool_execution_end",
        "agent_end": "_handle_agent_end",
        "compaction_start": "_handle_compaction",
        "compaction_end": "_handle_compaction",
    }

    def _map_event_to_sse(self, event: dict) -> Optional[str]:
        """Map Pi AgentSessionEvent to SSE format via dispatch table."""
        event_type = event.get("type", "")
        method_name = self._EVENT_HANDLERS.get(event_type)
        if method_name is None:
            return None
        return getattr(self, method_name)(event)

    def _handle_message_update(self, event: dict) -> Optional[str]:
        assistant_event = event.get("assistantMessageEvent", {})
        event_kind = assistant_event.get("type", "")

        if "text" in event_kind or "thinking" in event_kind:
            content = assistant_event.get("content", "")
            is_reasoning = "thinking" in event_kind
            if content:
                return sse_event("token", {
                    "content": content,
                    "is_reasoning": is_reasoning,
                    "session_id": self._session_id,
                })
        elif "tool_call" in event_kind or "toolcall" in event_kind:
            return sse_event("tool_call", {
                "name": assistant_event.get("name", ""),
                "arguments": assistant_event.get("arguments", ""),
            })
        return None

    def _handle_tool_execution_start(self, event: dict) -> Optional[str]:
        return sse_event("step_start", self._base_step_payload(event, {
            "step_index": 0,  # TODO: derive from actual step metadata when available
            "tool": event.get("toolName", ""),
        }))

    def _handle_tool_execution_end(self, event: dict) -> Optional[str]:
        result = event.get("result", {})
        is_error = event.get("isError", False)
        tool_name = event.get("toolName", "")
        tool_call_id = event.get("toolCallId", "")

        if is_error:
            error_msg = self._extract_error_text(result)
            return sse_event("step_error", self._base_step_payload(event, {
                "tool": tool_name,
                "error": error_msg,
            }))
        else:
            try:
                from app.services.chat.sse_helpers import slim_event_result
                slim = slim_event_result(result)
            except Exception:
                slim = result
            return sse_event("step_result", self._base_step_payload(event, {
                "tool": tool_name,
                "result": slim,
            }))

    def _handle_agent_end(self, event: dict) -> Optional[str]:
        return sse_event("task_complete", self._base_step_payload(event, {
            "step_count": 0,
            "summary": "",
        }))

    def _handle_compaction(self, event: dict) -> Optional[str]:
        is_start = event.get("type") == "compaction_start"
        return sse_event("content", {
            "content": COMPACTION_START_MSG if is_start else COMPACTION_END_MSG,
            "session_id": self._session_id,
        })

    def _base_step_payload(self, event: dict, extra: dict) -> dict:
        """Build base SSE payload with common fields shared across step events."""
        base = {
            "task_id": self._session_id,
            "step_id": event.get("toolCallId", ""),
            "session_id": self._session_id,
        }
        base.update(extra)
        return base

    def _extract_text_from_event(self, event: dict) -> str:
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

    def _extract_error_text(self, result: Any) -> str:
        """Extract error text from a tool result."""
        if isinstance(result, dict):
            content = result.get("content", [])
            if isinstance(content, list) and content:
                return content[0].get("text", str(result))
            return result.get("message", str(result))
        return str(result)


# Global bridge instance
_pi_bridge: Optional[PiBridge] = None


async def get_pi_bridge(extension_paths: Optional[list[str]] = None) -> PiBridge:
    """Get or create the global Pi bridge instance.

    Note: extension_paths is only honored on the first call. Subsequent calls
    ignore the parameter because the bridge singleton is already initialized.
    If you need to change extensions, call shutdown_pi_bridge() first.
    """
    global _pi_bridge
    if _pi_bridge is None:
        _pi_bridge = PiBridge(extension_paths=extension_paths or [])
        await _pi_bridge.start()
    return _pi_bridge


async def shutdown_pi_bridge() -> None:
    """Shutdown the global Pi bridge instance."""
    global _pi_bridge
    if _pi_bridge is not None:
        await _pi_bridge.stop()
        _pi_bridge = None
