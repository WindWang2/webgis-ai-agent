"""HTTP 状态码信号检测（GOAL 05 / ADR-0281，review R2-1/R2-4 收敛点）。

词边界 + **语境词**双门：裸数字子串会撞坐标/计数（"buffered 1500
features"不得判瞬态；"exported 403 records"不得判权限拒绝）。

语境文本**不含 status 字段**——生产 TDS 错误结果的 status 恒为
"error"/"failed"（is_error_dict 词表），把它算进语境会让语境门恒真
（review 第二轮 P2-1 的复现根因）；语境只看 code/error_type/message。

单一事实源：failure_taxonomy 与 recovery_policy.diagnose 共用本模块，
消除双份手维护漂移（review P3-3）。
"""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"(?<![0-9])(429|500|502|503|504|401|403)(?![0-9])")

#: 语境词（小写子串；请求/错误/远端语义）。
_CONTEXT_WORDS = (
    "http", "status", "error", "fail", "upstream", "provider",
    "response", "returned", "code", "request", "service", "unavailable",
    "gateway", "temporarily", "denied", "unauthorized",
    "错误", "失败", "上游", "服务", "请求", "拒绝", "不可用",
)

_RATE_LIMIT_CODES = frozenset({"429"})
_SERVER_ERROR_CODES = frozenset({"500", "502", "503", "504"})
_PERMISSION_CODES = frozenset({"401", "403"})


def _context_present(text: str) -> bool:
    return any(w in (text or "") for w in _CONTEXT_WORDS)


def http_status_codes(text: str) -> frozenset:
    """词边界命中的状态码集合（无语境判定 —— 低层原语）。"""
    if not text:
        return frozenset()
    return frozenset(_WORD_RE.findall(text))


def has_rate_limit_signal(*, context_text: str) -> bool:
    """429 信号：词边界 + 语境（context_text 不含 status 字段）。"""
    codes = http_status_codes(context_text)
    return bool(codes & _RATE_LIMIT_CODES) and _context_present(context_text)


def has_server_error_signal(*, context_text: str) -> bool:
    """5xx 信号：词边界 + 语境。"""
    codes = http_status_codes(context_text)
    return bool(codes & _SERVER_ERROR_CODES) and _context_present(context_text)


def has_permission_code_signal(*, context_text: str) -> bool:
    """401/403 信号：词边界 + 语境（词面 permission/forbidden 等不受此门）。"""
    codes = http_status_codes(context_text)
    return bool(codes & _PERMISSION_CODES) and _context_present(context_text)


def any_http_status_signal(*, context_text: str) -> bool:
    """任一数字状态码 + 语境（粗分类器 timeout 归并入口）。"""
    codes = http_status_codes(context_text)
    return bool(codes) and _context_present(context_text)


__all__ = [
    "any_http_status_signal",
    "has_permission_code_signal",
    "has_rate_limit_signal",
    "has_server_error_signal",
    "http_status_codes",
]
