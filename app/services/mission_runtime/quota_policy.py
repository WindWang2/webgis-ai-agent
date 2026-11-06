"""Server-owned finite mission ceilings; callers may only tighten them."""
from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.models.db_model import OrgQuota
from app.services.org_quota import (
    DEFAULT_MAX_STORAGE_BYTES, DEFAULT_MAX_CONCURRENT_TASKS, DEFAULT_RATE_PER_MIN,
)

MISSION_CEILINGS = {
    "tokens": 200000.0, "tool_calls": 500.0, "compute_ms": 600000.0,
    "cpu_ms": 600000.0, "wall_ms": 3600000.0, "memory_mb": 2048.0,
    "bytes": float(100 * 1024 * 1024), "storage_bytes": float(100 * 1024 * 1024),
    "concurrent_tasks": 8.0,
}


def mission_quota(db: Session, org_id: str, requested: Mapping[str, float]) -> dict[str, float]:
    row = db.get(OrgQuota, org_id)
    storage = getattr(row, "max_storage_bytes", None)
    concurrent = getattr(row, "max_concurrent_tasks", None)
    rate = getattr(row, "rate_limit_per_min", None)
    limits = dict(MISSION_CEILINGS)
    org_storage = int(storage if storage is not None else DEFAULT_MAX_STORAGE_BYTES)
    # Organization <=0 is unlimited; finite server ceilings remain enforced.
    if org_storage > 0:
        for key in ("bytes", "storage_bytes"):
            limits[key] = min(limits[key], org_storage)
    org_concurrent = int(concurrent if concurrent is not None else DEFAULT_MAX_CONCURRENT_TASKS)
    if org_concurrent > 0:
        limits["concurrent_tasks"] = min(limits["concurrent_tasks"], org_concurrent)
    limits["tool_calls"] = min(limits["tool_calls"], max(0, int(
        rate if rate is not None else DEFAULT_RATE_PER_MIN)))
    for key, value in requested.items():
        if key not in limits:
            raise ValueError("Unknown mission quota dimension")
        limits[key] = min(limits[key], value)
    return limits
