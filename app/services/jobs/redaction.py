"""job 载荷脱敏与体积上限（规范 §7 / §35 / §38）。

durable job 行会被前端任务中心读取，而任务参数/结果里可能混进：完整 GeoJSON
（50 MB 级）、raster 数组、signed URL、token、密码、原始 traceback。直接落库会
同时踩三个坑：DB 行膨胀、任务列表查询变慢、凭据经任务中心泄漏。

规则：
  * 敏感 key 一律替换为 "[REDACTED]"（按 key 名匹配，大小写无关，子串命中）。
  * 大型几何体（features / coordinates / 栅格数组）不落库，替换为摘要
    ``{"__omitted__": "features", "count": N}``。
  * 字符串截断；序列化后仍超上限则整体降级为 stub 摘要。
  * 错误只留 "ExcType: 首行消息"，绝不落 traceback。

这些函数是纯函数，无 IO，便于单测。
"""
from __future__ import annotations

import json
from typing import Any

#: 参数摘要上限（序列化后字节）。
MAX_PARAMETERS_BYTES = 8_192
#: 结果摘要上限。比参数略宽 —— 结果里常有统计量与 bbox。
MAX_RESULT_BYTES = 16_384
#: 单个字符串字段截断长度。
MAX_STRING_CHARS = 512
#: 错误摘要长度。
MAX_ERROR_CHARS = 500
#: 递归深度上限，防御自引用/超深结构。
MAX_DEPTH = 6
#: 集合类字段保留的元素个数上限。
MAX_SEQUENCE_ITEMS = 20

REDACTED = "[REDACTED]"

#: 命中即脱敏的 key 子串。覆盖规范 §35 点名的 credentials / signed URL / secret /
#: token / password / auth header，以及 prompt 类原文。
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "authorization",
    "auth_header",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "signed_url",
    "signature",
    "cookie",
    "session_token",
    "owner_token",
    "bearer",
)

#: 体积敏感、不做内容保留只做摘要的 key。
BULK_KEYS: tuple[str, ...] = (
    "features",
    "coordinates",
    "geojson",
    "geometry",
    "raster",
    "array",
    "image",
    "image_base64",
    "png",
    "tiles",
    "band_data",
    "values",
    "pixels",
)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _is_bulk(key: str) -> bool:
    return key.lower() in BULK_KEYS


def _summarize_bulk(key: str, value: Any) -> Any:
    """把大对象替换成结构化摘要，保留对用户仍有意义的元信息。"""
    if isinstance(value, (list, tuple)):
        return {"__omitted__": key, "count": len(value)}
    if isinstance(value, dict):
        summary: dict[str, Any] = {"__omitted__": key}
        # GeoJSON FeatureCollection 的 type/feature 数仍然值得展示
        if isinstance(value.get("type"), str):
            summary["type"] = value["type"]
        feats = value.get("features")
        if isinstance(feats, list):
            summary["count"] = len(feats)
        return summary
    if isinstance(value, (str, bytes)):
        return {"__omitted__": key, "bytes": len(value)}
    return {"__omitted__": key}


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "[TRUNCATED_DEPTH]"

    if value is None or isinstance(value, (bool, int, float)):
        return value

    if isinstance(value, bytes):
        return {"__omitted__": "bytes", "bytes": len(value)}

    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            return value[:MAX_STRING_CHARS] + f"…[+{len(value) - MAX_STRING_CHARS} chars]"
        return value

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, raw_val in value.items():
            key = str(raw_key)
            if _is_sensitive(key):
                out[key] = REDACTED
            elif _is_bulk(key):
                out[key] = _summarize_bulk(key, raw_val)
            else:
                out[key] = _scrub(raw_val, depth + 1)
        return out

    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if len(items) > MAX_SEQUENCE_ITEMS:
            head = [_scrub(v, depth + 1) for v in items[:MAX_SEQUENCE_ITEMS]]
            return head + [{"__omitted__": "items", "count": len(items) - MAX_SEQUENCE_ITEMS}]
        return [_scrub(v, depth + 1) for v in items]

    # 其它类型（datetime / ORM 对象 / numpy 标量…）退化为可读字符串
    return _scrub(str(value), depth + 1)


def _fits(value: Any, max_bytes: int) -> bool:
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return False
    return len(encoded.encode("utf-8")) <= max_bytes


def _shrink_to_limit(scrubbed: Any, max_bytes: int, label: str) -> Any:
    """逐级降级直到满足体积上限，保证落库大小有硬上界。"""
    if _fits(scrubbed, max_bytes):
        return scrubbed

    # 第一级：只保留标量字段（丢掉所有嵌套结构）
    if isinstance(scrubbed, dict):
        flat = {
            k: v
            for k, v in scrubbed.items()
            if v is None or isinstance(v, (bool, int, float)) or (isinstance(v, str) and len(v) <= 120)
        }
        flat["__truncated__"] = label
        if _fits(flat, max_bytes):
            return flat

    # 第二级：只留一个 stub
    return {"__truncated__": label, "note": "payload exceeded storage limit"}


def safe_parameters(params: Any) -> dict[str, Any]:
    """脱敏 + 限长的参数摘要，可直接写入 analysis_tasks.parameters。"""
    scrubbed = _scrub(params if params is not None else {})
    if not isinstance(scrubbed, dict):
        scrubbed = {"value": scrubbed}
    return _shrink_to_limit(scrubbed, MAX_PARAMETERS_BYTES, "parameters")


def safe_result(result: Any) -> dict[str, Any] | None:
    """脱敏 + 限长的结果摘要，可直接写入 analysis_tasks.result_summary。

    巨型结果（完整 GeoJSON / raster）只留指针与统计量 —— 真实产物应通过
    ``result_ref``（artifact id / 路径）引用，而不是塞进 task 行。
    """
    if result is None:
        return None
    scrubbed = _scrub(result)
    if not isinstance(scrubbed, dict):
        scrubbed = {"value": scrubbed}
    return _shrink_to_limit(scrubbed, MAX_RESULT_BYTES, "result_summary")


def safe_error(error: BaseException | str | None) -> str | None:
    """单行错误摘要。绝不包含 traceback（规范 §7 / §10）。"""
    if error is None:
        return None
    if isinstance(error, BaseException):
        message = str(error).strip() or error.__class__.__name__
        text = f"{error.__class__.__name__}: {message}"
    else:
        text = str(error).strip()
    # traceback 是多行的 —— 只留第一行，杜绝 "Traceback (most recent call last)" 泄漏
    text = text.splitlines()[0] if text else ""
    if len(text) > MAX_ERROR_CHARS:
        text = text[:MAX_ERROR_CHARS] + "…"
    return text or None


#: dispatch 描述符上限。它必须**忠实**可重跑，所以不做内容截断 —— 超限就整体丢弃，
#: 让 retry 明确不可用，而不是留一份跑不起来的残缺参数。
MAX_DISPATCH_BYTES = 4_096


def _contains_sensitive_key(value: Any, depth: int = 0) -> bool:
    """递归判断结构里是否出现敏感键名。"""
    if depth > MAX_DEPTH:
        return False
    if isinstance(value, dict):
        for key, val in value.items():
            if _is_sensitive(str(key)):
                return True
            if _contains_sensitive_key(val, depth + 1):
                return True
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive_key(v, depth + 1) for v in value)
    return False


def safe_dispatch_spec(spec: Any) -> dict[str, Any] | None:
    """校验并返回可用于重跑的 dispatch 描述符 ``{task, args, kwargs}``。

    与 ``safe_parameters`` 不同，这里**不**截断内容（截断后的参数无法重跑），只做两件事：
      1. 拒绝任何携带敏感键的 kwargs —— 凭据绝不落库（规范 §35）；
      2. 超过体积上限就整体丢弃，并打上 ``__truncated__`` 标记，store 据此判定
         该 job 不可重试，而不是提供一个必然失败的 retry。

    该字段永不通过 API 返回（JobView 里没有它）。
    """
    if not isinstance(spec, dict):
        return None
    task = spec.get("task")
    if not task or not isinstance(task, str):
        return None

    args = spec.get("args") or []
    kwargs = spec.get("kwargs") or {}
    if not isinstance(args, (list, tuple)) or not isinstance(kwargs, dict):
        return None

    # 递归筛查：args 里的嵌套 dict（例如一个带 properties.token 的 GeoJSON）
    # 同样可能夹带凭据，只看顶层 kwargs 的 key 是不够的。
    if _contains_sensitive_key(kwargs) or _contains_sensitive_key(list(args)):
        return {"__truncated__": "dispatch_spec", "reason": "sensitive_argument"}

    candidate: dict[str, Any] = {"task": task, "args": list(args), "kwargs": dict(kwargs)}
    if not _fits(candidate, MAX_DISPATCH_BYTES):
        return {"__truncated__": "dispatch_spec", "reason": "too_large"}
    return candidate
