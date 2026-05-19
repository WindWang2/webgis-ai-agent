"""HMAC 签名 URL：用于私有静态文件下载等场景。

签名格式（追加到 URL 查询串）：
    ?exp=<unix ts>&sig=<hex hmac sha256>

签名输入：`{path}|{exp}`，密钥是 JWT_SECRET_KEY。
TTL 默认 1 小时；超过 exp 即视为过期。
"""
from __future__ import annotations

import hashlib
import hmac
import time

from app.core.config import settings

_ALG = hashlib.sha256


def _secret() -> bytes:
    return (settings.JWT_SECRET_KEY or "").encode("utf-8")


def make_signature(path: str, exp: int) -> str:
    """生成给定路径 + 过期时间的 HMAC-SHA256 签名（hex）。"""
    msg = f"{path}|{exp}".encode("utf-8")
    return hmac.new(_secret(), msg, _ALG).hexdigest()


def sign_path(path: str, ttl_seconds: int = 3600) -> tuple[int, str]:
    """返回 (exp, sig)。"""
    exp = int(time.time()) + max(60, int(ttl_seconds))
    return exp, make_signature(path, exp)


def verify_signature(path: str, exp: int | str, sig: str) -> bool:
    """常量时间比较；过期或签名错均返回 False。"""
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_int < int(time.time()):
        return False
    expected = make_signature(path, exp_int)
    return hmac.compare_digest(expected, sig or "")
