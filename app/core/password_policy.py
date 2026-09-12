"""密码策略与登录渐进延迟（ADR-0139 P6，兑现 README Phase 6 认证增强）。

策略（在既有 scrypt 哈希与限流之上）：

- **长度**：8–128（schema 双重声明；本模块 fail-loud 复核）；
- **字符类**：≥3/4 类（小写/大写/数字/符号），**或**长度 ≥12（口令短语
  豁免 —— 现代指导：长度优先于强制组合）；
- **常见密码表**：内置 top-N 常见口令（小写匹配；无新依赖 —— 不引
  zxcvbn 这类重依赖，词表随 ADR 演进）；
- **渐进延迟**：登录失败按每标识符指数递增的人为延迟（0.5s→8s 封顶，
  进程内 best-effort）；跨进程的硬上界仍由既有限流器承担（5/5min/IP
  + 30/5min/标识符）——两层各管一个爆炸半径，不重复造轮子。
"""
from __future__ import annotations

import time
from typing import Optional

#: 内置常见口令表（top 128 截选；小写比对）。
COMMON_PASSWORDS: frozenset[str] = frozenset({
    "123456", "password", "123456789", "12345678", "12345", "qwerty",
    "1234567890", "123123", "000000", "abc123", "password1", "iloveyou",
    "admin", "qwerty123", "1q2w3e4r", "111111", "123qwe", "monkey",
    "dragon", "letmein", "login", "princess", "qwertyuiop", "solo",
    "passw0rd", "starwars", "hello", "freedom", "whatever", "qazwsx",
    "trustno1", "baseball", "superman", "michael", "football", "shadow",
    "master", "jordan", "harley", "ranger", "hunter", "buster", "soccer",
    "hockey", "killer", "george", "sexy", "andrew", "charlie", "thomas",
    "robert", "access", "love", "summer", "winter", "spring", "test",
    "test123", "root", "toor", "changeme", "secret", "password123",
    "password!", "p@ssw0rd", "p@ssword", "abcd1234", "asdfgh", "zxcvbnm",
    "qweasd", "aaa", "1qaz2wsx", "asdfghjkl", "qazxsw", "1234qwer",
    "pass123", "pass1234", "qwertz", "sunshine", "princess1", "welcome",
    "welcome1", "administrator", "admin123", "admin888", "88888888",
    "666666", "888888", "a123456", "123321", "123abc", "a123456789",
    "woaini", "woaini1314", "5201314", "qq123456", "taobao", "wangpeng",
    "123456a", "1q2w3e", "1q2w3e4r5t", "qwerty12", "abcdef", "pussy",
    "ninja", "azerty", "samsung", "google", "facebook", "pokemon",
    "slayer", "marina", "corvette", "mustang", "mercedes", "jackson",
})

#: 渐进延迟阶梯（秒）：失败次数 → 延迟；封顶 8s。
_DELAY_BASE_S = 0.5
_DELAY_CAP_S = 8.0
_DELAY_MAX_TRACKED_FAILURES = 8
_DELAY_TTL_S = 900  # 15min 无失败即清零

# 进程内失败计数 {identifier_lower: (count, last_ts)}；best-effort。
_failures: dict[str, tuple[int, float]] = {}


class PasswordPolicyError(ValueError):
    """密码不满足策略（HTTP 层映射 422）。"""


def validate_password_strength(password: str) -> None:
    """校验密码强度；不满足抛 :class:`PasswordPolicyError`。

    规则见模块 docstring。仅对**注册/改密**路径调用 —— 登录路径绝不
    校验（存量弱密码用户不能被锁在门外，认证只有哈希校验）。
    """
    if not isinstance(password, str) or not (8 <= len(password) <= 128):
        raise PasswordPolicyError("密码长度须为 8–128 位")
    lowered = password.lower()
    if lowered in COMMON_PASSWORDS:
        raise PasswordPolicyError("密码过于常见，请更换")
    classes = sum([
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if classes < 3 and len(password) < 12:
        raise PasswordPolicyError(
            "密码须含至少 3 类字符（小写/大写/数字/符号），"
            "或长度不少于 12 位")


def record_login_failure(identifier: str) -> float:
    """记录一次登录失败，返回本次应施加的人为延迟（秒）。"""
    key = (identifier or "").strip().lower()[:255]
    now = time.monotonic()
    count, last_ts = _failures.get(key, (0, 0.0))
    if now - last_ts > _DELAY_TTL_S:
        count = 0
    count += 1
    _failures[key] = (count, now)
    # 第 4 次失败起施加延迟，指数递增，8s 封顶
    if count < 4:
        return 0.0
    exponent = min(count - 4, _DELAY_MAX_TRACKED_FAILURES)
    return min(_DELAY_BASE_S * (2 ** exponent), _DELAY_CAP_S)


def reset_login_failures(identifier: str) -> None:
    """登录成功后清零（诚实计量：成功即之前的失败不再是当前威胁）。"""
    _failures.pop((identifier or "").strip().lower()[:255], None)


def pending_failure_count(identifier: Optional[str]) -> int:
    """当前窗口内的失败计数（观测/测试用）。"""
    if not identifier:
        return 0
    entry = _failures.get(identifier.strip().lower()[:255])
    if entry is None:
        return 0
    count, last_ts = entry
    if time.monotonic() - last_ts > _DELAY_TTL_S:
        return 0
    return count
