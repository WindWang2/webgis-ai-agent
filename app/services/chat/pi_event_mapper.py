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
from app.services.chat.error_taxonomy import error_class_for_tool_failure

logger = logging.getLogger(__name__)

# SSE content strings for compaction events.
COMPACTION_START_MSG = "[压缩上下文...]\n"
COMPACTION_END_MSG = "[上下文压缩完成]\n"

# SSE content strings for vendor auto-retry events (#855).
AUTO_RETRY_START_FMT = "[模型瞬时错误，{delay:.0f}s 后自动重试（第 {attempt}/{max_attempts} 次）：{error}]\n"
AUTO_RETRY_END_OK_FMT = "[自动重试成功（第 {attempt} 次），继续生成]\n"
AUTO_RETRY_END_FAIL_FMT = "[自动重试失败（第 {attempt} 次）：{error}]\n"


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
            first = content[0]
            if isinstance(first, dict):
                raw = first.get("text", str(result))
            else:
                raw = str(first)
        else:
            raw = result.get("message", str(result))
    else:
        raw = str(result)
    return _sanitize_for_client(raw)


# ── v1.6.x 诊断事件（ADR-0301 D5 / C18、C20）────────────────────────────────
#
# `extension_error`（v1.6.1 RPC 事件，json.md:193）与未知事件**不是** turn 终态、
# 不进 SSE（用户面噪音为零），但必须可观测：有界计数 + 脱敏摘录，供 doctor /
# 运维诊断消费。这里是有界记录的唯一权威；不假设任何事件的 success 语义。

#: 未知事件统计上限（防异常 vendor 刷爆内存）。
_DIAG_MAX_RECENT = 32


class _BoundedEventStats:
    """进程级有界事件统计：计数按类型聚合，摘录环形保留。"""

    #: 不同 kind 键数上界（R1-P3-9：防异常 vendor 用无限新类型撑爆 dict；
    #: 词表封闭时远达不到）。
    MAX_KINDS = 64

    def __init__(self, max_recent: int = _DIAG_MAX_RECENT) -> None:
        import threading

        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._recent: list[dict[str, str]] = []
        self._max_recent = max_recent

    def record(self, kind: str, detail: dict[str, str]) -> None:
        with self._lock:
            if kind not in self._counts and len(self._counts) >= self.MAX_KINDS:
                kind = "_overflow"
            self._counts[kind] = self._counts.get(kind, 0) + 1
            self._recent.append(detail)
            del self._recent[:-self._max_recent]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "counts": dict(self._counts),
                "recent": list(self._recent),
            }

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._recent.clear()


_diag_stats = _BoundedEventStats()


def vendor_event_diagnostics() -> dict:
    """extension_error / 未知事件的进程级有界快照（doctor 与运维消费）。"""
    return _diag_stats.snapshot()


def reset_vendor_event_diagnostics() -> None:
    """测试隔离用；生产路径不调用。"""
    _diag_stats.reset()


def _handle_unknown_event(
    event: dict,
    session_id: str,
    cache_lookup: Optional[Callable[[str], Optional[Any]]] = None,
    turn_stats: Optional[Callable[[], dict]] = None,
) -> Optional[str]:
    """vendor 新增事件词表的有界记录（v1.6.x 演进前瞻）：不假设 success、
    不进 SSE。已知但本仓有意不映射的高频事件（queue_update /
    bash_execution_update 等，见 ``KNOWN_UNMAPPED_EVENTS``）只静默跳过；
    真正未知的类型才 WARN + 计数（防刷屏）。"""
    event_type = str(event.get("type", "") or "unknown")[:64]
    if event_type in KNOWN_UNMAPPED_EVENTS:
        return None
    logger.warning("pi event of unmapped type received: %s", event_type)
    _diag_stats.record("unmapped_event", {"type": event_type})
    return None


#: vendor 文档化的词表里、本仓**有意不映射**为 SSE 的事件（高频/宿主侧
#: 自行处理）。集合依据 v1.6.1 docs/json.md 事件总表；vendor 新增的
#: 未文档化事件不会命中集合 → 走 WARN 计数路径。
#: agent_start/turn_*/message_start/message_end 是每 turn 必达的文档化
#: 事件（宿主从 agent_end/agent_settled/message_update 取权威值，这五个
#: 的冗余投影不进 SSE）——漏列会让每个 turn 刷 9 条 WARN 并冲空诊断
#: 摘录环（R1-P1-1 实测）。
KNOWN_UNMAPPED_EVENTS = frozenset({
    "agent_start",
    "turn_start",
    "turn_end",
    "message_start",
    "message_end",
    "tool_execution_update",
    "queue_update",
    "bash_execution_update",
    "entry_appended",
    "session_info_changed",
    "thinking_level_changed",
    "summarization_retry_scheduled",
    "summarization_retry_attempt_start",
    "summarization_retry_finished",
})


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
    """Build base SSE payload with common fields shared across step events.

    ADR-0288（W06）：嵌套调用事件（vendor C13）携带 ``parentToolCallId`` ——
    投影为加性 ``parent_tool_call_id`` 字段；顶层调用无该字段（旧消费者零
    漂移），前端据它把子步骤挂到父工具行下。
    """
    base = {
        "task_id": session_id,
        "step_id": event.get("toolCallId", ""),
        "session_id": session_id,
    }
    parent = event.get("parentToolCallId")
    if parent:
        base["parent_tool_call_id"] = str(parent)
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


def _append_widget_events(events: list[str], raw_result: object, session_id: str) -> None:
    """ADR-0194：从工具结果摘取 mount_widget 部件并追加 ui_action SSE。

    与 step_result 拼接为单个返回 chunk（路由批处理/恢复缓冲本就以
    合并 chunk 为形态，前端按 SSE 帧切分）。摘取失败静默降级。
    """
    try:
        from app.services.gis_situation.canvas_affordance import (
            extract_pending_widgets,
            sse_mount_widget,
        )

        widgets, _stripped = extract_pending_widgets(raw_result, "")
        for widget in widgets:
            events.append(sse_mount_widget(widget, session_id=session_id))
    except Exception:  # noqa: BLE001 — widget 下发是增值面，绝不影响 step_result
        return


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
            # H03 错误分类 parity：与 legacy step_error 同一分类函数，同一
            # failure_class/recovery_action/additive error_class 字段 ——
            # Pi 与 legacy 的工具失败语义经此收敛为同一事件契约。
            extra: dict = {
                "tool": tool_name,
                "error": getattr(cached, "error_msg", "") or "",
            }
            try:
                from app.services.chat.turn_recovery import classify_failure

                fc, ra = classify_failure(cached)
            except Exception:  # noqa: BLE001 — 分类失败不阻断 step_error
                fc, ra = None, None
            if fc:
                extra["failure_class"] = fc
                extra["error_class"] = error_class_for_tool_failure(fc)
                if ra:
                    extra["recovery_action"] = ra
            else:
                extra["error_class"] = error_class_for_tool_failure(None)
            return sse_event("step_error", _base_step_payload(event, session_id, extra))
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
        # ADR-0052 parity（Pi 兼容）：本步骤派生的 durable job —— 与 legacy
        # step_result 同款携带，前端把后台 GIS job 挂到该 tool step 下。
        bg_jobs = getattr(cached, "background_job_ids", None)
        if bg_jobs:
            payload["background_job_ids"] = list(bg_jobs)
        events = [sse_event("step_result", payload)]
        _append_widget_events(events, getattr(cached, "raw_result", None), session_id)
        return "".join(events)

    # 缓存未命中（Pi 重复回传 / dispatch 未走 service 路径）：退化到旧行为
    result = event.get("result", {})
    is_error = event.get("isError", False)
    logger.warning(
        f"[PiEventMapper] dispatch cache miss for toolCallId={tool_call_id} "
        f"(session={session_id}); falling back to Pi-echoed result without geojson_ref"
    )
    if is_error:
        error_msg = _extract_error_text(result)
        # 缓存未命中 → 无 dispatch 分类真值；诚实给 tool_error（语境已是
        # 工具执行失败），细分类不猜。
        return sse_event("step_error", _base_step_payload(event, session_id, {
            "tool": tool_name,
            "error": error_msg,
            "error_class": error_class_for_tool_failure(None),
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
    """``agent_end`` is NOT a turn-final event (#855).

    Vendor protocol (vendor/pi/packages/coding-agent/src/core/agent-session.ts,
    v0.81.1): ``agent_end{willRetry}`` fires after EVERY agent run — with
    ``willRetry=true`` before an auto-retry, and even ``willRetry=false`` may be
    followed by a compaction or queued-message continuation run. Only
    ``agent_settled`` (emitted in ``_runAgentPrompt``'s finally) unconditionally
    ends the turn — the same signal the vendor's own ``RpcClient.waitForIdle``
    waits on. The final text is already streamed as ``token`` deltas and the
    authoritative ``messages[]`` snapshot is stashed bridge-side; the
    ``task_complete`` SSE is emitted by ``_handle_agent_settled``.
    """
    return None


def _handle_agent_settled(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    """task_complete with truthful counters (audit #820, #855).

    ``turn_stats`` (injected by PiBridge) reports the turn's observed tool-step
    count and final text so the payload matches the legacy engine's
    len(task.steps)/content[:100] semantics instead of hardcoded zeros.
    """
    stats = turn_stats() if callable(turn_stats) else {}
    summary_src = stats.get("final_text", "")
    payload = {
        "step_count": int(stats.get("tool_step_count", 0) or 0),
        "summary": str(summary_src)[:100],
    }
    # ADR-0081：task_complete 有界披露地图成品完成态（DAG 完成 ≠ 地图
    # 完成；前端据此区分“turn 结束且地图已验证”与“turn 结束但未验证”）。
    map_product = stats.get("map_product")
    if (
        isinstance(map_product, dict)
        and map_product.get("status")
        and not map_product.get("reused_prior_completion")
    ):
        payload["map_product"] = {
            "status": str(map_product.get("status")),
            "summary": str(map_product.get("summary") or "")[:120],
        }
    # ADR-0183：goal satisfaction 投影（additive；verdict/signal/缺失码
    # 摘要 —— 「地图 READY ≠ 任务完成」的任务语义面。旧 map_product 无
    # 该键 / 投影失败 → 键省略，零漂移）。
    if isinstance(map_product, dict):
        try:
            from app.services.gis_harness.goal_satisfaction import (
                bounded_payload,
            )

            gs_payload = bounded_payload(map_product.get("goal_satisfaction"))
            if gs_payload is not None:
                payload["goal_satisfaction"] = gs_payload
        except Exception:  # noqa: BLE001 — 投影失败只少一键
            pass
    return sse_event("task_complete", _base_step_payload(event, session_id, payload))


def _handle_auto_retry_start(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    """Surface the vendor's auto-retry backoff as a transient content note (#855).

    Vendor payload: ``{attempt, maxAttempts, delayMs, errorMessage}``
    (agent-session.ts `_prepareRetry`). Emitted between
    ``agent_end{willRetry:true}`` and the retried run.
    """
    error = _sanitize_for_client(str(event.get("errorMessage", "") or "unknown error"))
    # ADR-0301 D5 / WP5：provider 失败/重试进有界诊断（typed 计数 + 脱敏
    # 摘录）——SSE 面之外，运维/治理可观测，不记录请求全文。
    try:
        _diag_stats.record("auto_retry_start", {
            "attempt": str(int(event.get("attempt", 0) or 0)),
            "max_attempts": str(int(event.get("maxAttempts", 0) or 0)),
            "error": error[:160],
        })
    except Exception:  # noqa: BLE001 — 诊断绝不影响 SSE 主路径
        pass
    content = AUTO_RETRY_START_FMT.format(
        delay=float(event.get("delayMs", 0) or 0) / 1000.0,
        attempt=int(event.get("attempt", 0) or 0),
        max_attempts=int(event.get("maxAttempts", 0) or 0),
        error=error,
    )
    return sse_event("content", {"content": content, "session_id": session_id})


def _handle_auto_retry_end(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    """Surface the vendor's auto-retry outcome (#855).

    Success (``success=true``) fires when a retried assistant response lands;
    failure carries ``finalError`` after retries are exhausted (or a retry was
    cancelled). The turn itself still ends on ``agent_settled``.
    """
    attempt = int(event.get("attempt", 0) or 0)
    success = bool(event.get("success"))
    # ADR-0301 D5 / WP5：重试结果进有界诊断（finalError 脱敏截断）。
    try:
        end_detail = {"attempt": str(attempt), "success": "1" if success else "0"}
        if not success:
            end_detail["final_error"] = _sanitize_for_client(
                str(event.get("finalError", "") or "unknown error"))[:160]
        _diag_stats.record("auto_retry_end", end_detail)
    except Exception:  # noqa: BLE001 — 诊断绝不影响 SSE 主路径
        pass
    if success:
        content = AUTO_RETRY_END_OK_FMT.format(attempt=attempt)
    else:
        error = _sanitize_for_client(str(event.get("finalError", "") or "unknown error"))
        content = AUTO_RETRY_END_FAIL_FMT.format(attempt=attempt, error=error)
    return sse_event("content", {"content": content, "session_id": session_id})


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


# extension_error 观测面字段上界（宿主完整性信号 —— 有界记录，不转发 SSE）。
_EXTENSION_ERROR_PATH_CAP = 120
_EXTENSION_ERROR_MSG_CAP = 300


def _handle_extension_error(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    """extension runner 的宿主完整性信号（#11264，v1.6.0 起 RPC 模式事件化）。

    Vendor 形状（rpc-mode.ts onError）：``{type: "extension_error",
    extensionPath, event, error}`` —— ``error`` 是 message 字符串，vendor 在
    RPC 重发时已丢弃 ``stack``，本层同样只收 message、不收堆栈。

    **丢弃 ≠ 吞掉**：返回 None（这是宿主/扩展完整性信号，不是会话内容，
    绝不污染 SSE 流），但必须经 logger.warning 结构化落盘，让模型回退、
    扩展 hook 崩溃等在观测面可见。原始事件本体由 bridge drain 循环
    （agent_pi_bridge.py ``record_sse_event``）无差别写入 turn evidence ring，
    断面恢复/审计可重放 —— 本层不重复造缝。
    """
    ext_path = str(event.get("extensionPath") or "unknown").replace("\\", "/")
    ext_name = ext_path.rsplit("/", 1)[-1][:_EXTENSION_ERROR_PATH_CAP] or "unknown"
    inner = event.get("event")
    if isinstance(inner, dict):
        event_kind = str(inner.get("type", "") or "")[:80]
    elif inner:
        event_kind = str(inner)[:80]
    else:
        event_kind = "<unknown>"
    raw_error = event.get("error")
    if isinstance(raw_error, dict):
        raw_error = raw_error.get("message", raw_error)
    # redact_paths：错误消息里不得出现宿主文件系统路径（无路径穿越）。
    error_msg = _sanitize_for_client(str(raw_error if raw_error not in (None, "") else "unknown"))
    error_msg = error_msg[:_EXTENSION_ERROR_MSG_CAP]
    logger.warning(
        "[PiEventMapper] extension_error (dropped from SSE, visible in logs/turn evidence): "
        "extension=%s event=%s error=%s",
        ext_name,
        event_kind,
        error_msg,
    )
    _diag_stats.record("extension_error", {
        "extension": ext_name, "event": event_kind[:64], "error": error_msg[:200],
    })
    return None


def _handle_extension_ui_request(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    """G29：原生 dialog 请求 → ``pi_interaction`` 卡片投影。

    纯翻译：卡片负载由交互服务在 sink 侧富化进 ``_webgis_interaction``
    （宿主交互 id / turn 归属 / 选项表 / deadline），本层不加状态。事件
    经 rpc.events 队列进入本循环 → 自动落入 TurnResumeRegistry ring，
    断线重连可重放（WP10）。
    """
    payload = event.get("_webgis_interaction")
    if not isinstance(payload, dict):
        return None
    return sse_event("pi_interaction", payload)


def _handle_extension_ui_update(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    """G29：卡片状态更新（answered/cancelled/timed_out/invalidated/resolved）。"""
    payload = event.get("_webgis_interaction")
    if not isinstance(payload, dict):
        return None
    return sse_event("pi_interaction_update", payload)


def _handle_extension_ui_fire(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    """G29：fire-and-forget 只读投影（notify/status/widget/title/editor 建议）。"""
    payload = event.get("_webgis_fire")
    if not isinstance(payload, dict):
        return None
    return sse_event("pi_ui", payload)


def _handle_pi_turn_message(event: dict, session_id: str, cache_lookup: Optional[Callable], *, turn_stats: Optional[Callable[[], dict]] = None) -> Optional[str]:
    """G29：运行中追加指令的接纳回执（steer/follow_up disposition）。"""
    payload = event.get("_webgis_steer")
    if not isinstance(payload, dict):
        return None
    return sse_event("pi_turn_message", payload)


_EVENT_HANDLERS: dict[str, Callable[..., Optional[str]]] = {
    "message_update": _handle_message_update,
    "tool_execution_start": _handle_tool_execution_start,
    "tool_execution_end": _handle_tool_execution_end,
    "agent_end": _handle_agent_end,
    "agent_settled": _handle_agent_settled,
    "auto_retry_start": _handle_auto_retry_start,
    "auto_retry_end": _handle_auto_retry_end,
    "compaction_start": _handle_compaction_start,
    "compaction_end": _handle_compaction_end,
    # #11264（v1.6.0）：extension runner 完整性信号 —— 只观测不转发 SSE。
    "extension_error": _handle_extension_error,
    # G29：原生 UI 子协议合成事件（sink 侧富化，本层纯翻译）。
    "extension_ui_request": _handle_extension_ui_request,
    "extension_ui_update": _handle_extension_ui_update,
    "extension_ui_fire": _handle_extension_ui_fire,
    "pi_steer_disposition": _handle_pi_turn_message,
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
                    consumed by the agent_settled handler (audit #820, #855).

    Returns:
        SSE-formatted string or None if unhandled
    """
    event_type = event.get("type", "")
    handler = _EVENT_HANDLERS.get(event_type)
    if handler is None:
        return _handle_unknown_event(event, session_id)
    return handler(event, session_id, cache_lookup, turn_stats=turn_stats)
