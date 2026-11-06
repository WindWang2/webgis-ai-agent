"""Runner-local J2 fixture: real NDVI failure, repaired input, real retry.

This is deliberately a CLI, never an application endpoint. The browser first
signs in and creates a real conversation. We verify that persisted ownership,
submit the ordinary NDVI Celery task with a missing input, and only restore the
input after the worker has persisted a failed first attempt. No task state is
manufactured. The browser owns cancellation/retry through the production API.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import uuid

from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.db_model import AnalysisTask, Conversation, User
from app.services.jobs import DurableJobStore
from app.services.jobs.context import JobOrigin, use_origin
from app.services.jobs.submit import submit_durable_job
from app.services.spatial_tasks import run_ndvi_analysis


JOB_NAME = "NDVI 任务重试旅程"
MISSING_INPUT_ERROR = "输入影像文件不存在"


def require_fixture_owner(session_id: str, user_id: str) -> None:
    """Fail closed outside the runner-owned, authenticated SQLite stack."""
    data_dir = Path(settings.DATA_DIR).resolve()
    db_url = make_url(settings.DATABASE_URL)
    if (
        os.environ.get("E2E_MODE") != "real"
        or settings.is_production()
        or settings.AUTH_DISABLED
        or db_url.get_backend_name() != "sqlite"
        or not db_url.database
        or not Path(db_url.database).resolve().is_relative_to(data_dir)
    ):
        raise RuntimeError("J2 fixture requires the authenticated runner-owned SQLite stack")
    username = os.environ.get("E2E_USER")
    if not username or not session_id or not user_id:
        raise RuntimeError("J2 fixture requires E2E_USER and an existing owned conversation")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        conversation = db.get(Conversation, session_id)
        if (
            user is None or user.username != username or not user.is_active
            or conversation is None or conversation.user_id != user.id
        ):
            raise RuntimeError("J2 fixture session/user ownership mismatch")


def require_fixture_job(db: Session, job_id: str, session_id: str, user_id: str) -> AnalysisTask:
    job = DurableJobStore.get_sync(db, job_id)
    if (
        job is None or job.creator_id != user_id or job.session_id != session_id
        or job.display_name != JOB_NAME
    ):
        raise RuntimeError("J2 fixture job ownership mismatch")
    return job


def prepare_retry_fixture(session_id: str, user_id: str, *, timeout: float = 30) -> dict:
    require_fixture_owner(session_id, user_id)
    from app.services.scoped_raster_paths import scoped_raster_roots

    directory = scoped_raster_roots(session_id=session_id)[0] / "e2e-journeys" / "j2" / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    raster_path = directory / "recovered-input.tif"
    with use_origin(JobOrigin(session_id=session_id, owner_id=user_id)):
        submitted = submit_durable_job(
            celery_task=run_ndvi_analysis,
            task_type="ndvi",
            display_name=JOB_NAME,
            params={"raster_path": str(raster_path), "red_band": 1, "nir_band": 2},
            task_kwargs={
                "raster_path": str(raster_path), "red_band": 1, "nir_band": 2,
                "session_id": session_id,
            },
        )
    job_id = submitted["job_id"]
    deadline = time.monotonic() + timeout
    while True:
        with SessionLocal() as db:
            job = require_fixture_job(db, job_id, session_id, user_id)
            status = job.status
            if status == "failed":
                if job.attempt != 1 or MISSING_INPUT_ERROR not in (job.error_trace or ""):
                    raise RuntimeError(f"J2 fixture failed for an unexpected reason: {job.error_trace}")
                break
            if status not in ("pending", "queued", "running") or time.monotonic() >= deadline:
                raise RuntimeError(f"J2 fixture never reached the expected worker failure: {status}")
        # Poll the persisted worker state, not a fixed delay before assertions.
        time.sleep(0.1)

    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    # Small, deterministic real data: NDVI = (6 - 2) / (6 + 2) = 0.5.
    with rasterio.open(
        raster_path, "w", driver="GTiff", width=16, height=16, count=2,
        dtype="float32", crs="EPSG:4326", transform=from_origin(116.3, 39.9, 0.001, 0.001),
    ) as raster:
        raster.write(np.full((16, 16), 2, dtype="float32"), 1)
        raster.write(np.full((16, 16), 6, dtype="float32"), 2)
    return {"job_id": job_id, "name": JOB_NAME, "session_id": session_id, "status": "failed", "attempt": 1}


def verify_retry_output(job_id: str, session_id: str, user_id: str) -> dict:
    require_fixture_owner(session_id, user_id)
    with SessionLocal() as db:
        job = require_fixture_job(db, job_id, session_id, user_id)
        if job.status != "completed" or job.attempt != 2 or job.retry_count != 1:
            raise RuntimeError(f"J2 retry did not complete attempt 2: {job.status}, attempt={job.attempt}")
        if MISSING_INPUT_ERROR not in (job.error_trace or ""):
            raise RuntimeError("J2 retry lost the first failure evidence")
        if not job.result_ref:
            raise RuntimeError("J2 retry produced no raster artifact")
        result_path = Path(job.result_ref).resolve()
        if not result_path.is_relative_to(Path(settings.DATA_DIR).resolve()):
            raise RuntimeError("J2 result escaped the runner-owned data directory")

    import numpy as np
    import rasterio

    with rasterio.open(result_path) as raster:
        values = raster.read(1, masked=True)
        if (
            raster.count != 1 or raster.width != 16 or raster.height != 16
            or values.count() != 256 or not np.allclose(values.compressed(), 0.5)
        ):
            raise RuntimeError("J2 retry artifact does not contain the expected NDVI result")
    return {"job_id": job_id, "status": "completed", "attempt": 2, "valid_pixels": 256, "ndvi": 0.5}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--job-id")
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare_retry_fixture(args.session_id, args.user_id)
    else:
        if not args.job_id:
            parser.error("verify requires --job-id")
        result = verify_retry_output(args.job_id, args.session_id, args.user_id)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
