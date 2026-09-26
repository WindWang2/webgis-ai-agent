"""秘密净化原语（中立模块，ADR-0214 D1）。

值级/键级秘密识别与剥离的**唯一权威实现**。历史上住在
``app/lib/harness/replay/sanitize.py``（测试 oracle 包），而生产 runtime
（``runtime/decision_record`` / ``runtime/gis_trace``）反向 import 它 ——
方向性倒挂（ADR-0212 §3 登记）。本模块把该面下沉为平台能力：

- ``SECRET_KEY_MARKERS``   键名子串名单（命中 → 值整体 REDACTED）；
- ``SECRET_STRING_PATTERNS`` 字符串值内的高置信秘密形态（repr 嵌套载荷、
  header 片段的兜底防线）；
- :func:`is_secret_key`    键名判定（连字符/空格归一后匹配）;
- :func:`scrub_secret_strings` 字符串值净化（幂等、never-raises）。

纪律：只匹配**高置信**形态，避免误伤普通文本；pattern 演进必须携带
fuzz/negative 测试（tests/unit/test_redaction_fuzz.py）。
"""
from __future__ import annotations

import re

#: 键名命中这些子串（大小写不敏感）→ 值替换为 REDACTED。
SECRET_KEY_MARKERS = (
    "token", "secret", "password", "passwd", "authorization", "api_key",
    "apikey", "credential", "cookie", "session_key", "private_key",
    "privatekey", "passphrase", "access_key", "auth_key", "signing_key",
)

#: 字符串值内的秘密模式（repr 形态嵌套载荷、header 片段）。
#: 只匹配高置信形态，避免误伤普通文本。
SECRET_STRING_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"(?i)bearer\s*:?[\s]*[A-Za-z0-9._\-]{8,}"),
    re.compile(
        r"(?i)(api[-_]?key|secret|passphrase|password|token|authorization)"
        r"\s*[=:]\s*['\"]?[A-Za-z0-9._\-]{6,}"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def is_secret_key(key: str) -> bool:
    """键名是否命中秘密名单（连字符/空格归一：X-Api-Key / Private Key 等）。"""
    lowered = re.sub(r"[-\s]+", "_", str(key).lower())
    return any(marker in lowered for marker in SECRET_KEY_MARKERS)


def scrub_secret_strings(text: str) -> str:
    """字符串值内的秘密模式 → REDACTED（repr 嵌套载荷的兜底防线）。

    幂等（净化后的输出再净化不变）、never-raises（非 str 输入按 str() 走）。
    """
    out = text if isinstance(text, str) else str(text)
    for pattern in SECRET_STRING_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


__all__ = [
    "SECRET_KEY_MARKERS",
    "SECRET_STRING_PATTERNS",
    "is_secret_key",
    "scrub_secret_strings",
]
