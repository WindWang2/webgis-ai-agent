"""Startup crash recovery and orphan staging cleanup for G48 georeferencing (R27)."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Dict

from app.core.database import SessionLocal
from app.services.georeferencing.store import (
    _data_base,
    iter_all_session_states,
    mutate_plan_cas,
)
from app.services.jobs.lifecycle import JobStatus
from app.services.jobs.store import DurableJobStore

logger = logging.getLogger(__name__)


def sweep_staging() -> int:
    """Remove abandoned `.g48-stage-*` and `*.pub-*` staging files left by process crashes."""
    base = _data_base()
    if not base.is_dir():
        return 0
    cleaned = 0
    for pattern in ("*/georeferencing/**/.g48-stage-*", "georeferencing/**/.g48-stage-*"):
        for d in base.glob(pattern):
            try:
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
                    cleaned += 1
            except OSError:
                continue
    for pattern in ("*/georeferencing/**/*.pub-*", "georeferencing/**/*.pub-*"):
        for f in base.glob(pattern):
            try:
                if f.is_file():
                    f.unlink(missing_ok=True)
                    cleaned += 1
            except OSError:
                continue
    return cleaned


def recover_interrupted_jobs() -> Dict[str, int]:
    """Reconcile any G48 jobs stuck in active states across server restarts (R27)."""
    staged = sweep_staging()
    recovered_failed = 0
    recovered_cancelled = 0

    states = iter_all_session_states()
    active_job_ids: list[tuple[str, str, str]] = []
    for st in states:
        sid = str(st.get("session_id") or "")
        plans = st.get("plans") or {}
        if not isinstance(plans, dict):
            continue
        for pid, rp in plans.items():
            if isinstance(rp, dict) and rp.get("active_job_id"):
                active_job_ids.append((sid, str(pid), str(rp["active_job_id"])))

    if not active_job_ids:
        return {"staging_cleaned": staged, "failed": 0, "cancelled": 0}

    with SessionLocal() as db:
        for sid, pid, jid_str in active_job_ids:
            try:
                jid = int(jid_str)
            except ValueError:
                continue
            row = DurableJobStore.get_sync(db, jid)
            if row is not None:
                job_st = JobStatus(row.status)
                if job_st == JobStatus.cancelling:
                    DurableJobStore.confirm_cancelled_sync(
                        db,
                        jid,
                        message="Cancelled job reconciled during startup recovery",
                    )
                    recovered_cancelled += 1
                elif job_st in (JobStatus.pending, JobStatus.queued, JobStatus.running):
                    DurableJobStore.mark_failed_sync(
                        db,
                        jid,
                        error="WORKER_RESTART_INTERRUPTED: Georeferencing job interrupted by process restart; retry cleanly.",
                        message="WORKER_RESTART_INTERRUPTED: Georeferencing job interrupted by process restart; retry cleanly.",
                    )
                    recovered_failed += 1
            try:
                def _clear_active(plan, expected_jid=jid_str):
                    if plan.active_job_id == expected_jid:
                        plan.active_job_id = None
                        plan.active_execution_id = None
                    return plan

                mutate_plan_cas(sid, pid, expected_revision=None, mutator=_clear_active)
            except Exception:  # noqa: BLE001
                logger.debug("[georef-recovery] failed to clear active_job_id %s/%s", sid, pid)
        db.commit()

    return {
        "staging_cleaned": staged,
        "failed": recovered_failed,
        "cancelled": recovered_cancelled,
    }
