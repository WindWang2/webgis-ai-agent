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
import contextlib
from collections import OrderedDict
import copy
from datetime import datetime, timezone
import hashlib
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
from app.services.jobs.cancellation import CancellationToken, use_token
from app.services.tool_dispatch_service import ToolDispatchService, normalize_tool_name
from app.lib.harness.pi_agent_harness import PiAgentHarness
from app.lib.harness.tool_call_event import ToolCallEvent
from app.lib.runtime import context as rt_ctx
from app.lib.runtime.evidence import (
    Outcome,
    TurnEvidence,
    TURN_EVIDENCE,
    bind_turn_evidence,
    emit_turn_summary,
)

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
    turnToken: Optional[str] = None
    # Set only by the authenticated HTTP adapter after token verification.
    # The extension never supplies this field as routing authority.
    verifiedTurnId: Optional[str] = None


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
# dispatch_tool（HTTP 回调）调度一次后把 ToolDispatchResult 按已验证 session 与
# toolCallId 缓存；_handle_tool_execution_end（SSE 适配器）随后按同一键读取，发 step_result
# 时携带 geojson_ref。这样两条适配器共享一次 dispatch，避免 Pi 回传的 result 与服务端
# 视图（已 slim、已落 ref）不一致 -- 此前 Pi 路径从不产生 ref，前端图层挂载失效。
#
# 缓存短命（一个 turn 内）：dispatch 时写入，SSE 读取后即清除，杜绝跨任务泄漏。
#
# The Pi extension carries a signed turn capability. The HTTP route verifies it and
# supplies the immutable turn session, preventing a delayed callback or colliding
# toolCallId from crossing sessions. Direct helper callers can omit session only for
# backward-compatible, unique-key lookup; production always supplies it.
# ADR-0022: this cache + the two dispatch adapters stay here - they are the
# deliberate rendezvous between the Pi HTTP-callback and SSE adapters.
_dispatch_result_cache: dict[tuple[str, str], "ToolDispatchResult"] = {}

# #554 defect 3: ONE long-lived ToolDispatchService shared by every Pi HTTP
# callback dispatch (mirrors the legacy engine's RUN-01 pattern — a fresh
# service per callback carried a fresh instance-level _completed_keys set, so
# a repeat call within the same turn ALWAYS got the "still in flight" message
# even after the first call had long since completed; post-success dedup
# semantics were permanently dead on the Pi path). The bridge is a singleton
# and turns are strictly serial, so a module-level instance is the correct
# scope. The registry is injected once at startup; tests replace it via
# set_tool_registry, so the cache re-keys when the registry object changes.
_dispatch_service: "Optional[ToolDispatchService]" = None
_dispatch_service_registry: "Optional[ToolRegistry]" = None


def _get_shared_dispatch_service() -> "ToolDispatchService":
    """Return the bridge-wide ToolDispatchService (rebuilt only if the
    injected registry was replaced — e.g. per-test set_tool_registry swaps)."""
    global _dispatch_service, _dispatch_service_registry
    registry = get_tool_registry()
    if _dispatch_service is None or _dispatch_service_registry is not registry:
        _dispatch_service = ToolDispatchService(registry=registry)
        _dispatch_service_registry = registry
    return _dispatch_service


def cache_dispatch_result(
    tool_call_id: str,
    result: "ToolDispatchResult",
    session_id: str = "",
) -> None:
    """缓存一次 dispatch 的结果，供 SSE 适配器按 session/toolCallId 读取。"""
    _dispatch_result_cache[(session_id, tool_call_id)] = result
    # 防御上限：turn 末清理兜底正常路径，此上限兜住"无后续 turn"的病态窗口
    if len(_dispatch_result_cache) > 128:
        _dispatch_result_cache.pop(next(iter(_dispatch_result_cache)))


def get_cached_dispatch_result(
    tool_call_id: str,
    session_id: Optional[str] = None,
) -> "Optional[ToolDispatchResult]":
    """读取并弹出缓存的 dispatch 结果（读后即清，单次消费）。"""
    if session_id is not None:
        return _dispatch_result_cache.pop((session_id, tool_call_id), None)
    # Backward-compatible test/helper path. Production always supplies the
    # turn session and therefore never performs this cross-key search.
    matches = [key for key in _dispatch_result_cache if key[1] == tool_call_id]
    if len(matches) != 1:
        return None
    return _dispatch_result_cache.pop(matches[0], None)


def _clear_dispatch_cache() -> None:
    """清空所有缓存的 dispatch 结果（新 turn 开始时调用）。"""
    _dispatch_result_cache.clear()


def _cleanup_turn_state(turn_sid: str) -> None:
    """Turn-end cleanup: dedup sets + dispatch cache.

    Dispatch results written by the HTTP callback but never consumed by the
    SSE adapter (client disconnected / turn aborted) are dropped too. This is
    safe because turns are strictly serial and
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
    tool_name = normalize_tool_name(request.name)
    # The HTTP route verifies a signed turn token and writes its immutable sid
    # here. Direct in-process callers/tests may still supply sessionId. Never
    # fall back to the bridge's mutable active turn: a delayed callback could
    # otherwise mutate the next session.
    session_id = request.sessionId or ""
    if not session_id:
        raise PiRpcError("Pi tool callback has no verified turn session")

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
    # 服务实例按 bridge 复用（#554 缺陷 3）：每回调新建实例会让 _completed_keys
    # 永远为空，同回合内已完成调用的重复调用被误报为"仍在执行中"。
    service = _get_shared_dispatch_service()
    tc = {"id": request.toolCallId, "function": {"name": tool_name, "arguments": request.arguments or {}}}
    executed = _session_executed_sets.setdefault(session_id, set())
    # 防御上限：turn 末清理兜底正常路径，此上限兜住跨会话无 turn 的病态累积
    if len(_session_executed_sets) > 128:
        _session_executed_sets.pop(next(iter(_session_executed_sets)))

    # Runtime observability (W3): recover the active turn's correlation.
    turn_id, run_id, active_session = active_turn_correlation()
    if (
        request.sessionId
        and active_session
        and request.sessionId != active_session
    ):
        turn_id, run_id = None, None
    rt_ev = TURN_EVIDENCE.get(turn_id)

    with contextlib.ExitStack() as _rt_stack:
        _rt_stack.enter_context(rt_ctx.bind_runtime_context(
            turn_id=turn_id, run_id=run_id, session_id=request.sessionId
        ))
        if rt_ev is not None:
            _rt_stack.enter_context(bind_turn_evidence(rt_ev))
        t0 = time.monotonic()
        try:
            dispatch_token = (
                _active_turn_token
                if _active_turn_context is not None
                and session_id == _active_turn_context[0]
                else None
            )
            with use_token(dispatch_token):
                result = await service.dispatch(tc, session_id, executed)
        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            harness = _get_session_harness(session_id)
            if harness is not None:
                harness.record_event(ToolCallEvent(
                    tool_call_id=request.toolCallId,
                    tool_name=request.name,
                    arguments=request.arguments or {},
                    duration_ms=duration_ms,
                    is_error=True,
                    error_msg=str(exc)[:200],
                    result={},
                    session_id=session_id,
                ))
            raise

        if result.status == "ok" and rt_ev is not None:
            for ma in result.map_actions:
                rt_ev.record_map_action_issued(ma["action_id"])

    duration_ms = int((time.monotonic() - t0) * 1000)

    # 缓存供 SSE 适配器按已验证 turn session 读取。
    cache_dispatch_result(request.toolCallId, result, session_id)

    raw = result.raw_result if isinstance(result.raw_result, dict) else {}
    has_cartographic_generation = bool(raw.get("mapspec_fingerprint"))
    harness = _get_session_harness(
        session_id,
        create=has_cartographic_generation,
    )
    if harness is not None:
        # V3: 记录本次 dispatch 发出的地图动作（issued 侧证据）。仅 status=="ok" ——
        # error/repeated 不产生可执行的地图命令。turn_id 在此不可得（HTTP 回调无
        # turn 上下文）；turn 级 correlation 由前端 ack 的 correlation 侧补齐。
        is_error = result.status == "error"
        # HARNESS-V2: forward real MapSpec mutation evidence (is_compiled /
        # success / warnings / checkpoint_id) from raw_result so the validity
        # ladder isn't starved in production. Slim to evidence fields only — the
        # full mapspec is fetched via fetch-on-demand, never logged wholesale.
        ev = {"status": result.status, "llm_payload_len": len(result.llm_payload)}
        # ADR-0052: also forward cartography_findings so the Harness surfaces
        # thematic drift (paint↔legend equivalence) in semantic_errors.
        for k in (
            "success",
            "is_compiled",
            "warnings",
            "checkpoint_id",
            "message",
            "correction_hint",
            "cartography_findings",
            "cartographic_review",
            "mapspec_fingerprint",
            "runtime_observation_seq",
            "runtime_projection_fingerprint",
            "mutation_revision",
        ):
            if k in raw:
                ev[k] = raw[k]
        event = ToolCallEvent(
            tool_call_id=request.toolCallId,
            tool_name=("webgis_layer_upsert" if has_cartographic_generation else tool_name),
            arguments=request.arguments or {},
            duration_ms=duration_ms,
            is_error=is_error,
            # P1 fix: truncate to a short message rather than the full payload.
            error_msg=(result.llm_payload[:200] if is_error else ""),
            result=ev,
            session_id=session_id,
        )
        if has_cartographic_generation and result.status == "ok":
            generation_current = await _persist_cartographic_harness_context(
                session_id, event, result.map_actions
            )
            if not generation_current:
                # The GIS result is still returned, but a completion from an
                # older MapSpec revision cannot enter or evaluate the current
                # process-local harness.
                return PiToolResponse(
                    toolCallId=request.toolCallId,
                    content=[{"type": "text", "text": result.llm_payload}],
                    details=result.raw_result,
                    isError=(result.status == "error"),
                )
        if result.status == "ok":
            for ma in result.map_actions:
                harness.record_map_action_issued(
                    session_id=session_id,
                    tool_call_id=request.toolCallId,
                    turn_id="",
                    action_id=ma["action_id"],
                    command=ma["command"],
                    requested=ma["requested"],
                    mapspec_fingerprint=ma.get("mapspec_fingerprint"),
                )
        harness.record_event(event)

        # Desired-state evidence is available immediately.  Runtime PASS is
        # deliberately impossible until a matching live observation and ACK
        # arrive; those event endpoints invoke the same session evaluator.
        if has_cartographic_generation:
            try:
                await evaluate_cartographic_session(session_id)
            except Exception as review_error:  # noqa: BLE001 - GIS success is immutable
                logger.warning(
                    "[PiBridge] cartographic evaluation unavailable for %s: %s",
                    session_id,
                    review_error,
                )

    return PiToolResponse(
        toolCallId=request.toolCallId,
        content=[{"type": "text", "text": result.llm_payload}],
        details=result.raw_result,
        isError=(result.status == "error"),
    )


# 每个 session 的已执行工具集合，供重复调用拦截（service 接受外部 set）。
_session_executed_sets: dict[str, set[tuple[str, str]]] = {}

# F24: 当前在飞 turn 的 CancellationToken。stream_prompt 在持锁期间设置/清除；
# dispatch_tool（Pi HTTP 回调适配器）通过 use_token() 绑定它，使 checkpoint()
# 协作式工具在 abort/disconnect 后及时停止，而不是对已被抛弃的 turn 跑到底。
# 模块级而非实例级：dispatch_tool 是模块函数（ADR-0022 rendezvous），拿不到
# bridge 实例；turn 在单例 bridge 上严格串行，全局唯一在飞 turn 语义安全。
_active_turn_token: Optional[CancellationToken] = None
# Exact active capability for the singleton Pi subprocess.  A valid HMAC is
# necessary but insufficient: once its turn ends, a delayed callback must not
# remain executable for the token's remaining clock lifetime.
_active_turn_context: Optional[tuple[str, str]] = None


def is_active_pi_turn(session_id: str, turn_id: str) -> bool:
    """Return whether ``(session, turn)`` owns the live Pi prompt."""
    return _active_turn_context == (session_id, turn_id)

# Runtime observability: 当前在飞 turn 的关联身份（turn_id / run_id / session_id）。
# 与 ``_active_turn_token`` 同源——dispatch_tool 是独立的 HTTP 回调 task，看不到流
# 任务的 ContextVar；故按「单例 bridge 严格串行 turn」这一既有不变量（cancel
# token 已依赖它）暴露在飞 turn 的关联身份，让 dispatch_tool 能把工具证据按
# turn_id 写进 TurnEvidence 注册表、并把 run_id/turn_id 透传给 tool_metrics /
# JobOrigin。abort/超时的 finally 会先清 token 再清这里——迟到的回调读到 None
# 时回退为空（graceful，不伪造关联）。session_id 用于 dispatch_tool 的归属守卫：
# 回调携带的 session 与在飞 turn 的 session 不一致时，认定是迟到/跨 worker 的串号
# 回调，不把它的工具证据误归属到当前 turn（仍执行工具，只放弃关联）。
_active_turn_turn_id: Optional[str] = None
_active_turn_run_id: Optional[str] = None
_active_turn_session_id: Optional[str] = None


def active_turn_correlation() -> tuple[Optional[str], Optional[str], Optional[str]]:
    """返回在飞 turn 的 (turn_id, run_id, session_id)。

    无在飞 turn 或已被清理时返回 (None, None, None)。供 dispatch_tool（独立 HTTP
    回调 task）恢复 turn 级关联使用，并据 session_id 守卫串号回调。单例 bridge 严格
    串行 turn，故全局唯一在飞 turn 语义安全（同 ``_active_turn_token``）。
    """
    return _active_turn_turn_id, _active_turn_run_id, _active_turn_session_id


# ── Session-scoped evaluation harnesses ────────────────────────────────
# General telemetry remains opt-in. Display-producing MapSpec mutations create
# a harness even when telemetry is disabled because cartographic verification
# is part of the product result, not optional observability.
_harness: Optional[PiAgentHarness] = None
_harnesses: "OrderedDict[str, PiAgentHarness]" = OrderedDict()
_cartography_eval_cache: "OrderedDict[tuple[str, str, int, str], dict[str, Any]]" = OrderedDict()
_cartography_eval_locks: dict[str, asyncio.Lock] = {}
_deleted_cartography_sessions: "OrderedDict[str, None]" = OrderedDict()
_DELETED_SESSION_TOMBSTONE_LIMIT = 1_024
_HARNESS_REGISTRY_LIMIT = 128
_harness_feature_enabled = os.getenv("PI_HARNESS_ENABLED", "").lower() in (
    "true", "1", "yes"
)


def _build_session_harness(session_id: str) -> PiAgentHarness:
    """Create one evidence accumulator whose readers are tenant scoped."""
    from app.services.session_data import session_data_manager as sdm
    from app.services.mapspec.coordinator import validate as validate_mapspec
    from app.services.mapspec.store import mapspec_store_instance
    from app.lib.harness.ref_resolver import make_session_store_resolver

    async def read_cartographic_state(requested_session_id: str) -> dict[str, Any]:
        mapspec, map_state = await asyncio.gather(
            mapspec_store_instance.get_mapspec(requested_session_id),
            sdm.get_map_state(requested_session_id),
        )
        return {
            "session_id": requested_session_id,
            "mapspec": mapspec,
            "map_state": map_state,
        }

    return PiAgentHarness(
        session_id=session_id,
        ref_resolver=make_session_store_resolver(sdm),
        mapspec_validator=validate_mapspec,
        cartography_state_reader=read_cartographic_state,
        map_action_reader=sdm.get_map_action_events,
    )


def _get_session_harness(
    session_id: str,
    *,
    create: bool = False,
) -> Optional[PiAgentHarness]:
    """Return a bounded, exact-session harness; never retag a singleton."""
    global _harness
    if not session_id:
        return None
    if session_id in _deleted_cartography_sessions:
        return None
    if _harness is not None and _harness.session_id == session_id:
        return _harness
    existing = _harnesses.get(session_id)
    if existing is not None:
        _harnesses.move_to_end(session_id)
        return existing
    if not create and not _harness_feature_enabled:
        return None
    try:
        created = _build_session_harness(session_id)
    except Exception as harness_err:  # noqa: BLE001 - map interaction must survive telemetry failure
        logger.warning(
            "[PiBridge] session harness wiring failed for %s: %s",
            session_id,
            harness_err,
        )
        return None
    _harnesses[session_id] = created
    _harness = created  # compatibility/telemetry summary: most recently active
    while len(_harnesses) > _HARNESS_REGISTRY_LIMIT:
        evicted_session, _ = _harnesses.popitem(last=False)
        _cartography_eval_locks.pop(evicted_session, None)
        for key in list(_cartography_eval_cache):
            if key[0] == evicted_session:
                del _cartography_eval_cache[key]
    return created


def _discard_session_harness(session_id: str) -> None:
    """Evict one stale process-local accumulator without tombstoning it."""
    global _harness
    removed = _harnesses.pop(session_id, None)
    if removed is not None and _harness is removed:
        _harness = next(reversed(_harnesses.values()), None) if _harnesses else None


def _persistable_cartographic_result(result: dict[str, Any]) -> dict[str, Any]:
    """Project one mutation result to bounded, non-dataset harness evidence."""
    projected: dict[str, Any] = {}
    for key in (
        "status", "success", "is_compiled", "checkpoint_id",
        "mapspec_fingerprint", "runtime_observation_seq",
        "runtime_projection_fingerprint",
        "mutation_revision",
    ):
        value = result.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            projected[key] = value
    warnings = result.get("warnings")
    if isinstance(warnings, list):
        projected["warnings"] = [str(value)[:500] for value in warnings[:32]]
    review = result.get("cartographic_review")
    if isinstance(review, dict) and isinstance(review.get("attempts"), list):
        projected["cartographic_review"] = {
            "attempts": copy.deepcopy(review["attempts"][:2]),
        }
    return projected


async def _persist_cartographic_harness_context(
    session_id: str,
    event: ToolCallEvent,
    actions: list[dict[str, Any]],
    *,
    session_lock_held: bool = False,
) -> bool:
    """Persist the latest mutation seam so another API replica can rehydrate.

    Only correlation, the semantic layer id, slim tool outcome, and already
    bounded issued-action requests are retained. Source/feature bodies and raw
    tool arguments never enter this session-state projection.
    """
    if not session_lock_held:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(session_id):
            from app.services.session_data import session_data_manager
            session_data_manager.invalidate_local_cache(session_id)
            return await _persist_cartographic_harness_context(
                session_id,
                event,
                actions,
                session_lock_held=True,
            )
    if session_id in _deleted_cartography_sessions:
        return False

    layer = (
        event.arguments.get("layer")
        if isinstance(event.arguments, dict)
        and isinstance(event.arguments.get("layer"), dict)
        else {}
    )
    context = {
        "version": 2,
        "session_id": session_id,
        "tool_call": {
            "tool_call_id": event.tool_call_id,
            "tool_name": event.tool_name,
            "arguments": {"layer": {"id": str(layer.get("id") or "")}},
            "duration_ms": max(0, int(event.duration_ms)),
            "is_error": bool(event.is_error),
            "error_msg": str(event.error_msg or "")[:200],
            "result": _persistable_cartographic_result(event.result),
        },
        "map_actions": [
            {
                "action_id": str(action.get("action_id") or ""),
                "command": str(action.get("command") or ""),
                "requested": copy.deepcopy(action.get("requested") or {}),
                "mapspec_fingerprint": action.get("mapspec_fingerprint"),
            }
            for action in actions[:16]
            if isinstance(action, dict) and action.get("action_id")
        ],
    }
    from app.services.session_data import session_data_manager

    try:
        state = await session_data_manager.get_map_state(session_id)
        if state.get("_cartographic_deleted") is True:
            _discard_session_harness(session_id)
            return False
        try:
            current_revision = int(
                state.get("_cartographic_mutation_revision", 0)
            )
            incoming_revision = int(
                (event.result or {}).get("mutation_revision", 0)
            )
        except (TypeError, ValueError):
            _discard_session_harness(session_id)
            return False
        # Lifecycle commit and this persistence use the same distributed lock,
        # but the completed tool may wait behind a newer mutation. Reject its
        # late context rather than regressing durable provenance.
        if incoming_revision <= 0 or incoming_revision != current_revision:
            # A late tool completion may already have populated this worker's
            # process-local harness before durable serialization. Discard it;
            # the next evaluation rehydrates the current durable generation.
            _discard_session_harness(session_id)
            return False
        stored_context = state.get("_cartographic_harness_context")
        if isinstance(stored_context, dict):
            try:
                stored_revision = int(stored_context.get("mutation_revision", 0))
            except (TypeError, ValueError):
                stored_revision = 0
            if stored_revision > incoming_revision:
                _discard_session_harness(session_id)
                return False
        context["mutation_revision"] = incoming_revision
        persisted = await session_data_manager.set_map_state(
            session_id, "_cartographic_harness_context", context
        )
        if persisted is False:
            raise RuntimeError("cartographic context persistence rejected")
        return True
    except Exception as error:  # noqa: BLE001 - preserve completed GIS work
        logger.warning(
            "[PiBridge] unable to persist cartographic context for %s: %s",
            session_id,
            error,
        )
        _discard_session_harness(session_id)
        return False


async def _hydrate_cartographic_harness(
    session_id: str, harness: PiAgentHarness
) -> bool:
    """Restore the latest owned mutation evidence into a fresh worker."""
    has_local_mutation = any(
        mutation.get("session_id") == session_id
        for mutation in harness.mapspec_mutations
    )
    from app.services.session_data import session_data_manager

    state = await session_data_manager.get_map_state(session_id)
    if state.get("_cartographic_deleted") is True:
        return False
    context = state.get("_cartographic_harness_context")
    if (
        not isinstance(context, dict)
        or context.get("session_id") != session_id
        or context.get("version") not in (1, 2)
    ):
        return has_local_mutation
    if context.get("version") == 2:
        try:
            if int(context.get("mutation_revision", 0)) != int(
                state.get("_cartographic_mutation_revision", 0)
            ):
                return has_local_mutation
        except (TypeError, ValueError):
            return has_local_mutation
    call = context.get("tool_call")
    if (
        not isinstance(call, dict)
        or not isinstance(call.get("result"), dict)
        or not call["result"].get("mapspec_fingerprint")
    ):
        return has_local_mutation
    tool_call_id = str(call.get("tool_call_id") or "")
    if not tool_call_id:
        return has_local_mutation
    if not any(
        mutation.get("tool_call_id") == tool_call_id
        for mutation in harness.mapspec_mutations
    ):
        for action in context.get("map_actions") or []:
            if not isinstance(action, dict) or not action.get("action_id"):
                continue
            if any(
                issued.get("action_id") == action["action_id"]
                for issued in harness.map_actions_issued
            ):
                continue
            harness.record_map_action_issued(
                session_id=session_id,
                tool_call_id=tool_call_id,
                action_id=str(action["action_id"]),
                command=str(action.get("command") or ""),
                requested=action.get("requested") or {},
                mapspec_fingerprint=action.get("mapspec_fingerprint"),
            )
        harness.record_event(ToolCallEvent(
            tool_call_id=tool_call_id,
            tool_name=str(call.get("tool_name") or "webgis_layer_upsert"),
            arguments=call.get("arguments") or {},
            duration_ms=max(0, int(call.get("duration_ms") or 0)),
            is_error=bool(call.get("is_error")),
            error_msg=str(call.get("error_msg") or "")[:200],
            result=call.get("result") or {},
            session_id=session_id,
        ))
    return True


async def _persist_cartographic_issued_action(
    session_id: str,
    action: dict[str, Any],
    *,
    session_lock_held: bool = False,
) -> None:
    """Add a runtime-repair command to the cross-worker harness projection."""
    if not session_lock_held:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(session_id):
            from app.services.session_data import session_data_manager
            session_data_manager.invalidate_local_cache(session_id)
            await _persist_cartographic_issued_action(
                session_id,
                action,
                session_lock_held=True,
            )
        return
    if session_id in _deleted_cartography_sessions:
        return
    from app.services.session_data import session_data_manager

    state = await session_data_manager.get_map_state(session_id)
    if state.get("_cartographic_deleted") is True:
        return
    context = state.get("_cartographic_harness_context")
    if not isinstance(context, dict) or context.get("session_id") != session_id:
        return
    try:
        if int(context.get("mutation_revision", 0)) != int(
            state.get("_cartographic_mutation_revision", 0)
        ):
            return
    except (TypeError, ValueError):
        return
    actions = [
        item for item in (context.get("map_actions") or [])
        if isinstance(item, dict) and item.get("action_id") != action.get("action_id")
    ]
    actions.append({
        "action_id": str(action.get("action_id") or ""),
        "command": str(action.get("command") or ""),
        "requested": copy.deepcopy(action.get("requested") or {}),
        "mapspec_fingerprint": action.get("mapspec_fingerprint"),
    })
    context = copy.deepcopy(context)
    context["map_actions"] = actions[-16:]
    persisted = await session_data_manager.set_map_state(
        session_id, "_cartographic_harness_context", context
    )
    if persisted is False:
        raise RuntimeError("cartographic issued-action persistence rejected")


async def evaluate_cartographic_session(
    session_id: str, *, session_lock_held: bool = False
) -> dict[str, Any]:
    """Serialize and recompute the session gate after meaningful evidence.

    The distributed session lock makes evaluation atomic with deletion across
    API replicas. Route handlers already holding it opt out of reacquisition.
    """
    if not session_lock_held:
        from app.services.distributed_lock import session_lock_registry

        async with session_lock_registry.lock(session_id):
            from app.services.session_data import session_data_manager
            session_data_manager.invalidate_local_cache(session_id)
            return await evaluate_cartographic_session(
                session_id, session_lock_held=True
            )
    if session_id in _deleted_cartography_sessions:
        return {
            "session_id": session_id,
            "cartography": {
                "status": "not_evaluated",
                "trusted": False,
                "evaluated": False,
                "passed": False,
                "termination_reason": "no_session_harness",
            },
        }
    harness = _get_session_harness(session_id, create=True)
    if harness is None or not await _hydrate_cartographic_harness(session_id, harness):
        return {
            "session_id": session_id,
            "cartography": {
                "status": "not_evaluated",
                "trusted": False,
                "evaluated": False,
                "passed": False,
                "termination_reason": "no_session_harness",
            },
        }
    lock = _cartography_eval_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        return await _evaluate_cartographic_session_unlocked(session_id)


async def record_cartographic_dispatch_evidence(
    session_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    outcome: "ToolDispatchResult",
    duration_ms: int,
) -> None:
    """Shared legacy-agent seam for one display-producing dispatch.

    Pi records all telemetry in ``dispatch_tool``. The legacy chat pipeline
    calls this narrow additive seam so both production agents feed the same
    session-scoped harness and runtime observation/ACK evaluator.
    """
    raw = outcome.raw_result if isinstance(outcome.raw_result, dict) else {}
    if outcome.status != "ok" or not raw.get("mapspec_fingerprint"):
        return
    result_evidence: dict[str, Any] = {
        "status": outcome.status,
        "llm_payload_len": len(outcome.llm_payload),
    }
    for key in (
        "success", "is_compiled", "warnings", "checkpoint_id", "message",
        "correction_hint", "cartography_findings", "cartographic_review",
        "mapspec_fingerprint", "runtime_observation_seq",
        "runtime_projection_fingerprint",
        "mutation_revision",
    ):
        if key in raw:
            result_evidence[key] = raw[key]
    event = ToolCallEvent(
        tool_call_id=tool_call_id,
        tool_name="webgis_layer_upsert",
        arguments=arguments,
        duration_ms=duration_ms,
        is_error=False,
        result=result_evidence,
        session_id=session_id,
    )
    if not await _persist_cartographic_harness_context(
        session_id, event, outcome.map_actions
    ):
        return
    harness = _get_session_harness(session_id, create=True)
    if harness is None:
        return
    for action in outcome.map_actions:
        harness.record_map_action_issued(
            session_id=session_id,
            tool_call_id=tool_call_id,
            action_id=action["action_id"],
            command=action["command"],
            requested=action["requested"],
            mapspec_fingerprint=action.get("mapspec_fingerprint"),
        )
    harness.record_event(event)
    await evaluate_cartographic_session(session_id)


async def _evaluate_cartographic_session_unlocked(
    session_id: str,
) -> dict[str, Any]:
    harness = _get_session_harness(session_id)
    if harness is None:
        return {
            "session_id": session_id,
            "cartography": {
                "status": "not_evaluated",
                "trusted": False,
                "evaluated": False,
                "passed": False,
                "termination_reason": "no_session_harness",
            },
        }
    from app.lib.harness.evaluator import HarnessEvaluator
    from app.services.session_data import session_data_manager

    state = await session_data_manager.get_map_state(session_id)
    observation = state.get("_cartographic_observation")
    sequence = int(observation.get("sequence", 0)) if isinstance(observation, dict) else 0
    fingerprint = str(observation.get("mapspec_fingerprint") or "") if isinstance(observation, dict) else ""
    actions = await session_data_manager.get_map_action_events(session_id)
    evidence_revision = hashlib.sha256(json.dumps(
        {
            # Session stores cap ACKs at 200 and harness tool evidence at 1000.
            # Hash the complete bounded windows: a late ACK for an older repair
            # must invalidate a prior evaluation even after 16 newer actions.
            "actions": [
                {
                    "id": action.get("action_id"),
                    "status": action.get("status"),
                    "finished_at": action.get("finished_at"),
                }
                for action in actions
                if isinstance(action, dict)
            ],
            "tools": [
                {
                    "id": call.get("tool_call_id"),
                    "name": call.get("name"),
                    "error": call.get("is_error"),
                }
                for call in harness.tool_results
                if isinstance(call, dict)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    cache_key = (
        session_id,
        fingerprint,
        sequence,
        evidence_revision,
    )
    cached = _cartography_eval_cache.get(cache_key)
    if cached is not None:
        _cartography_eval_cache.move_to_end(cache_key)
        return copy.deepcopy(cached)

    evidence = await harness.evaluate_with_evidence()
    gate = HarnessEvaluator().evaluate_evidence(
        evidence,
        require_evaluated=False,
        require_cartography=True,
    )
    result = {
        "session_id": session_id,
        "cartography": evidence.get("cartography") or {},
        "gate": gate.get("checks", {}).get("CartographicQuality") or {},
        "overall_passed": bool(gate.get("overall_passed")),
    }
    result = await _advance_runtime_cartographic_repair(
        session_id=session_id,
        harness=harness,
        result=result,
        map_state=state,
        actions=actions,
    )
    terminal_cacheable = (
        result.get("repair_action") is None
        and (result.get("cartography") or {}).get("status") in {
            "passed", "passed_with_warnings", "failed_unrepairable",
            "repair_exhausted", "superseded",
        }
    )
    if terminal_cacheable:
        _cartography_eval_cache[cache_key] = copy.deepcopy(result)
    while len(_cartography_eval_cache) > _HARNESS_REGISTRY_LIMIT * 4:
        _cartography_eval_cache.popitem(last=False)
    if session_id in _deleted_cartography_sessions:
        return {
            "session_id": session_id,
            "cartography": {
                "status": "superseded",
                "trusted": False,
                "evaluated": False,
                "passed": False,
                "termination_reason": "session_deleted",
            },
            "overall_passed": False,
        }
    persisted = await session_data_manager.set_map_state(
        session_id, "_cartographic_review", result
    )
    if persisted is False:
        return {
            "session_id": session_id,
            "cartography": {
                "status": "not_evaluated",
                "trusted": False,
                "evaluated": False,
                "passed": False,
                "termination_reason": "evidence_persistence_unavailable",
            },
            "overall_passed": False,
        }
    return result


async def _advance_runtime_cartographic_repair(
    *,
    session_id: str,
    harness: PiAgentHarness,
    result: dict[str, Any],
    map_state: dict[str, Any],
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Issue at most one event-driven AUTO_SAFE repair for current evidence."""
    if session_id in _deleted_cartography_sessions:
        return result
    from app.lib.cartography.runtime_repair import (
        MAX_RUNTIME_REPAIR_ITERATIONS,
        plan_runtime_repairs,
    )
    from app.services.mapspec.store import mapspec_store_instance
    from app.services.session_data import session_data_manager

    async def persist_repair_state(value: dict[str, Any]) -> None:
        if session_id not in _deleted_cartography_sessions:
            persisted = await session_data_manager.set_map_state(
                session_id, "_cartographic_repair_state", value
            )
            if persisted is False:
                raise RuntimeError("cartographic repair-state persistence rejected")

    cartography = result.get("cartography")
    if not isinstance(cartography, dict):
        return result
    fingerprint = str(cartography.get("mapspec_fingerprint") or "")
    if not fingerprint:
        return result
    observation = map_state.get("_cartographic_observation")
    if not isinstance(observation, dict):
        return result
    mapspec = await mapspec_store_instance.get_mapspec(session_id)
    if not isinstance(mapspec, dict):
        return result

    previous = map_state.get("_cartographic_repair_state")
    repair_state = (
        copy.deepcopy(previous)
        if isinstance(previous, dict) and previous.get("mapspec_fingerprint") == fingerprint
        else {"mapspec_fingerprint": fingerprint, "attempts": []}
    )
    attempts = repair_state.get("attempts")
    if not isinstance(attempts, list):
        attempts = []
    attempts = [attempt for attempt in attempts[:MAX_RUNTIME_REPAIR_ITERATIONS] if isinstance(attempt, dict)]
    acks_by_id = {
        str(action.get("action_id")): action
        for action in actions
        if isinstance(action, dict) and action.get("action_id")
    }
    for attempt in attempts:
        ack = acks_by_id.get(str(attempt.get("action_id") or ""))
        attempt["status"] = str(ack.get("status")) if ack else "issued"
        if ack and ack.get("error"):
            attempt["error"] = str(ack["error"])[:200]
    repair_state["attempts"] = attempts
    cartography["repair_attempts"] = copy.deepcopy(attempts)

    if cartography.get("status") in ("passed", "passed_with_warnings"):
        repair_state["termination_reason"] = "quality_converged"
        await persist_repair_state(repair_state)
        return result
    if cartography.get("status") != "failed_repairable":
        if attempts:
            repair_state["termination_reason"] = str(
                cartography.get("termination_reason") or "repair_stopped"
            )
            await persist_repair_state(repair_state)
        return result

    plan = plan_runtime_repairs(mapspec, observation, cartography)
    if plan is None:
        cartography["status"] = "failed_unrepairable"
        cartography["passed"] = False
        cartography["termination_reason"] = "no_safe_runtime_repair"
        result["overall_passed"] = False
        repair_state["termination_reason"] = "no_safe_runtime_repair"
        await persist_repair_state(repair_state)
        return result

    patch_fingerprint = str(plan["patch_fingerprint"])
    prior = next(
        (
            attempt for attempt in attempts
            if attempt.get("patch_fingerprint") == patch_fingerprint
        ),
        None,
    )
    if prior is not None:
        if prior.get("status") == "issued":
            cartography["status"] = "not_evaluated"
            cartography["passed"] = False
            cartography["termination_reason"] = "runtime_repair_ack_pending"
        elif prior.get("status") in ("cancelled", "superseded"):
            cartography["status"] = "superseded"
            cartography["passed"] = False
            cartography["termination_reason"] = "user_or_newer_intent"
        else:
            cartography["status"] = "repair_exhausted"
            cartography["passed"] = False
            cartography["termination_reason"] = "repeated_runtime_repair"
        result["overall_passed"] = False
        repair_state["termination_reason"] = cartography["termination_reason"]
        await persist_repair_state(repair_state)
        return result
    if len(attempts) >= MAX_RUNTIME_REPAIR_ITERATIONS:
        cartography["status"] = "repair_exhausted"
        cartography["passed"] = False
        cartography["termination_reason"] = "runtime_repair_iteration_limit"
        result["overall_passed"] = False
        repair_state["termination_reason"] = "runtime_repair_iteration_limit"
        await persist_repair_state(repair_state)
        return result

    sequence = int(observation.get("sequence") or 0)
    action_id = f"ma-carto-{uuid.uuid4().hex[:16]}"
    source_tool_call_id = str(cartography.get("source_tool_call_id") or "")
    action = {
        "action_id": action_id,
        "command": "cartographic_runtime_repair",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "correlation": {
            "session_id": session_id,
            "step_id": source_tool_call_id,
        },
        "params": {
            "mapspec_fingerprint": fingerprint,
            "observation_sequence": sequence,
            "patch_fingerprint": patch_fingerprint,
            "repair_patches": plan["patches"],
        },
    }
    harness.record_map_action_issued(
        session_id=session_id,
        tool_call_id=source_tool_call_id,
        action_id=action_id,
        command="cartographic_runtime_repair",
        requested={
            "mapspec_fingerprint": fingerprint,
            "observation_sequence": sequence,
            "patch_fingerprint": patch_fingerprint,
        },
        mapspec_fingerprint=fingerprint,
    )
    await _persist_cartographic_issued_action(
        session_id,
        {
            "action_id": action_id,
            "command": "cartographic_runtime_repair",
            "requested": {
                "mapspec_fingerprint": fingerprint,
                "observation_sequence": sequence,
                "patch_fingerprint": patch_fingerprint,
            },
            "mapspec_fingerprint": fingerprint,
        },
        session_lock_held=True,
    )
    attempts.append({
        "iteration": len(attempts) + 1,
        "action_id": action_id,
        "patch_fingerprint": patch_fingerprint,
        "observation_sequence": sequence,
        "status": "issued",
        "repairability": "auto_safe",
        "rules": sorted({
            rule
            for patch in plan["patches"]
            for rule in patch.get("rules", [])
        }),
    })
    repair_state["attempts"] = attempts
    repair_state["termination_reason"] = "runtime_repair_issued"
    cartography["repair_attempts"] = copy.deepcopy(attempts)
    await persist_repair_state(repair_state)
    result["repair_action"] = action
    result["overall_passed"] = False
    return result


def get_harness(session_id: Optional[str] = None) -> Optional[PiAgentHarness]:
    """Return the active evaluation harness, or None if disabled."""
    if session_id:
        return _get_session_harness(session_id)
    return _harness


def clear_cartographic_session_state(session_id: str) -> None:
    """Drop process-local evidence when the owned session is deleted."""
    global _harness
    _deleted_cartography_sessions[session_id] = None
    _deleted_cartography_sessions.move_to_end(session_id)
    while len(_deleted_cartography_sessions) > _DELETED_SESSION_TOMBSTONE_LIMIT:
        _deleted_cartography_sessions.popitem(last=False)
    removed = _harnesses.pop(session_id, None)
    _cartography_eval_locks.pop(session_id, None)
    for key in list(_cartography_eval_cache):
        if key[0] == session_id:
            del _cartography_eval_cache[key]
    if removed is not None and _harness is removed:
        _harness = next(reversed(_harnesses.values()), None) if _harnesses else None


def restore_cartographic_session_state(session_id: str) -> None:
    """Undo a pre-delete tombstone when the authoritative delete failed."""
    _deleted_cartography_sessions.pop(session_id, None)


def is_cartographic_session_deleted(session_id: str) -> bool:
    """Deletion tombstone consulted by late observation/ACK writers."""
    return session_id in _deleted_cartography_sessions


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

    # Session id of the turn currently holding the bridge lock (set/cleared by
    # stream_prompt). Class-level default so tests that bypass __init__
    # (PiBridge.__new__) still read None. None = no turn in flight.
    _active_turn_sid: Optional[str] = None

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
        self._active_turn_sid: Optional[str] = None

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

    async def abort(self, session_id: Optional[str] = None) -> dict:
        """Abort the currently-running Pi prompt (fire-and-forget from callers).

        BUG-18 fix re-added: ADR-0031 F3 extraction removed the abort wrapper,
        leaving chat.py:320's `await pi_bridge.abort()` to AttributeError into
        a swallowed `except Exception` — the in-flight prompt kept consuming
        tokens and writing files after session deletion.

        Session scoping (F5): the abort RPC and ``fail_all_pending`` are
        GLOBAL — they hit whatever turn currently owns the singleton
        subprocess. When a turn for a DIFFERENT session is in flight, an
        abort meant for an already-dead session would kill the wrong
        session's turn; skip the RPC + fail_all_pending and log a warning
        instead. When ``session_id`` matches the active turn, is None
        (back-compat: existing callers pass nothing), or no turn is active,
        behave as before.

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

        Knock-on note (fail_all_pending scoping): ``fail_all_pending`` fails
        EVERY pending future, which looks over-broad — but on this singleton
        client requests are serialized by the turn lock, so the only futures
        that can be pending at abort time belong to the in-flight turn (the
        abort RPC's own future has already resolved, and the F5 guard above
        skips this call entirely when the in-flight turn is another
        session's). The failed futures are therefore already exactly the
        abort-relevant ones; a per-command pending registry would add
        bookkeeping for no additional safety.
        """
        active = self._active_turn_sid
        if session_id is not None and active is not None and session_id != active:
            logger.warning(
                "[PiBridge] abort(session_id=%s) skipped: turn for session %s "
                "is in flight on the singleton bridge; a global abort would "
                "kill the wrong session's turn",
                session_id, active,
            )
            return {}
        # CONC-F1: snapshot the turn identity + pending futures BEFORE the
        # (potentially slow) abort RPC. The same-session successor case —
        # page close → immediate resend is the most common disconnect pattern —
        # leaves _active_turn_sid UNCHANGED while the token and pending
        # futures belong to the NEW turn; sid equality alone would kill it.
        abort_token = _active_turn_token
        abort_pending_ids = self._rpc.pending_request_ids()
        result = await self._rpc.request("abort")
        # P1 (TOCTOU) / R2-5: the active sid above was read lock-free BEFORE
        # the abort RPC was awaited. In that gap the matching turn can end and
        # a DIFFERENT session's turn start (notably when a shielded
        # abort-on-disconnect outlives its turn's finally). In that case the
        # abort RPC itself has already flown — nothing can un-send it — but we
        # must NOT also ignite the NEW turn's token or fail its pending
        # futures, so skip both global effects and log the flip.
        active_after = self._active_turn_sid
        if (
            session_id is not None
            and active_after is not None
            and active_after != session_id
        ):
            logger.warning(
                "[PiBridge] abort(session_id=%s) TOCTOU: the active turn "
                "flipped %s -> %s while the abort RPC was in flight; skipping "
                "token-cancel/fail_all_pending so the new turn is not killed",
                session_id, active, active_after,
            )
            return result or {}
        # F24: ignite the SNAPSHOT turn's cancellation token so checkpoint()-
        # cooperative tool dispatches (HTTP callback path) stop promptly too,
        # not just the Pi subprocess. CONC-F1: cancel only the token the abort
        # was AIMED at — if a same-session successor turn replaced it, the
        # current token belongs to the new turn and must not be ignited
        # (cancelling the stale snapshot token is correct and harmless: its
        # turn is already gone).
        if abort_token is not None:
            abort_token.cancel("abort requested")
        if _active_turn_token is not abort_token and _active_turn_token is not None:
            logger.warning(
                "[PiBridge] abort TOCTOU: active turn token was replaced while "
                "the abort RPC was in flight; cancelled only the stale snapshot "
                "token and failed only the snapshot futures"
            )
        # Cancel pending futures from the SNAPSHOT only (CONC-F1): failing
        # everything would kill a successor turn's freshly registered prompt.
        self._rpc.fail_pending_ids(abort_pending_ids, "abort requested")
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
            # Scope the abort to THIS turn's session: the F5 guard inside
            # abort() then skips the global RPC/fail_all_pending when another
            # session's turn is already active (the shielded caller may resume
            # long after this turn ended and the lock was released).
            await self.abort(session_id=turn_sid)
            logger.info("[PiBridge] abort sent on client disconnect (turn=%s)", turn_sid)
        except Exception as e:  # noqa: BLE001 — abort failure must not break cleanup
            logger.warning("[PiBridge] abort-on-disconnect failed (turn=%s): %s", turn_sid, e)

    async def prompt(
        self,
        message: str,
        session_id: Optional[str] = None,
        cartography_context: Optional[str] = None,
    ) -> dict:
        """Send a prompt to Pi agent (non-streaming).

        Args:
            message: User message
            session_id: Optional session ID
            cartography_context: Optional bounded harness verdict block,
                prepended ahead of the turn marker (see attach_turn_context).

        Returns:
            Response dict with session_id and content

        Raises:
            PiRpcError: If the Pi agent returns an error or the request fails.
        """
        global _active_turn_token, _active_turn_turn_id, _active_turn_run_id, _active_turn_session_id, _active_turn_context
        # Turn-scoped session id: attribution uses this local, never the
        # mutable self._session_id field (which a concurrent/preceding turn
        # could overwrite). Pi events carry no session of their own.
        turn_sid = session_id or ""
        from app.api.routes.pi_tools import get_bridge_secret
        from app.services.chat.pi_turn_context import attach_turn_context, issue_turn_token

        turn_id = _mint_turn_id()
        turn_token = issue_turn_token(get_bridge_secret(), turn_sid, turn_id)
        data: dict[str, Any] = {
            "message": attach_turn_context(message, turn_token, cartography_context or ""),
        }
        if turn_sid:
            data["sessionId"] = turn_sid

        # Runtime observability: prompt() now mints a real turn_id (previously
        # its cancellation token carried the *session* id as job_id, so the
        # non-stream path had no turn identity). run_id is a first-class turn
        # handle (the prior "run_id == session_id" reuse is retired here).
        # Do NOT remint: the signed turn token above is bound to turn_id.
        run_id = rt_ctx.new_run_id()
        rt_ev = TurnEvidence(
            request_id=rt_ctx.current_runtime_context().request_id if rt_ctx.current_runtime_context() else None,
            session_id=turn_sid or None,
            turn_id=turn_id,
            run_id=run_id,
        )

        # Hold the lock across send + drain so turns are strictly serial on the
        # singleton bridge. Pi processes one prompt at a time, so this matches
        # the real execution model and prevents two turns' events interleaving
        # in the shared queue. Abort bypasses the lock (see abort()).
        await self._lock.acquire()
        # Clear caches only after the lock so a concurrent stream_prompt
        # cannot wipe another in-flight turn's dispatch results / dedup set.
        _session_executed_sets.pop(turn_sid, None)
        _clear_dispatch_cache()
        with rt_ctx.bind_runtime_context(turn_id=turn_id, run_id=run_id), bind_turn_evidence(rt_ev):
            TURN_EVIDENCE.register(rt_ev)
            cancelled = False
            timed_out = False
            try:
                # F5/F24: publish the active turn's identity + cancellation token
                # while the lock is held — abort() reads the sid for session
                # scoping, dispatch_tool binds the token via use_token. Same as
                # stream_prompt; cleared in the finally. P1: prompt() previously
                # never set these, so abort(session_id=other) saw active=None and
                # the GLOBAL abort killed this turn, and HTTP-callback tool
                # dispatches ran unbounded (F24 no-op) on this path.
                self._active_turn_sid = turn_sid
                _active_turn_context = (turn_sid, turn_id)
                _active_turn_token = CancellationToken(job_id=turn_id)
                _active_turn_turn_id = turn_id
                _active_turn_run_id = run_id
                _active_turn_session_id = turn_sid or None
                # Drop any residual events from a prior turn before sending, so they
                # cannot be attributed to this turn.
                await self._drain_stale_events()
                await self._rpc.request("prompt", data)

                # Drain events from the queue (non-streaming mode)
                content_parts: list[str] = []
                _drained_complete = False
                while True:
                    try:
                        event = await asyncio.wait_for(self._rpc.events.get(), timeout=PI_EVENT_DRAIN_TIMEOUT)
                        if event.get("type") == "agent_end":
                            _drained_complete = True
                            break
                        text = _extract_text_from_event(event)
                        if text:
                            content_parts.append(text)
                    except asyncio.TimeoutError:
                        timed_out = True
                        break
                # Best-effort: drain any leftover events so the next turn starts clean.
                await self._drain_remaining_turn_events()
                if timed_out:
                    # #554 defect 2: a drain that timed out without agent_end is
                    # a PARTIAL turn. Previously the caller received a 200 with
                    # truncated content and NO abort RPC — Pi kept generating
                    # tokens and executing tools (up to the 300s RPC timeout)
                    # while the client believed the turn succeeded. Surface an
                    # error instead; the finally below sends the abort RPC
                    # (mirrors stream_prompt's stall handling). First-wins settle
                    # keeps this "drain_timeout" classification over the generic
                    # failure_class in the except handler below.
                    rt_ev.settle(Outcome.FAILED, failure_class="drain_timeout")
                    raise PiRpcError(
                        f"Pi agent did not emit agent_end within {PI_EVENT_DRAIN_TIMEOUT}s "
                        "(event-drain timeout); the turn has been aborted."
                    )
                # Only a clean agent_end reaches this point — a timeout already
                # raised above, so PARTIAL is no longer reachable.
                rt_ev.settle(Outcome.SUCCEEDED)
            except asyncio.CancelledError:
                cancelled = True
                rt_ev.settle(Outcome.CANCELLED)
                raise
            except Exception as exc:  # noqa: BLE001
                rt_ev.settle(Outcome.FAILED, failure_class=type(exc).__name__, detail=str(exc)[:200])
                raise
            finally:
                if cancelled or timed_out:
                    # #554 defect 2: tell Pi to stop generating tokens / executing
                    # tools when the non-streaming turn ends without a clean
                    # agent_end (drain timeout) or is cancelled mid-drain — a
                    # retry otherwise duplicates side effects. Mirrors
                    # stream_prompt's finally guard; abort() is lock-free by
                    # design, so it is safe to call while the lock is still held.
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(self._abort_on_disconnect(turn_sid)),
                            timeout=5.0,
                        )
                    except (asyncio.TimeoutError, asyncio.CancelledError):
                        pass
                    except Exception as e:  # noqa: BLE001
                        logger.warning("[PiBridge] abort-on-failed-turn (turn=%s): %s", turn_sid, e)
                _cleanup_turn_state(turn_sid)
                # Clear the active-turn markers before releasing the lock.
                self._active_turn_sid = None
                _active_turn_token = None
                if _active_turn_context == (turn_sid, turn_id):
                    _active_turn_context = None
                _active_turn_turn_id = None
                _active_turn_run_id = None
                _active_turn_session_id = None
                self._lock.release()
                rt_ev.mark_ended()
                emit_turn_summary(rt_ev)
                TURN_EVIDENCE.remove(turn_id)

        return {
            "sessionId": turn_sid,
            "content": "".join(content_parts),
        }

    async def stream_prompt(
        self,
        message: str,
        session_id: Optional[str] = None,
        cartography_context: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a prompt to Pi agent, yielding SSE events.

        Args:
            message: User message
            session_id: Optional session ID
            cartography_context: Optional bounded harness verdict block,
                prepended ahead of the turn marker (see attach_turn_context).

        Yields:
            SSE-formatted event strings
        """
        global _active_turn_token, _active_turn_turn_id, _active_turn_run_id, _active_turn_session_id, _active_turn_context
        # Turn-scoped session id: every SSE payload is stamped with this local
        # value, not the mutable self._session_id field. Pi events carry no
        # session of their own, so attribution must come from the request that
        # owns this turn — not bridge instance state a prior turn could have
        # overwritten.
        turn_sid = session_id or ""
        from app.api.routes.pi_tools import get_bridge_secret
        from app.services.chat.pi_turn_context import attach_turn_context, issue_turn_token

        turn_id = _mint_turn_id()
        turn_token = issue_turn_token(get_bridge_secret(), turn_sid, turn_id)
        data: dict[str, Any] = {
            "message": attach_turn_context(message, turn_token, cartography_context or ""),
        }
        if turn_sid:
            data["sessionId"] = turn_sid

        # V3: 每个 Pi turn 铸一个 turn_id，显式透传给 harness 记录与 SSE 负载
        # （绝不用全局 set_correlation —— 单例 harness 跨 session 累积会互相污染）。
        # Runtime observability: first-class run_id (retires "run_id == session_id").
        # turn_id is already minted above so the signed turn token stays consistent.
        run_id = rt_ctx.new_run_id()
        _parent_ctx = rt_ctx.current_runtime_context()
        rt_ev = TurnEvidence(
            request_id=_parent_ctx.request_id if _parent_ctx else None,
            session_id=turn_sid or None,
            turn_id=turn_id,
            run_id=run_id,
        )

        # Hold the lock across send + drain + cleanup so turns are strictly
        # serial on the singleton bridge (Pi processes one prompt at a time).
        # try/finally ensures a client-disconnect GeneratorExit/CancelledError
        # at the yield still releases the lock, drains residual events, AND
        # (transport goal B-P0-1) sends an abort RPC so Pi stops generating
        # tokens and executing tools against an abandoned session.
        # Abort deliberately bypasses this lock (see abort()).
        with rt_ctx.bind_runtime_context(turn_id=turn_id, run_id=run_id), bind_turn_evidence(rt_ev):
            TURN_EVIDENCE.register(rt_ev)
            # #554 defect 1: the turn lock is held across the ENTIRE previous
            # turn (send + drain + cleanup), so a concurrent second stream
            # blocks here for up to the holder's whole turn (PI_RPC_TIMEOUT=300s
            # worst case). The SSE headers have already flushed by this point
            # (StreamingResponse starts iterating), yet ZERO bytes flowed while
            # waiting — proxies/LBs with shorter idle timeouts dropped user 2's
            # connection before their turn even started. Poll the lock and emit
            # a keepalive SSE comment (ignored by the client parser, bytes on
            # the wire) at the heartbeat cadence until acquired.
            while True:
                try:
                    await asyncio.wait_for(self._lock.acquire(), timeout=PI_HEARTBEAT_INTERVAL)
                    break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
            _session_executed_sets.pop(turn_sid, None)
            _clear_dispatch_cache()
            cancelled = False
            timed_out = False
            send_failed = False
            # G: set True when the pump observes the Pi subprocess dying mid-stream
            # (see the process_died_event watcher below). Initialized here so the
            # finally can read it even on the early-return send-failure path.
            process_died = False
            # F5/F24: publish the active turn's identity + cancellation token while
            # the lock is held — abort() reads the sid for session scoping,
            # dispatch_tool binds the token via use_token. Cleared in the finally.
            self._active_turn_sid = turn_sid
            _active_turn_context = (turn_sid, turn_id)
            _active_turn_token = CancellationToken(job_id=turn_id)
            _active_turn_turn_id = turn_id
            _active_turn_run_id = run_id
            _active_turn_session_id = turn_sid or None
            try:
                # Drop residual events from a prior turn so they can't be dequeued
                # and attributed to this session.
                await self._drain_stale_events()

                # Send prompt command
                try:
                    await self._rpc.request("prompt", data)
                except PiRpcError as e:
                    logger.error(f"[PiBridge] stream_prompt send failed: {e}")
                    send_failed = True
                    yield sse_event("task_error", {
                        "task_id": turn_sid,
                        "session_id": turn_sid,
                        "turn_id": turn_id,
                        "error": str(e),
                    })
                    yield sse_event("done", {"session_id": turn_sid})
                    return

                # Stream events from Pi. task_start is a bridge-emitted structural
                # event (not a Pi event), so first_event is NOT marked here — it is
                # marked on the first REAL Pi event below, so first_event/TTFT-proxy
                # measures prompt→first-Pi-event, not lock+drain+RPC-send.
                yield sse_event("task_start", {"task_id": turn_sid, "session_id": turn_sid, "turn_id": turn_id})

                # G: watch the subprocess death signal alongside the event queue so
                # a mid-stream Pi crash ends the turn promptly (error + done +
                # no abort) instead of parking on heartbeat silence for the whole
                # PI_EVENT_STREAM_TIMEOUT=180s stall budget and then retrying with
                # duplicate side effects. Bare MagicMock rpc fakes (used by other
                # test suites) auto-create a MagicMock for ``process_died_event``,
                # which is not awaitable — fall back to a never-set event so those
                # fakes degrade to the old heartbeat-only behavior.
                process_died_event = getattr(self._rpc, "process_died_event", None)
                if not isinstance(process_died_event, asyncio.Event):
                    process_died_event = asyncio.Event()

                silence_seconds = 0.0
                get_task = asyncio.ensure_future(self._rpc.events.get())
                died_task = asyncio.ensure_future(process_died_event.wait())
                pending = {get_task, died_task}
                try:
                    while True:
                        done, pending = await asyncio.wait(
                            pending, timeout=PI_HEARTBEAT_INTERVAL,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if died_task in done:
                            # Pi subprocess died mid-stream: the reader's finally
                            # already failed every pending future; end the turn now
                            # (error + done below), no abort RPC needed.
                            process_died = True
                            break
                        if get_task in done:
                            event = get_task.result()
                            silence_seconds = 0.0
                            rt_ev.mark_first_event()
                            sse = map_event_to_sse(
                                event,
                                turn_sid,
                                cache_lookup=lambda tool_call_id, _sid=turn_sid: get_cached_dispatch_result(
                                    tool_call_id, _sid
                                ),
                            )
                            # V3: 给原始 SSE 事件记录补 turn/run correlation（显式透传）。
                            # B-8: record against THIS turn's session harness, not the
                            # module-global "most recently created" harness — otherwise
                            # two interleaved sessions misattribute one session's events
                            # to the other (record_sse_event stamps self.session_id).
                            turn_harness = _get_session_harness(turn_sid)
                            if turn_harness is not None:
                                turn_harness.record_sse_event({
                                    **event, "run_id": run_id, "turn_id": turn_id,
                                })
                            if sse:
                                rt_ev.inc_sse_event()
                                # V3: step_result 负载加 additive turn_id 字段（前端 ack 时
                                # 通过 correlation 回传，闭环才完整）。
                                yield _inject_turn_id(sse, turn_id)
                            if event.get("type") == "agent_end":
                                break
                            # Re-arm the queue waiter for the next event; the
                            # completed waiter task is dropped from `pending` by
                            # the next asyncio.wait round.
                            get_task = asyncio.ensure_future(self._rpc.events.get())
                            pending.add(get_task)
                        else:
                            # Neither an event nor a death within one heartbeat
                            # interval -> accumulate silence, keepalive.
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
                finally:
                    # G: cancel the parked wait tasks so no queue-get / death-wait
                    # task outlives the turn; await their completion so nothing
                    # lingers past loop teardown. Also covers generator
                    # cancellation (client disconnect) mid-await.
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)

                if timed_out:
                    yield sse_event("error", {
                        "session_id": turn_sid,
                        "error": f"Pi agent stalled — no events for {int(PI_EVENT_STREAM_TIMEOUT)}s. The agent may be stuck; please retry.",
                    })
                if process_died:
                    yield sse_event("error", {
                        "session_id": turn_sid,
                        "error": "Pi agent process exited unexpectedly mid-stream. The agent's tools may have run partially; please retry.",
                    })

                yield sse_event("done", {"session_id": turn_sid})
            except (asyncio.CancelledError, GeneratorExit):
                # Client disconnected (page close / new send / session switch /
                # network drop). Re-raise after flagging so the finally sends the
                # abort RPC — otherwise Pi keeps running against an abandoned turn.
                cancelled = True
                raise
            finally:
                if process_died:
                    # G: the Pi subprocess is dead — the reader's finally already
                    # failed every pending future, so an abort RPC would only
                    # raise 'Pi process not started' (duplicate of the fast-fail).
                    # Still ignite the turn's cancellation token so in-flight
                    # HTTP-callback tool dispatches (bound via use_token) stop at
                    # their next checkpoint() instead of running to completion
                    # against a dead turn.
                    logger.error(
                        "[PiBridge] Pi subprocess exited mid-stream (turn=%s); "
                        "skipping abort RPC (reader already failed pending futures)",
                        turn_sid,
                    )
                    if _active_turn_token is not None:
                        _active_turn_token.cancel("pi process exited unexpectedly")
                elif cancelled or timed_out or send_failed:
                    # Tell Pi to stop generating tokens / executing tools. F10:
                    # this now covers the stall-timeout path too — previously a
                    # stalled turn yielded error+done and returned WITHOUT the
                    # abort RPC, so Pi kept executing tools (up to the 300s RPC
                    # timeout) and a user retry duplicated side effects.
                    # B-6: ``send_failed`` (the prompt RPC raised) must also
                    # abort — Pi may already have started executing the prompt
                    # and its tools, so without the abort a retry duplicates the
                    # side effects (same class of bug F10 fixed for the stall).
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
                # Clear the active-turn markers AFTER the abort above (abort reads
                # them to cancel the token) and before releasing the lock.
                self._active_turn_sid = None
                _active_turn_token = None
                _active_turn_turn_id = None
                _active_turn_run_id = None
                _active_turn_session_id = None
                if _active_turn_context == (turn_sid, turn_id):
                    _active_turn_context = None
                self._lock.release()
                # Runtime observability: settle turn outcome (cancelled ≠ failed),
                # emit the diagnostic summary, and unregister the evidence. Order:
                # outcome settled from flags captured above.
                if cancelled:
                    rt_ev.settle(Outcome.CANCELLED)
                elif timed_out or send_failed or process_died:
                    if timed_out:
                        failure_class = "pi_stall"
                    elif process_died:
                        failure_class = "pi_process_died"
                    else:
                        failure_class = "pi_send_error"
                    rt_ev.settle(Outcome.FAILED, failure_class=failure_class)
                else:
                    rt_ev.settle(Outcome.SUCCEEDED)
                rt_ev.mark_ended()
                emit_turn_summary(rt_ev)
                TURN_EVIDENCE.remove(turn_id)



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
