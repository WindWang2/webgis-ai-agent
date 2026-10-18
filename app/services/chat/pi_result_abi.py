"""Pi 结果 ABI v1（Goal 04, ADR-0224）—— ref-first 的有界 typed envelope。

master 现状：``PiToolResponse.details`` 是 ``Any``（slim 后的 raw result dict），
形状随工具漂移、无版本、大 payload 依赖启发式 64KB 截断。本模块提供**加性**
envelope（挂 ``details["resultAbi"]``），让 Pi/扩展消费一份稳定契约：

- status / summary / refs（geojson、result、image —— 大对象一律 ref-first）；
- map_action_ids / background_job_ids / evidence 引用；
- error class + reason code（错误分类，不泄漏原始异常）；
- envelope 有界（默认 4KB）：超出即收缩 summary / warnings，**绝不**内联大对象。
"""
from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover — 仅类型标注；运行时延迟导入防环
    from app.agent_pi_bridge import PiToolResponse

logger = logging.getLogger(__name__)

RESULT_ABI_VERSION = "pi_result.v1"
RESULT_ABI_POLICY = "pi_result_abi.v1"

#: 总闸（默认 ON；=0 → details 不挂 envelope，master 行为）。
PI_RESULT_ABI_ENV = "PI_RESULT_ABI"

#: envelope 序列化上限（字节）。超限先裁 warnings，再截 summary —— refs /
#: status / error class 永不因预算丢失。
_ENVELOPE_MAX_BYTES = 4 * 1024
_SUMMARY_MAX_CHARS = 1200
_WARNING_MAX_CHARS = 160
_MAX_WARNINGS = 6
_MAX_IDS = 16


def result_abi_enabled() -> bool:
    raw = (os.getenv(PI_RESULT_ABI_ENV) or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _bounded_ids(values: Optional[List[Any]]) -> List[str]:
    out: List[str] = []
    for value in values or []:
        text = str(value or "")
        if text and text not in out:
            out.append(text)
        if len(out) >= _MAX_IDS:
            break
    return out


def _bounded_summary(text: str) -> str:
    return str(text or "").strip()[:_SUMMARY_MAX_CHARS]


def build_result_envelope(
    result: Any,
    *,
    cancelled: bool = False,
    extra_warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """从 ToolDispatchResult（或取消路径）构造有界 envelope。

    纯投影：不吞不改 result 本体；任何字段缺席 → 省略（诚实缺省），
    不伪造。大 GeoJSON/raster 绝不内联 —— 只带 ref 串。
    """
    envelope: Dict[str, Any] = {"abi": RESULT_ABI_VERSION}
    if cancelled:
        envelope["status"] = "cancelled"
        envelope["summary"] = "Tool execution was cancelled before completion."
        envelope["error"] = {"class": "cancelled", "code": "OPERATION_CANCELLED"}
        return envelope

    status = str(getattr(result, "status", "") or "")
    envelope["status"] = status or "unknown"
    summary = _bounded_summary(getattr(result, "llm_payload", "") or "")
    raw = getattr(result, "raw_result", None)
    if isinstance(raw, dict):
        summary = _bounded_summary(
            str(raw.get("summary") or raw.get("message") or "") or summary
        ) or summary
    envelope["summary"] = summary

    refs: Dict[str, str] = {}
    geojson_ref = getattr(result, "geojson_ref", None)
    if geojson_ref:
        # 与兄弟 ref 同一 256 上限（review P3-1）：预算收缩序里 refs 恒保留，
        # 唯一未设限的 ref 会破坏 ≤4KB 承诺。
        refs["geojson"] = str(geojson_ref)[:256]
    if isinstance(raw, dict):
        for source_key, ref_key in (
            ("result_ref", "result"),
            ("imageRef", "image"),
            ("mapspec_fingerprint", "mapspec"),
        ):
            value = raw.get(source_key)
            if isinstance(value, str) and value:
                refs[ref_key] = value[:256]
    if refs:
        envelope["refs"] = refs

    map_action_ids = _bounded_ids(
        [ma.get("action_id") for ma in (getattr(result, "map_actions", None) or [])
         if isinstance(ma, dict)]
    )
    if map_action_ids:
        envelope["map_action_ids"] = map_action_ids

    background_job_ids = _bounded_ids(getattr(result, "background_job_ids", None))
    if background_job_ids:
        envelope["background_job_ids"] = background_job_ids

    evidence: Dict[str, Any] = {}
    capability_evidence = getattr(result, "capability_evidence", None)
    if isinstance(capability_evidence, dict):
        evidence["capability_bind"] = {
            k: capability_evidence[k]
            for k in ("decision", "code", "capability_id")
            if k in capability_evidence
        }
    action_evidence = getattr(result, "action_evidence", None)
    if isinstance(action_evidence, dict):
        evidence["action_ir"] = {
            k: action_evidence[k]
            for k in ("decision", "code") if k in action_evidence
        }
    if evidence:
        envelope["evidence"] = evidence

    warnings: List[str] = []
    for hint in (extra_warnings or []):
        text = str(hint or "").strip()[:_WARNING_MAX_CHARS]
        if text and text not in warnings:
            warnings.append(text)
    if status == "repeated":
        warnings.append("deduplicated: identical call already executed this turn")
    if warnings:
        envelope["warnings"] = warnings[:_MAX_WARNINGS]

    if status == "error":
        error_msg = str(getattr(result, "error_msg", "") or "")
        envelope["error"] = {
            "class": _error_class(error_msg, raw),
            "code": _error_code(raw),
        }
    return envelope


def _error_class(error_msg: str, raw: Any) -> str:
    """错误分类（封闭词表；不透传原始异常文本）。"""
    if isinstance(raw, dict):
        code = str(raw.get("code") or raw.get("error") or "")
        if code == "CAPABILITY_INELIGIBLE":
            return "capability_ineligible"
        if "validation" in code.lower():
            return "schema_validation"
    text = (error_msg or "").lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "cancel" in text:
        return "cancelled"
    if "not found" in text or "unknown tool" in text:
        return "not_found"
    if "permission" in text or "credential" in text or "auth" in text:
        return "authorization"
    return "tool_error"


def _error_code(raw: Any) -> str:
    if isinstance(raw, dict):
        code = str(raw.get("code") or "")
        if code:
            return code[:64]
    return "TOOL_ERROR"


def attach_result_abi(
    details: Any,
    result: Any,
    *,
    cancelled: bool = False,
    extra_warnings: Optional[List[str]] = None,
) -> Any:
    """把 envelope 挂进 details（加性 key；budget 内必然成功，失败保 details 原样）。

    details 非 dict（如 string/None）时包一层 ``{"raw": details}`` 以保不丢
    原内容 —— envelope 是加性披露，绝不清空既有字段。
    """
    if not result_abi_enabled():
        return details
    try:
        envelope = build_result_envelope(
            result, cancelled=cancelled, extra_warnings=extra_warnings
        )
        return _fit_envelope(details, envelope)
    except Exception:  # noqa: BLE001 — envelope 是披露面，绝不阻断工具返回
        logger.debug("[PiResultAbi] envelope build failed", exc_info=True)
        return details


def abi_tool_response(
    tool_call_id: str,
    text: str,
    details: Any,
    result: Any,
    *,
    is_error: Optional[bool] = None,
    cancelled: bool = False,
    extra_warnings: Optional[List[str]] = None,
) -> "PiToolResponse":
    """组装带 resultAbi envelope 的 ``PiToolResponse``（bridge 返回面统一入口）。

    与 bridge 原 4 个返回点逐点等价：``is_error=None`` → isError 按
    ``result.status == "error"`` 派生。``PiToolResponse`` 延迟导入
    （bridge 模块级导入本模块，防环）。
    """
    from app.agent_pi_bridge import PiToolResponse

    if is_error is None:
        is_error = str(getattr(result, "status", "") or "") == "error"
    return PiToolResponse(
        toolCallId=tool_call_id,
        content=[{"type": "text", "text": text}],
        details=attach_result_abi(
            details, result, cancelled=cancelled, extra_warnings=extra_warnings
        ),
        isError=is_error,
    )


def _fit_envelope(details: Any, envelope: Dict[str, Any]) -> Any:
    """envelope 预算收缩（先 warnings 后 summary；status/refs/error 恒保留）。"""
    while len(json.dumps(envelope, ensure_ascii=False).encode("utf-8")) > _ENVELOPE_MAX_BYTES:
        if envelope.get("warnings"):
            envelope["warnings"] = envelope["warnings"][:-1]
            continue
        if envelope.get("evidence"):
            envelope.pop("evidence", None)
            continue
        summary = str(envelope.get("summary") or "")
        if len(summary) > 160:
            envelope["summary"] = summary[:160]
            continue
        if envelope.get("summary"):
            envelope.pop("summary", None)
            continue
        break  # 已最小化（status/refs/error 不再裁）
    if not isinstance(details, dict):
        details = {"raw": details}
    out = dict(details)
    out["resultAbi"] = envelope
    return out
