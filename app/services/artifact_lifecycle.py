"""Artifact disk lifecycle (audit #837).

The mapspec family (``.webgis-agent/<sid>/``) always had reclamation
(``sweep_expired_session_files`` + clear_session linkage); three other
artifact families grew unbounded:

- ``data/exports`` (+ ``.owner`` sidecars) — write-only since introduction;
- ``data/reports`` — Report rows and PDF/HTML files never reclaimed, and
  Report rows are not cascaded with their conversation;
- ``data/uploads`` — UploadRecord rows + ``uploads/<uuid>/`` dirs orphaned
  after session deletion / idle eviction (#546 covered only failed uploads).

This module gives all three families the same two reclamation paths as the
mapspec family:

1. ``purge_session_artifacts(session_id)`` — session delete / eviction hook
   (reports + uploads are session-keyed; exports carry only user ownership
   sidecars, so they are handled by the age sweep alone);
2. ``sweep_aged_artifacts()`` — periodic age-based sweep (exports by mtime,
   reports by row age, orphaned uploads by row age).

Every step is independently fault-isolated (warning only): reclamation must
never break the delete/periodic path, and must never delete outside its own
directory family.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

from app.core.config import settings

logger = logging.getLogger(__name__)

_DATA_DIR = Path(settings.DATA_DIR)
EXPORT_DIR = _DATA_DIR / "exports"
REPORT_DIR = _DATA_DIR / "reports"
UPLOADS_DIR = _DATA_DIR / "uploads"

_DEFAULT_EXPORT_RETENTION_DAYS = 7.0
_DEFAULT_REPORT_RETENTION_DAYS = 14.0
_DEFAULT_UPLOAD_ORPHAN_RETENTION_DAYS = 7.0


def _retention_days(env_name: str, default: float) -> float:
    raw = os.environ.get(env_name, "")
    try:
        val = float(raw)
        return val if val > 0 else default
    except (TypeError, ValueError):
        return default


def _safe_unlink(path: Path) -> bool:
    try:
        if path.is_file() or path.is_symlink():
            path.unlink()
            return True
    except OSError as e:
        logger.warning("[artifact-lifecycle] unlink failed for %s: %s", path, e)
    return False


def _safe_rmtree(path: Path) -> bool:
    import shutil

    try:
        if path.is_dir():
            shutil.rmtree(path)
            return True
    except OSError as e:
        logger.warning("[artifact-lifecycle] rmtree failed for %s: %s", path, e)
    return False


async def purge_session_artifacts(session_id: str) -> Dict[str, Any]:
    """Session-delete/eviction hook: reclaim session-keyed artifacts.

    Returns a per-family result dict. Best-effort — failures are logged and
    reported, never raised (the caller's delete flow must not break).
    """
    result: Dict[str, Any] = {"session_id": session_id, "reports_removed": 0,
                              "upload_rows_removed": 0, "upload_dirs_removed": 0}
    import asyncio

    # ── reports (rows + files) ───────────────────────────────────────────
    async def _purge_reports() -> None:
        from sqlalchemy import select

        from app.models.report import Report
        from app.tools._utils import async_db_session

        async with async_db_session() as db:
            rows = (await db.execute(
                select(Report).where(Report.session_id == session_id)
            )).scalars().all()
            removed = 0
            for row in rows:
                fp = row.file_path
                if fp:
                    # only ever delete inside REPORT_DIR (defense in depth)
                    candidate = Path(fp)
                    try:
                        if candidate.resolve().parent == REPORT_DIR.resolve():
                            _safe_unlink(candidate)
                    except OSError:
                        pass
                await db.delete(row)
                removed += 1
            await db.commit()
            result["reports_removed"] = removed

    # ── uploads (rows + dirs) ────────────────────────────────────────────
    async def _purge_uploads() -> None:
        from sqlalchemy import select

        from app.models.upload import UploadRecord
        from app.tools._utils import async_db_session

        async with async_db_session() as db:
            rows = (await db.execute(
                select(UploadRecord).where(UploadRecord.session_id == session_id)
            )).scalars().all()
            removed_rows = removed_dirs = 0
            for row in rows:
                upload_id = str(row.filename or "").split("/")[0]
                if upload_id:
                    # only ever delete inside UPLOADS_DIR
                    candidate = UPLOADS_DIR / upload_id
                    try:
                        if candidate.resolve().parent == UPLOADS_DIR.resolve():
                            removed_dirs += int(_safe_rmtree(candidate))
                    except OSError:
                        pass
                await db.delete(row)
                removed_rows += 1
            await db.commit()
            result["upload_rows_removed"] = removed_rows
            result["upload_dirs_removed"] = removed_dirs

    for step in (_purge_reports, _purge_uploads):
        try:
            await asyncio.wait_for(step(), timeout=15.0)
        except Exception as e:  # noqa: BLE001 — reclamation must not break delete
            logger.warning(
                "[artifact-lifecycle] purge step failed for %s: %s", session_id, e
            )
    return result


async def sweep_aged_artifacts() -> Dict[str, int]:
    """Periodic age-based sweep across all three artifact families."""
    import asyncio

    result = {"exports_removed": 0, "report_rows_removed": 0,
              "orphan_upload_rows_removed": 0, "orphan_upload_dirs_removed": 0}

    def _sweep_exports() -> None:
        retention = _retention_days(
            "EXPORT_RETENTION_DAYS", _DEFAULT_EXPORT_RETENTION_DAYS)
        cutoff = time.time() - retention * 86400.0
        if not EXPORT_DIR.is_dir():
            return
        removed = 0
        for entry in EXPORT_DIR.iterdir():
            try:
                if entry.name.endswith(".owner"):
                    continue  # removed alongside its primary file
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    removed += int(_safe_unlink(entry))
                    sidecar = entry.with_name(entry.name + ".owner")
                    if sidecar.exists():
                        _safe_unlink(sidecar)
            except OSError:
                continue
        result["exports_removed"] = removed

    async def _sweep_reports() -> None:
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select

        from app.models.report import Report
        from app.tools._utils import async_db_session

        retention = _retention_days(
            "REPORT_RETENTION_DAYS", _DEFAULT_REPORT_RETENTION_DAYS)
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
        async with async_db_session() as db:
            rows = (await db.execute(
                select(Report).where(Report.created_at < cutoff)
            )).scalars().all()
            removed = 0
            for row in rows:
                if row.file_path:
                    candidate = Path(row.file_path)
                    try:
                        if candidate.resolve().parent == REPORT_DIR.resolve():
                            _safe_unlink(candidate)
                    except OSError:
                        pass
                await db.delete(row)
                removed += 1
            await db.commit()
            result["report_rows_removed"] = removed

    async def _sweep_orphan_uploads() -> None:
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import select

        from app.models.db_model import Conversation
        from app.models.upload import UploadRecord
        from app.tools._utils import async_db_session

        retention = _retention_days(
            "UPLOAD_ORPHAN_RETENTION_DAYS", _DEFAULT_UPLOAD_ORPHAN_RETENTION_DAYS)
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
        async with async_db_session() as db:
            # uploads whose session row is GONE (deleted conversation) — join-free
            # anti-join via NOT EXISTS keeps it portable across sqlite/pg.
            from sqlalchemy import exists
            orphan_stmt = select(UploadRecord).where(
                UploadRecord.session_id.isnot(None),
                UploadRecord.upload_time < cutoff,
                ~exists(select(Conversation.id).where(
                    Conversation.id == UploadRecord.session_id)),
            )
            rows = (await db.execute(orphan_stmt)).scalars().all()
            removed_rows = removed_dirs = 0
            for row in rows:
                upload_id = str(row.filename or "").split("/")[0]
                if upload_id:
                    candidate = UPLOADS_DIR / upload_id
                    try:
                        if candidate.resolve().parent == UPLOADS_DIR.resolve():
                            removed_dirs += int(_safe_rmtree(candidate))
                    except OSError:
                        pass
                await db.delete(row)
                removed_rows += 1
            await db.commit()
            result["orphan_upload_rows_removed"] = removed_rows
            result["orphan_upload_dirs_removed"] = removed_dirs

    try:
        await asyncio.wait_for(asyncio.to_thread(_sweep_exports), timeout=30.0)
    except Exception as e:  # noqa: BLE001
        logger.warning("[artifact-lifecycle] export sweep failed: %s", e)
    for step in (_sweep_reports, _sweep_orphan_uploads):
        try:
            await asyncio.wait_for(step(), timeout=30.0)
        except Exception as e:  # noqa: BLE001
            logger.warning("[artifact-lifecycle] sweep step failed: %s", e)
    if any(result.values()):
        logger.info("[artifact-lifecycle] sweep: %s", result)
    return result
