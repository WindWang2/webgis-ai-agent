"""记忆写入的消毒层（R2/R8 支撑）。

职责（fail-closed 与 fail-safe 的分工）：
- ``sanitize_value``：**尽力裁剪**——剥凭证形键、遮蔽用户绝对路径、截断
  超长字符串、限制嵌套深度与键数。返回裁剪后的 value；
- ``assert_no_secrets``：**硬拒绝**——裁剪后仍含高置信凭证形态（bearer/
  private key / 连接串密码段）时抛错，宁可不记也不落库。

红线：不存 credential；本地绝对路径（用户目录形态）不得进入记忆——
ref-first，路径一律转 ref/描述符。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from app.services.gis_memory.contract import (
    REFS_MAX,
    MemoryPolicyError,
)

_MAX_STR = 256
_MAX_DEPTH = 4
_MAX_KEYS = 24
_MAX_LIST = 16

#: 键名黑名单（两段式）：高信号子串匹配 + 整键等值匹配。
#: 注意 "version_token"/"revision_token" 是数据供给的合法语义字段，必须放行
#: （等值匹配只剥裸 "token"/"auth_token"，不碰带语义前缀的 token 变体）。
_SECRET_KEY_SUBSTR_RE = re.compile(
    r"(password|passwd|secret|credential|authorization|api[_-]?key|"
    r"apikey|access[_-]?key|private[_-]?key|cookie)",
    re.IGNORECASE,
)
_SECRET_KEY_EXACT = {
    "token", "auth_token", "auth", "bearer", "session_id", "sessionid",
    "set_cookie", "password_hash",
}

#: 用户/家目录绝对路径（Windows/Unix 形态）→ 遮蔽。
_USER_PATH_RE = re.compile(
    r"""(?:[A-Za-z]:\\+Users\\+[^\s"']+|/home/[^\s"']+|/Users/[^\s"']+)""",
)

#: 高置信凭证形态（硬拒绝线）。
_SECRET_VALUE_RE = re.compile(
    r"(Bearer\s+[A-Za-z0-9._\-]{16,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"://[^/\s:]+:[^@\s/]{6,}@)"   # scheme://user:password@host
)

_MEMORY_SCOPE_ID_BAD_RE = re.compile(r"[\s'\"\\<>;]|\x00")


def _scrub_str(text: str) -> str:
    scrubbed = _USER_PATH_RE.sub("[redacted-path]", text)
    return scrubbed[:_MAX_STR]


def _walk(value: Any, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return None
    if isinstance(value, str):
        return _scrub_str(value)
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in list(value.items())[:_MAX_KEYS]:
            key_s = str(key)[:64]
            if _SECRET_KEY_SUBSTR_RE.search(key_s) or key_s.lower() in _SECRET_KEY_EXACT:
                continue
            cleaned = _walk(item, depth + 1)
            if cleaned is None and item is not None:
                continue
            out[key_s] = cleaned
        return out
    if isinstance(value, (list, tuple)):
        cleaned_list: List[Any] = []
        for item in list(value)[:_MAX_LIST]:
            cleaned = _walk(item, depth + 1)
            if cleaned is not None:
                cleaned_list.append(cleaned)
        return cleaned_list
    return str(value)[:_MAX_STR]


def sanitize_value(value: Any) -> Dict[str, Any]:
    """裁剪 value dict；非 dict 输入包装为 {"value": ...}；空值归空 dict。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        value = {"value": value}
    cleaned = _walk(value, 0)
    return cleaned if isinstance(cleaned, dict) else {}


def sanitize_refs(refs: Any) -> List[str]:
    """refs：只收 ref 形态短字符串（≤256），剥路径与凭证形态，封顶 REFS_MAX。"""
    if not isinstance(refs, (list, tuple)):
        return []
    out: List[str] = []
    for item in refs:
        if not isinstance(item, str) or not item:
            continue
        scrubbed = _scrub_str(item)
        if _SECRET_VALUE_RE.search(scrubbed):
            continue
        if scrubbed and scrubbed != "[redacted-path]" and scrubbed not in out:
            out.append(scrubbed)
        if len(out) >= REFS_MAX:
            break
    return out


def assert_no_secrets(value: Dict[str, Any]) -> None:
    """裁剪后的最终硬门：命中高置信凭证形态即拒绝写入（fail-closed）。"""
    blob = repr(value)
    if _SECRET_VALUE_RE.search(blob):
        raise MemoryPolicyError("value 含疑似凭证形态，拒绝写入记忆")


def sanitize_subject(subject: Any) -> str:
    """subject：规范化的短语义键（地名/dataset_key/工具名 …）。"""
    text = str(subject or "").strip()
    return text[:255]


def sanitize_scope_id(scope: str, scope_id: Any) -> str:
    text = str(scope_id or "").strip()
    if scope == "session":
        # session id 是读取侧的等值边界：拒空白/引号/控制形态，防越权等值
        # 匹配与注入；长度收窄（session id 从不为长自由文本）。
        if not text or len(text) > 128 or _MEMORY_SCOPE_ID_BAD_RE.search(text):
            raise MemoryPolicyError("session scope_id 形态非法")
    return text[:255]
