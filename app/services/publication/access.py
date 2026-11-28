"""发布访问控制（WP2）—— share_code 签发/校验/撤销/过期 + 读者限流。

范式对齐 ``app/api/report_sharing.py``：
- token 用 ``secrets.token_urlsafe``；
- 过期字段是 naive UTC DateTime（F-04）；
- 读者限流 per-IP（``publication-read:{ip}``）；
- 校验失败一律 404 语义（不泄露存在性）。
"""
from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request

from app.core.client_ip import client_ip_from
from app.core.rate_limiter import get_rate_limiter
from app.models.map_publication import (
    PUBLICATION_STATUS_PUBLISHED,
    MapPublication,
    utcnow_naive,
)

#: share code 熵（128 bits；Report 用 12 → 这里略升不降）。
_SHARE_CODE_BYTES = 16
#: 链接默认/最大有效期（天）。
DEFAULT_SHARE_TTL_DAYS = 7
MAX_SHARE_TTL_DAYS = 30


def mint_share_code() -> str:
    return secrets.token_urlsafe(_SHARE_CODE_BYTES)


def share_expiry(ttl_days: Optional[int]) -> Optional[datetime]:
    """ttl → naive UTC 过期时刻（None=永久链接直到撤销）。"""
    if ttl_days is None:
        return None
    clamped = max(1, min(int(ttl_days), MAX_SHARE_TTL_DAYS))
    return utcnow_naive() + timedelta(days=clamped)


def share_expired(publication: MapPublication, now: Optional[datetime] = None) -> bool:
    """naive-UTC 过期判定（同 report.py::_share_expired 的 F-04 处理）。"""
    expires = publication.share_expires_at
    if expires is None:
        return False
    current = now or utcnow_naive()
    if expires.tzinfo is not None:
        expires = expires.astimezone(timezone.utc).replace(tzinfo=None)
    return current >= expires


def reader_share_denied_reason(publication: Optional[MapPublication]) -> Optional[str]:
    """读者访问阻断理由（None=放行）。fail-closed：任何不一致都阻断。"""
    if publication is None:
        return "not_found"
    if publication.status != PUBLICATION_STATUS_PUBLISHED:
        return "not_published"
    if publication.access_policy != "link" or not publication.share_code:
        return "not_shared"
    if share_expired(publication):
        return "expired"
    return None


def share_code_matches(publication: MapPublication, presented: str) -> bool:
    """常量时间比较（唯一索引定位后仍走 compare_digest —— 纵深防御）。"""
    stored = publication.share_code or ""
    if not stored or not presented:
        return False
    return hmac.compare_digest(stored.encode("utf-8"), presented.encode("utf-8"))


async def limit_publication_read(
    request: Request,
    *,
    scope: str = "publication-data",
    max_requests: int = 600,
    window_seconds: int = 60,
) -> None:
    """读者侧 per-IP 限流依赖（R1 P3-15 分桶）。

    - ``publication-meta``（doc/map）：120/min —— 纯浏览语义；
    - ``publication-data``（层数据/要素/统计）：600/min —— 地图一屏会拉
      多层数据的突发（与 layer_data 面同量级）。
    共享 NAT 出口不再被单桶 240 卡死。Redis 限流器缺席时 fail-open
    （与全局限流同语义）。
    """
    address = client_ip_from(request)
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(f"{scope}:{address}", max_requests, window_seconds):
        raise HTTPException(
            status_code=429,
            detail="Too many publication requests",
            headers={"Retry-After": "60"},
        )


async def limit_publication_meta(request: Request) -> None:
    """元数据端点限流（doc/map；120/min）。"""
    await limit_publication_read(request, scope="publication-meta", max_requests=120, window_seconds=60)


__all__ = [
    "limit_publication_meta",
    "mint_share_code",
    "share_expiry",
    "share_expired",
    "reader_share_denied_reason",
    "share_code_matches",
    "limit_publication_read",
    "DEFAULT_SHARE_TTL_DAYS",
    "MAX_SHARE_TTL_DAYS",
]
