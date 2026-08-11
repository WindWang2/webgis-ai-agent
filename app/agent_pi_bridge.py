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
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from pydantic import BaseModel

from app.utils.sse import sse_event, sse_event_type
from app.services.chat.pi_event_mapper import map_event_to_sse, _extract_text_from_event
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

# transport goal B-P1-3: how often to emit an SSE keepalive comment when Pi is
# silent (long GIS tool, compaction, slow first token). Keeps the connection
# alive through proxies/LBs with shorter idle timeouts than the chat-SSE
# nginx budget (3600s) and tells the browser the turn is still progressing.
PI_HEARTBEAT_INTERVAL = _env_float_drain("PI_HEARTBEAT_INTERVAL", 8.0)

# Max CONTINUOUS silence before a turn is declared stalled. Previously 30s,
# which false-failed any healthy turn whose tool phase produced no SSE event
# for >30s (the tool kept running server-side while the user saw an error +
# retried → duplicate execution). With heartbeats the connection stays alive,
# so this now only needs to catch a true Pi hang; bumped to 180s to tolerate
# long toolchains (still well under PI_RPC_TIMEOUT=300s that bounds one RPC).
# Operators may tune via the env var.
PI_EVENT_STREAM_TIMEOUT = _env_float_drain("PI_EVENT_STREAM_TIMEOUT", 180.0)


# ── V3: turn_id 铸造 + step_result SSE 注入（Harness–Map Interaction Closed Loop）─
#
# turn_id 形如 ``turn-<uuid4hex[:12]>``，每个 Pi turn（stream_prompt）铸一个，
# 显式透传给 harness 记录与 step_result SSE 负载。绝不用全局 set_correlation ——
# 单例 harness 跨 session 累积，全局 correlation 在并发/串行交错的 session 间
# 会互相污染。
TURN_ID_PREFIX = "turn-"


def _mint_turn_id() -> str:
    """铸一个本 turn 唯一的 correlation id。"""
    return f"{TURN_ID_PREFIX}{uuid.uuid4().hex[:12]}"


def _inject_turn_id(sse_str: str, turn_id: str) -> str:
    """把 turn_id 写入 step_result SSE 负载（additive 字段）。

    pi_event_mapper 属其它 slice，不越界修改；此处仅在 bridge 层对映射结果做
    防御性改写：只处理 step_result 事件，保留原有的 event:/id: 行与编号语义
    （重建时手动改 data 行，避免 sse_event() 重新消耗 DUP-1 的 id 计数器）。
    """
    if not turn_id or sse_event_type(sse_str) != "step_result":
        return sse_str
    lines = sse_str.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("data: "):
            try:
                payload = json.loads(line[len("data: "):])
            except (TypeError, ValueError):
                return sse_str
            if isinstance(payload, dict):
                payload["turn_id"] = turn_id
                lines[i] = "data: " + json.dumps(payload, ensure_ascii=False)
            break
    return "\n".join(lines)


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
    # 防御上限：turn 末清理兜底正常路径，此上限兜住"无后续 turn"的病态窗口
    if len(_dispatch_result_cache) > 128:
        _dispatch_result_cache.pop(next(iter(_dispatch_result_cache)))


def get_cached_dispatch_result(tool_call_id: str) -> "Optional[ToolDispatchResult]":
    """读取并弹出缓存的 dispatch 结果（读后即清，单次消费）。"""
    return _dispatch_result_cache.pop(tool_call_id, None)


def _clear_dispatch_cache() -> None:
    """清空所有缓存的 dispatch 结果（新 turn 开始时调用）。"""
    _dispatch_result_cache.clear()


def _cleanup_turn_state(turn_sid: str) -> None:
    """Turn-end cleanup: dedup sets + dispatch cache.

    The Pi extension posts tool dispatches with sessionId=None, so every
    dispatch lands in the ``""`` bucket of _session_executed_sets; it must be
    popped here (alongside the real sid) or it grows by one (tool, args) tuple
    per dispatch across all sessions forever. Dispatch results written by the
    HTTP callback but never consumed by the SSE adapter (client disconnected /
    turn aborted) are dropped too — safe because turns are strictly serial and
    the mapper only consumes within the turn.
    """
    _session_executed_sets.pop(turn_sid, None)
    _session_executed_sets.pop("", None)
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
    # 防御上限：turn 末清理兜底正常路径，此上限兜住跨会话无 turn 的病态累积
    if len(_session_executed_sets) > 128:
        _session_executed_sets.pop(next(iter(_session_executed_sets)))

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
        # V3: 记录本次 dispatch 发出的地图动作（issued 侧证据）。仅 status=="ok" ——
        # error/repeated 不产生可执行的地图命令。turn_id 在此不可得（HTTP 回调无
        # turn 上下文）；turn 级 correlation 由前端 ack 的 correlation 侧补齐。
        if result.status == "ok":
            for ma in result.map_actions:
                _harness.record_map_action_issued(
                    session_id=request.sessionId or "",
                    tool_call_id=request.toolCallId,
                    turn_id="",
                    action_id=ma["action_id"],
                    command=ma["command"],
                    requested=ma["requested"],
                )
        is_error = result.status == "error"
        # HARNESS-V2: forward real MapSpec mutation evidence (is_compiled /
        # success / warnings / checkpoint_id) from raw_result so the validity
        # ladder isn't starved in production. Slim to evidence fields only — the
        # full mapspec is fetched via fetch-on-demand, never logged wholesale.
        ev = {"status": result.status, "llm_payload_len": len(result.llm_payload)}
        raw = result.raw_result if isinstance(result.raw_result, dict) else {}
        for k in ("success", "is_compiled", "warnings", "checkpoint_id", "message", "correction_hint"):
            if k in raw:
                ev[k] = raw[k]
        event = ToolCallEvent(
            tool_call_id=request.toolCallId,
            tool_name=request.name,
            arguments=request.arguments or {},
            duration_ms=duration_ms,
            is_error=is_error,
            # P1 fix: truncate to a short message rather than the full payload.
            error_msg=(result.llm_payload[:200] if is_error else ""),
            result=ev,
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
# V2（HARNESS-V2）：注入真实证据 resolver —— ref 解析走 SessionStore，
# MapSpec 校验走真实 validate()，杜绝"没报错=100%有效"的假成功。
_harness: Optional[PiAgentHarness] = None
if os.getenv("PI_HARNESS_ENABLED", "").lower() in ("true", "1", "yes"):
    try:
        from app.services.session_data import session_data_manager as _sdm
        from app.services.mapspec.coordinator import validate as _validate_mapspec
        from app.lib.harness.ref_resolver import make_session_store_resolver

        _harness = PiAgentHarness(
            session_id="production",
            ref_resolver=make_session_store_resolver(_sdm),
            mapspec_validator=_validate_mapspec,
        )
        logger.info("[PiBridge] Evaluation harness V2 enabled (real SessionStore ref resolver)")
    except Exception as _harness_err:  # noqa: BLE001 - never block startup on telemetry
        logger.warning(f"[PiBridge] Harness V2 wiring failed, degrading to no harness: {_harness_err}")
        _harness = None


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

    async def _abort_on_disconnect(self, turn_sid: str) -> None:
        """Send the abort RPC after a client disconnect (transport goal B-P0-1).

        Without this, a client disconnect (page close / new send / session
        switch) leaves the Pi subprocess generating tokens and — critically —
        executing GIS tools via the ``/pi-tools/execute`` HTTP callback (DB/ref
        writes, possibly destructive ops) against a session the user has
        already left. ``abort()`` is lock-free by design (see its docstring) so
        it is safe to call from the turn's finally while the lock is still
        held. Best-effort: the vendor must honour the abort; a failure here
        must never raise into the generator-cleanup path.
        """
        try:
            await self.abort()
            logger.info("[PiBridge] abort sent on client disconnect (turn=%s)", turn_sid)
        except Exception as e:  # noqa: BLE001 — abort failure must not break cleanup
            logger.warning("[PiBridge] abort-on-disconnect failed (turn=%s): %s", turn_sid, e)

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
                    text = _extract_text_from_event(event)
                    if text:
                        content_parts.append(text)
                except asyncio.TimeoutError:
                    break
            # Best-effort: drain any leftover events so the next turn starts clean.
            await self._drain_remaining_turn_events()
        finally:
            _cleanup_turn_state(turn_sid)
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

        # V3: 每个 Pi turn 铸一个 turn_id，显式透传给 harness 记录与 SSE 负载
        # （绝不用全局 set_correlation —— 单例 harness 跨 session 累积会互相污染）。
        turn_id = _mint_turn_id()

        # Hold the lock across send + drain + cleanup so turns are strictly
        # serial on the singleton bridge (Pi processes one prompt at a time).
        # try/finally ensures a client-disconnect GeneratorExit/CancelledError
        # at the yield still releases the lock, drains residual events, AND
        # (transport goal B-P0-1) sends an abort RPC so Pi stops generating
        # tokens and executing tools against an abandoned session.
        # Abort deliberately bypasses this lock (see abort()).
        await self._lock.acquire()
        cancelled = False
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
                    "turn_id": turn_id,
                    "error": str(e),
                })
                yield sse_event("done", {"session_id": turn_sid})
                return

            # Stream events from Pi
            yield sse_event("task_start", {"task_id": turn_sid, "session_id": turn_sid, "turn_id": turn_id})

            timed_out = False
            silence_seconds = 0.0
            while True:
                try:
                    event = await asyncio.wait_for(
                        self._rpc.events.get(), timeout=PI_HEARTBEAT_INTERVAL
                    )
                    silence_seconds = 0.0
                    sse = map_event_to_sse(event, turn_sid, cache_lookup=get_cached_dispatch_result)
                    if _harness is not None:
                        # V3: 给原始 SSE 事件记录补 turn/run correlation（显式透传）。
                        _harness.record_sse_event({
                            **event, "run_id": turn_sid, "turn_id": turn_id,
                        })
                    if sse:
                        # V3: step_result 负载加 additive turn_id 字段（前端 ack 时
                        # 通过 correlation 回传，闭环才完整）。
                        yield _inject_turn_id(sse, turn_id)
                    if event.get("type") == "agent_end":
                        break
                except asyncio.TimeoutError:
                    silence_seconds += PI_HEARTBEAT_INTERVAL
                    if silence_seconds >= PI_EVENT_STREAM_TIMEOUT:
                        # True stall: no Pi event for the whole stall budget.
                        timed_out = True
                        break
                    # Keepalive: an SSE comment line (``: ...``) is ignored by
                    # the client parser and never enters chat history or the
                    # LLM context, but it produces bytes on the wire so proxies
                    # and browsers see activity and don't drop the connection.
                    yield ": keepalive\n\n"

            if timed_out:
                yield sse_event("error", {
                    "session_id": turn_sid,
                    "error": f"Pi agent stalled — no events for {int(PI_EVENT_STREAM_TIMEOUT)}s. The agent may be stuck; please retry.",
                })

            yield sse_event("done", {"session_id": turn_sid})
        except (asyncio.CancelledError, GeneratorExit):
            # Client disconnected (page close / new send / session switch /
            # network drop). Re-raise after flagging so the finally sends the
            # abort RPC — otherwise Pi keeps running against an abandoned turn.
            cancelled = True
            raise
        finally:
            if cancelled:
                # Tell Pi to stop generating tokens / executing tools. Awaiting
                # inline (shielded + bounded) rather than fire-and-forget so no
                # detached task retains the bridge (transport goal leak test).
                # On GeneratorExit (aclose) the driving task is not cancelled, so
                # this completes normally; on CancelledError the shield keeps the
                # abort running while wait_for returns promptly. A failure or
                # timeout here must never raise into generator cleanup.
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._abort_on_disconnect(turn_sid)),
                        timeout=5.0,
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                except Exception as e:  # noqa: BLE001
                    logger.warning("[PiBridge] abort-on-disconnect failed (turn=%s): %s", turn_sid, e)
            # Drain any leftover events so a timeout/disconnect doesn't poison
            # the next turn. (On a normal agent_end the queue is already empty;
            # this is a no-op there.)
            await self._drain_remaining_turn_events()
            # Turn-end cleanup: drop this turn's dedup sets (incl. the "" bucket
            # where sessionId-less Pi dispatches land) and orphaned dispatch
            # results (written by the HTTP callback, never consumed after a
            # disconnect). Without this they accumulate across sessions.
            _cleanup_turn_state(turn_sid)
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
