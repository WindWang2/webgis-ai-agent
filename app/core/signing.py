"""HMAC 签名 URL：用于私有静态文件下载等场景。

签名格式（追加到 URL 查询串）：
    ?exp=<unix ts>&sig=<hex hmac sha256>

签名输入：`{path}|{exp}`，密钥由 JWT_SECRET_KEY 经域分离派生
（#1221/D-13：静态文件能力 URL 与 JWT 不再共享同一对称密钥命名空间 ——
任一信道的泄露/轮换不再耦合另一信道；派生确定性，密钥轮换行为不变）。
TTL 默认 1 小时；超过 exp 即视为过期。
"""
from __future__ import annotations

import hashlib
import hmac
import time

from app.core.config import settings

_ALG = hashlib.sha256
# HKDF-style 域分离标签（RFC 5869 expand 语义的简化：单块输出足够 SHA-256）。
_INFO = b"webgis:static-url-signing:v1"


def _secret() -> bytes:
    base = (settings.JWT_SECRET_KEY or "").encode("utf-8")
    # extract-then-expand（单块）：prk = HMAC(base, salt=info)，key = HMAC(prk, info||0x01)
    prk = hmac.new(_INFO, base, _ALG).digest()
    return hmac.new(prk, _INFO + b"\x01", _ALG).digest()


def make_signature(path: str, exp: int) -> str:
    """生成给定路径 + 过期时间的 HMAC-SHA256 签名（hex）。"""
    msg = f"{path}|{exp}".encode("utf-8")
    return hmac.new(_secret(), msg, _ALG).hexdigest()


def sign_path(path: str, ttl_seconds: int = 3600) -> tuple[int, str]:
    """返回 (exp, sig)。"""
    exp = int(time.time()) + max(60, int(ttl_seconds))
    return exp, make_signature(path, exp)


#: 签名 URL 有效期（地图状态里的图片 URL 需跨刷新可用；只授权单个 PNG）。
RASTER_URL_TTL_S = 24 * 3600


def signed_raster_query(path: str, ttl_seconds: int = RASTER_URL_TTL_S) -> str:
    """``/api/v1/sessions/<sid>/raster/<id>.png`` → ``exp=..&sig=..``（F-13）。

    #1678：原先定义在 app/api/routes/raster.py，被 app/tools/cartography_tools.py
    反向 import（tools→api 跨层边，ADR-0216 §6.5）。签名查询串装配本就是 core
    签名基础设施职责，下沉至此；raster.py 保留 re-export 兼容。
    """
    exp, sig = sign_path(path.split("?", 1)[0], ttl_seconds)
    return f"exp={exp}&sig={sig}"


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
