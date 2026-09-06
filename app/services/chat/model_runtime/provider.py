"""Provider 适配层 —— 失败分类 / 响应与错误归一化（ADR-0102 Wave 4, §17/§20）。

不做 transport 重写：llm_client（httpx，OpenAI 兼容）继续是唯一通道，其
「只在连接期重试、绝不在流中重试」的既有纪律保持（ADR-0043 同源理由：
流中重试会重复 token，发送后重试可能重复执行工具）。本模块在应用侧补齐：

1. **失败分类**（§20 的 9 类）—— fallback 决策与健康记录的统一词汇；
2. **归一化视图** —— finish_reason / usage / 工具调用形态的稳定枚举；
3. **错误体消毒**（§39）—— provider 返回的错误体在回注模型上下文前
   有界化 + 控制字符清洗（provider 内容按不可信处理）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class FailureKind(str, Enum):
    """§20 fallback 语义的失败分类。"""

    TRANSPORT = "transport"                # 连接失败 / DNS / 池超时
    TIMEOUT = "timeout"                    # 连接后墙钟超时（含流中断）
    RATE_LIMIT = "rate_limit"              # 429 / quota
    PROVIDER_UNAVAILABLE = "provider_unavailable"   # 5xx / 503
    UNSUPPORTED = "unsupported"            # 请求了模型不支持的能力（422 族）
    CONTEXT_TOO_LARGE = "context_too_large"  # 400 context/exceed 变体
    INVALID_TOOL_SCHEMA = "invalid_tool_schema"     # provider 拒绝 tools 定义
    MODEL_REFUSAL = "model_refusal"        # 模型内容拒绝（finish=content_filter）
    MALFORMED_OUTPUT = "malformed_output"  # 流断裂/重复 chunk/无法解析的工具参数
    UNKNOWN = "unknown"


_STATUS_MAP = {
    408: FailureKind.TIMEOUT,
    429: FailureKind.RATE_LIMIT,
    500: FailureKind.PROVIDER_UNAVAILABLE,
    502: FailureKind.PROVIDER_UNAVAILABLE,
    503: FailureKind.PROVIDER_UNAVAILABLE,
    504: FailureKind.PROVIDER_UNAVAILABLE,
}

_CONTEXT_HINTS = ("context length", "maximum context", "too many tokens",
                  "input too long", "context_length_exceeded", "上下文过长")
_SCHEMA_HINTS = ("tool_choice", "tool schema", "invalid tools", "tools definition",
                 "unsupported parameter: tools")
_REFUSAL_HINTS = ("content_filter", "refus")


def classify_status_failure(status: int, body_text: str = "") -> FailureKind:
    """HTTP 状态 + 响应体提示 → FailureKind。"""
    kind = _STATUS_MAP.get(status)
    if kind:
        return kind
    lowered = (body_text or "").lower()
    if status == 400 or status == 413 or status == 422:
        if any(h in lowered for h in _CONTEXT_HINTS):
            return FailureKind.CONTEXT_TOO_LARGE
        if any(h in lowered for h in _SCHEMA_HINTS):
            return FailureKind.INVALID_TOOL_SCHEMA
        return FailureKind.UNSUPPORTED
    if status == 404:
        return FailureKind.UNSUPPORTED  # 模型不存在 → 换模型才有用
    return FailureKind.UNKNOWN


def classify_exception(exc: BaseException) -> FailureKind:
    """异常 → FailureKind（与 llm_client 的重试门同源词汇）。"""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "connecttimeout" in name or "connecttimeout" in msg:
        # review R1 minor：ConnectTimeout 是连接相位失败（可重试类），
        # 显式归类 TRANSPORT 而非落入 TIMEOUT。
        return FailureKind.TRANSPORT
    if "timeout" in name or "timed out" in msg:
        return FailureKind.TIMEOUT
    # review R1 minor：修正 or/and 优先级（原式 = a or (b and c)）。
    if "connect" in name or ("connect" in msg and "error" in name):
        return FailureKind.TRANSPORT
    if "pool" in msg:
        return FailureKind.TRANSPORT
    if "disconnect" in msg or "remote protocol" in msg or "server disconnected" in msg:
        return FailureKind.TIMEOUT
    if "json" in name or "decode" in name:
        return FailureKind.MALFORMED_OUTPUT
    return FailureKind.UNKNOWN


# ---------------------------------------------------------------------------
# 归一化视图
# ---------------------------------------------------------------------------

class FinishReason(str, Enum):
    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"


def normalize_finish_reason(raw: Any) -> FinishReason:
    if not raw:
        return FinishReason.UNKNOWN
    val = str(raw).strip().lower()
    if val in ("stop", "end_turn", "eos"):
        return FinishReason.STOP
    if val in ("tool_calls", "tool_use", "function_call", "tool_calls_requested"):
        return FinishReason.TOOL_CALLS
    if val in ("length", "max_tokens", "truncated"):
        return FinishReason.LENGTH
    if "filter" in val or "refus" in val:
        return FinishReason.CONTENT_FILTER
    return FinishReason.UNKNOWN


@dataclass(frozen=True)
class ProviderResponseView:
    """call_llm 结果的归一化只读视图（replay/评测/trace 共用词汇）。"""

    content: str
    reasoning: str
    tool_calls: Tuple[Dict[str, Any], ...]
    finish_reason: FinishReason
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    raw_result_keys: Tuple[str, ...] = ()

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def view_response(result: Any) -> ProviderResponseView:
    """把 llm_client.call_llm 的返回适配为 ProviderResponseView（防御式）。"""
    if not isinstance(result, dict):
        return ProviderResponseView(
            content=str(result or ""), reasoning="", tool_calls=(),
            finish_reason=FinishReason.UNKNOWN,
        )
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    return ProviderResponseView(
        content=str(result.get("content") or result.get("text") or ""),
        reasoning=str(result.get("reasoning_content") or ""),
        tool_calls=tuple(result.get("tool_calls") or ()),
        finish_reason=normalize_finish_reason(result.get("finish_reason")),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        raw_result_keys=tuple(sorted(result.keys())),
    )


# ---------------------------------------------------------------------------
# 错误体消毒（§39：provider 内容 = 不可信输入）
# ---------------------------------------------------------------------------

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FENCE_BREAK = re.compile(
    r"</?(?:system|tool|env|untrusted|user|assistant|instructions|role|context)[^>]*>",
    re.IGNORECASE,
)


def sanitize_provider_error(text: str, max_chars: int = 600) -> str:
    """provider 错误体回注模型前的消毒：控制字符剥离、伪标签围栏剥离、有界。

    提示注入面：错误体可能回显请求片段（含系统提示）或携带伪造指令文本。
    消毒不追求语义净化，只保证 (a) 有界 (b) 不含控制字符 (c) 不含可被
    下游 XML 围栏机制误认的闭合标签。
    """
    if not text:
        return ""
    cleaned = _CONTROL_CHARS.sub(" ", str(text))
    cleaned = _FENCE_BREAK.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "…"
    return cleaned


def failure_evidence(kind: FailureKind, detail: str = "") -> Dict[str, Any]:
    """fallback 事件的证据负载（trace 用；有界、无内容存储）。"""
    return {
        "failure_kind": kind.value,
        "detail": sanitize_provider_error(detail, max_chars=200),
    }
