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

import asyncio
import inspect
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

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


# ── Promotion store refcount GC（Wave 1，audit §7.8）──────────────────────
# 此前晋升内容库只报告不删除（无任何保留策略）。现在：blob 可删当且仅当
# —— 零 artifact_revisions 引用 AND 无任何 Artifact.metadata_json.
# content_location 指针 AND 未被 pin AND 超出宽限期。保护判定是**单一
# 函数**（plan/execute 双侧同规则 —— 仅 planner 声明而执行器不执行 =
# 假保护，同 §十四的 plan/execute 对称纪律）；被任何 revision/Artifact 行
# 引用的 blob 无论层级绝不删除。

_DEFAULT_PROMOTION_GC_GRACE_HOURS = 168.0  # 7d，与 exports 同量级的保守宽限


def _promotion_gc_grace_hours() -> float:
    return _retention_days(
        "PROMOTION_STORE_GC_GRACE_HOURS", _DEFAULT_PROMOTION_GC_GRACE_HOURS
    )


def _promotion_blob_protection(
    key: str,
    location: str,
    mtime: float,
    *,
    now: float,
    grace_hours: float,
    refcounts_by_location: Dict[str, int],
    refcounts_by_sha: Dict[str, int],
    artifact_locations: set,
    pinned_shas: set,
) -> Optional[str]:
    """共享保护判定（plan 与 execute 都调用）：返回保护原因；None = 可删。

    前四条是引用保护（与层级无关 —— 被任何 revision/Artifact 行引用的
    blob 永不删除；pin 是用户显式表态，报告优先级最高）；宽限期只保护
    "新写入尚未入账"的窗口。
    """
    if key in pinned_shas:
        return "pinned"
    if refcounts_by_location.get(location, 0) > 0:
        return "referenced by artifact_revisions"
    if refcounts_by_sha.get(key, 0) > 0:
        return "referenced by artifact_revisions (sha)"
    if location in artifact_locations:
        return "head pointer (Artifact.metadata_json.content_location)"
    if mtime > now - grace_hours * 3600.0:
        return "grace period"
    return None


def _promotion_gc_snapshot(db, locations):
    """一次 DB 快照：引用计数（按 location 与按 sha 双口径）+ head 指针 + pin。"""
    from sqlalchemy import func, select

    from app.models.project import ArtifactRevision
    from app.services.artifact_revisions import (
        artifact_content_locations,
        pinned_content_sha256s,
        referencing_counts,
    )

    ref_by_loc = referencing_counts(db, locations)
    sha_rows = db.execute(
        select(
            ArtifactRevision.content_sha256, func.count(ArtifactRevision.id)
        ).group_by(ArtifactRevision.content_sha256)
    ).all()
    ref_by_sha = {sha: int(cnt) for sha, cnt in sha_rows}
    return {
        "refcounts_by_location": ref_by_loc,
        "refcounts_by_sha": ref_by_sha,
        "artifact_locations": set(artifact_content_locations(db)),
        "pinned_shas": set(pinned_content_sha256s(db)),
    }


def _plan_promotion_store_gc_sync(grace_hours: float, now: float) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.services.durable_blob_store import get_filesystem_blob_store

    store = get_filesystem_blob_store()
    candidates = [(key, path) for key, path in store.iter_blob_files()]
    locations = []
    for _key, path in candidates:
        try:
            locations.append(str(path.relative_to(store.root)))
        except ValueError:  # 防御：越界路径不参与（也不会被删）
            locations.append("")
    with SessionLocal() as db:
        snap = _promotion_gc_snapshot(db, locations)
    deletable = []
    protected: Dict[str, int] = {}
    deletable_bytes = 0
    for (key, path), location in zip(candidates, locations):
        if not location:
            continue
        reason = _promotion_blob_protection(
            key, location, store.blob_mtime(path),
            now=now, grace_hours=grace_hours, **snap,
        )
        if reason is not None:
            protected[reason] = protected.get(reason, 0) + 1
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        deletable.append({"key": key, "location": location, "bytes": size})
        deletable_bytes += size
    deletable.sort(key=lambda d: d["key"])
    return {
        "grace_hours": grace_hours,
        "now": now,
        "candidate_blobs": len(candidates),
        "protected_counts": dict(sorted(protected.items())),
        "deletable": deletable,
        "deletable_bytes": deletable_bytes,
    }


def _execute_promotion_store_gc_sync(plan: Dict[str, Any]) -> Dict[str, Any]:
    from app.core.database import SessionLocal
    from app.services.durable_blob_store import get_filesystem_blob_store

    store = get_filesystem_blob_store()
    # 复检与 planner 同一参数（grace/now 取自 plan）：同 DB 状态 ⇒ 同判定。
    grace_hours = float(plan.get("grace_hours") or _promotion_gc_grace_hours())
    now = float(plan.get("now") or time.time())
    planned = [
        (str(d["key"]), str(d["location"]))
        for d in (plan.get("deletable") or [])
    ]
    locations = [loc for _k, loc in planned]
    with SessionLocal() as db:
        snap = _promotion_gc_snapshot(db, locations)
    deleted = []
    skipped = []
    bytes_freed = 0
    failed = []
    for key, location in planned:
        # 执行前以**新鲜 DB 状态 + 同一谓词**复检（plan→execute 之间可能有
        # 新修订/pin 落地 —— 任何新引用即刻受保护）。
        path = store.root / location
        reason = _promotion_blob_protection(
            key, location, store.blob_mtime(path),
            now=now, grace_hours=grace_hours, **snap,
        )
        if reason is not None:
            skipped.append({"key": key, "reason": reason})
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if store.delete_blob(key):
            deleted.append(key)
            bytes_freed += size
        else:
            failed.append(key)
    return {
        "deleted": deleted,
        "skipped_protected": skipped,
        "failed": failed,
        "bytes_freed": bytes_freed,
    }


async def plan_promotion_store_gc(
    *, grace_hours: Optional[float] = None, now: Optional[float] = None
) -> Dict[str, Any]:
    """Dry-run 引用计数清扫计划（只读，不删除任何字节）。

    可删 = 零修订引用 AND 无 head 指针 AND 未 pin AND 超出宽限期
    （判定细节见共享谓词 ``_promotion_blob_protection``）。sync DB/文件
    IO 在 worker 线程执行（与既有清扫步骤同纪律）。
    """
    return await asyncio.to_thread(
        plan_promotion_store_gc_sync,
        grace_hours=grace_hours,
        now=now,
    )


def plan_promotion_store_gc_sync(
    *, grace_hours: Optional[float] = None, now: Optional[float] = None
) -> Dict[str, Any]:
    """``plan_promotion_store_gc`` 的同步体（W12 运维端点 / 测试直接调用 ——
    sync 路由已在 worker 线程池，无需再起事件循环）。"""
    return _plan_promotion_store_gc_sync(
        float(grace_hours) if grace_hours is not None else _promotion_gc_grace_hours(),
        float(now) if now is not None else time.time(),
    )


async def execute_promotion_store_gc(plan: Dict[str, Any]) -> Dict[str, Any]:
    """执行 dry-run 计划：对计划中的每个 key 以新鲜 DB 状态 + **同一保护
    谓词**复检后删除。绝不删除被任何 revision/Artifact 行引用的 blob。"""
    if not isinstance(plan, dict) or not plan.get("deletable"):
        return {"deleted": [], "skipped_protected": [], "failed": [], "bytes_freed": 0}
    return await asyncio.to_thread(execute_promotion_store_gc_sync, plan)


def execute_promotion_store_gc_sync(plan: Dict[str, Any]) -> Dict[str, Any]:
    """``execute_promotion_store_gc`` 的同步体（W12 运维端点 / 测试直接调用）。"""
    if not isinstance(plan, dict) or not plan.get("deletable"):
        return {"deleted": [], "skipped_protected": [], "failed": [], "bytes_freed": 0}
    return _execute_promotion_store_gc_sync(plan)


async def _sweep_promotion_store() -> Dict[str, Any]:
    """Wave 1：引用计数清扫替代只报告 pass（plan → execute）。

    Returns a partial result dict merged into the sweep result by the caller
    (fault-isolated: failures are logged and skipped, never raised).
    """
    partial: Dict[str, Any] = {}
    try:
        plan = await plan_promotion_store_gc()
        deleted = 0
        if plan.get("deletable"):
            gc = await execute_promotion_store_gc(plan)
            deleted = len(gc.get("deleted", []))
            partial["promotion_store_gc_bytes"] = int(gc.get("bytes_freed", 0))
        partial["promotion_store_gc_deleted"] = deleted
        partial["promotion_store_gc_protected"] = dict(
            plan.get("protected_counts") or {}
        )
    except Exception as e:  # noqa: BLE001 — reclamation must not break delete
        logger.warning("[artifact-lifecycle] promotion-store gc failed: %s", e)
    return partial


async def _sweep_retention_and_orphans() -> Dict[str, Any]:
    """Wave 12：保留策略（unpinned 修订超龄）+ 孤儿修订行清扫。

    两者都是 plan → execute pair（同一保护谓词双侧复检，W12 quota.py）。
    默认部署（无 env）保留策略 keep-forever → 保留计划恒空；孤儿修订行
    清理有界（FK-less 漂移防护）。fault-isolated：失败只告警不阻断。
    sync DB/文件 IO 整体在 worker 线程执行（独立 Session，不跨线程共享）。
    """
    from app.services.data_lifecycle.quota import (
        execute_orphan_revision_cleanup,
        execute_retention_cleanup,
        plan_orphan_revision_cleanup,
        plan_retention_cleanup,
    )

    def _sync_body() -> Dict[str, Any]:
        from app.core.database import SessionLocal

        partial: Dict[str, Any] = {}
        with SessionLocal() as db:
            retention = execute_retention_cleanup(plan_retention_cleanup(db))
            orphans = execute_orphan_revision_cleanup(
                plan_orphan_revision_cleanup(db)
            )
        partial["retention_deleted_revisions"] = len(
            retention.get("deleted_revisions") or [])
        partial["retention_deleted_blobs"] = len(
            retention.get("deleted_blobs") or [])
        partial["retention_bytes_freed"] = int(retention.get("bytes_freed") or 0)
        partial["orphan_revision_rows_removed"] = int(
            orphans.get("deleted_count") or 0)
        return partial

    return await asyncio.to_thread(_sync_body)


async def sweep_aged_artifacts() -> Dict[str, Any]:
    """Periodic age-based sweep across all artifact families.

    Wave 1: 晋升内容库从「只报告」升级为「报告 + 引用计数清扫」
    （``_sweep_promotion_store``：plan → execute，保护谓词双侧共享）。
    """
    import asyncio

    result = {"exports_removed": 0, "report_rows_removed": 0,
              "orphan_upload_rows_removed": 0, "orphan_upload_dirs_removed": 0,
              "artifact_cache_orphans_removed": 0}

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

    def _sweep_artifact_cache_dir() -> None:
        """V3 data foundation 第四族：``data/artifacts`` 磁盘缓存孤儿清扫。

        （audit #D-gap：该目录此前只有写路径字节上限 LRU —— .meta 缺失的
        .tif、崩溃遗留临时件、超龄条目均无人回收。清扫器在 artifact_cache
        内定义（与键命名纪律同源），这里只做接线与容错。）
        """
        try:
            from app.lib.artifact_cache import sweep_orphan_disk_artifacts

            result["artifact_cache_orphans_removed"] = sum(
                sweep_orphan_disk_artifacts().values()
            )
        except Exception as e:  # noqa: BLE001 — reclamation must not break delete
            logger.warning("[artifact-lifecycle] artifact-cache sweep failed: %s", e)

    def _report_promotion_store_usage() -> None:
        """V3 data foundation：晋升内容库使用量诊断。

        promoted 内容属 workspace/persistent 层 —— §十四的保护对象。规模
        与最老条目年龄仍照常暴露；**删除**由下方引用计数清扫器承担
        （plan/execute 共享保护谓词，audit #D-gap / §7.8）。
        """
        try:
            from app.services.project_artifact_promotion import content_store_root

            root = content_store_root()
            if not root.is_dir():
                return
            files = 0
            total = 0
            oldest = 0.0
            for p in root.rglob("*.json"):
                try:
                    st = p.stat()
                except OSError:
                    continue
                files += 1
                total += st.st_size
                oldest = max(oldest, max(time.time() - st.st_mtime, 0.0))
            result["promotion_store_files"] = files
            result["promotion_store_bytes"] = total
            result["promotion_store_oldest_age_s"] = int(oldest)
        except Exception as e:  # noqa: BLE001 — diagnostics only
            logger.warning("[artifact-lifecycle] promotion-store report failed: %s", e)

    try:
        await asyncio.wait_for(asyncio.to_thread(_sweep_exports), timeout=30.0)
    except Exception as e:  # noqa: BLE001
        logger.warning("[artifact-lifecycle] export sweep failed: %s", e)
    for step in (_sweep_reports, _sweep_orphan_uploads, _sweep_artifact_cache_dir,
                 _report_promotion_store_usage, _sweep_promotion_store,
                 _sweep_retention_and_orphans):
        try:
            # 同步步骤必须经 to_thread 卸载，否则 wait_for 收到 None 结果
            # 抛 TypeError 被吞掉 —— 步骤体执行了但超时保护失效（预存缺陷修复）。
            coro = step() if inspect.iscoroutinefunction(step) else asyncio.to_thread(step)
            step_result = await asyncio.wait_for(coro, timeout=30.0)
            if isinstance(step_result, dict):
                result.update(step_result)
        except Exception as e:  # noqa: BLE001
            logger.warning("[artifact-lifecycle] sweep step failed: %s", e)
    if any(result.values()):
        logger.info("[artifact-lifecycle] sweep: %s", result)
    return result
