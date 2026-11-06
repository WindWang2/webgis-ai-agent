"""Committed synchronous-execution occupancy in the existing durable job ledger.

Only the worker which actually finishes releases its reservation. HTTP task
cancellation does not stop a to_thread worker and must not refund its slot.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import threading
import uuid

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.errors import ErrorCategory, PlatformError
from app.lib.cancellation import CancellationToken, use_token
from app.models.db_model import AnalysisTask
from app.services import org_quota
from app.services.geocompute.plan import ExecutionPlan, ExecutionRun
from app.services.jobs.lifecycle import JobStatus
from app.services.jobs.store import DurableJobStore
from app.services.org_quota_admission import admit_persistent_write

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncReservation:
    job_id: int
    worker_id: str
    run_id: str
    factory: Callable[[], Session]


def reserve_sync_execution(
    plan: ExecutionPlan, *, org_id: str, caller: dict, session_id: str | None,
    factory: Callable[[], Session] | None = None,
) -> SyncReservation:
    if factory is None:
        from app.core.database import SessionLocal
        factory = SessionLocal
    worker = 'sync:' + uuid.uuid4().hex
    run_id = 'gexec-' + uuid.uuid4().hex[:12]
    try:
        with factory() as db:
            admit_persistent_write(db, org_id, task_increment=1)
            row = DurableJobStore.create_sync(db, task_type=org_quota.SYNC_EXECUTION_TASK_TYPE,
                display_name='GeoCompute synchronous execution', owner_id=caller.get('user_id'),
                org_id=int(org_id), session_id=session_id, run_id=run_id,
                parameters={'plan_id': plan.plan_id, 'plan_fingerprint': plan.graph_fingerprint()},
                max_retries=0, status=JobStatus.running)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            db.execute(update(AnalysisTask).where(AnalysisTask.id == row.id).values(
                worker_id=worker, started_at=now, heartbeat_at=now))
            db.commit()
            return SyncReservation(int(row.id), worker, run_id, factory)
    except PlatformError:
        raise
    except Exception as exc:
        raise PlatformError('synchronous execution admission unavailable',
                            category=ErrorCategory.RETRYABLE, http_status=503) from exc


def _finish(reservation: SyncReservation, status: str) -> None:
    with reservation.factory() as db:
        changed = db.execute(update(AnalysisTask).where(
            AnalysisTask.id == reservation.job_id,
            AnalysisTask.worker_id == reservation.worker_id,
            AnalysisTask.task_type == org_quota.SYNC_EXECUTION_TASK_TYPE,
        ).values(status=status, completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                 progress=100 if status == 'completed' else 0).returning(AnalysisTask.id)).scalar_one_or_none()
        if changed is None:
            raise RuntimeError('synchronous reservation ownership lost')
        db.commit()


def execute_reserved(
    reservation: SyncReservation,
    operation: Callable[[CancellationToken, str], ExecutionRun],
) -> ExecutionRun:
    token = CancellationToken()
    stopped = threading.Event()

    def monitor() -> None:
        while not stopped.wait(10.0):
            try:
                with reservation.factory() as db:
                    row = db.get(AnalysisTask, reservation.job_id)
                    if row is None or row.worker_id != reservation.worker_id or row.status not in ('running', 'cancelling'):
                        token.cancel('synchronous reservation lost')
                        return
                    if row.cancel_requested_at is not None or row.status == 'cancelling':
                        token.cancel('durable cancellation requested')
                    db.execute(update(AnalysisTask).where(
                        AnalysisTask.id == reservation.job_id,
                        AnalysisTask.worker_id == reservation.worker_id,
                        AnalysisTask.status.in_(('running', 'cancelling')),
                    ).values(heartbeat_at=datetime.now(timezone.utc).replace(tzinfo=None)))
                    db.commit()
            except Exception:
                logger.warning('synchronous execution heartbeat unavailable', exc_info=True)
                token.cancel('reservation heartbeat unavailable')

    watcher = threading.Thread(target=monitor, name=f'sync-execution-{reservation.job_id}', daemon=True)
    watcher.start()
    status = 'failed'
    try:
        with use_token(token):
            result = operation(token, reservation.run_id)
        value = str(getattr(result.status, 'value', result.status))
        status = value if value in ('completed', 'cancelled') else 'failed'
        return result
    finally:
        stopped.set()
        watcher.join(timeout=2)
        # Runs inside the compute thread, even when its HTTP waiter disconnects.
        # If DB persistence fails the slot stays occupied (conservative retry).
        _finish(reservation, status)
