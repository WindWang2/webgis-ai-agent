"""Organization limits for storage, in-flight tasks and admission rate.

Policy/usage/rate failures reject writes. HTTP ``serialized_admission`` is a
preflight/coordination boundary. Every durable producer additionally uses
``org_quota_admission`` in its real SQL transaction; standalone check helpers
are observations and never substitute for that resource admission boundary.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator
from typing import Optional, cast

from sqlalchemy import func, select, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCategory, PlatformError

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


#: env 默认（per-org 覆盖缺省时生效）。
DEFAULT_MAX_STORAGE_BYTES = _env_int("ORG_QUOTA_STORAGE_BYTES", 100 * 1024 ** 3)
DEFAULT_MAX_CONCURRENT_TASKS = _env_int("ORG_QUOTA_CONCURRENT_TASKS", 50)
DEFAULT_RATE_PER_MIN = _env_int("ORG_QUOTA_RATE_PER_MIN", 240)

# Server-owned type; synchronous occupancy shares the existing job ledger.
SYNC_EXECUTION_TASK_TYPE = "geocompute_sync"


class QuotaExceededError(PlatformError):
    """组织配额越限（category=QUOTA → 429 分类学信封）。"""

    def __init__(self, resource: str, org_id: str, *,
                 limit: Optional[int] = None, used: Optional[int] = None):
        super().__init__(
            f"org quota exceeded: {resource} org={org_id}",
            category=ErrorCategory.QUOTA,
            context={"resource": resource, "org_id": org_id,
                     "limit": limit, "used": used},
        )
        self.resource = resource


@dataclass(frozen=True)
class OrgQuotaLimits:
    max_storage_bytes: int
    max_concurrent_tasks: int
    rate_per_min: int


async def effective_limits(db: AsyncSession, org_id: str) -> OrgQuotaLimits:
    """per-org 覆盖 → env 默认（NULL = 跟随全局默认）。读失败拒绝。"""
    from app.models.db_model import OrgQuota

    limits = OrgQuotaLimits(
        max_storage_bytes=DEFAULT_MAX_STORAGE_BYTES,
        max_concurrent_tasks=DEFAULT_MAX_CONCURRENT_TASKS,
        rate_per_min=DEFAULT_RATE_PER_MIN,
    )
    try:
        row = (await db.execute(
            select(OrgQuota).where(OrgQuota.org_id == org_id)
        )).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - 缺少策略真相时拒绝写入
        logger.warning("[quota] org_quotas unavailable; rejecting write",
                       exc_info=True)
        await db.rollback()
        raise PlatformError("organization quota policy unavailable", category=ErrorCategory.RETRYABLE, http_status=503)
    if row is None:
        return limits
    return OrgQuotaLimits(
        max_storage_bytes=cast(int, row.max_storage_bytes)
        if row.max_storage_bytes is not None else limits.max_storage_bytes,
        max_concurrent_tasks=cast(int, row.max_concurrent_tasks)
        if row.max_concurrent_tasks is not None else limits.max_concurrent_tasks,
        rate_per_min=cast(int, row.rate_limit_per_min)
        if row.rate_limit_per_min is not None else limits.rate_per_min,
    )


async def storage_bytes(db: AsyncSession, org_id: str) -> int:
    """org 存储字节（lakehouse 版本 + 目录 + gc artifacts 字节列聚合）。"""
    from app.models.db_model import GeoComputeArtifact
    from app.models.lakehouse_catalog import LakehouseCatalogItem
    from app.models.lakehouse_datasets import LakehouseDatasetVersion

    total = 0
    try:
        for col, model in (
            (LakehouseDatasetVersion.byte_size, LakehouseDatasetVersion),
            (LakehouseCatalogItem.byte_size, LakehouseCatalogItem),
            (GeoComputeArtifact.size_bytes, GeoComputeArtifact),
        ):
            stmt = select(func.coalesce(func.sum(case((col > 0, col), else_=0)), 0)).where(
                model.org_id == org_id)
            if model is LakehouseCatalogItem:
                stmt = stmt.where(LakehouseCatalogItem.status == "active")
            total += int((await db.execute(stmt)).scalar_one() or 0)
    except Exception:  # noqa: BLE001 - 用量不可确认时拒绝写入
        logger.warning("[quota] storage aggregation failed", exc_info=True)
        await db.rollback()
        raise PlatformError("organization storage usage unavailable", category=ErrorCategory.RETRYABLE, http_status=503)
    return total


async def concurrent_tasks(db: AsyncSession, org_id: str) -> int:
    """org 并发任务数（geocompute 在飞 + workflow running）。"""
    from app.models.db_model import GeoComputeClusterRun, WorkflowInstanceRow, AnalysisTask

    try:
        gc = int((await db.execute(
            select(func.count()).select_from(GeoComputeClusterRun).where(
                GeoComputeClusterRun.org_id == org_id,
                GeoComputeClusterRun.status.not_in(("completed", "failed", "cancelled")),
            )
        )).scalar_one() or 0)
        wf = int((await db.execute(
            select(func.count()).select_from(WorkflowInstanceRow).where(
                WorkflowInstanceRow.org_id == org_id,
                WorkflowInstanceRow.status.not_in(("succeeded", "failed", "cancelled", "superseded")),
            )
        )).scalar_one() or 0)
        sync = int((await db.execute(select(func.count()).select_from(AnalysisTask).where(
            AnalysisTask.org_id == int(org_id), AnalysisTask.task_type == SYNC_EXECUTION_TASK_TYPE,
            AnalysisTask.status.not_in(("completed", "failed", "cancelled")),
        ))).scalar_one() or 0)
        return gc + wf + sync
    except Exception:  # noqa: BLE001
        logger.warning("[quota] concurrency aggregation failed", exc_info=True)
        await db.rollback()
        raise PlatformError("organization concurrency usage unavailable", category=ErrorCategory.RETRYABLE, http_status=503)


async def check_concurrency(db: AsyncSession, org_id: str) -> None:
    """并发任务配额裁决（越限抛 QuotaExceededError；查询失败拒绝）。"""
    limits = await effective_limits(db, org_id)
    if limits.max_concurrent_tasks <= 0:
        return
    used = await concurrent_tasks(db, org_id)
    if used >= limits.max_concurrent_tasks:
        await _audit_exceeded(db, "concurrent_tasks", org_id,
                              limits.max_concurrent_tasks, used)
        raise QuotaExceededError("concurrent_tasks", org_id,
                                 limit=limits.max_concurrent_tasks, used=used)


async def check_storage(db: AsyncSession, org_id: str, *,
                        incoming_bytes: int = 0) -> None:
    """存储配额裁决（含本次将写入的字节）。"""
    limits = await effective_limits(db, org_id)
    if limits.max_storage_bytes <= 0:
        return
    used = await storage_bytes(db, org_id)
    if used + max(0, incoming_bytes) > limits.max_storage_bytes:
        await _audit_exceeded(db, "storage_bytes", org_id,
                              limits.max_storage_bytes, used)
        raise QuotaExceededError("storage_bytes", org_id,
                                 limit=limits.max_storage_bytes, used=used)


async def _audit_exceeded(db: AsyncSession, resource: str, org_id: str,
                          limit: int, used: int) -> None:
    """越限事件落审计（fail-open；失败不影响 429 的抛出）。"""
    try:
        from app.services.audit import ACTION_QUOTA_EXCEEDED, record_audit

        await record_audit(
            db, action=ACTION_QUOTA_EXCEEDED, org_id=org_id,
            target_type="org_quota", target_id=resource,
            detail={"limit": limit, "used": used},
        )
    except Exception:  # noqa: BLE001
        logger.warning("[quota] exceed audit failed", exc_info=True)


async def check_rate(org_id: str, *, limit_per_min: Optional[int] = None,
                     weight: int = 1) -> bool:
    """org 维度速率桶（复用限流设施；Redis 不可用拒绝，返回是否允许）。

    ``limit_per_min``：调用方已解析的 effective limit（缺省用 env 默认）。
    速率桶不可用时拒绝写入，避免把无法确认额度的请求当作已授权。
    """
    from app.core.rate_limiter import get_rate_limiter, RedisRateLimiter
    from app.core.config import settings

    limiter = await get_rate_limiter()
    if limiter is None:
        raise PlatformError("organization rate limiter unavailable", category=ErrorCategory.RETRYABLE, http_status=503)
    if settings.is_production() and not isinstance(limiter, RedisRateLimiter):
        raise PlatformError("shared organization rate limiter unavailable", category=ErrorCategory.RETRYABLE, http_status=503)
    try:
        if isinstance(limiter, RedisRateLimiter):
            return await limiter.is_allowed(
                f"org_quota_rate:{org_id}", max(1, limit_per_min or DEFAULT_RATE_PER_MIN),
                60, fail_closed=True,
            )
        return await limiter.is_allowed(
            f"org_quota_rate:{org_id}",
            max(1, limit_per_min or DEFAULT_RATE_PER_MIN),
            60,
        )
    except Exception:  # noqa: BLE001
        logger.warning("[quota] rate limiter unavailable; rejecting write", exc_info=True)
        raise PlatformError("organization rate limiter unavailable", category=ErrorCategory.RETRYABLE, http_status=503)


async def usage_snapshot(db: AsyncSession, org_id: str) -> dict:
    """Admin usage snapshot; standalone reads are observations, not reservations."""
    limits = await effective_limits(db, org_id)
    return {
        "org_id": org_id,
        "storage_bytes": {
            "used": await storage_bytes(db, org_id),
            "limit": limits.max_storage_bytes,
        },
        "concurrent_tasks": {
            "used": await concurrent_tasks(db, org_id),
            "limit": limits.max_concurrent_tasks,
        },
        "rate_per_min": {"limit": limits.rate_per_min},
    }


async def enforce_for_principal(user: Optional[dict], *,
                                storage_incoming: int = 0,
                                include_storage: bool = False,
                                include_concurrency: bool = True) -> str:
    """Resolve organization and check current limits; call inside the admission guard."""
    from app.core import tenancy
    from app.core.database import AsyncSessionLocal

    if AsyncSessionLocal is None:
        raise PlatformError("organization quota database unavailable", category=ErrorCategory.RETRYABLE, http_status=503)
    async with AsyncSessionLocal() as qdb:
        org_eff = await tenancy.effective_org_id(user, qdb)
        limits = await effective_limits(qdb, org_eff)
        if not await check_rate(org_eff, limit_per_min=limits.rate_per_min):
            raise QuotaExceededError("rate_per_min", org_eff,
                                     limit=limits.rate_per_min)
        if include_concurrency:
            await check_concurrency(qdb, org_eff)
        if include_storage:
            await check_storage(qdb, org_eff, incoming_bytes=storage_incoming)
        return org_eff


@asynccontextmanager
async def serialized_admission(user: Optional[dict]) -> AsyncIterator[str]:
    """Serialize quota check plus committed HTTP write for an organization.

    This guard must cover the actual commit/cluster insertion. Redis-backed
    locks renew while held and fail closed when configured storage is lost.
    The development no-Redis mode is limited to one process.
    """
    import hashlib
    from app.core import tenancy
    from app.core.database import AsyncSessionLocal
    from app.services.distributed_lock import session_lock_registry
    from app.core.config import settings

    if str(settings.ENV).lower() in ("prod", "production") and not session_lock_registry._is_redis_configured():
        raise PlatformError("shared organization admission lock unavailable", category=ErrorCategory.RETRYABLE, http_status=503)

    if AsyncSessionLocal is None:
        raise PlatformError("organization quota database unavailable", category=ErrorCategory.RETRYABLE, http_status=503)
    async with AsyncSessionLocal() as db:
        org = await tenancy.effective_org_id(user, db)
    key = "quota-" + hashlib.sha256(str(org).encode()).hexdigest()
    async with session_lock_registry.lock(key, fail_on_degraded=True, fail_on_lost=True):
        yield org
