"""G35 标准服务：UI 与 Pi 工具共用同一条链（能力→计划→提交→查询→成果）。

回执一律有界：只返回摘要与授权 ref，不把媒体字节/帧数组/全量要素塞进
LLM 上下文。``queued/pending/tool-returned`` 不等于完成；只有状态投影为
``completed`` 且 ``verification.passed`` 才登记成果与下载。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, Optional

from .capabilities import probe_capabilities
from .contracts import (
    CapabilitiesArgs, JobRefArgs, MediaPlanArgs, ResultArgs, SubmitArgs,
    TemporalMediaError,
)
from .freeze import freeze_document
from .planning import assert_plan_hash, build_plan
from .store import (
    initial_state, job_dir, load_plan, load_state, owner_key, request_cancel, resolve_preview, save_plan, save_preview,
    save_state, update_state,
)

_TASK_TYPE = "temporal_media"


def _principal(user_id: Optional[str], session_id: str,
               owner_token: Optional[str] = None) -> str:
    """UI 与 Pi 使用**同一**会话作用域 principal（保证同一份计划/成果/授权）。

    与 G26 工具导出的 ``session:<sid>`` 口径一致；下载路由另以 ``.owner``
    侧车 + 签名 URL 做 fail-closed 鉴权，会话归属由路由的
    ``verify_session_owner`` 证明。用户 id 不进入 principal，避免 UI 与 Pi
    在同一会话产生两份不同计划。
    """
    return f"session:{session_id}"


def _bounded_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """用户/LLM 看到的计划：完整帧集与时长映射，但不带冻结文档大对象。"""
    return {
        "schema_version": plan["schema_version"],
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "plan_ref": str(plan["plan_hash"])[:16],
        "owner_key": owner_key(plan["owner"]),
        "mapspec_revision": plan["mapspec_revision"],
        "expected_revision": plan["expected_revision"],
        "temporal_contract": plan["temporal_contract"],
        "observations": plan["observations"],
        "missing_periods": plan["gaps"],
        "selection": plan["selection"],
        "display": {
            "segments": [
                {k: s[k] for k in (
                    "index", "kind", "observation_id", "observation_label",
                    "observation_start", "observation_end", "time_label",
                    "identity", "missing_period", "held_from", "duration_ms",
                    "encoded_repeat", "encoded_durations_ms")}
                for s in plan["display"]["segments"]],
            "timing": plan["display"]["timing"],
            "order": plan["display"]["order"],
            "direction": plan["display"]["direction"],
            "missing_display": plan["display"]["missing_display"],
            "time_display": plan["display"]["time_display"],
            "encoded_frame_count": plan["display"]["encoded_frame_count"],
            "observation_count": plan["display"]["observation_count"],
            "missing_period_count": plan["display"]["missing_period_count"],
            "missing_period_labels": plan["display"]["missing_period_labels"],
        },
        "canvas": plan["canvas"],
        "format": plan["format"],
        "loop": plan["loop"],
        "partial": plan["partial"],
        "frozen_fingerprints": plan["frozen_fingerprints"],
        "disclosures": plan["disclosures"],
        "resource_estimate": plan["resource_estimate"],
    }


async def capabilities(args: CapabilitiesArgs, principal: str) -> Dict[str, Any]:
    """能力查询：实际 encoder/版本/限制/依赖缺失；绑定状态如实披露。"""
    caps = probe_capabilities()
    binding: Dict[str, Any] = {"available": False}
    try:
        frozen = await freeze_document(args.session_id, args.expected_revision)
        from .capabilities import layer_support
        binding = {
            "available": True,
            "mapspec_revision": frozen["mapspec_revision"],
            "temporal_fingerprint": frozen["temporal_fingerprint"],
            "observations": len(frozen["temporal"].get("frames") or []),
            "layer_support": layer_support(frozen["document"], frozen["temporal"]),
            "render_path": frozen["temporal"].get("render_path"),
            "representation": frozen["temporal"].get("time_representation"),
            "timezone": frozen["temporal"].get("timezone"),
            "granularity": frozen["temporal"].get("granularity"),
        }
    except TemporalMediaError as exc:
        binding = {"available": False, "code": exc.code, "message": exc.message}
    return {"success": True, "schema_version": "temporal_media_capabilities.v1",
            "capabilities": caps, "binding": binding,
            "owner_key": owner_key(principal)}


async def preview(args: MediaPlanArgs, principal: str) -> Dict[str, Any]:
    """预览 = 计划本身（与提交采用同一份构建函数 + 同一 hash）。"""
    frozen = await freeze_document(args.session_id, args.expected_revision)
    caps = probe_capabilities()
    plan = build_plan(frozen, args, principal, caps)
    plan_ref = save_preview(principal, plan)
    # 勾选面板必须始终看到**全部**真实观测；plan.observations 是本次选中的
    # 子集，不能用它渲染 checkbox（否则取消勾选后无法再选回）。
    all_observations = [{
        "id": str(f["id"]),
        "label": str(f.get("label") or f["id"]),
        "start": str(f["start"]),
        "end": str(f["end"]),
        "feature_count": f.get("feature_count"),
    } for f in ((frozen.get("temporal") or {}).get("frames") or [])]
    all_observations.sort(key=lambda o: o["start"])
    return {
        "success": True,
        "status": "preview",
        "schema_version": plan["schema_version"],
        # 16 位短句柄不受生产 dispatch 的长字符串上下文截断影响。
        "plan_ref": plan_ref,
        "plan_hash": plan["plan_hash"],
        "plan": _bounded_plan(plan),
        "all_observations": all_observations,
        "capabilities": caps,
        "message": "预览不是渲染结果；提交后用 job_id 查询真实进度与成果",
    }


def _job_id_for(principal: str, plan_hash: str, attempt: int = 0) -> str:
    seed = f"{owner_key(principal)}|{plan_hash}|{attempt}"
    return "g35-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


async def submit(args: SubmitArgs, principal: str) -> Dict[str, Any]:
    """提交：未知/漂移 plan_hash 拒绝；返回 **pending**，不冒充完成。"""
    if args.plan_ref:
        expected_hash = str(resolve_preview(principal, args.plan_ref)["plan_hash"])
    elif args.plan_hash:
        expected_hash = str(args.plan_hash)
    else:
        raise TemporalMediaError(
            "PLAN_VERSION_MISMATCH",
            "submit requires plan_ref (from preview) or the full plan_hash",
            status_code=409)
    frozen = await freeze_document(args.session_id, args.expected_revision)
    caps = probe_capabilities()
    plan = build_plan(frozen, args, principal, caps)
    assert_plan_hash(plan, expected_hash)

    attempt = 0
    while True:
        job_id = _job_id_for(principal, plan["plan_hash"], attempt)
        state_path = job_dir(principal, job_id) / "state.json"
        if not state_path.is_file():
            break
        existing = load_state(principal, job_id)
        existing_status = str(existing.get("status") or "")
        # 派发失败可能留下 durable_job_id 为空的孤儿 pending；先标 failed，
        # 让本轮 attempt+1 重新派发，而不是永久卡住。
        if existing_status == "pending" and not existing.get("durable_job_id"):
            update_state(principal, job_id, lambda s: s.update({
                "status": "failed", "phase": "failed", "progress": None,
                "message": "上次派发未登记 durable job，已释放本计划版本",
                "error": {"code": "DURABLE_DISPATCH_FAILED",
                          "message": "orphan pending released"},
            }))
            attempt += 1
            continue
        # 同一计划的重复提交（双击 / SSE 重连 / Pi 重试）必须复用同一逻辑
        # 任务；durable job 只入队一次，不能留下永不执行的第二个 pending 域任务。
        if existing_status in ("pending", "running", "completed"):
            return {
                "success": True,
                "status": existing_status,
                "job_id": job_id,
                "durable_job_id": existing.get("durable_job_id"),
                "plan_hash": plan["plan_hash"],
                "plan_ref": str(plan["plan_hash"])[:16],
                "idempotent_reuse": True,
                "progress": existing.get("progress"),
                "artifacts": existing.get("artifacts") or None,
                "verification": existing.get("verification") or None,
                "message": "同一冻结计划已有任务，复用而不是重复入队",
            }
        attempt += 1
        if attempt > 8:
            raise TemporalMediaError("TOO_MANY_ATTEMPTS",
                                     "too many historical attempts for this plan",
                                     status_code=409)

    save_plan(principal, job_id, plan)
    save_state(principal, job_id, initial_state(principal, job_id, plan))

    from app.services.jobs.submit import submit_durable_job
    from app.services.jobs.lifecycle import JobKind
    from .tasks import run_temporal_media_job

    try:
        dispatched = submit_durable_job(
            celery_task=run_temporal_media_job,
            task_type=_TASK_TYPE,
            display_name="时序地图动画媒体交付",
            # 幂等键含域 job_id：失败/取消后的新 attempt 不会被 TTL 内的
            # 旧 durable job 误判为「已入队」而留下永不执行的域任务。
            params={"plan_hash": plan["plan_hash"],
                    "session_id": args.session_id,
                    "owner_key": owner_key(principal),
                    "media_job_id": job_id},
            task_kwargs={"media_job_id": job_id, "principal": principal},
            kind=JobKind.temporal_media,
            session_id=args.session_id,
            idempotent=True,
            max_retries=2,
        )
    except Exception as exc:  # noqa: BLE001 — 入队失败不得留下永久 pending 孤儿
        error_type = type(exc).__name__
        update_state(principal, job_id, lambda s: s.update({
            "status": "failed", "phase": "failed", "progress": None,
            "message": "durable job dispatch failed; retry is allowed",
            "error": {"code": "DURABLE_DISPATCH_FAILED", "message": error_type},
        }))
        raise TemporalMediaError(
            "DURABLE_DISPATCH_FAILED",
            "background job could not be enqueued; the media task was marked "
            "failed (not left pending) and can be retried",
            status_code=503, details={"cause": error_type}) from exc
    durable_id = str(dispatched.get("job_id") or "")
    update_state(principal, job_id, lambda s: s.update({
        "durable_job_id": durable_id or None,
        "phase": "queued",
        "message": "媒体任务已提交给 durable worker（pending，不代表完成）",
    }))
    return {
        "success": True,
        "status": "pending",
        "job_id": job_id,
        "durable_job_id": durable_id,
        "plan_hash": plan["plan_hash"],
        "plan_ref": str(plan["plan_hash"])[:16],
        "idempotent_reuse": bool(dispatched.get("idempotent_reuse")),
        "progress": None,
        "poll_hint": "webgis_temporal_media_status / GET /api/v1/temporal-media/jobs/{job_id}",
        "message": "计划已冻结并提交后台任务；渲染/编码/核验完成前不会返回成果链接",
    }


def _refresh_url(filename: str) -> str:
    from app.core.signing import sign_path
    path = f"/api/v1/export/download/{filename}"
    exp, sig = sign_path(path, ttl_seconds=3600)
    return f"{path}?exp={exp}&sig={sig}"


def _durable_status(durable_job_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not durable_job_id:
        return None
    try:
        from app.services.jobs.store import DurableJobStore
        from app.tools._utils import db_session
        with db_session() as db:
            record = DurableJobStore.get_sync(db, durable_job_id)
        if record is None:
            return None
        return {"status": str(getattr(record, "status", "") or ""),
                "attempt": int(getattr(record, "attempt", 0) or 0),
                "progress": getattr(record, "progress", None),
                "message": getattr(record, "message", None)}
    except Exception:  # noqa: BLE001 — 平台投影失败不影响本领域状态
        return None


async def status(args: JobRefArgs, principal: str) -> Dict[str, Any]:
    state = load_state(principal, args.job_id)
    artifacts = dict(state.get("artifacts") or {})
    if state.get("status") == "completed" and artifacts:
        for item in artifacts.values():
            if isinstance(item, dict) and item.get("filename"):
                item["url"] = _refresh_url(str(item["filename"]))
    return {
        "success": True,
        "status": state.get("status"),
        "phase": state.get("phase"),
        "job_id": args.job_id,
        "plan_hash": state.get("plan_hash"),
        "progress": state.get("progress"),
        "message": state.get("message"),
        "rendered_frames": state.get("rendered_frames"),
        "cache_hits": state.get("cache_hits"),
        "encoded_frames": state.get("encoded_frames"),
        "total_frames": state.get("total_frames"),
        "artifacts": artifacts or None,
        "verification": state.get("verification"),
        "error": state.get("error"),
        "durable_job": _durable_status(state.get("durable_job_id")),
        "completed": state.get("status") == "completed"
                     and bool((state.get("verification") or {}).get("passed")),
    }


async def cancel(args: JobRefArgs, principal: str) -> Dict[str, Any]:
    state = load_state(principal, args.job_id)
    if state.get("status") == "completed":
        return {"success": True, "status": "completed", "job_id": args.job_id,
                "message": "成果已核验发布，无法取消"}
    durable_id = state.get("durable_job_id")
    if durable_id:
        try:
            from app.services.jobs.store import DurableJobStore
            from app.tools._utils import db_session
            from app.services.jobs.cancellation import registry as cancel_registry
            with db_session() as db:
                DurableJobStore.request_cancel_sync(db, durable_id)
            cancel_registry.cancel(durable_id)
        except Exception:  # noqa: BLE001 — 平台取消失败仍保留本领域取消终态
            pass
    updated = request_cancel(principal, args.job_id)
    return {"success": True, "status": updated.get("status"),
            "job_id": args.job_id, "phase": updated.get("phase"),
            "message": "取消已登记：后续帧/编码停止，迟到成功不得覆盖 cancelled"}


async def result(args: ResultArgs, principal: str) -> Dict[str, Any]:
    state = load_state(principal, args.job_id)
    artifacts = dict(state.get("artifacts") or {})
    verification = state.get("verification") or {}
    if state.get("status") != "completed" or not artifacts or not verification.get("passed"):
        raise TemporalMediaError(
            "RESULT_NOT_READY",
            f"media result is not ready (status={state.get('status')}, "
            f"phase={state.get('phase')}); pending/tool-returned is not completion",
            status_code=409,
            details={"status": state.get("status"), "phase": state.get("phase")})
    if args.refresh_urls:
        for item in artifacts.values():
            if isinstance(item, dict) and item.get("filename"):
                item["url"] = _refresh_url(str(item["filename"]))
    plan = load_plan(principal, args.job_id)
    return {
        "success": True,
        "status": "completed",
        "job_id": args.job_id,
        "plan_hash": state.get("plan_hash"),
        "format": plan.get("format"),
        "artifacts": artifacts,
        "verification": verification,
        "manifest_summary": {
            "observation_count": len(plan.get("observations") or []),
            "missing_periods": len(plan.get("gaps") or []),
            "segments": len(plan["display"]["segments"]),
            "total_display_ms": plan["display"]["timing"]["total_display_ms"],
            "canvas": plan["canvas"],
            "omitted_layers": plan["partial"]["omitted_layers"],
            "disclosures": plan["disclosures"],
        },
    }


__all__ = ["capabilities", "preview", "submit", "status", "cancel", "result",
           "_principal", "_bounded_plan"]
