"""Public report links expose only publication metadata and bounded access."""
from fastapi import HTTPException, Request

from app.core.rate_limiter import get_rate_limiter
from app.core.client_ip import client_ip_from
from app.models.report import Report


async def limit_public_report_access(request: Request) -> None:
    address = client_ip_from(request)
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(f"report-share:{address}", 60, 60):
        raise HTTPException(status_code=429, detail="Too many report requests", headers={"Retry-After": "60"})


def public_report_projection(report: Report) -> dict[str, object]:
    return {
        "title": report.title,
        "format": report.format,
        "status": report.status,
        "file_size": report.file_size,
        "created_at": report.created_at.isoformat() if report.created_at else None,
        "expires_at": report.share_expires_at.isoformat() if report.share_expires_at else None,
    }
