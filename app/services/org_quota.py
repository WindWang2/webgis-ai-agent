"""组织级配额服务（ADR-0139 P5）：存储字节 / 并发任务数 / 速率。

三层裁决：

1. **配置**：per-org 覆盖（``org_quotas`` 表，admin 端点维护）→ env
   默认值兜底（``ORG_QUOTA_*``）；显式 NULL = 跟随全局默认。
2. **用量**：
   - 存储字节 = lakehouse 版本 + 目录投影 + geocompute artifacts 的
     字节列按 ``org_id`` 聚合（V8 表已带 org，单一 SUM）；
   - 并发任务 = geocompute_runs（queued/leased/running）+ workflow
     instances（running）计数；
   - 速率 = org 维度 Redis 滑窗桶（``get_rate_limiter`` 既有设施，
     in-memory 兜底）。
3. **裁决**：越限 → ``QuotaExceededError``（PlatformError, category=
   QUOTA → 429 分类学信封，A 线对齐点）+ 审计事件（fail-open）。

检查全部 fail-open 于**读**（查询失败视为未超限，不阻塞业务）——
配额是保护性限流，不是可用性单点。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
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
    """per-org 覆盖 → env 默认（NULL = 跟随全局默认）。fail-open。"""
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
    except Exception:  # noqa: BLE001 - 配置读失败按默认值（保护性限流不阻塞）
        logger.warning("[quota] org_quotas unavailable; using defaults",
                       exc_info=True)
        return limits
    if row is None:
        return limits
    return OrgQuotaLimits(
        max_storage_bytes=row.max_storage_bytes
        if row.max_storage_bytes is not None else limits.max_storage_bytes,
        max_concurrent_tasks=row.max_concurrent_tasks
        if row.max_concurrent_tasks is not None else limits.max_concurrent_tasks,
        rate_per_min=row.rate_limit_per_min
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
            stmt = select(func.coalesce(func.sum(col), 0)).where(
                model.org_id == org_id)
            if model is LakehouseCatalogItem:
                stmt = stmt.where(LakehouseCatalogItem.status == "active")
            total += int((await db.execute(stmt)).scalar_one() or 0)
    except Exception:  # noqa: BLE001 - 用量聚合失败按 0（fail-open 于读）
        logger.warning("[quota] storage aggregation failed", exc_info=True)
    return total


async def concurrent_tasks(db: AsyncSession, org_id: str) -> int:
    """org 并发任务数（geocompute 在飞 + workflow running）。"""
    from app.models.db_model import GeoComputeClusterRun, WorkflowInstanceRow

    try:
        gc = int((await db.execute(
            select(func.count()).select_from(GeoComputeClusterRun).where(
                GeoComputeClusterRun.org_id == org_id,
                GeoComputeClusterRun.status.in_(
                    ("queued", "leased", "running")),
            )
        )).scalar_one() or 0)
        wf = int((await db.execute(
            select(func.count()).select_from(WorkflowInstanceRow).where(
                WorkflowInstanceRow.org_id == org_id,
                WorkflowInstanceRow.status == "running",
            )
        )).scalar_one() or 0)
        return gc + wf
    except Exception:  # noqa: BLE001
        logger.warning("[quota] concurrency aggregation failed", exc_info=True)
        return 0


async def check_concurrency(db: AsyncSession, org_id: str) -> None:
    """并发任务配额裁决（越限抛 QuotaExceededError；查询失败放行）。"""
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
    """org 维度速率桶（复用限流设施；Redis 不可用 → 放行，返回是否允许）。

    ``limit_per_min``：调用方已解析的 effective limit（缺省用 env 默认）。
    放行策略说明：速率桶的失败方向与并发/存储一致——保护性限流在
    基础设施不可用时不放大故障（限流降级，不是拒绝服务）。
    """
    from app.core.rate_limiter import get_rate_limiter

    limiter = await get_rate_limiter()
    if limiter is None:
        return True
    try:
        return await limiter.is_allowed(
            f"org_quota_rate:{org_id}",
            max(1, limit_per_min or DEFAULT_RATE_PER_MIN),
            60,
        )
    except Exception:  # noqa: BLE001
        logger.warning("[quota] rate limiter unavailable; allowing", exc_info=True)
        return True


async def usage_snapshot(db: AsyncSession, org_id: str) -> dict:
    """usage 投影（admin 面展示；配额与当前用量一页看全）。"""
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
    """路由入口一站式裁决（ADR-0139 P5）。

    org 解析 + 速率 + 并发（+ 可选存储）。任何配额面故障 fail-open
    （不放大故障）；越限抛 QuotaExceededError（QUOTA → 429 信封）。
    返回 effective org 供调用方复用。

    需要 ``DATABASE_URL`` 可用；AsyncSessionLocal 缺席（极端测试环境）
    直接放行。
    """
    from app.core import tenancy
    from app.core.database import AsyncSessionLocal

    if AsyncSessionLocal is None:
        return ""
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
