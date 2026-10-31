"""FinalDisplayConfirmation human seam 端点（V7 ADR-0134 D6 · Track A M7）。

``display_confirmation`` 模块此前只提供后端判定 seam（record_display_ack
无任何路由/前端调用方 —— required 模式永远无法 confirmed）。本端点补上
写半边：

- ``GET  /chat/sessions/{sid}/display-confirmation`` —— 确认状态（mode/
  confirmed/pending/裁决代次/最新观察代次）。前端确认卡的自水合与刷新
  恢复都走这里（auto 模式 pending 恒 false —— 历史行为零变化）。
- ``POST /chat/sessions/{sid}/display-confirmation/ack`` —— owner 显式
  确认当前显示。新鲜度门（fail-closed）：
  1. ack 的 render_seq 必须等于**当前最新**观察代次（用户确认的是此刻
     看到的渲染，不是历史观察）；
  2. 且 ≥ 存储裁决的 render_observation_seq（旧代次不能使新裁决通过）。
  通过后写 ack（幂等）并 force finalizer 重跑 → task_complete 披露刷新。

鉴权：require_owned_session（与 cartographic-observation 同族）；ack 的
可信度来自 owner 身份（登录归属/匿名 owner_token），与客户端可写的
map_state 身份字段无关（#1841/#1843 攻击面不外溢）。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.client_ip import client_ip_from
from app.core.rate_limiter import get_rate_limiter
from app.models.db_model import Conversation
from app.schemas.map_operation_schema import (
    DisplayAckRequest,
    DisplayAckResponse,
    DisplayConfirmationStatusResponse,
)
from app.services.auth_history_bridge import require_owned_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["AI对话"])

_RATE_LIMIT_MAX = 60
_RATE_LIMIT_WINDOW = 60


async def _raise_if_deleted(session_id: str) -> None:
    from app.services.session_data import session_data_manager

    if await session_data_manager.get_state_field(session_id, "_cartographic_deleted") is True:
        raise HTTPException(status_code=410, detail="Session was deleted")


async def _stored_product(session_id: str) -> Optional[dict]:
    """读取持久化终验块（render_observation_seq = 裁决代次钥匙）。"""
    try:
        from app.services.session_plan import load_session_plan

        plan = await load_session_plan(session_id)
        chapter = getattr(plan, "gis_chapter", None) if plan is not None else None
        stored = chapter.get("map_product") if isinstance(chapter, dict) else None
        return stored if isinstance(stored, dict) else None
    except Exception:  # noqa: BLE001 — 只读面 fail-open 为「无裁决」
        return None


async def _latest_observation_seq(session_id: str) -> int:
    try:
        from app.services.gis_harness.render_observation import (
            load_render_observation,
            observation_sequence,
        )

        return observation_sequence(await load_render_observation(session_id))
    except Exception:  # noqa: BLE001
        return 0


def _verdict_render_seq(stored: Optional[dict]) -> int:
    if not isinstance(stored, dict):
        return 0
    try:
        return int(stored.get("render_observation_seq") or 0)
    except (TypeError, ValueError):
        return 0


@router.get(
    "/sessions/{session_id}/display-confirmation",
    response_model=DisplayConfirmationStatusResponse,
)
async def get_display_confirmation(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
):
    """确认状态（auto → confirmed/pending 恒为 True/False 的历史语义）。"""
    await _raise_if_deleted(session_id)
    from app.services.gis_harness.display_confirmation import (
        display_mode,
        is_display_confirmed,
    )

    mode = display_mode()
    stored = await _stored_product(session_id)
    required_seq = _verdict_render_seq(stored)
    confirmed = await is_display_confirmed(session_id, render_seq=required_seq)
    pending = bool(mode == "required" and stored is not None and not confirmed)
    return DisplayConfirmationStatusResponse(
        session_id=session_id,
        mode=mode,
        confirmed=confirmed,
        pending=pending,
        required_render_seq=required_seq,
        latest_observation_seq=await _latest_observation_seq(session_id),
        verdict_present=stored is not None,
    )


@router.post(
    "/sessions/{session_id}/display-confirmation/ack",
    response_model=DisplayAckResponse,
)
async def post_display_ack(
    session_id: str,
    req: DisplayAckRequest,
    request: Request,
    _conv: Conversation = Depends(require_owned_session),
):
    """owner 显式确认当前地图显示（新鲜度门 fail-closed）。"""
    await _raise_if_deleted(session_id)
    from app.services.gis_harness.display_confirmation import (
        display_mode,
        is_display_confirmed,
        record_display_ack,
    )

    client_ip = client_ip_from(request)
    limiter = await get_rate_limiter()
    if not await limiter.is_allowed(
        f"display_ack:{client_ip}", _RATE_LIMIT_MAX, _RATE_LIMIT_WINDOW
    ):
        raise HTTPException(status_code=429, detail="Too many display acks")

    mode = display_mode()
    stored = await _stored_product(session_id)
    required_seq = _verdict_render_seq(stored)
    latest_seq = await _latest_observation_seq(session_id)

    if mode != "required":
        # auto 模式确认无意义（恒 confirmed）—— 如实告知，不写状态。
        return DisplayAckResponse(
            accepted=False,
            confirmed=True,
            reason="mode_auto",
        )

    if stored is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "no_verdict",
                "message": "尚无终验裁决可确认。",
            },
        )
    if req.render_seq != latest_seq:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "stale_ack",
                "message": (
                    f"确认代次 {req.render_seq} 与当前最新观察代次 {latest_seq} "
                    "不一致；请确认当前看到的地图后重试。"
                ),
                "latest_observation_seq": latest_seq,
            },
        )
    if req.render_seq < required_seq:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "stale_verdict",
                "message": (
                    f"确认代次 {req.render_seq} 早于当前裁决依据代次 {required_seq}。"
                ),
            },
        )

    ack = await record_display_ack(session_id, render_seq=req.render_seq)
    if ack is None:
        raise HTTPException(status_code=503, detail="ack write failed; retry")

    confirmed = await is_display_confirmed(session_id, render_seq=required_seq)

    # ack 落地后 force finalizer 重跑：display_confirmed 翻转 → task_complete
    # 披露刷新（重验幂等门自然放行 revision 未变场景的披露更新）。
    task_complete: Optional[bool] = None
    try:
        from app.services.gis_harness.completion.pipeline import (
            maybe_finalize_map_product,
        )

        completion = await maybe_finalize_map_product(
            session_id, reason="display_ack", force=True,
        )
        if completion is not None:
            task_complete = getattr(completion, "task_complete", None)
    except Exception:  # noqa: BLE001 — finalizer 失败不回滚 ack（下次触发重披露）
        logger.warning("[display-ack] finalizer re-run failed", exc_info=True)

    return DisplayAckResponse(
        accepted=True,
        confirmed=confirmed,
        render_seq=req.render_seq,
        task_complete=task_complete,
    )
