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
    if not settings.JWT_SECRET_KEY:
        raise RuntimeError("JWT_SECRET_KEY is required for URL signing")
    base = settings.JWT_SECRET_KEY.encode("utf-8")
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


def hmac_equals(left: object, right: object) -> bool:
    """常量时间比较。非 ASCII / 非字符串输入返回 False，不抛 TypeError。

    ``hmac.compare_digest`` 对含非 ASCII 的 ``str`` 直接 TypeError（HTTP
    头按 latin-1 解码后可以带这类字符）——调用方会把它变成 500 而不是
    401/False。双方先变成 bytes 再比。
    """
    try:
        left_b = left if isinstance(left, (bytes, bytearray)) else str(left).encode("utf-8")
        right_b = right if isinstance(right, (bytes, bytearray)) else str(right).encode("utf-8")
        return hmac.compare_digest(left_b, right_b)
    except (TypeError, UnicodeError, AttributeError):
        return False


def verify_signature(path: str, exp: int | str, sig: str) -> bool:
    """常量时间比较；过期或签名错均返回 False。"""
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_int < int(time.time()):
        return False
    expected = make_signature(path, exp_int)
    return hmac_equals(expected, sig or "")


def terrain_owner_binding(owner_token: object | None) -> str:
    """Stable binding for the session's current anonymous credential.

    Empty when the session has no owner token (authenticated sessions).
    ``OwnerTokenDigest`` is already the stored verifier; raw tokens are hashed
    the same way so issue and verify agree. Rotate changes the stored value,
    so previously issued signatures stop verifying.
    """
    if not owner_token:
        return ""
    from app.core.owner_tokens import owner_token_digest

    try:
        return owner_token_digest(str(owner_token))
    except UnicodeError:
        return ""


def terrain_capability_path(
    session_id: str, ref_id: str, *, owner_token: object | None = None,
) -> str:
    """Resource-bound capability, independent of any tile coordinate."""
    base = f"terrain:{session_id}:{ref_id}"
    binding = terrain_owner_binding(owner_token)
    if not binding:
        return base
    return f"{base}:{binding}"


def signed_terrain_query(
    session_id: str,
    ref_id: str,
    *,
    owner_token: object | None = None,
    max_exp: int | None = None,
) -> str:
    """Read capability for one ref, valid for nine to ten minutes.

    Minute issuance windows keep TileJSON bytes/ETags stable across adjacent
    requests. ``max_exp`` caps a reissue at the caller's incoming capability
    so a leaked URL cannot mint a later expiry. ``owner_token`` binds the
    signature to the current credential (rotate invalidates it).
    """
    from urllib.parse import urlencode
    exp = (int(time.time()) // 60) * 60 + 600
    if max_exp is not None:
        exp = min(exp, int(max_exp))
    sig = make_signature(
        terrain_capability_path(session_id, ref_id, owner_token=owner_token), exp,
    )
    return "?" + urlencode({"session_id": session_id, "exp": exp, "sig": sig})
