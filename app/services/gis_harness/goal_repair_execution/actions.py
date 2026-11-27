"""G42 WP05/WP06 —— 修复动作执行体（复用既有服务；零旁路）。

纪律：

- 写副作用动作一律经 ``ToolDispatchService``（同 owner/scope/闸）；只读
  状态查询可直接调服务（与 skill runner 同族）；
- ``JobOrigin`` 上下文携带 owner 身份（submit_data_delivery 等工具的
  actor 解析依赖它）；绝不信任客户端自报参数 —— 全部参数由服务端从
  绑定矩阵（bindings，源自权威状态）派生；
- 导出保持 blocking 语义（不自动 allow_partial）；交付损失确认缺席 =
  typed 阻断（零提交）；runtime_repair 预算内部自理（不双扣）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .contracts import (
    ACTION_BLOCKED,
    ACTION_FAILED,
    ACTION_NEEDS_ACK,
    ACTION_NEEDS_USER,
    ACTION_SUCCEEDED,
    ACTION_WAITING_DELIVERY,
    ACTION_WAITING_OBSERVATION,
    bounded_action_receipt,
)
from .bindings import (
    delivery_ack_codes,
    delivery_blocking_issues,
)

logger = logging.getLogger(__name__)

#: reobserve 有界等待（浏览器离线/无新观察 → waiting_observation 披露）。
REOBSERVE_WAIT_S = 45.0
REOBSERVE_POLL_INTERVAL_S = 1.5
#: 交付 job 有界等待（超时 → waiting_delivery + 读路径惰性对账）。
DELIVERY_WAIT_S = 120.0
DELIVERY_POLL_INTERVAL_S = 2.0


@dataclass
class RepairOwnerContext:
    """attempt 的归属/授权上下文（服务端装配；客户端不可自报）。"""

    session_id: str
    user_id: Optional[str] = None
    org_id: Optional[int] = None
    project_id: Optional[str] = None
    owner_token: Optional[str] = None
    turn_id: str = ""
    tool_call_id: str = ""


def _origin(ctx: RepairOwnerContext):
    from app.services.jobs.context import JobOrigin

    return JobOrigin(
        session_id=ctx.session_id or None,
        owner_id=ctx.user_id or None,
        owner_token=ctx.owner_token or None,
        org_id=ctx.org_id,
        project_id=ctx.project_id or None,
        run_id="goal_repair",
        turn_id=ctx.turn_id or None,
        tool_call_id=ctx.tool_call_id or None,
        tool_name="goal_repair_execution",
    )


async def _dispatch_tool(
    ctx: RepairOwnerContext, tool_name: str, args: Dict[str, Any],
    *, call_id: str,
) -> Dict[str, Any]:
    """统一派发（同 wave gate/权限闸；JobOrigin 绑定 actor 身份）。"""
    from app.agent_pi_bridge import _get_shared_dispatch_service
    from app.services.jobs.context import use_origin

    tc = {"id": call_id[:64],
          "function": {"name": tool_name, "arguments": args}}
    service = _get_shared_dispatch_service()
    executed: set = set()
    with use_origin(_origin(ctx)):
        result = await service.dispatch(tc, ctx.session_id, executed)
    raw = getattr(result, "raw_result", None)
    if isinstance(raw, dict):
        return raw
    llm = getattr(result, "llm_payload", None)
    if isinstance(llm, dict):
        return llm
    status = str(getattr(result, "status", "") or "")
    return {"success": status in ("ok", "repeated"), "status": status,
            "message": f"dispatch status={status}"}


def derive_delivery_spec(
    session_id: str, fmt: str, source_ref: str, *, filename_hint: str = "",
) -> Any:
    """服务端派生 DeliverySpec（source=session_ref；format=需求格式）。"""
    from app.schemas.data_delivery_schema import DeliverySpec

    hint = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                   for ch in (filename_hint or "delivery")[:60]).strip("_")
    return DeliverySpec(
        source={"kind": "session_ref", "session_id": session_id[:128],
                "ref": source_ref[:256]},
        format=fmt[:16],
        filename=hint[:100] or "delivery",
    )


def _delivery_actor(ctx: RepairOwnerContext):
    from app.services.data_delivery.source_resolver import DeliveryActor

    return DeliveryActor(
        user_id=ctx.user_id, org_id=ctx.org_id,
        owner_token=ctx.owner_token)


async def preview_delivery_for_binding(
    ctx: RepairOwnerContext, fmt: str, source_ref: str, *,
    filename_hint: str = "",
) -> Tuple[Optional[Any], Optional[str]]:
    """G18 只读预览（披露损失确认义务/阻断项）；异常 → (None, reason)。"""
    from app.services.data_delivery import preview_delivery

    try:
        spec = derive_delivery_spec(ctx.session_id, fmt, source_ref,
                                    filename_hint=filename_hint)
        preview = await preview_delivery(spec, _delivery_actor(ctx))
        return preview, None
    except Exception as exc:  # noqa: BLE001 — 预览失败 = 诚实披露原因
        return None, str(getattr(exc, "reason", None) or exc)[:200]


async def execute_map_export(
    ctx: RepairOwnerContext, binding: Dict[str, Any], *, attempt_id: str,
) -> Dict[str, Any]:
    """真实地图导出（webgis_map_export；blocking 保留 + CAS 修订）。"""
    params = binding.get("params") or {}
    fmt = str(params.get("format") or "")[:8]
    revision = int(params.get("expected_revision") or 0)
    raw = await _dispatch_tool(
        ctx, "webgis_map_export",
        {"format": fmt, "expected_revision": revision or None,
         "partial_policy": "blocking"},
        call_id=f"goalrepair-{attempt_id}-{binding.get('action_id')}-export")
    if raw.get("success"):
        return bounded_action_receipt(
            action_id=str(binding.get("action_id") or ""),
            action=str(binding.get("action") or ""),
            requirement_id=str(binding.get("requirement_id") or ""),
            status=ACTION_SUCCEEDED,
            detail=(f"map export {fmt} revision="
                    f"{raw.get('mapspec_revision')} bytes={raw.get('bytes')}"),
            evidence_ref=str(raw.get("filename") or "")[:128],
            download_url=str(raw.get("download_url") or "")[:256],
        )
    return bounded_action_receipt(
        action_id=str(binding.get("action_id") or ""),
        action=str(binding.get("action") or ""),
        requirement_id=str(binding.get("requirement_id") or ""),
        status=ACTION_FAILED,
        detail=str(raw.get("message") or raw.get("error") or "")[:200],
        error_code=str(raw.get("code") or "EXPORT_FAILED")[:40],
    )


async def execute_delivery(
    ctx: RepairOwnerContext, binding: Dict[str, Any], *,
    attempt_id: str, acked_losses: List[str],
    filename_hint: str = "",
    check_cancel: Optional[Any] = None,
) -> Dict[str, Any]:
    """数据成果交付（预览复检 → 损失确认闸 → submit → 有界等待 job）。

    ``check_cancel``：协作取消检查点（runner 注入 ``handle.checkpoint``；
    轮询等待期间及时响应两阶段取消）。
    """
    params = binding.get("params") or {}
    fmt = str(params.get("format") or "")[:16]
    source_ref = str(params.get("source_ref") or "")[:256]
    rid = str(binding.get("requirement_id") or "")
    aid = str(binding.get("action_id") or "")
    action = str(binding.get("action") or "")

    def _receipt(status: str, *, detail: str = "", error_code: str = "",
                 job_id: str = "", download_url: str = "",
                 evidence_ref: str = "") -> Dict[str, Any]:
        return bounded_action_receipt(
            action_id=aid, action=action, requirement_id=rid, status=status,
            detail=detail, evidence_ref=evidence_ref,
            download_url=download_url, job_id=job_id, error_code=error_code)

    # 动作时预览复检（授权/快照/损失词表以当前真实状态为准）。
    preview, preview_err = await preview_delivery_for_binding(
        ctx, fmt, source_ref, filename_hint=filename_hint)
    if preview is None:
        return _receipt(ACTION_BLOCKED, detail=f"delivery preview failed: "
                                              f"{preview_err}",
                        error_code="DELIVERY_PREVIEW_FAILED")
    blocking = delivery_blocking_issues(preview)
    if blocking:
        return _receipt(ACTION_BLOCKED,
                        detail="; ".join(blocking)[:200],
                        error_code="DELIVERY_BLOCKED")
    required = delivery_ack_codes(preview)
    missing = [c for c in required if c not in set(acked_losses)]
    if missing:
        # 复用既有确认义务：未确认 = 零提交（R09）。
        return _receipt(ACTION_NEEDS_ACK,
                        detail=f"格式损失确认缺席: {missing}",
                        error_code="LOSS_ACK_REQUIRED")


    spec = derive_delivery_spec(ctx.session_id, fmt, source_ref,
                                filename_hint=filename_hint)
    raw = await _dispatch_tool(
        ctx, "submit_data_delivery",
        {"spec": spec.model_dump(), "acknowledged_losses": required,
         **({"project_id": ctx.project_id} if ctx.project_id else {})},
        call_id=f"goalrepair-{attempt_id}-{aid}-delivery")
    if str(raw.get("status")) == "error" or not raw.get("job_id"):
        return _receipt(ACTION_FAILED,
                        detail=str(raw.get("error") or raw.get("message")
                                   or "submit failed")[:200],
                        error_code=str(raw.get("error_type")
                                       or "DELIVERY_SUBMIT_FAILED")[:40])
    job_id = str(raw.get("job_id"))[:48]
    return await _wait_delivery_job(ctx, job_id, rid=rid, aid=aid,
                                    action=action, check_cancel=check_cancel)


async def _wait_delivery_job(
    ctx: RepairOwnerContext, job_id: str, *, rid: str, aid: str,
    action: str, check_cancel: Optional[Any] = None,
) -> Dict[str, Any]:
    """有界等待交付 job（读路径只读轮询；超时 → waiting_delivery 披露）。"""
    from app.services.data_delivery import get_delivery_job
    from app.services.jobs.lifecycle import JobStatus

    deadline = asyncio.get_event_loop().time() + DELIVERY_WAIT_S
    last_status = ""
    while True:
        if check_cancel is not None:
            check_cancel()  # 协作取消（步内及时响应，不留悬挂等待）
        try:
            view = await get_delivery_job(job_id, _delivery_actor(ctx))
            last_status = str(getattr(view, "status", "") or "")[:16]
        except Exception as exc:  # noqa: BLE001 — 瞬时读失败重试至 deadline
            if asyncio.get_event_loop().time() >= deadline:
                return bounded_action_receipt(
                    action_id=aid, action=action, requirement_id=rid,
                    status=ACTION_FAILED, job_id=job_id,
                    detail=f"delivery job read failed: {exc}"[:200],
                    error_code="DELIVERY_JOB_READ_FAILED")
            await asyncio.sleep(DELIVERY_POLL_INTERVAL_S)
            continue
        if last_status == JobStatus.completed.value:
            receipt = getattr(view, "receipt", None)
            rows = getattr(receipt, "row_count", None)
            sha = str(getattr(receipt, "content_sha256", "") or "")[:16]
            return bounded_action_receipt(
                action_id=aid, action=action, requirement_id=rid,
                status=ACTION_SUCCEEDED, job_id=job_id,
                detail=f"delivery completed rows={rows} sha256={sha}",
                evidence_ref=f"delivery:{job_id}")
        if last_status in (JobStatus.failed.value, JobStatus.cancelled.value):
            return bounded_action_receipt(
                action_id=aid, action=action, requirement_id=rid,
                status=ACTION_FAILED, job_id=job_id,
                detail=f"delivery job {last_status}",
                error_code=f"DELIVERY_{last_status.upper()}")
        if asyncio.get_event_loop().time() >= deadline:
            # 已提交的合法副作用照常存在 —— 如实披露等待态（不谎报成功）。
            return bounded_action_receipt(
                action_id=aid, action=action, requirement_id=rid,
                status=ACTION_WAITING_DELIVERY, job_id=job_id,
                detail=f"delivery job still {last_status}; "
                       f"receipt enters next legal finalization")
        await asyncio.sleep(DELIVERY_POLL_INTERVAL_S)


async def execute_runtime_repair(
    ctx: RepairOwnerContext, binding: Dict[str, Any], *,
    chapter: Dict[str, Any],
) -> Dict[str, Any]:
    """复用既有 runtime_repair（预算/CAS/用户锁内部自理，不双扣）。"""
    from app.services.gis_harness.map_completion import (
        current_mapspec_for_disclosure,
    )
    from app.services.gis_harness.render_observation import (
        load_render_observation,
    )
    from app.services.gis_harness.runtime_repair import run_runtime_repair
    from app.services.session_data import session_data_manager

    def _receipt(status: str, *, detail: str, error_code: str = "",
                 evidence_ref: str = "") -> Dict[str, Any]:
        return bounded_action_receipt(
            action_id=str(binding.get("action_id") or ""),
            action=str(binding.get("action") or ""),
            requirement_id=str(binding.get("requirement_id") or ""),
            status=status, detail=detail, evidence_ref=evidence_ref,
            error_code=error_code)

    map_state = await session_data_manager.get_map_state(ctx.session_id)
    mapspec, revision = await current_mapspec_for_disclosure(ctx.session_id)
    observation = await load_render_observation(ctx.session_id, map_state)
    outcome = await run_runtime_repair(
        ctx.session_id, chapter=chapter, mapspec=mapspec or {},
        observation=observation if isinstance(observation, dict) else None,
        current_revision=int(revision or 0), map_state=map_state)
    applied = list(getattr(outcome, "applied", None) or [])
    if getattr(outcome, "exhausted", False):
        reason = str(getattr(outcome, "token_denial_reason", "")
                     or "repair budget exhausted")[:120]
        return _receipt(ACTION_BLOCKED, detail=reason,
                        error_code="REPAIR_BUDGET_EXHAUSTED")
    if not applied:
        return _receipt(
            ACTION_NEEDS_USER,
            detail="runtime repair found no safe auto-applicable divergence")
    return _receipt(
        ACTION_SUCCEEDED, detail=f"applied: {', '.join(applied[:6])}",
        evidence_ref=f"runtime_repair:{','.join(applied[:3])}"[:128])


async def wait_fresh_observation(
    ctx: RepairOwnerContext, binding: Dict[str, Any], *,
    start_seq: Any, revision: int,
    check_cancel: Optional[Any] = None,
) -> Dict[str, Any]:
    """等待当前修订的真实浏览器新观察（不伪造 verified；有界等待）。"""
    from app.services.gis_harness.render_observation import (
        load_render_observation,
        observation_sequence,
    )
    from app.services.session_data import session_data_manager

    def _receipt(status: str, *, detail: str, error_code: str = "",
                 evidence_ref: str = "") -> Dict[str, Any]:
        return bounded_action_receipt(
            action_id=str(binding.get("action_id") or ""),
            action=str(binding.get("action") or ""),
            requirement_id=str(binding.get("requirement_id") or ""),
            status=status, detail=detail, evidence_ref=evidence_ref,
            error_code=error_code)

    deadline = asyncio.get_event_loop().time() + REOBSERVE_WAIT_S
    while True:
        if check_cancel is not None:
            check_cancel()  # 协作取消（等待期及时响应）
        map_state = await session_data_manager.get_map_state(ctx.session_id)
        obs = await load_render_observation(ctx.session_id, map_state)
        seq = observation_sequence(obs)
        obs_rev = 0
        if isinstance(obs, dict):
            try:
                obs_rev = int(obs.get("mapspec_revision") or 0)
            except (TypeError, ValueError):
                obs_rev = 0
        fresh = (seq != start_seq) and (not revision or obs_rev == revision)
        if fresh:
            return _receipt(
                ACTION_SUCCEEDED,
                detail=f"fresh render observation seq={seq} rev={obs_rev}",
                evidence_ref=f"observation:{seq}@rev{obs_rev}")
        if asyncio.get_event_loop().time() >= deadline:
            return _receipt(
                ACTION_WAITING_OBSERVATION,
                detail=f"no fresh observation within {REOBSERVE_WAIT_S:.0f}s "
                       f"(seq={seq} rev={obs_rev}; browser offline or "
                       f"renderer idle)",
                error_code="OBSERVATION_TIMEOUT")
        await asyncio.sleep(REOBSERVE_POLL_INTERVAL_S)


async def execute_state_reread(
    ctx: RepairOwnerContext, binding: Dict[str, Any],
) -> Dict[str, Any]:
    """stale_reread：只读重读当前权威状态（新事实由重终验自然消费）。"""
    from app.services.session_data import session_data_manager

    map_state = await session_data_manager.get_map_state(ctx.session_id)
    revision = 0
    if isinstance(map_state, dict):
        try:
            revision = int(map_state.get("_cartographic_mutation_revision")
                           or 0)
        except (TypeError, ValueError):
            revision = 0
    return bounded_action_receipt(
        action_id=str(binding.get("action_id") or ""),
        action=str(binding.get("action") or ""),
        requirement_id=str(binding.get("requirement_id") or ""),
        status=ACTION_SUCCEEDED,
        detail=f"session state re-read at revision={revision}",
        evidence_ref=f"state:rev{revision}")


async def execute_requalify(
    ctx: RepairOwnerContext, binding: Dict[str, Any],
    chapter: Dict[str, Any],
) -> Dict[str, Any]:
    """requalify：只读数据资格重判（workflow_contract 角色状态重读）。"""
    contract = (chapter or {}).get("workflow_contract") \
        if isinstance(chapter, dict) else None
    roles = contract.get("roles") if isinstance(contract, dict) else None
    counts: Dict[str, int] = {}
    if isinstance(roles, list):
        for role in roles[:16]:
            if isinstance(role, dict):
                status = str(role.get("status") or "unknown")[:24]
                counts[status] = counts.get(status, 0) + 1
    detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or \
        "no workflow roles recorded"
    return bounded_action_receipt(
        action_id=str(binding.get("action_id") or ""),
        action=str(binding.get("action") or ""),
        requirement_id=str(binding.get("requirement_id") or ""),
        status=ACTION_SUCCEEDED,
        detail=f"data qualification re-read ({detail})",
        evidence_ref="qualification:reread")


def needs_user_receipt(binding: Dict[str, Any]) -> Dict[str, Any]:
    return bounded_action_receipt(
        action_id=str(binding.get("action_id") or ""),
        action=str(binding.get("action") or ""),
        requirement_id=str(binding.get("requirement_id") or ""),
        status=ACTION_NEEDS_USER,
        detail=str(binding.get("reason") or "user input required")[:200])


__all__ = [
    "RepairOwnerContext", "REOBSERVE_WAIT_S", "DELIVERY_WAIT_S",
    "derive_delivery_spec", "preview_delivery_for_binding",
    "execute_map_export", "execute_delivery", "execute_runtime_repair",
    "wait_fresh_observation", "execute_state_reread", "execute_requalify",
    "needs_user_receipt",
]
