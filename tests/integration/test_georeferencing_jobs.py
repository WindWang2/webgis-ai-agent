"""G48 WP10: Durable G23 job idempotency, mid-warp cancel fencing, and crash recovery tests (R25-R28)."""
from __future__ import annotations

import asyncio
import io
import threading

import numpy as np
import pytest
from PIL import Image

from app.core.config import settings
from app.core.database import SessionLocal
from app.schemas.georeferencing_schema import GcpInputItem, GeorefDomainError, GeorefErrorCode
from app.services.durable_blob_store import reset_filesystem_blob_store
from app.services.georeferencing import jobs as georef_jobs
from app.services.georeferencing.intake import ingest_source_bytes
from app.services.georeferencing.recovery import recover_interrupted_jobs
from app.services.georeferencing.refs import purge_session_georef_state
from app.services.georeferencing.service import cancel_job, create_plan, execute_plan, revise_draft
from app.services.georeferencing.store import georef_session_dir, get_plan, mutate_plan_cas
from app.services.georeferencing.warp import WarpCancelledError
from app.services.jobs.lifecycle import JobStatus
from app.services.jobs.store import DurableJobStore


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    data_dir = (tmp_path / "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(settings, "DATA_DIR", str(data_dir), raising=False)
    import app.services.session_data as sd
    import app.services.project_artifact_promotion as pap

    monkeypatch.setattr(sd, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(pap, "_ROOT_CACHE", None, raising=False)
    reset_filesystem_blob_store()
    sid = "sess-g48-jobs-test"
    yield sid
    purge_session_georef_state(sid)
    reset_filesystem_blob_store()


async def _seed_valid_plan(sid: str):
    arr = np.full((32, 32, 3), 150, dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="PNG")
    src = await ingest_source_bytes(session_id=sid, filename="job_test.png", raw_bytes=buf.getvalue())
    plan = create_plan(session_id=sid, source_id=src.source_id, name="Jobs Test", target_crs="EPSG:3857")
    gcps = [
        GcpInputItem(gcp_id="g1", label="1", px=0.0, py=0.0, map_x=13370000.0, map_y=3530000.0),
        GcpInputItem(gcp_id="g2", label="2", px=32.0, py=0.0, map_x=13370064.0, map_y=3530000.0),
        GcpInputItem(gcp_id="g3", label="3", px=32.0, py=32.0, map_x=13370064.0, map_y=3529936.0),
        GcpInputItem(gcp_id="g4", label="4", px=0.0, py=32.0, map_x=13370000.0, map_y=3529936.0),
    ]
    plan = revise_draft(session_id=sid, plan_id=plan.plan_id, expected_revision=1, gcps=gcps)
    return src, plan


@pytest.mark.asyncio
async def test_idempotency_key_reuses_same_job_and_execution(isolated_env):
    sid = isolated_env
    _, plan = await _seed_valid_plan(sid)

    first = await execute_plan(
        session_id=sid,
        plan_id=plan.plan_id,
        expected_revision=plan.revision,
        background=False,
        idempotency_key="idem-key-01",
    )
    assert first["status"] == "completed"
    assert first["idempotent_reuse"] is False
    assert first["artifact"] is not None

    second = await execute_plan(
        session_id=sid,
        plan_id=plan.plan_id,
        expected_revision=plan.revision,
        background=False,
        idempotency_key="idem-key-01",
    )
    assert second["idempotent_reuse"] is True
    assert second["job_id"] == first["job_id"]
    assert second["execution_id"] == first["execution_id"]
    assert second["artifact"].artifact_id == first["artifact"].artifact_id


@pytest.mark.asyncio
async def test_r26_mid_warp_cancel_and_pre_publish_fence(isolated_env, monkeypatch):
    sid = isolated_env
    _, plan = await _seed_valid_plan(sid)

    entered_warp = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _blocking_warp(**kwargs):
        check_cancelled = kwargs["check_cancelled"]
        progress_cb = kwargs["progress_cb"]
        progress_cb(30, 100, "warp")
        loop.call_soon_threadsafe(entered_warp.set)
        for _ in range(200):
            if check_cancelled():
                raise WarpCancelledError("mid-warp cancellation observed")
            threading.Event().wait(0.01)
        raise AssertionError("Cancel signal never observed during warp")

    monkeypatch.setattr(georef_jobs, "run_direct_affine_warp", _blocking_warp)

    submitted = await execute_plan(
        session_id=sid,
        plan_id=plan.plan_id,
        expected_revision=plan.revision,
        background=True,
    )
    jid = int(submitted["job_id"])
    await asyncio.wait_for(entered_warp.wait(), timeout=5.0)
    await cancel_job(sid, plan.plan_id)

    task = georef_jobs._RUNNERS.get(jid)
    if task is not None:
        await asyncio.wait_for(task, timeout=5.0)

    with SessionLocal() as db:
        row = DurableJobStore.get_sync(db, jid)
        assert row is not None and row.status == JobStatus.cancelled.value
    fresh = get_plan(sid, plan.plan_id)
    assert fresh is not None
    assert fresh.latest_artifact is None
    assert fresh.active_job_id is None

    # Second test: cancel triggered AFTER warp returns but BEFORE publication fence!
    from app.services.georeferencing.warp import run_direct_affine_warp as real_warp

    async def _cancel_during_registration(**kwargs):
        from app.services.data_operations.runner import cancellation_registry

        cur_plan = get_plan(sid, plan.plan_id)
        assert cur_plan is not None and cur_plan.active_job_id
        cancellation_registry.cancel(int(cur_plan.active_job_id))
        return "ref:cancelled-before-publish"

    monkeypatch.setattr(georef_jobs, "run_direct_affine_warp", real_warp)
    monkeypatch.setattr(georef_jobs, "_register_raster_artifact", _cancel_during_registration)

    res2 = await execute_plan(
        session_id=sid,
        plan_id=plan.plan_id,
        expected_revision=fresh.revision,
        background=True,
    )
    jid2 = int(res2["job_id"])
    task2 = georef_jobs._RUNNERS.get(jid2)
    if task2 is not None:
        await asyncio.wait_for(task2, timeout=10.0)

    with SessionLocal() as db:
        row2 = DurableJobStore.get_sync(db, jid2)
        assert row2 is not None and row2.status == JobStatus.cancelled.value
    fresh2 = get_plan(sid, plan.plan_id)
    assert fresh2 is not None
    assert fresh2.latest_artifact is None
    # Verify no .tif artifact leaked onto disk from cancelled run
    art_dir = georef_session_dir(sid) / "artifacts"
    assert not list(art_dir.glob("*.tif"))


@pytest.mark.asyncio
async def test_r27_crash_recovery_cleans_orphan_staging_and_reconciles_stuck_jobs(isolated_env):
    sid = isolated_env
    _, plan1 = await _seed_valid_plan(sid)
    plan2 = create_plan(session_id=sid, source_id=plan1.source_id, name="Cancelling plan", target_crs="EPSG:3857")

    # Seed orphan staging dirs & partial publication files
    stage_dir = georef_session_dir(sid) / "artifacts" / ".g48-stage-crash"
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "partial.tif").write_bytes(b"half")
    pub_tmp = georef_session_dir(sid) / "artifacts" / "gart_x.pub-123"
    pub_tmp.write_bytes(b"pub")

    # Seed two stuck durable jobs in SQLite
    with SessionLocal() as db:
        row_run = DurableJobStore.create_sync(
            db,
            task_type="georeference_warp",
            kind="georeference",
            display_name="crashed running job",
            session_id=sid,
            parameters={},
        )
        DurableJobStore.transition_sync(db, row_run.id, JobStatus.running)
        row_canc = DurableJobStore.create_sync(
            db,
            task_type="georeference_warp",
            kind="georeference",
            display_name="crashed cancelling job",
            session_id=sid,
            parameters={},
        )
        DurableJobStore.mark_running_sync(db, row_canc.id)
        DurableJobStore.request_cancel_sync(db, row_canc.id)
        db.commit()
        jid_run = row_run.id
        jid_canc = row_canc.id

    mutate_plan_cas(
        sid,
        plan1.plan_id,
        expected_revision=None,
        mutator=lambda c: c.model_copy(update={"active_job_id": str(jid_run)}),
    )
    mutate_plan_cas(
        sid,
        plan2.plan_id,
        expected_revision=None,
        mutator=lambda c: c.model_copy(update={"active_job_id": str(jid_canc)}),
    )

    summary = recover_interrupted_jobs()
    assert summary["staging_cleaned"] >= 2
    assert summary["failed"] >= 1
    assert summary["cancelled"] >= 1
    assert not stage_dir.exists()
    assert not pub_tmp.exists()

    with SessionLocal() as db:
        r1 = DurableJobStore.get_sync(db, jid_run)
        r2 = DurableJobStore.get_sync(db, jid_canc)
        assert r1 is not None and r1.status == JobStatus.failed.value
        assert "WORKER_RESTART_INTERRUPTED" in str(r1.error_trace or "")
        assert r2 is not None and r2.status == JobStatus.cancelled.value

    assert get_plan(sid, plan1.plan_id).active_job_id is None
    assert get_plan(sid, plan2.plan_id).active_job_id is None

    # Plan remains cleanly executable after recovery
    res = await execute_plan(
        session_id=sid,
        plan_id=plan1.plan_id,
        expected_revision=plan1.revision,
        background=False,
    )
    assert res["status"] == "completed"
    assert res["artifact"] is not None


@pytest.mark.asyncio
async def test_r25_scoped_idempotency_retry_live_draft_isolation_and_reexec_cleanup(isolated_env, monkeypatch):
    from pathlib import Path
    from app.services.georeferencing.refs import blob_has_live_ref
    from app.services.session_data import session_data_manager

    sid = isolated_env
    _, plan = await _seed_valid_plan(sid)

    # 1) Retry after failure MUST be permitted even with the same idempotency_key
    def _fail_once(**_kwargs):
        raise RuntimeError("injected transient warp error")

    monkeypatch.setattr(georef_jobs, "run_direct_affine_warp", _fail_once)
    with pytest.raises(GeorefDomainError):
        await execute_plan(
            session_id=sid,
            plan_id=plan.plan_id,
            expected_revision=plan.revision,
            background=False,
            idempotency_key="retry-key",
        )

    from app.services.georeferencing.warp import run_direct_affine_warp as real_warp

    # 2) Same idempotency_key retries cleanly -> idempotent_reuse=False; meanwhile revise_draft concurrently
    #    mutates plan CRS/revision mid-warp without altering the frozen execution snapshot!
    mutated_mid_warp = False

    def _warp_and_mutate_draft(**kwargs):
        nonlocal mutated_mid_warp
        if not mutated_mid_warp:
            mutated_mid_warp = True
            revise_draft(
                session_id=sid,
                plan_id=plan.plan_id,
                expected_revision=plan.revision,
                target_crs="EPSG:32650",
            )
        return real_warp(**kwargs)

    monkeypatch.setattr(georef_jobs, "run_direct_affine_warp", _warp_and_mutate_draft)
    exec1 = await execute_plan(
        session_id=sid,
        plan_id=plan.plan_id,
        expected_revision=plan.revision,
        background=False,
        idempotency_key="retry-key",
    )
    assert exec1["status"] == "completed"
    assert exec1["idempotent_reuse"] is False
    art1 = exec1["artifact"]
    assert art1 is not None
    # Frozen snapshot kept EPSG:3857 even though draft moved to rev 3 (EPSG:32650)!
    assert art1.target_crs == "EPSG:3857"
    assert get_plan(sid, plan.plan_id).target_crs == "EPSG:32650"
    assert Path(art1.file_path).is_file()
    assert blob_has_live_ref(art1.sha256, sid)

    # 3) Same idempotency_key on the newer revision runs a fresh job and cleans up superseded unbound art1!
    monkeypatch.setattr(georef_jobs, "run_direct_affine_warp", real_warp)
    updated_plan = get_plan(sid, plan.plan_id)
    assert updated_plan is not None and updated_plan.revision == 3
    exec2 = await execute_plan(
        session_id=sid,
        plan_id=plan.plan_id,
        expected_revision=updated_plan.revision,
        background=False,
        idempotency_key="retry-key",
    )
    assert exec2["idempotent_reuse"] is False
    art2 = exec2["artifact"]
    assert art2 is not None and art2.artifact_id != art1.artifact_id
    assert art2.target_crs == "EPSG:32650"
    assert not Path(art1.file_path).exists()
    assert not blob_has_live_ref(art1.sha256, sid)
    assert await session_data_manager.get(sid, art1.raster_ref) is None


@pytest.mark.asyncio
async def test_r29_mid_job_source_swap_aborts_fail_closed(isolated_env, monkeypatch):
    from pathlib import Path
    from app.services.georeferencing.warp import run_direct_affine_warp as real_warp

    sid = isolated_env
    src, plan = await _seed_valid_plan(sid)
    swapped = False

    def _swap_source_mid_warp(**kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            Path(src.normalized_path).write_bytes(b"SWAPPED_DURING_WARP")
        return real_warp(**kwargs)

    monkeypatch.setattr(georef_jobs, "run_direct_affine_warp", _swap_source_mid_warp)
    with pytest.raises(GeorefDomainError) as exc_info:
        await execute_plan(
            session_id=sid,
            plan_id=plan.plan_id,
            expected_revision=plan.revision,
            background=False,
        )
    assert exc_info.value.code == GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED
    assert not list((georef_session_dir(sid) / "artifacts").glob("*.tif"))
