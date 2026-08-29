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

from app.services.report_service import REPORT_DIR

_DATA_DIR = Path(settings.DATA_DIR)
EXPORT_DIR = _DATA_DIR / "exports"
UPLOADS_DIR = _DATA_DIR / "uploads"
# audit #851: REPORT_DIR 直接采用写入方（report_service）的单一事实源 ——
# 此前按 settings.DATA_DIR 重算，DATA_DIR 覆写时清扫基地址与写入地址分叉。


def _upload_id_from_filename(filename: str) -> str:
    """audit #849: 从 UploadRecord.filename 恢复 upload_id。

    写入方存的是 `"{upload_dir}/{original}"`（upload_dir = base/uploads/<id>，
    base 可相对可绝对），取 `uploads` 段的**下一段**；仅当该段形如 32-hex
    id（写入方 uuid4().hex）时返回，绝不基于猜测删除目录。
    """
    if not filename:
        return ""
    parts = [seg for seg in str(filename).replace("\\", "/").split("/") if seg]
    for i, seg in enumerate(parts):
        if seg == "uploads" and i + 1 < len(parts):
            candidate = parts[i + 1]
            if len(candidate) == 32 and all(c in "0123456789abcdef" for c in candidate.lower()):
                return candidate
            return ""
    return ""

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
                upload_id = _upload_id_from_filename(str(row.filename or ""))
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
                    # #1068(E-11): 两次 unlink 之间崩溃会留下永生孤儿边车 ——
                    # 超龄且主件已缺失的边车按孤儿一并清除。
                    if entry.is_file() and entry.stat().st_mtime < cutoff:
                        primary = entry.with_name(entry.name[: -len(".owner")])
                        if not primary.exists():
                            removed += int(_safe_unlink(entry))
                    continue  # 有主件的随主件删除
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
            # audit #850: 仍有效的分享（share_expires_at 在未来）不随龄清除
            # —— 分享 API 允许最长 30 天 TTL，纯按 created_at 会提前杀死链接。
            rows = (await db.execute(
                select(Report).where(
                    Report.created_at < cutoff,
                    ~(
                        (Report.share_expires_at.isnot(None))
                        & (Report.share_expires_at > datetime.now(timezone.utc))
                    ),
                )
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
            # #1068(E-11): NULL-session 上传（匿名上传是合法路径，upload.py:95）
            # 此前被 `session_id.isnot(None)` 过滤排除 —— 既无会话可归属也
            # 永不老化，磁盘永久泄漏。NULL + 超龄即孤儿。
            from sqlalchemy import exists, or_
            orphan_stmt = select(UploadRecord).where(
                or_(
                    UploadRecord.session_id.is_(None),
                    ~exists(select(Conversation.id).where(
                        Conversation.id == UploadRecord.session_id)),
                ),
                UploadRecord.upload_time < cutoff,
            )
            rows = (await db.execute(orphan_stmt)).scalars().all()
            removed_rows = removed_dirs = 0
            for row in rows:
                upload_id = _upload_id_from_filename(str(row.filename or ""))
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
