"""Shared bounded admission for automatic screenshot-based revalidation."""
from __future__ import annotations
from app.core.rate_limiter import get_rate_limiter


async def admit_snapshot_reverify(session_id: str, revision: int, sha256: str) -> bool:
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(f"visual_reverify:session:{session_id}", 3, 60):
        return False
    if not await limiter.is_allowed("visual_reverify:global", 30, 60):
        return False
    return await limiter.is_allowed(f"visual_reverify:content:{session_id}:{revision}:{sha256}", 1, 60)
