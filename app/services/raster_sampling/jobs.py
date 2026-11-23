"""G39 durable 作业 — G23 runner 的采样域接线（WP09）。

模式与 ``data_operations/compute_jobs.py`` 一致：

* 幂等键 = (session, plan_id, params_digest) —— params_digest 覆盖站位
  几何、源内容代次、方法与算法版本；
* 收据短路优先于 job 化：既有 completed 收据（产物仍存活）直接返回；
* 并发收敛：同键活跃 job 存在 → 复用并等待终态（S23 双副本单发布）；
* 取消：DB 事实 → runner 看门狗点燃 token → 批间 checkpoint（S24；
  迟到 attempt 的结果由 store 的 attempt fencing 丢弃）；
* 恢复：job 行 / 收据 / 产物三者的因果证据齐备才算完成 —— status 端点
  对「completed 但无收据」如实标 recovery_required（S25）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.services.raster_sampling import service as domain_service
from app.services.raster_sampling import store
from app.services.raster_sampling.contracts import RasterSamplingError

logger = logging.getLogger(__name__)

TASK_TYPE = "raster_sampling_execute"
#: 等待「另一副本正在执行的同一计划」的收敛上限
JOIN_ACTIVE_JOB_TIMEOUT_S = 600.0


def build_idempotency_key(session_id: str, plan_id: str, digest: str) -> str:
    return f"{TASK_TYPE}:{session_id}:{plan_id}:{digest}"


async def _outputs_alive(receipt: Any) -> bool:
    from app.services.artifact_registry import fabric_parquet_ref_exists

    ref = receipt.outputs.get("sample_table_ref") or ""
    return bool(ref) and fabric_parquet_ref_exists(receipt.session_id, ref)


def _release_idempotency_key(job_id: int) -> None:
    """释放终态行的幂等键，让同键重提能建新行（与 store.create_sync 对
    不可复用终态的处理同款）。失败不阻断：重提仍会命中旧行，由调用方如实
    报告现状，绝不谎报 running。"""
    from app.services.data_operations.runner import _sync_session
    from app.models.db_model import AnalysisTask

    try:
        with _sync_session() as db:
            row = db.get(AnalysisTask, job_id)
            if row is not None and row.idempotency_key is not None:
                row.idempotency_key = None
                db.commit()
    except Exception:  # noqa: BLE001 — 释放失败由重提结果兜底
        logger.warning(
            "[raster-sampling] release idempotency key failed job_id=%s", job_id,
            exc_info=True)


async def submit_sampling_job(
    *,
    plan: Any,
    session_id: str,
    owner_token: Optional[str],
    expected_revision: Optional[int],
    background: bool,
    actor: Optional[Dict[str, Any]],
    project_id: str,
    trusted_dispatch: bool,
) -> Dict[str, Any]:
    from app.services.data_operations.runner import (
        run_async_job,
        submit_async_job,
    )

    if expected_revision is not None and int(expected_revision) != plan.revision:
        raise RasterSamplingError(
            "PLAN_REVISION_CONFLICT",
            f"plan {plan.plan_id} is at revision {plan.revision}, "
            f"expected {expected_revision}",
            field="expected_revision", status_code=409)
    # 0) 绑定现行性复核（短路/执行共用闸）：源内容/代次漂移 → typed 拒绝
    #    —— 历史收据仍可读（旧版事实），但「再执行」不允许静默算新数据
    #    或复用旧收据冒充当前内容（S20/S31）。
    from app.services.raster_authoring.source import resolve_source
    from app.schemas.raster_spec import RasterExpressionError
    from app.services.raster_sampling import freeze as rs_freeze

    for binding in plan.sources:
        try:
            current = await resolve_source(
                session_id, binding.ref_id, owner_token,
                trusted_dispatch=trusted_dispatch)
        except RasterSamplingError:
            raise
        except RasterExpressionError as exc:
            raise RasterSamplingError(
                "SOURCE_UNAVAILABLE", str(exc), field="ref_id",
                status_code=getattr(exc, "status_code", 404)) from exc
        rs_freeze.verify_binding_generation(binding, current)
    # 1) 收据短路（绑定现行 + 产物存活才认；GC → 允许重算发布新收据）
    existing = await store.find_receipt(session_id, plan.plan_id,
                                        plan.params_digest)
    if existing is not None and await _outputs_alive(existing):
        return {"receipt": existing, "idempotent_reuse": True}

    # 2) 幂等建 job
    key = build_idempotency_key(session_id, plan.plan_id, plan.params_digest)
    params = {
        "session_id": session_id,
        "plan_id": plan.plan_id,
        "params_digest": plan.params_digest,
        "plan_revision": plan.revision,
    }
    owner = actor or {}
    job_id, job_status, reused = submit_async_job(
        task_type=TASK_TYPE,
        kind="raster_sampling",
        display_name=f"栅格采样 · {plan.plan_id[:22]}",
        params=params,
        idempotency_key=key,
        owner_id=owner.get("user_id"),
        owner_token=owner.get("owner_token") or owner_token,
        org_id=owner.get("org_id"),
        session_id=session_id,
        project_id=project_id or None,
    )
    if reused and job_status in ("pending", "queued", "running", "cancelling"):
        join_status, receipt = await _join_active_job(job_id, session_id, plan)
        if join_status == "cancelled":
            return {"job_id": str(job_id), "status": "cancelled",
                    "plan_id": plan.plan_id, "idempotent_reuse": True}
        if receipt is not None:
            return {"receipt": receipt, "idempotent_reuse": True}
        job_id, job_status, reused = submit_async_job(
            task_type=TASK_TYPE,
            kind="raster_sampling",
            display_name=f"栅格采样 · {plan.plan_id[:22]}",
            params=params,
            idempotency_key=key,
            owner_id=owner.get("user_id"),
            owner_token=owner.get("owner_token") or owner_token,
            org_id=owner.get("org_id"),
            session_id=session_id,
            project_id=project_id or None,
        )
        if reused:
            return {"job_id": str(job_id), "status": job_status,
                    "plan_id": plan.plan_id, "idempotent_reuse": True}
    elif reused and job_status == "completed":
        # 收据短路未命中（产物已被 GC / 收据丢失）而幂等行仍是 TTL 内的
        # completed：这一行不可能再执行（runner._claim 终态拒领 →
        # AlreadyFinished），直接复用只会空转 —— background 吞掉异常后谎报
        # running，内联则把 AlreadyFinished 抛给调用方。按收据短路注释的
        # 「GC → 允许重算发布新收据」释放旧行幂等键后以同键重提，建新行真跑。
        _release_idempotency_key(job_id)
        job_id, job_status, reused = submit_async_job(
            task_type=TASK_TYPE,
            kind="raster_sampling",
            display_name=f"栅格采样 · {plan.plan_id[:22]}",
            params=params,
            idempotency_key=key,
            owner_id=owner.get("user_id"),
            owner_token=owner.get("owner_token") or owner_token,
            org_id=owner.get("org_id"),
            session_id=session_id,
            project_id=project_id or None,
        )
        if reused:
            # 极端竞态（重提瞬间又被并发占位）：如实报告现状，不谎报 running
            return {"job_id": str(job_id), "status": job_status,
                    "plan_id": plan.plan_id, "idempotent_reuse": True}

    async def _body(handle) -> Dict[str, Any]:
        def _progress(done: int, total: int = 0) -> None:
            try:
                denom = max(1, int(total or done or 1))
                handle.set_progress(min(99, int(done * 100 / denom)))
            except Exception:  # noqa: BLE001
                pass

        out = await domain_service.run_plan_execution(
            plan=plan, session_id=session_id, owner_token=owner_token,
            trusted_dispatch=trusted_dispatch, progress_hook=_progress)
        # 收据里的 job_id 在终态落定前补写（job 行 result_summary 由 runner
        # 承载；收据作为独立事实源也记录归属 job）
        receipt = await store.get_receipt(session_id, out["execution_id"])
        if receipt is not None and receipt.job_id is None:
            receipt.job_id = str(job_id)
            await store.append_receipt(receipt, published=True)
        return out["summary"]

    if background:
        runner_task = asyncio.get_running_loop().create_task(
            _run_quietly(job_id, plan, session_id, owner_token,
                         trusted_dispatch, _body),
            name=f"raster-sampling:{job_id}",
        )
        _RUNNERS[job_id] = runner_task
        runner_task.add_done_callback(lambda t: _RUNNERS.pop(job_id, None))
        return {"job_id": str(job_id), "status": "running",
                "plan_id": plan.plan_id, "background": True,
                "idempotent_reuse": reused,
                "poll": f"/api/v1/tasks/jobs/{job_id}"}

    status = await run_async_job(job_id, _body)
    if status.value == "completed":
        receipt = await store.find_receipt(session_id, plan.plan_id,
                                           plan.params_digest)
        if receipt is not None:
            return {"receipt": receipt, "job_id": str(job_id),
                    "idempotent_reuse": reused}
    return {"job_id": str(job_id), "status": status.value,
            "plan_id": plan.plan_id, "idempotent_reuse": reused}


_RUNNERS: Dict[int, asyncio.Task] = {}


async def _run_quietly(job_id, plan, session_id, owner_token,  # noqa: ANN001
                       trusted_dispatch, body) -> None:
    """后台模式：异常只记日志（终态由 run_async_job 落 DB）。"""
    from app.services.data_operations.runner import run_async_job

    try:
        await run_async_job(job_id, body)
    except Exception:  # noqa: BLE001
        logger.warning("[raster-sampling] background job %s crashed", job_id,
                       exc_info=True)


async def _join_active_job(job_id: int, session_id: str, plan: Any):
    """等待另一副本的活跃 job 终态（S23 收敛）。"""
    from app.services.data_operations.runner import _sync_session
    from app.models.db_model import AnalysisTask

    loop = asyncio.get_event_loop()
    deadline = loop.time() + JOIN_ACTIVE_JOB_TIMEOUT_S
    while loop.time() < deadline:
        await asyncio.sleep(0.5)
        with _sync_session() as db:
            row = db.get(AnalysisTask, job_id)
            status = str(row.status) if row is not None else "unknown"
            db.commit()
        if status == "completed":
            receipt = await store.find_receipt(
                session_id, plan.plan_id, plan.params_digest)
            return ("completed", receipt)
        if status in ("cancelled", "failed", "stale"):
            return (status, None)
    return ("timeout", None)
