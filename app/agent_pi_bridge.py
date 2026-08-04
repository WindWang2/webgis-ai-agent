"""Python bridge to Pi (earendil-works/pi) RPC mode.

Spawns Pi as a subprocess and communicates via JSON-RPC over stdin/stdout.
Supports both request/response and streaming event patterns.
Also owns tool dispatch logic for the Pi extension's HTTP callback.

Architecture-review F3: the subprocess lifecycle + async JSON-RPC multiplexing
(the genuinely deep core, ~150 LOC) is extracted into
:class:`app.services.chat.pi_rpc_client.PiRpcClient`. The event->SSE mapping
is extracted into :func:`app.services.chat.pi_event_mapper.map_event_to_sse`.
This module retains: the singleton + feature flag, the ADR-0022 dispatch-result
cache + the two dispatch adapters (the deliberate rendezvous between the Pi
HTTP-callback and SSE adapters), and the thin ``PiBridge`` orchestrator that
glues the RPC client + mapper together.

ADR-0022 constraint: ``_dispatch_result_cache``, ``_session_executed_sets``,
``dispatch_tool``, ``cache_dispatch_result``, ``get_cached_dispatch_result``,
and ``_clear_dispatch_cache`` stay here. The extracted modules have
zero knowledge of the cache.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from pydantic import BaseModel

from app.utils.sse import sse_event
from app.services.tool_dispatch_service import ToolDispatchService
from app.lib.harness.pi_agent_harness import PiAgentHarness
from app.lib.harness.tool_call_event import ToolCallEvent

logger = logging.getLogger(__name__)

# Feature flag
USE_NEW_AGENT = os.getenv("USE_NEW_AGENT", "").lower() in ("true", "1", "yes")


def _env_float_drain(name: str, default: float) -> float:
    """Env-tunable timeout with safe default (CONFIG-04 pattern, local to the bridge)."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("CONFIG-04: ignoring invalid %s=%r, using default %s", name, raw, default)
        return default


# Timeout constants for prompt/stream event draining (prompt semantics, not RPC-generic).
# These stay in the bridge because they're about draining the event queue into SSE,
# not about the RPC transport itself (which has its own PI_RPC_TIMEOUT in pi_rpc_client).
PI_EVENT_DRAIN_TIMEOUT = _env_float_drain("PI_EVENT_DRAIN_TIMEOUT", 2.0)    # prompt() 非流式 drain 超时
PI_EVENT_STREAM_TIMEOUT = _env_float_drain("PI_EVENT_STREAM_TIMEOUT", 30.0)  # stream_prompt 等待下一个 event 的超时


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


class PiRpcError(Exception):
    """Error from Pi RPC."""
    pass


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


# ── Dispatch result cache (unified-tool-dispatch 票据 02) ──
#
# dispatch_tool（HTTP 回调）调度一次后把 ToolDispatchResult 按 toolCallId 缓存；
# _handle_tool_execution_end（SSE 适配器）随后按同一 toolCallId 读取，发 step_result
# 时携带 geojson_ref。这样两条适配器共享一次 dispatch，避免 Pi 回传的 result 与服务端
# 视图（已 slim、已落 ref）不一致 -- 此前 Pi 路径从不产生 ref，前端图层挂载失效。
#
# 缓存短命（一个 turn 内）：dispatch 时写入，SSE 读取后即清除，杜绝跨任务泄漏。
#
# Keyed by tool_call_id only. The prior (session_id, tool_call_id) key was asymmetric:
# the HTTP callback (`dispatch_tool`) receives sessionId=None (the Pi extension posts
# no sessionId — see app/extensions/webgis-tools/index.mjs), so writes landed under
# ("", tool_call_id); the SSE adapter read with the bridge's current session_id, which
# is non-empty for any real chat turn -> guaranteed cache miss -> step_result lost its
# geojson_ref. Turns are now strictly serial on the singleton bridge (whole-turn lock
# in stream_prompt/prompt), so the session dimension is redundant for correlation.
# ADR-0022: this cache + the two dispatch adapters stay here - they are the
# deliberate rendezvous between the Pi HTTP-callback and SSE adapters.
_dispatch_result_cache: dict[str, "ToolDispatchResult"] = {}


def cache_dispatch_result(tool_call_id: str, result: "ToolDispatchResult") -> None:
    """缓存一次 dispatch 的结果，供 SSE 适配器按 toolCallId 读取。"""
    _dispatch_result_cache[tool_call_id] = result


def get_cached_dispatch_result(tool_call_id: str) -> "Optional[ToolDispatchResult]":
    """读取并弹出缓存的 dispatch 结果（读后即清，单次消费）。"""
    return _dispatch_result_cache.pop(tool_call_id, None)


def _clear_dispatch_cache() -> None:
    """清空所有缓存的 dispatch 结果（新 turn 开始时调用）。"""
    _dispatch_result_cache.clear()


async def dispatch_tool(request: PiToolRequest) -> PiToolResponse:
    """Dispatch a GIS tool call via ToolDispatchService, cache the result for the SSE adapter.

    Pi 边界安全检查（工具存在性、tier>=3 拒绝）保留在此处--它们是 Pi 路径特有的
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

    # P1 fix: time the dispatch, record telemetry on BOTH the success and the
    # exception path (previously an exception skipped recording entirely),
    # and truncate error_msg so a multi-KB llm_payload doesn't flood telemetry.
    t0 = time.monotonic()
    try:
        result = await service.dispatch(tc, request.sessionId or "", executed)
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        if _harness is not None:
            err_msg = str(exc)[:200]
            _harness.record_event(ToolCallEvent(
                tool_call_id=request.toolCallId,
                tool_name=request.name,
                arguments=request.arguments or {},
                duration_ms=duration_ms,
                is_error=True,
                error_msg=err_msg,
                result={},
                session_id=request.sessionId,
            ))
        raise

    duration_ms = int((time.monotonic() - t0) * 1000)

    # 缓存供 SSE 适配器读取（keyed by tool_call_id only — see cache docstring）
    cache_dispatch_result(request.toolCallId, result)

    if _harness is not None:
        is_error = result.status == "error"
        event = ToolCallEvent(
            tool_call_id=request.toolCallId,
            tool_name=request.name,
            arguments=request.arguments or {},
            duration_ms=duration_ms,
            is_error=is_error,
            # P1 fix: truncate to a short message rather than the full payload.
            error_msg=(result.llm_payload[:200] if is_error else ""),
            result={"status": result.status, "llm_payload_len": len(result.llm_payload)},
            session_id=request.sessionId,
        )
        _harness.record_event(event)

    return PiToolResponse(
        toolCallId=request.toolCallId,
        content=[{"type": "text", "text": result.llm_payload}],
        details=result.raw_result,
        isError=(result.status == "error"),
    )


# 每个 session 的已执行工具集合，供重复调用拦截（service 接受外部 set）。
_session_executed_sets: dict[str, set[tuple[str, str]]] = {}


# ── Optional evaluation harness (opt-in via PI_HARNESS_ENABLED=true) ──
_harness: Optional[PiAgentHarness] = None
if os.getenv("PI_HARNESS_ENABLED", "").lower() in ("true", "1", "yes"):
    _harness = PiAgentHarness(session_id="production")
    logger.info("[PiBridge] Evaluation harness enabled for production telemetry")


def get_harness() -> Optional[PiAgentHarness]:
    """Return the active evaluation harness, or None if disabled."""
    return _harness


# ── PiBridge: thin orchestrator (holds PiRpcClient + delegates mapping) ──────


class PiBridge:
    """Bridge to Pi agent via RPC mode.

    Thin orchestrator: holds a :class:`PiRpcClient` (the deep RPC core),
    owns the prompt/stream_prompt turn lifecycle (including the ADR-0022 cache
    clearing), and delegates event->SSE mapping to the pure
    :func:`map_event_to_sse` function.

    The ADR-0022 dispatch-result cache + the two dispatch adapters
    (``dispatch_tool`` HTTP callback + ``_handle_tool_execution_end`` SSE
    adapter) stay at module level above - they are the deliberate rendezvous
    between the two adapters and cannot move.
    """

    def __init__(
        self,
        pi_rpc_entry: Optional[Path] = None,
        session_dir: Optional[Path] = None,
        cwd: Optional[Path] = None,
        extension_paths: Optional[list[str]] = None,
        rpc: Optional["PiRpcClient"] = None,
    ):
        # Lazy import to avoid a circular import: pi_rpc_client.py imports
        # PiRpcError from this module. The bridge creates the client internally
        # by default; tests can inject one.
        if rpc is not None:
            self._rpc = rpc
        else:
            from app.services.chat.pi_rpc_client import PiRpcClient
            self._rpc = PiRpcClient(
                pi_rpc_entry=pi_rpc_entry,
                session_dir=session_dir,
                cwd=cwd,
                extension_paths=extension_paths or [],
            )
        self._lock = asyncio.Lock()

    @property
    def _process_died(self) -> bool:
        """Delegate to the RPC client (back-compat for _use_pi_bridge)."""
        return self._rpc.process_died

    # ── Public API ──────────────────────────────────────────────

    async def start(self) -> None:
        """Start the Pi subprocess (delegates to the RPC client)."""
        await self._rpc.start()

    async def stop(self) -> None:
        """Stop the Pi subprocess (delegates to the RPC client)."""
        await self._rpc.stop()

    async def abort(self) -> dict:
        """Abort the currently-running Pi prompt (fire-and-forget from callers).

        BUG-18 fix re-added: ADR-0031 F3 extraction removed the abort wrapper,
        leaving chat.py:320's `await pi_bridge.abort()` to AttributeError into
        a swallowed `except Exception` — the in-flight prompt kept consuming
        tokens and writing files after session deletion.

        The RPC client's `request("abort")` matches vendor/pi's rpc-mode.js
        `case "abort"` handler. Failures (no process, RPC error) propagate;
        callers wrap in try/except because abort must never block the cleanup
        path that follows.

        Deliberately does NOT acquire ``self._lock``: ``prompt``/``stream_prompt``
        hold the lock across the whole turn (send + drain) to serialize turns
        on the singleton bridge, and an abort that blocked on the lock would
        deadlock against the in-flight turn it is trying to cancel. The
        ``request("abort")`` RPC + ``fail_all_pending`` below provide
        cancellation without the lock.
        """
        result = await self._rpc.request("abort")
        # Cancel any pending request future the client hasn't resolved yet.
        # Without this, an in-flight `prompt` would keep waiting for its
        # response up to the stream timeout (30s) and then emit a generic
        # timeout error to the user instead of a clean cancellation.
        self._rpc.fail_all_pending("abort requested")
        return result or {}

    async def _drain_stale_events(self) -> int:
        """Drop any events left in the shared queue from a prior turn.

        Called at the start of each turn (under ``self._lock``) so that a
        previous turn's residual events — left behind after a consumer timeout
        or client disconnect — cannot be dequeued and attributed to this turn.
        Returns the number of events dropped. Bounded by the queue maxsize so
        a runaway producer can't make this loop unbounded.
        """
        dropped = 0
        last_type = ""
        events = self._rpc.events
        for _ in range(events.maxsize or 1024):
            try:
                event = events.get_nowait()
            except asyncio.QueueEmpty:
                break
            dropped += 1
            last_type = event.get("type", "") if isinstance(event, dict) else ""
        if dropped:
            logger.warning(
                "[PiBridge] drained %d stale event(s) from prior turn before "
                "starting this one (last type=%s); they would otherwise have "
                "been attributed to the new session",
                dropped, last_type,
            )
        return dropped

    async def _drain_remaining_turn_events(self) -> None:
        """Best-effort drain of this turn's leftover events after timeout/disconnect.

        Without this, a timed-out or cancelled stream leaves the turn's
        remaining events (up to ``agent_end``) in the shared queue, where the
        next turn's drain loop would pick them up. Bounded and non-blocking so
        a stuck producer cannot block cleanup.
        """
        events = self._rpc.events
        for _ in range(events.maxsize or 1024):
            try:
                event = events.get_nowait()
            except asyncio.QueueEmpty:
                break
            if isinstance(event, dict) and event.get("type") == "agent_end":
                break

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
        # Turn-scoped session id: attribution uses this local, never the
        # mutable self._session_id field (which a concurrent/preceding turn
        # could overwrite). Pi events carry no session of their own.
        turn_sid = session_id or ""
        data: dict[str, Any] = {"message": message}
        if turn_sid:
            data["sessionId"] = turn_sid

        # 新 turn 开始：清空重复调用拦截集合与 dispatch 结果缓存。
        # 重复拦截语义是「同一 turn 内」-- 跨 turn 用户重发同一问题是合法的。
        _session_executed_sets.pop(turn_sid, None)
        _clear_dispatch_cache()

        # Hold the lock across send + drain so turns are strictly serial on the
        # singleton bridge. Pi processes one prompt at a time, so this matches
        # the real execution model and prevents two turns' events interleaving
        # in the shared queue. Abort bypasses the lock (see abort()).
        await self._lock.acquire()
        try:
            # Drop any residual events from a prior turn before sending, so they
            # cannot be attributed to this turn.
            await self._drain_stale_events()
            await self._rpc.request("prompt", data)

            # Drain events from the queue (non-streaming mode)
            content_parts: list[str] = []
            while True:
                try:
                    event = await asyncio.wait_for(self._rpc.events.get(), timeout=PI_EVENT_DRAIN_TIMEOUT)
                    if event.get("type") == "agent_end":
                        break
                    from app.services.chat.pi_event_mapper import _extract_text_from_event
                    text = _extract_text_from_event(event)
                    if text:
                        content_parts.append(text)
                except asyncio.TimeoutError:
                    break
            # Best-effort: drain any leftover events so the next turn starts clean.
            await self._drain_remaining_turn_events()
        finally:
            self._lock.release()

        return {
            "sessionId": turn_sid,
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
        # Turn-scoped session id: every SSE payload is stamped with this local
        # value, not the mutable self._session_id field. Pi events carry no
        # session of their own, so attribution must come from the request that
        # owns this turn — not bridge instance state a prior turn could have
        # overwritten.
        turn_sid = session_id or ""
        data: dict[str, Any] = {"message": message}
        if turn_sid:
            data["sessionId"] = turn_sid

        # 新 turn 开始：清空重复调用拦截集合与 dispatch 结果缓存。
        # 重复拦截语义是「同一 turn 内」-- 跨 turn 用户重发同一问题是合法的。
        _session_executed_sets.pop(turn_sid, None)
        _clear_dispatch_cache()

        # Hold the lock across send + drain + cleanup so turns are strictly
        # serial on the singleton bridge (Pi processes one prompt at a time).
        # try/finally ensures a client-disconnect GeneratorExit/CancelledError
        # at the yield still releases the lock and drains residual events.
        # Abort deliberately bypasses this lock (see abort()).
        await self._lock.acquire()
        try:
            # Drop residual events from a prior turn so they can't be dequeued
            # and attributed to this session.
            await self._drain_stale_events()

            # Send prompt command
            try:
                await self._rpc.request("prompt", data)
            except PiRpcError as e:
                logger.error(f"[PiBridge] stream_prompt send failed: {e}")
                yield sse_event("task_error", {
                    "task_id": turn_sid,
                    "session_id": turn_sid,
                    "error": str(e),
                })
                yield sse_event("done", {"session_id": turn_sid})
                return

            # Stream events from Pi
            yield sse_event("task_start", {"task_id": turn_sid, "session_id": turn_sid})

            from app.services.chat.pi_event_mapper import map_event_to_sse
            timed_out = False
            while True:
                try:
                    event = await asyncio.wait_for(self._rpc.events.get(), timeout=PI_EVENT_STREAM_TIMEOUT)
                    sse = map_event_to_sse(event, turn_sid, cache_lookup=get_cached_dispatch_result)
                    if _harness is not None:
                        _harness.record_sse_event(event)
                    if sse:
                        yield sse
                    if event.get("type") == "agent_end":
                        break
                except asyncio.TimeoutError:
                    timed_out = True
                    break

            if timed_out:
                yield sse_event("error", {
                    "session_id": turn_sid,
                    "error": f"Pi agent response timed out ({int(PI_EVENT_STREAM_TIMEOUT)}s). The agent may be processing or stalled.",
                })

            yield sse_event("done", {"session_id": turn_sid})
        finally:
            # Drain any leftover events so a timeout/disconnect doesn't poison
            # the next turn. (On a normal agent_end the queue is already empty;
            # this is a no-op there.)
            await self._drain_remaining_turn_events()
            self._lock.release()


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
