"""Organization quota admission in the SAME transaction as durable writes.

All persistent producers share one organization transaction lock and query the
actual resource tables. No reservation counter can drift from committed rows.
PostgreSQL uses transaction advisory locks; SQLite serializes writers with a
no-op UPDATE (also safe when a caller already began a deferred transaction).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import case, func, select, text, update
from sqlalchemy.orm import Session

from app.core.errors import ErrorCategory, PlatformError
from app.models.db_model import OrgQuota, GeoComputeClusterRun, WorkflowInstanceRow, GeoComputeArtifact, AnalysisTask
from app.models.lakehouse_catalog import LakehouseCatalogItem
from app.models.lakehouse_datasets import LakehouseDatasetVersion
from app.services import org_quota as quota

logger = logging.getLogger(__name__)


def lock_org_transaction(db: Session, org_id: str) -> None:
    """Hold admission serialization until this Session commits or rolls back."""
    if not str(org_id or "").strip():
        raise PlatformError("organization quota scope unavailable", category=ErrorCategory.RETRYABLE, http_status=503)
    try:
        dialect = db.get_bind().dialect.name
        with db.no_autoflush:
            connection = db.connection()
            dbapi = connection.connection.dbapi_connection
            if dialect == "postgresql":
                isolation = connection.get_isolation_level().upper().replace("_", " ")
                if isolation not in {"READ COMMITTED", "READ UNCOMMITTED"} or getattr(dbapi, "autocommit", False):
                    # An advisory lock cannot refresh a snapshot established
                    # earlier in a REPEATABLE READ/SERIALIZABLE transaction.
                    # Autocommit releases xact locks before the later write.
                    raise PlatformError("organization quota admission requires READ COMMITTED without autocommit", category=ErrorCategory.RETRYABLE, http_status=503)
                key = int.from_bytes(hashlib.sha256(("org-quota:" + str(org_id)).encode()).digest()[:8], "big", signed=True)
                if not db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}).scalar_one():
                    # Do not hold a worker forever behind a stalled caller's
                    # transaction. Contention is a retryable admission failure.
                    raise RuntimeError("organization admission busy; retry after current transaction")
            elif dialect == "sqlite":
                if getattr(dbapi, "isolation_level", None) is None or getattr(dbapi, "autocommit", None) is True:
                    raise PlatformError("organization quota admission requires a transactional SQLite connection", category=ErrorCategory.RETRYABLE, http_status=503)
                # Even with no policy row, UPDATE obtains SQLite's writer lock.
                # Unlike BEGIN IMMEDIATE this works in caller-owned transactions.
                db.execute(update(OrgQuota).where(OrgQuota.org_id == str(org_id)).values(org_id=OrgQuota.org_id))
            else:
                raise RuntimeError("atomic organization quota admission requires PostgreSQL or SQLite")
    except Exception as exc:
        logger.warning("[quota] transaction admission lock unavailable", exc_info=True)
        if isinstance(exc, PlatformError):
            raise
        raise PlatformError("organization quota admission unavailable", category=ErrorCategory.RETRYABLE, http_status=503) from exc


def quota_byte_size(value: Any) -> int:
    """Validate the integer byte accounting that will actually be persisted."""
    number = int(value or 0)
    if isinstance(value, bool) or number < 0 or number > 2**63 - 1 or (isinstance(value, float) and value != number):
        raise ValueError("invalid nonnegative integer quota byte size")
    return number


def _limit(value: Any, default: int) -> int:
    return default if value is None else int(value)


def admit_persistent_write(
    db: Session, org_id: str, *, task_increment: int = 0, storage_delta: int | None = None,
) -> None:
    """Check actual usage while holding the org lock through the caller's write.

    Storage callers provide the increase after replacing an existing row, not
    the full size twice. A negative delta is a legitimate same-org shrink.
    Policy/aggregate/lock errors reject; limits <=0 keep documented unlimited
    semantics. This helper never commits or rolls back caller-owned work.
    """
    lock_org_transaction(db, org_id)
    try:
        row = db.execute(select(OrgQuota).where(OrgQuota.org_id == str(org_id))).scalar_one_or_none()
        max_tasks = _limit(row.max_concurrent_tasks if row is not None else None, quota.DEFAULT_MAX_CONCURRENT_TASKS)
        max_storage = _limit(row.max_storage_bytes if row is not None else None, quota.DEFAULT_MAX_STORAGE_BYTES)
        if task_increment > 0 and max_tasks > 0:
            used_tasks = int(db.execute(select(func.count()).select_from(GeoComputeClusterRun).where(
                GeoComputeClusterRun.org_id == str(org_id),
                GeoComputeClusterRun.status.not_in(("completed", "failed", "cancelled")),
            )).scalar_one())
            used_tasks += int(db.execute(select(func.count()).select_from(WorkflowInstanceRow).where(
                WorkflowInstanceRow.org_id == str(org_id),
                WorkflowInstanceRow.status.not_in(("succeeded", "failed", "cancelled", "superseded")),
            )).scalar_one())
            used_tasks += int(db.execute(select(func.count()).select_from(AnalysisTask).where(
                AnalysisTask.org_id == int(org_id), AnalysisTask.task_type == quota.SYNC_EXECUTION_TASK_TYPE,
                AnalysisTask.status.not_in(("completed", "failed", "cancelled")),
            )).scalar_one())
            if used_tasks + task_increment > max_tasks:
                raise quota.QuotaExceededError("concurrent_tasks", str(org_id), limit=max_tasks, used=used_tasks)
        if storage_delta is not None and max_storage > 0:
            used_storage = 0
            for column, model in (
                (LakehouseDatasetVersion.byte_size, LakehouseDatasetVersion),
                (LakehouseCatalogItem.byte_size, LakehouseCatalogItem),
                (GeoComputeArtifact.size_bytes, GeoComputeArtifact),
            ):
                # Historical negative bytes cannot buy future quota headroom.
                statement = select(func.coalesce(func.sum(case((column > 0, column), else_=0)), 0)).where(model.org_id == str(org_id))
                if model is LakehouseCatalogItem:
                    statement = statement.where(LakehouseCatalogItem.status == "active")
                used_storage += int(db.execute(statement).scalar_one())
            if storage_delta > 0 and used_storage + storage_delta > max_storage:
                raise quota.QuotaExceededError("storage_bytes", str(org_id), limit=max_storage, used=used_storage)
    except quota.QuotaExceededError:
        logger.info("[quota] persistent admission rejected org=%s", org_id)
        raise
    except Exception as exc:
        logger.warning("[quota] authoritative policy/usage unavailable", exc_info=True)
        raise PlatformError("organization quota policy or usage unavailable", category=ErrorCategory.RETRYABLE, http_status=503) from exc
