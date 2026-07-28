"""Python bridge to Pi (earendil-works/pi) RPC mode.

Spawns Pi as a subprocess and communicates via JSON-RPC over stdin/stdout.
Supports both request/response and streaming event patterns.
Also owns tool dispatch logic for the Pi extension's HTTP callback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from pydantic import BaseModel

from app.utils.sse import sse_event
from app.services.tool_dispatch_service import ToolDispatchService

logger = logging.getLogger(__name__)

# Feature flag
USE_NEW_AGENT = os.getenv("USE_NEW_AGENT", "").lower() in ("true", "1", "yes")

# Pi RPC entry point
PI_RPC_ENTRY = Path(__file__).parent.parent.parent / "vendor" / "pi" / "packages" / "coding-agent" / "dist" / "rpc-entry.js"

# Default session directory
DEFAULT_SESSION_DIR = Path(__file__).parent.parent.parent / ".pi" / "sessions"

# Timeout constants (seconds)
# CONFIG-04: 之前硬编码在代码中，运维调参需改代码重发。现改为从环境变量读取，
# 括号内为默认值，保持原行为。数值非法时回退默认值并告警（避免单次拼写错误
# 导致整个 Pi bridge 启动失败）。
def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "CONFIG-04: ignoring invalid %s=%r, using default %s", name, raw, default,
        )
        return default


PI_RPC_TIMEOUT = _env_float("PI_RPC_TIMEOUT", 300.0)        # _send_request 等待 Pi 响应的上限
PI_EVENT_DRAIN_TIMEOUT = _env_float("PI_EVENT_DRAIN_TIMEOUT", 2.0)  # prompt() 非流式模式下 drain event queue 的单次超时
PI_EVENT_STREAM_TIMEOUT = _env_float("PI_EVENT_STREAM_TIMEOUT", 30.0)  # stream_prompt 等待下一个 event 的超时
PI_STARTUP_READY_TIMEOUT = _env_float("PI_STARTUP_READY_TIMEOUT", 10.0)  # start()  readiness check 的总超时

# SSE content strings for compaction events.
# TODO: Replace with i18n-aware strings when the frontend supports localized SSE events.
# 当前硬编码中文，因为 compaction 事件仅用于 backend 日志 + 前端 content SSE，
# 不暴露给最终用户（前端会以 content 事件渲染）。
COMPACTION_START_MSG = "[压缩上下文...]\n"
COMPACTION_END_MSG = "[上下文压缩完成]\n"


# ── Pi tool dispatch models (owned by bridge, not pi_tools route) ──

class PiToolRequest(BaseModel):
    """Request from Pi extension to execute a GIS tool."""
    toolCallId: str
    name: str
    arguments: dict[str, Any] = {}
    sessionId: Optional[str] = None


class PiToolResponse(BaseModel):
    """Response from tool execution back to Pi extension."""
    toolCallId: str
    content: list[dict[str, Any]]
    details: Any = None
    isError: bool = False


# ── Tool registry (injected at startup, shared by bridge + route) ──

_tool_registry: Optional["ToolRegistry"] = None


def set_tool_registry(registry: "ToolRegistry") -> None:
    """Inject the live ToolRegistry so tool dispatch goes to real GIS tools."""
    global _tool_registry
    _tool_registry = registry
    logger.info(f"[PiBridge] Tool registry injected ({len(registry.list_tools())} tools)")


def get_tool_registry() -> "ToolRegistry":
    """Return the injected registry, raising if not yet initialized."""
    if _tool_registry is None:
        raise PiRpcError("Tool registry not initialized")
    return _tool_registry


# ── Session-keyed dispatch result cache (unified-tool-dispatch 票据 02) ──
#
# dispatch_tool（HTTP 回调）调度一次后把 ToolDispatchResult 按 (session_id, toolCallId)
# 缓存；_handle_tool_execution_end（SSE 适配器）随后按同一 toolCallId 读取，发 step_result
# 时携带 geojson_ref。这样两条适配器共享一次 dispatch，避免 Pi 回传的 result 与服务端
# 视图（已 slim、已落 ref）不一致 —— 此前 Pi 路径从不产生 ref，前端图层挂载失效。
#
# 缓存短命（一个 turn 内）：dispatch 时写入，SSE 读取后即清除，杜绝跨任务泄漏。
_dispatch_result_cache: dict[tuple[str, str], "ToolDispatchResult"] = {}


def cache_dispatch_result(session_id: Optional[str], tool_call_id: str, result: "ToolDispatchResult") -> None:
    """缓存一次 dispatch 的结果，供 SSE 适配器按 toolCallId 读取。"""
    key = (session_id or "", tool_call_id)
    _dispatch_result_cache[key] = result


def get_cached_dispatch_result(session_id: Optional[str], tool_call_id: str) -> "Optional[ToolDispatchResult]":
    """读取并弹出缓存的 dispatch 结果（读后即清，单次消费）。"""
    key = (session_id or "", tool_call_id)
    return _dispatch_result_cache.pop(key, None)


def _clear_session_dispatch_cache(session_id: str) -> None:
    """清空某 session 的所有缓存 dispatch 结果（新 turn 开始时调用）。"""
    sid = session_id or ""
    for key in [k for k in _dispatch_result_cache if k[0] == sid]:
        del _dispatch_result_cache[key]


async def dispatch_tool(request: PiToolRequest) -> PiToolResponse:
    """Dispatch a GIS tool call via ToolDispatchService, cache the result for the SSE adapter.

    Pi 边界安全检查（工具存在性、tier>=3 拒绝）保留在此处——它们是 Pi 路径特有的
    守卫（candidate #5，本批工作 Out of Scope），不属于统一调度的核心职责。
    实际调度（ref 存储、自愈、广播、去重）全部委托给 ToolDispatchService。

    HTTP 回调适配器：把 service 返回的 llm_payload 翻译成 PiToolResponse.content。
    结果按 (session_id, toolCallId) 缓存，供 SSE 适配器读取并发携带 geojson_ref 的 step_result。
    """
    registry = get_tool_registry()
    tool_name = request.name

    # Validate tool exists
    available = set(registry.list_tools())
    if tool_name not in available:
        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=[{
                "type": "text",
                "text": (
                    f"Tool '{tool_name}' not found. "
                    f"Available tools: {', '.join(sorted(available)[:20])}"
                ),
            }],
            isError=True,
        )

    # 审计 SEC-01/SEC-02：拒绝 tier>=3 工具（如 create_new_skill = RCE）。
    # Pi 扩展是半信任的（共享密钥保护），但仍不应能触发写盘 + exec_module。
    tier = registry.metadata(tool_name).get("tier", 1)
    if tier >= 3:
        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=[{
                "type": "text",
                "text": f"Tool '{tool_name}' is tier-{tier} (destructive) and cannot be dispatched via Pi bridge.",
            }],
            isError=True,
        )

    # 统一调度：委托给 ToolDispatchService（票据 01 引入）。
    # executed_tools 复用一个 session 级 set，让重复调用拦截在 service 内生效。
    service = ToolDispatchService(registry=registry)
    tc = {"id": request.toolCallId, "function": {"name": tool_name, "arguments": request.arguments or {}}}
    executed = _session_executed_sets.setdefault(request.sessionId or "", set())
    result = await service.dispatch(tc, request.sessionId or "", executed)

    # 缓存供 SSE 适配器读取
    cache_dispatch_result(request.sessionId, request.toolCallId, result)

    return PiToolResponse(
        toolCallId=request.toolCallId,
        content=[{"type": "text", "text": result.llm_payload}],
        details=result.raw_result,
        isError=(result.status == "error"),
    )


# 每个 session 的已执行工具集合，供重复调用拦截（service 接受外部 set）。
_session_executed_sets: dict[str, set[tuple[str, str]]] = {}



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
        self._stderr_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        # 审计 AGENT-05：Pi 进程死亡后标记为 True，让 _use_pi_bridge() 能回退
        self._process_died = False
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
        # 审计 SEC-01：注入共享密钥，Pi 扩展的 HTTP 回调用它调 /pi-tools/execute
        from app.api.routes.pi_tools import get_bridge_secret
        env["WEBGIS_BRIDGE_SECRET"] = get_bridge_secret()
        env["WEBGIS_API_BASE"] = env.get("WEBGIS_API_BASE", "http://127.0.0.1:8000")

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

        # Start reader tasks for stdout and stderr to avoid OS pipe deadlock
        self._reader_task = asyncio.create_task(self._read_responses())
        self._stderr_task = asyncio.create_task(self._read_stderr())

        # Yield to let the reader task start (avoid race where _send_request writes
        # to stdin before the reader is ready to consume the response).
        await asyncio.sleep(0)

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

        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None

    async def _read_stderr(self) -> None:
        """Read and log stderr lines from Pi subprocess to prevent OS pipe deadlock."""
        try:
            while self._process and self._process.stderr:
                line = await asyncio.get_running_loop().run_in_executor(None, self._process.stderr.readline)
                if not line:
                    break
                line = line.strip()
                if line:
                    logger.debug(f"[PiStderr] {line[:200]}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"[PiStderr] Reader exited: {e}")

    async def _read_responses(self) -> None:
        """Read responses and events from Pi stdout.

        审计 AGENT-05：之前 Pi 进程退出时 readline 返回 ""，循环 break 但
        所有 _pending_requests 的 future 永远不会被 resolve → 调用方挂 300s。
        现在退出时主动 fail 所有 pending 请求。
        """
        try:
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
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[PiBridge] Reader task crashed: {e}", exc_info=True)
        finally:
            # Pi 进程退出 / reader 异常 → fail 所有 pending 请求，避免 300s 挂起
            self._fail_all_pending("Pi process exited or reader stopped")
            # 标记 bridge 为不可用
            self._process_died = True
            logger.error("[PiBridge] Pi subprocess exited; bridge is now unavailable until restart")

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

    def _fail_all_pending(self, reason: str) -> None:
        """审计 AGENT-05：Pi 退出时主动 fail 所有 pending future。"""
        for rid, future in list(self._pending_requests.items()):
            if not future.done():
                future.set_exception(PiRpcError(reason))
        self._pending_requests.clear()

    async def _send_request(self, command: str, data: Optional[dict] = None) -> Any:
        """Send a request to Pi and wait for response.

        审计 BUG-02：之前 stdin.write + flush 是同步阻塞调用，在 async 路径里
        会卡住事件循环（所有并发协程被冻结）。现在用 run_in_executor 把
        write+flush 放到线程池，不阻塞事件循环。
        """
        if self._process is None or self._process.stdin is None:
            raise PiRpcError("Pi process not started")

        self._request_counter += 1
        request_id = str(self._request_counter)

        # 审计 AGENT-02：Pi 的 handleCommand 读扁平字段 (command.message,
        # command.provider, command.modelId 等)，不拆 command.data。
        # 之前嵌套在 data 里导致 Pi 收到 undefined 字段。
        request = {"id": request_id, "type": command}
        if data is not None:
            request.update(data)

        future = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = future

        try:
            line = json.dumps(request) + "\n"

            def _write_and_flush():
                self._process.stdin.write(line)
                self._process.stdin.flush()

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _write_and_flush)
            result = await asyncio.wait_for(future, timeout=PI_RPC_TIMEOUT)
            return result
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise PiRpcError(f"Pi request timeout: {command}")
        except (BrokenPipeError, OSError) as e:
            self._pending_requests.pop(request_id, None)
            raise PiRpcError(f"Pi pipe error: {e}")

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

        # 新 turn 开始：清空该 session 的重复调用拦截集合与 dispatch 结果缓存。
        _session_executed_sets.pop(self._session_id, None)
        _clear_session_dispatch_cache(self._session_id)

        async with self._lock:
            await self._send_request("prompt", data)

        # Drain events from the queue (non-streaming mode)
        content_parts: list[str] = []
        while True:
            try:
                event = await asyncio.wait_for(self._event_queue.get(), timeout=PI_EVENT_DRAIN_TIMEOUT)
                if event.get("type") == "agent_end":
                    break
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

        # 新 turn 开始：清空该 session 的重复调用拦截集合与 dispatch 结果缓存。
        # 重复拦截语义是「同一 turn 内」—— 跨 turn 用户重发同一问题是合法的。
        _session_executed_sets.pop(self._session_id, None)
        _clear_session_dispatch_cache(self._session_id)

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
        "compaction_start": "_handle_compaction_start",
        "compaction_end": "_handle_compaction_end",
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
        """SSE 适配器：读缓存的 dispatch 结果发 step_result / step_error。

        unified-tool-dispatch 票据 02：此前本方法从 Pi 事件 payload 取 result 再 slim，
        从不携带 geojson_ref（因为 dispatch 没存 ref），导致前端图层挂载失效。
        现在读 dispatch_tool 缓存的 ToolDispatchResult —— 服务端视图（已 slim、已落 ref）
        才是前端需要的真相。缓存未命中时退化到旧行为（不崩溃，但不带 geojson_ref）。
        """
        tool_name = event.get("toolName", "")
        tool_call_id = event.get("toolCallId", "")
        cached = get_cached_dispatch_result(self._session_id, tool_call_id)

        if cached is not None:
            if cached.status == "error":
                return sse_event("step_error", self._base_step_payload(event, {
                    "tool": tool_name,
                    "error": cached.error_msg or "",
                }))
            # ok / repeated：用服务端 slim_event + geojson_ref
            payload = self._base_step_payload(event, {
                "tool": tool_name,
                "result": cached.slim_event,
            })
            if cached.geojson_ref:
                payload["geojson_ref"] = cached.geojson_ref
            return sse_event("step_result", payload)

        # 缓存未命中（Pi 重复回传 / dispatch 未走 service 路径）：退化到旧行为
        result = event.get("result", {})
        is_error = event.get("isError", False)
        logger.warning(
            f"[PiBridge] dispatch cache miss for toolCallId={tool_call_id} "
            f"(session={self._session_id}); falling back to Pi-echoed result without geojson_ref"
        )
        if is_error:
            error_msg = self._extract_error_text(result)
            return sse_event("step_error", self._base_step_payload(event, {
                "tool": tool_name,
                "error": error_msg,
            }))
        try:
            from app.services.tool_dispatch_service import slim_event_result
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

    def _handle_compaction_start(self, event: dict) -> Optional[str]:
        return sse_event("content", {
            "content": COMPACTION_START_MSG,
            "session_id": self._session_id,
        })

    def _handle_compaction_end(self, event: dict) -> Optional[str]:
        return sse_event("content", {
            "content": COMPACTION_END_MSG,
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
        """Extract error text from a tool result, sanitized for SSE output.

        审计 BUG-16：原始实现直接 str(result)，会把内部文件系统绝对路径
        （/app/data/...、/usr/src/app/...）、堆栈片段等泄露给前端 SSE，
        便于攻击者做路径侦察。现在统一走 _sanitize_for_client：
          1. 复用 app.utils.security.sanitize_error_msg（脱敏 DB/API 凭证）；
          2. 额外把绝对路径替换成 <path>；
          3. 截断到 500 字符，避免单条 error 事件撑爆 SSE / 日志。
        """
        if isinstance(result, dict):
            content = result.get("content", [])
            if isinstance(content, list) and content:
                raw = content[0].get("text", str(result))
            else:
                raw = result.get("message", str(result))
        else:
            raw = str(result)
        return PiBridge._sanitize_for_client(raw)

    @staticmethod
    def _sanitize_for_client(text: str) -> str:
        """Sanitize an error string for safe emission to clients/SSE."""
        try:
            from app.utils.security import sanitize_error_msg
            text = sanitize_error_msg(text)
        except Exception:  # noqa: BLE001 — 工具层不应因脱敏失败而抛
            pass
        text = PiBridge._redact_paths(text)
        if len(text) > 500:
            text = text[:500] + "...(truncated)"
        return text

    @staticmethod
    def _redact_paths(text: str) -> str:
        """Replace filesystem absolute paths with a placeholder.

        匹配 Unix 绝对路径（/foo/bar）与 Windows 盘符路径（C:\\\\foo\\\\bar），
        避免把部署目录结构泄露给前端。lookbehind 排除：
          - ':' / '/' — URL scheme（https://...）和路径中段，避免把 URL 误伤；
          - '.' — 相对路径（./data/x.geojson），它们常是用户可见的输入参数；
          - word char — 避免吞掉标识符中段。
        """
        # Unix 绝对路径：至少两段 /word/word...
        text = re.sub(r'(?<![:\w./])/(?:[\w.-]+/){1,}[\w.-]+', '<path>', text)
        # Windows 盘符路径：C:\\foo\\bar
        text = re.sub(r'[A-Za-z]:\\\\(?:[^\\\\\s]+\\\\)+[^\\\\\s]+', '<path>', text)
        return text


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
