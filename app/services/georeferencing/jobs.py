"""G23 durable async execution, cancel fencing, and artifact registration for G48 (R25-R29, R32-R33)."""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.database import SessionLocal
from app.lib.cancellation import OperationCancelled
from app.services.data_operations.runner import (
    cancellation_registry,
    run_async_job,
    submit_async_job,
)
from app.services.georeferencing.refs import compute_file_sha256, pin_blob_from_path, release_blob_ref
from app.services.georeferencing.store import (
    georef_session_dir,
    get_idempotency_entry,
    get_plan,
    get_source,
    mutate_plan_cas,
    record_idempotency_entry,
)
from app.services.georeferencing.warp import (
    WarpCancelledError,
    compute_output_grid,
    run_direct_affine_warp,
)
from app.services.jobs.lifecycle import JobStatus
from app.services.jobs.store import DurableJobStore
from app.schemas.georeferencing_schema import (
    FrozenExecutionSnapshot,
    GeorefArtifactRecord,
    GeorefDomainError,
    GeorefDraftPlan,
    GeorefErrorCode,
    GeorefSourceRecord,
)

logger = logging.getLogger(__name__)

TASK_TYPE = "georeference_warp"
_RUNNERS: Dict[int, asyncio.Task] = {}
_STAGE_BY_JOB: Dict[int, str] = {}


def get_job_stage(job_id: int) -> str:
    return _STAGE_BY_JOB.get(int(job_id), "")


async def cleanup_artifact_resources(session_id: str, art: GeorefArtifactRecord) -> None:
    """Delete unreferenced artifact file, BlobStore pin, and raster registry entry."""
    from app.services.artifact_registry import mark_status
    from app.services.session_data import session_data_manager

    if art.file_path:
        try:
            Path(art.file_path).unlink(missing_ok=True)
        except OSError:
            pass
    if art.sha256:
        await asyncio.to_thread(release_blob_ref, art.sha256, session_id)
    if art.raster_ref:
        try:
            await session_data_manager.delete_ref(session_id, art.raster_ref)
        except Exception:  # noqa: BLE001
            pass
        try:
            await mark_status(session_id, art.raster_ref, "superseded")
        except Exception:  # noqa: BLE001
            pass


async def start_georeference_job(
    *,
    session_id: str,
    plan: GeorefDraftPlan,
    source: GeorefSourceRecord,
    expected_revision: int,
    background: bool = True,
    idempotency_key: Optional[str] = None,
    owner_id: Optional[str] = None,
    owner_token: Optional[str] = None,
    org_id: Optional[int] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Freeze plan at ``expected_revision`` and submit a G23 durable georeferencing job."""
    scoped_idem_key = (
        f"{plan.plan_id}:r{expected_revision}:{idempotency_key}" if idempotency_key else None
    )
    retry_after_terminal = False
    if scoped_idem_key:
        prior = get_idempotency_entry(session_id, scoped_idem_key)
        if prior is not None:
            prior_jid = str(prior.get("job_id") or "")
            live_status = ""
            if prior_jid.isdigit():
                with SessionLocal() as db:
                    row = DurableJobStore.get_sync(db, int(prior_jid))
                    if row is not None:
                        live_status = str(row.status)
            if live_status in ("pending", "queued", "running", "cancelling", "completed"):
                fresh_plan = get_plan(session_id, plan.plan_id)
                prior_exec_id = str(prior.get("execution_id") or "")
                artifact = None
                if (
                    live_status == "completed"
                    and fresh_plan is not None
                    and fresh_plan.latest_artifact is not None
                    and fresh_plan.latest_artifact.execution_id == prior_exec_id
                ):
                    artifact = fresh_plan.latest_artifact
                return {
                    "plan_id": plan.plan_id,
                    "job_id": prior_jid,
                    "execution_id": prior_exec_id,
                    "status": live_status,
                    "background": bool(background),
                    "idempotent_reuse": True,
                    "artifact": artifact,
                }
            retry_after_terminal = True

    if not plan.fit_metrics or plan.fit_metrics.status != "valid" or plan.fit_metrics.coefficients is None:
        raise GeorefDomainError(
            GeorefErrorCode.INSUFFICIENT_GCPS,
            "Plan does not have a valid 2D affine model; resolve blockers before execution.",
            status_code=422,
            details={"blockers": plan.fit_metrics.blockers if plan.fit_metrics else ["Missing fit metrics"]},
        )

    coeffs = plan.fit_metrics.coefficients
    grid = compute_output_grid(
        source, coeffs, plan.target_crs, plan.target_resolution_m
    )

    execution_id = f"gexec_{uuid.uuid4().hex[:14]}"
    fit_gcps = [g for g in plan.gcps if g.enabled and g.role == "fit"]
    check_gcps = [g for g in plan.gcps if g.enabled and g.role == "check"]
    snapshot = FrozenExecutionSnapshot(
        frozen_at=datetime.now(timezone.utc).isoformat(),
        revision=int(plan.revision),
        source_id=source.source_id,
        original_sha256=source.original_sha256,
        normalized_sha256=source.normalized_sha256,
        target_crs=plan.target_crs,
        resampling=plan.resampling,
        target_resolution_m=plan.target_resolution_m,
        generate_cog=plan.generate_cog,
        ignore_exif=plan.ignore_exif,
        affine=coeffs,
        fit_gcps=fit_gcps,
        check_gcps=check_gcps,
        out_width=grid.out_width,
        out_height=grid.out_height,
        dst_transform=list(grid.dst_transform)[:6],
        bounds_target_crs=grid.bounds_target_crs,
        footprint_wgs84=grid.footprint_wgs84,
        bounds_wgs84=grid.bounds_wgs84,
        estimated_output_pixels=grid.estimated_pixels,
        estimated_output_bytes=grid.estimated_bytes,
    )

    # Atomically check expected_revision, ensure no conflicting active job, and store frozen snapshot.
    def _freeze(cur: GeorefDraftPlan) -> GeorefDraftPlan:
        if cur.active_job_id:
            try:
                with SessionLocal() as db:
                    row = DurableJobStore.get_sync(db, int(cur.active_job_id))
                    if row is not None and JobStatus(row.status) in (
                        JobStatus.pending,
                        JobStatus.queued,
                        JobStatus.running,
                        JobStatus.cancelling,
                    ):
                        raise GeorefDomainError(
                            GeorefErrorCode.CAS_CONFLICT,
                            f"Active job {cur.active_job_id} is still running on plan '{cur.plan_id}'. Cancel it first or wait.",
                            status_code=409,
                        )
            except GeorefDomainError:
                raise
            except Exception:  # noqa: BLE001
                pass
        cur.frozen_snapshot = snapshot
        cur.active_execution_id = execution_id
        cur.updated_at = datetime.now(timezone.utc).isoformat()
        return cur

    frozen_plan = mutate_plan_cas(
        session_id,
        plan.plan_id,
        expected_revision=expected_revision,
        mutator=_freeze,
    )

    idem_db_key = (
        f"georef:{session_id}:{plan.plan_id}:{execution_id}"
        if not scoped_idem_key or retry_after_terminal
        else f"georef:{session_id}:{scoped_idem_key}"
    )

    job_id, job_status, reused = submit_async_job(
        task_type=TASK_TYPE,
        kind="georeference",
        display_name=f"影像配准 · {plan.name[:32]}",
        params={
            "plan_id": plan.plan_id,
            "execution_id": execution_id,
            "target_crs": snapshot.target_crs,
            "resampling": snapshot.resampling,
            "out_width": snapshot.out_width,
            "out_height": snapshot.out_height,
        },
        idempotency_key=idem_db_key,
        owner_id=owner_id,
        owner_token=owner_token,
        org_id=org_id,
        session_id=session_id,
        project_id=project_id,
    )

    def _attach_jid(cur: GeorefDraftPlan) -> GeorefDraftPlan:
        if cur.active_execution_id == execution_id:
            cur.active_job_id = str(job_id)
        return cur

    mutate_plan_cas(session_id, plan.plan_id, expected_revision=None, mutator=_attach_jid)

    if scoped_idem_key:
        record_idempotency_entry(
            session_id,
            scoped_idem_key,
            {"job_id": str(job_id), "execution_id": execution_id, "status": job_status},
        )

    async def _body(handle) -> Dict[str, Any]:
        def _is_cancelled() -> bool:
            if handle.token.cancelled or cancellation_registry.is_cancelled(job_id):
                return True
            try:
                with SessionLocal() as db:
                    if DurableJobStore.is_cancel_requested_sync(db, job_id):
                        handle.token.cancel()
                        return True
            except Exception:  # noqa: BLE001
                pass
            return False

        def _on_progress(pct: int, total: int, stage: str) -> None:
            _STAGE_BY_JOB[job_id] = stage
            try:
                handle.set_progress(min(99, max(0, int(pct))))
            except Exception:  # noqa: BLE001
                pass

        artifact_id = f"gart_{uuid.uuid4().hex[:14]}"
        artifacts_dir = georef_session_dir(session_id) / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        out_path = artifacts_dir / f"{artifact_id}.tif"

        try:
            warp_info = await asyncio.to_thread(
                run_direct_affine_warp,
                source=source,
                coeffs=snapshot.affine,
                target_crs=snapshot.target_crs,
                resampling=snapshot.resampling,
                target_resolution_m=snapshot.target_resolution_m,
                generate_cog=snapshot.generate_cog,
                out_path=out_path,
                check_cancelled=_is_cancelled,
                progress_cb=_on_progress,
            )
        except WarpCancelledError as exc:
            out_path.unlink(missing_ok=True)
            raise OperationCancelled(str(exc)) from exc
        except GeorefDomainError as exc:
            out_path.unlink(missing_ok=True)
            raise RuntimeError(f"{exc.code.value}: {exc.message}") from exc

        # Strict cancel fence BEFORE any external store / plan publication (R26).
        if _is_cancelled():
            out_path.unlink(missing_ok=True)
            raise OperationCancelled("Cancelled right before publication fence")

        out_sha256 = await asyncio.to_thread(compute_file_sha256, out_path)
        blob_key = await asyncio.to_thread(pin_blob_from_path, out_sha256, session_id, out_path)

        # Register into session_data_manager and artifact_registry atomically after fence.
        raster_ref = await _register_raster_artifact(
            session_id=session_id,
            plan_id=plan.plan_id,
            execution_id=execution_id,
            artifact_id=artifact_id,
            out_path=out_path,
            out_sha256=out_sha256,
            bounds_wgs84=snapshot.bounds_wgs84 or [],
            target_crs=snapshot.target_crs,
        )

        # Check one last time right before updating plan artifact state.
        if _is_cancelled():
            out_path.unlink(missing_ok=True)
            from app.services.artifact_registry import mark_status
            from app.services.session_data import session_data_manager

            await asyncio.to_thread(release_blob_ref, out_sha256, session_id)
            await session_data_manager.delete_ref(session_id, raster_ref)
            await mark_status(session_id, raster_ref, "superseded")
            raise OperationCancelled("Cancelled during registration fence")

        artifact = GeorefArtifactRecord(
            artifact_id=artifact_id,
            plan_id=plan.plan_id,
            execution_id=execution_id,
            job_id=str(job_id),
            session_id=session_id,
            format="GeoTIFF",
            cog_requested=warp_info["cog_requested"],
            cog_verified=warp_info["cog_verified"],
            cog_checks=warp_info["cog_checks"],
            target_crs=snapshot.target_crs,
            width=snapshot.out_width,
            height=snapshot.out_height,
            bands=int(warp_info["bands"]),
            dtype=str(warp_info["dtype"]),
            has_alpha=bool(warp_info["has_alpha"]),
            nodata_mode=str(warp_info["nodata_mode"]),
            sha256=out_sha256,
            byte_size=int(warp_info["byte_size"]),
            blob_key=blob_key,
            file_path=str(out_path),
            raster_ref=raster_ref,
            bounds_target_crs=snapshot.bounds_target_crs,
            bounds_wgs84=snapshot.bounds_wgs84 or [],
            footprint_wgs84=snapshot.footprint_wgs84 or {},
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        cleanup_old: list[GeorefArtifactRecord] = []

        def _publish_result(cur: GeorefDraftPlan) -> GeorefDraftPlan:
            old_art = cur.latest_artifact
            if old_art is not None and old_art.artifact_id != artifact.artifact_id:
                if old_art.mapspec_layer_id:
                    cur.superseded_artifacts.append(old_art)
                else:
                    cleanup_old.append(old_art)
            cur.latest_artifact = artifact
            if cur.active_job_id == str(job_id):
                cur.active_job_id = None
            cur.updated_at = datetime.now(timezone.utc).isoformat()
            return cur

        try:
            mutate_plan_cas(session_id, plan.plan_id, expected_revision=None, mutator=_publish_result)
        except Exception:
            # R 审计 round-2（P2-4b）：plan 在 warp 进行中被删除/替换时，
            # 发布期 CAS 失败 —— 此前 blob pin、session ref、artifact 行与
            # COG 文件已经落库/落盘，全部泄漏。回滚到与注册取消栅栏同一
            # 清理口径后再把失败上报给 job 记账。
            out_path.unlink(missing_ok=True)
            from app.services.artifact_registry import mark_status
            from app.services.session_data import session_data_manager

            await asyncio.to_thread(release_blob_ref, out_sha256, session_id)
            await session_data_manager.delete_ref(session_id, raster_ref)
            await mark_status(session_id, raster_ref, "superseded")
            raise
        for old_art in cleanup_old:
            await cleanup_artifact_resources(session_id, old_art)
        _STAGE_BY_JOB[job_id] = "completed"
        return {
            "artifact_id": artifact.artifact_id,
            "raster_ref": artifact.raster_ref,
            "sha256": artifact.sha256,
            "cog_verified": artifact.cog_verified,
            "width": artifact.width,
            "height": artifact.height,
        }

    captured_errs: list[BaseException] = []

    async def _run_guarded() -> JobStatus:
        try:
            return await run_async_job(
                job_id,
                _body,
                result_ref_builder=lambda s: str(s.get("raster_ref") or ""),
            )
        except Exception as exc:  # noqa: BLE001 — durable state & error_trace already persisted by run_async_job
            captured_errs.append(exc)
            return JobStatus.failed
        finally:
            def _cleanup_active(cur: GeorefDraftPlan) -> GeorefDraftPlan:
                if cur.active_job_id == str(job_id):
                    cur.active_job_id = None
                return cur

            try:
                mutate_plan_cas(session_id, plan.plan_id, expected_revision=None, mutator=_cleanup_active)
            except Exception:  # noqa: BLE001
                pass

    if background:
        task = asyncio.get_running_loop().create_task(_run_guarded(), name=f"georef:{job_id}")
        _RUNNERS[job_id] = task
        task.add_done_callback(lambda _t: _RUNNERS.pop(job_id, None))
        return {
            "plan_id": plan.plan_id,
            "job_id": str(job_id),
            "execution_id": execution_id,
            "status": "running",
            "background": True,
            "idempotent_reuse": reused,
            "artifact": None,
        }

    final_status = await _run_guarded()
    fresh = get_plan(session_id, plan.plan_id)
    if final_status == JobStatus.cancelled:
        raise GeorefDomainError(
            GeorefErrorCode.JOB_CANCELLED,
            f"Georeferencing job {job_id} was cancelled.",
            status_code=409,
        )
    if final_status != JobStatus.completed or fresh is None or fresh.latest_artifact is None:
        if captured_errs and isinstance(captured_errs[0], GeorefDomainError):
            raise captured_errs[0]
        with SessionLocal() as db:
            row = DurableJobStore.get_sync(db, job_id)
            err_msg = str(
                ((row.error_trace or row.progress_message) if row else None)
                or (str(captured_errs[0]) if captured_errs else None)
                or f"Job ended with status {final_status.value}"
            )
        is_tampered = (
            GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED.value in err_msg
            or "tamper" in err_msg.lower()
            or "digest" in err_msg.lower()
        )
        raise GeorefDomainError(
            GeorefErrorCode.SOURCE_CORRUPT_OR_TAMPERED
            if is_tampered
            else GeorefErrorCode.JOB_FAILED,
            err_msg,
            status_code=409 if is_tampered else 500,
        )
    return {
        "plan_id": plan.plan_id,
        "job_id": str(job_id),
        "execution_id": execution_id,
        "status": final_status.value,
        "background": False,
        "idempotent_reuse": reused,
        "artifact": fresh.latest_artifact,
    }


async def _register_raster_artifact(
    *,
    session_id: str,
    plan_id: str,
    execution_id: str,
    artifact_id: str,
    out_path: Path,
    out_sha256: str,
    bounds_wgs84: list[float],
    target_crs: str,
) -> str:
    """Register published GeoTIFF into session_data_manager and artifact_registry."""
    from app.services.artifact_registry import register_artifact
    from app.services.raster_tile_service import register_raster_ref
    from app.services.session_data import session_data_manager

    payload = {
        "kind": "numeric_raster",
        "file_path": str(out_path),
        "content_revision": out_sha256[:16],
        "georef_artifact_id": artifact_id,
        "georef_plan_id": plan_id,
        "bounds": bounds_wgs84,
        "crs": target_crs,
    }
    raster_ref = await session_data_manager.store(session_id, payload, prefix="raster_georef")
    register_raster_ref(session_id, raster_ref, str(out_path))
    await register_artifact(
        session_id=session_id,
        artifact_id=raster_ref,
        artifact_type="numeric_raster",
        producer_tool="webgis_georef_execute",
        producer_capability="georeferencing",
        descriptor={
            **({"bbox": bounds_wgs84} if len(bounds_wgs84) == 4 else {}),
            "crs": target_crs,
        },
        metadata={
            "storage_ref": str(out_path),
            "georef_artifact_id": artifact_id,
            "georef_plan_id": plan_id,
            "execution_id": execution_id,
            "sha256": out_sha256,
        },
    )
    return raster_ref


async def cancel_georeference_job(session_id: str, plan_id: str) -> Dict[str, Any]:
    """Request cooperative cancellation & record immediate cancel signal (R26)."""
    plan = get_plan(session_id, plan_id)
    if plan is None:
        raise GeorefDomainError(
            GeorefErrorCode.FORBIDDEN_OR_NOT_FOUND,
            f"Georeference plan '{plan_id}' not found.",
            status_code=404,
        )
    jid_str = plan.active_job_id or (plan.latest_artifact.job_id if plan.latest_artifact else None)
    if not jid_str:
        return {"plan": plan, "job_id": None, "status": "idle"}

    jid = int(jid_str)
    cancellation_registry.cancel(jid)
    with SessionLocal() as db:
        ok, new_status = DurableJobStore.request_cancel_sync(db, jid)
        db.commit()
    return {
        "plan": get_plan(session_id, plan_id) or plan,
        "job_id": jid_str,
        "status": new_status.value if ok else new_status.value,
    }
