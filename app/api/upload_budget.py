"""Admission budget for expensive authorized upload parsing."""
from __future__ import annotations
from fastapi import HTTPException
from starlette.requests import Request
from app.core.client_ip import client_ip_from
from app.core.rate_limiter import get_rate_limiter


async def enforce_upload_budget(session_id: str, request: Request | None) -> None:
    limiter = await get_rate_limiter()
    ip = client_ip_from(request) if request is not None else "internal"
    if not await limiter.is_allowed(f"upload:ip:{ip}", 30, 60):
        raise HTTPException(429, "上传过于频繁，请稍后再试", headers={"Retry-After": "60"})
    if not await limiter.is_allowed(f"upload:session:{session_id}", 8, 60):
        raise HTTPException(429, "本会话上传过于频繁，请稍后再试", headers={"Retry-After": "60"})
