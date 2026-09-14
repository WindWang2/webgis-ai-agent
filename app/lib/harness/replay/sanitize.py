"""ReplayTrace sanitize：白名单键 + 秘密剥离 + 字节上限 + 降级 digest-only。

纪律（decisions D5 / ADR-0183 决策二）：
- **不进 trace**：prompt/CoT/消息历史全文（``llm_payload``、``messages``、
  ``reasoning``…）、原始 GeoJSON/结果体（只留 ref + digest + 尺寸）、
  任何键名命中秘密模式的值；
- **有界**：字符串按类别截断；单 trace 超过总预算降级为 digest-only 模式
  并打 ``truncated=true``（绝不静默丢字段、也绝不无界）；
- 与生产先例对齐：链 payload 已过 ``bound_meta``（app/lib/runtime/trace.py），
  provenance 已有 ``redact_provenance_args`` —— 本模块是打包层的最后一道。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Tuple

REDACTED = "[REDACTED]"

#: 这些键的**值**整体不进 trace（连截断都不行 —— CoT/prompt 全文禁令）。
FORBIDDEN_VALUE_KEYS = frozenset({
    "llm_payload", "prompt", "messages", "reasoning", "cot", "scratchpad",
    "system_prompt", "tool_definition", "raw_result", "features", "geojson",
    "result", "payload", "data", "content",
})

#: 键名命中这些子串（大小写不敏感）→ 值替换为 REDACTED。
SECRET_KEY_MARKERS = (
    "token", "secret", "password", "passwd", "authorization", "api_key",
    "apikey", "credential", "cookie", "session_key", "private_key",
    "privatekey", "passphrase", "access_key", "auth_key", "signing_key",
)

#: 结果/数据体键：值替换为 {digest, bytes}（形状保留、体积归零）。
BODY_KEYS = frozenset({
    "geojson_ref", "ref_descriptor", "llm_payload_bytes", "slim_event",
})

_STR_MAX_DEFAULT = 512
_ARGS_BYTES_MAX = 2048
_NESTED_DEPTH_MAX = 6
_LIST_ITEM_MAX = 32


def bounded_str(value: Any, limit: int = _STR_MAX_DEFAULT) -> str:
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, default=str)
    if len(text) > limit:
        text = text[:limit] + f"…(+{len(text) - limit}B)"
    return scrub_secret_strings(text)


def _is_secret_key(key: str) -> bool:
    # 连字符/空格归一（X-Api-Key / Private Key 等表单）后再匹配。
    lowered = re.sub(r"[-\s]+", "_", key.lower())
    return any(marker in lowered for marker in SECRET_KEY_MARKERS)


#: 字符串值内的秘密模式（repr 形态嵌套载荷、header 片段）。
#: 只匹配高置信形态，避免误伤普通文本。
_SECRET_STRING_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"(?i)bearer\s*:?[\s]*[A-Za-z0-9._\-]{8,}"),
    re.compile(
        r"(?i)(api[-_]?key|secret|passphrase|password|token|authorization)"
        r"\s*[=:]\s*['\"]?[A-Za-z0-9._\-]{6,}"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def scrub_secret_strings(text: str) -> str:
    """字符串值内的秘密模式 → REDACTED（repr 嵌套载荷的兜底防线）。"""
    out = text
    for pattern in _SECRET_STRING_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


def digest_payload(value: Any) -> Dict[str, Any]:
    """任意载荷 → {digest, bytes}（体积归零、形状可追溯）。"""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return {
        "digest": hashlib.sha256(text.encode("utf-8")).hexdigest()[:32],
        "bytes": len(text.encode("utf-8")),
    }


def sanitize_value(
    value: Any,
    *,
    str_limit: int = _STR_MAX_DEFAULT,
    depth: int = 0,
) -> Any:
    """递归消毒：秘密键 REDACTED、禁值键剥除、长串截断、列表有界。"""
    if depth > _NESTED_DEPTH_MAX:
        return digest_payload(value)
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return bounded_str(value, str_limit)
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in list(value.items())[:64]:
            key_str = str(key)
            if _is_secret_key(key_str):
                out[key_str] = REDACTED
            elif key_str in FORBIDDEN_VALUE_KEYS:
                out[key_str] = digest_payload(item)
            else:
                out[key_str] = sanitize_value(item, str_limit=str_limit, depth=depth + 1)
        if len(value) > 64:
            out["_dropped_keys"] = len(value) - 64
        return out
    if isinstance(value, (list, tuple)):
        items = list(value)[:_LIST_ITEM_MAX]
        cleaned = [sanitize_value(v, str_limit=str_limit, depth=depth + 1) for v in items]
        if len(value) > _LIST_ITEM_MAX:
            cleaned.append({"_dropped_items": len(value) - _LIST_ITEM_MAX})
        return cleaned
    return bounded_str(value, str_limit)


def sanitize_arguments(arguments: Any, *, max_bytes: int = _ARGS_BYTES_MAX) -> Tuple[Dict[str, Any], int, bool]:
    """工具调用参数消毒。返回 (sanitized, bytes, truncated)。

    超预算时降级为 digest-only（保留行为可追溯性，丢明文）。
    """
    sanitized = sanitize_value(arguments if isinstance(arguments, dict) else
                               {"_value": arguments})
    try:
        size = len(json.dumps(sanitized, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        size = max_bytes + 1
    if size <= max_bytes:
        return sanitized, size, False
    return {"_digest_only": digest_payload(arguments)}, size, True


def sanitize_tool_result_ref(result: Any) -> Dict[str, Any]:
    """工具结果 → ref 摘要块（B1：只留 receipts 引用，不留载荷）。"""
    if isinstance(result, dict):
        ref = result.get("geojson_ref") or result.get("ref")
        mapspec_fp = result.get("mapspec_fingerprint")
        summary = {
            "geojson_ref": bounded_str(ref, 256) if ref else None,
            "mapspec_fingerprint": bounded_str(mapspec_fp, 128) if mapspec_fp else None,
            "status": bounded_str(result.get("status"), 32) if result.get("status") else None,
        }
        return {**{k: v for k, v in summary.items() if v is not None},
                "digest": digest_payload(result)}
    return {"digest": digest_payload(result)}
