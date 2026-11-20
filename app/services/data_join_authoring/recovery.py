"""G38 恢复收敛 —— 「内容已发布、run 行未落终态」窗口的因果收敛（J21）。

窗口场景：``atomic_publish`` 之后、run 行 ``completed`` 之前进程崩溃/取消。
恢复判据（有因果证据，不凭文件存在宣称发布）：

1. 终态包存在 + ``receipt.json`` 可读 + 成员 sha256 与 manifest 一致
   → 补登记 run 行 completed（receipt 即执行体在发布后写入的证据）；
2. 终态包存在但 receipt 缺失/校验失败 → 丢弃终态包，run 行 failed
   （receipt_missing）—— 绝不假 completed；
3. staging 残留（>1h）→ 丢弃（进程内执行中的 staging 由 mtime 保护）。

活跃 job 保护：run 关联的 durable job 仍活跃（pending/running/cancelling）
时不动 —— 由该 job 自己终态化。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from app.services.data_join_authoring import outputs
from app.services.data_join_authoring.errors import DataJoinError

logger = logging.getLogger(__name__)

_STAGING_MAX_AGE_S = 3600.0


def _job_terminal(job_id: Any) -> bool:
    """job 缺失或已终态 → True（可安全收敛）。"""
    if job_id is None:
        return True
    try:
        from app.core.database import SessionLocal
        from app.models.db_model import AnalysisTask

        with SessionLocal() as db:
            row = db.get(AnalysisTask, int(job_id))
            status = str(row.status) if row is not None else None
            db.commit()
        return status not in ("pending", "queued", "running", "cancelling")
    except Exception as exc:  # noqa: BLE001 — DB 瞬时不可用：保守不收敛
        logger.warning("[data-join] recovery job probe failed: %s", exc)
        return False


def sweep_staging(max_age_s: float = _STAGING_MAX_AGE_S) -> int:
    """清理超龄 staging（执行中的 staging 因 mtime 新而受保护）。"""
    root = outputs.join_root() / outputs._STAGING
    if not root.is_dir():
        return 0
    removed = 0
    now = time.time()
    for d in root.iterdir():
        try:
            if d.is_dir() and now - d.stat().st_mtime > max_age_s:
                outputs.discard_staging(d.name)
                removed += 1
        except OSError:
            continue
    return removed


def recover_runs(session_id: str | None = None) -> Dict[str, Any]:
    """扫描非终态 run 行并按因果证据收敛。

    Returns: {"completed": [...], "failed": [...], "skipped_active": n}
    """

    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    completed: List[str] = []
    failed: List[str] = []
    skipped = 0
    with SessionLocal() as db:
        stmt = select(DataJoinRun).where(
            DataJoinRun.status.in_(["pending", "running"]))
        if session_id:
            stmt = stmt.where(DataJoinRun.session_id == session_id)
        rows = list(db.scalars(stmt))
        candidates = [(r.id, r.job_id) for r in rows]
    for run_id, job_id in candidates:
        if not _job_terminal(job_id):
            skipped += 1
            continue
        receipt = outputs.read_receipt(run_id)
        final_exists = outputs.final_dir(run_id).is_dir()
        if receipt is not None and final_exists:
            try:
                outputs.verify_published(run_id)
            except DataJoinError:
                outputs.discard_final(run_id)
                _set_run_status(run_id, "failed", "receipt_missing")
                failed.append(run_id)
                continue
            _set_run_status(run_id, "completed", receipt=receipt)
            completed.append(run_id)
        elif final_exists:
            # 内容在但无收据 —— 无因果证据，按失败收敛（不假 completed）
            outputs.discard_final(run_id)
            _set_run_status(run_id, "failed", "receipt_missing")
            failed.append(run_id)
        else:
            _set_run_status(run_id, "failed", "receipt_missing")
            failed.append(run_id)
    return {"completed": completed, "failed": failed, "skipped_active": skipped}


def _set_run_status(
    run_id: str, status: str, code: str | None = None,
    receipt: Dict[str, Any] | None = None,
) -> None:
    from datetime import datetime, timezone

    from app.core.database import SessionLocal
    from app.models.data_join_authoring import DataJoinRun

    with SessionLocal() as db:
        run = db.get(DataJoinRun, run_id)
        if run is None:
            return
        run.status = status
        run.error_code = code
        if receipt is not None:
            run.receipt = receipt
            run.output_ref = receipt.get("output_ref")
            run.output_sha256 = receipt.get("output_sha256")
            run.output_rows = receipt.get("output_rows")
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
    logger.info("[data-join] recovery run=%s -> %s (%s)", run_id, status, code)
