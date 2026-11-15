"""G37（会话模型运行配置）—— 会话级模型目录/preference/用量视图路由。

挂载方式与 ``chat_session_views`` 相同（``chat.router.include_router``，
继承 ``/chat`` 前缀）—— 所有端点经 ``require_owned_session`` 做 owner 归
属校验（M33：跨用户/猜 id 一律 404，不泄漏目录/账本存在性之外的任何信
息；目录本身是管理员公开面，但会话状态面严格 owner-scoped）。

端点：
- ``GET  /chat/sessions/{id}/model``  目录 + 当前 preference + 实际生效/
  待生效状态 + 思考档（来自最近 turn 回执，不乐观推算）；
- ``PUT  /chat/sessions/{id}/model``  设置/恢复 preference（CAS，M11）；
  active turn 期间返回 pending_next_turn（M04）；
- ``GET  /chat/sessions/{id}/usage``  会话累计 + 最近 turn 用量 + coverage
  /completeness（来源与未知如实分层）。

不提供任何凭证读写：目录/能力来自服务器配置，客户端字符串不解释为
endpoint/key（M07/M34）。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_async_db
from app.models.db_model import Conversation
from app.services.auth_history_bridge import require_owned_session
from app.services.chat.model_runtime import session_catalog
from app.services.chat.model_runtime.session_model_prefs import (
    RevisionConflict,
    get_pref,
    set_pref,
)
from app.services.chat.model_runtime.session_model_service import (
    resolve_session_target,
)
from app.services.chat.model_runtime.usage_ledger import session_usage_summary

logger = logging.getLogger(__name__)
router = APIRouter()  # 前缀/tags 由挂载方（chat.router）提供


class ModelPreferenceUpdate(BaseModel):
    mode: str = Field(..., description="inherit | explicit")
    model_id: Optional[str] = None
    thinking_level: Optional[str] = None
    session_token_cap: Optional[int] = None
    expected_revision: int = Field(..., ge=0, description="CAS：当前已知 revision")


def _active_turn_pending(session_id: str) -> bool:
    """该会话是否有在飞 turn（preference 只对下一 turn 生效，M04）。"""
    try:
        from app.agent_pi_bridge import get_active_turn_entry

        return get_active_turn_entry(session_id) is not None
    except Exception:  # noqa: BLE001 — 状态面失败按无在飞 turn（保守披露）
        return False


async def _latest_receipt(db: AsyncSession, session_id: str):
    from sqlalchemy import select

    from app.models.db_model import ChatTurnRuntime

    return (
        await db.execute(
            select(ChatTurnRuntime)
            .where(ChatTurnRuntime.session_id == session_id)
            .order_by(ChatTurnRuntime.created_at.desc(), ChatTurnRuntime.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.get("/sessions/{session_id}/model")
async def get_session_model(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
):
    """目录 + preference + 实际生效状态（读真实回执，不从 UI 乐观态反推）。"""
    catalog = session_catalog.build_catalog()
    target = await resolve_session_target(db, session_id)
    pref = target["pref"]
    receipt = await _latest_receipt(db, session_id)

    return {
        "session_id": session_id,
        "catalog_generation": session_catalog.catalog_generation(),
        "catalog": [item.public_payload() for item in catalog],
        "preference": {
            "mode": pref.mode,
            "model_id": pref.catalog_item_id,
            "thinking_level": pref.thinking_level,
            "session_token_cap": pref.session_token_cap,
            "revision": pref.revision,
        },
        # 目标解析结果（含不可选原因 —— M08/M09 语义的服务器真值）
        "target": {
            "ok": target["ok"],
            "reason_code": target["reason_code"] or None,
            "reason_detail": target["reason_detail"] or None,
            "model_id": target["item"].item_id if target["item"] else None,
        },
        # 最近一个 turn 的真实运行回执（effective 以 get_state 回读为准）
        "last_turn": (
            {
                "turn_id": receipt.turn_id,
                "status": receipt.status,
                "mode": receipt.mode,
                "requested_model": receipt.requested_model,
                "effective_model": receipt.effective_model,
                "thinking_requested": receipt.thinking_requested,
                "thinking_effective": receipt.thinking_effective,
                "response_model": receipt.response_model,
                "catalog_generation": receipt.catalog_generation,
                "created_at": receipt.created_at.isoformat() if receipt.created_at else None,
            }
            if receipt is not None else None
        ),
        "pending_next_turn": _active_turn_pending(session_id),
        # 思考档可控性（当前端点契约 supportsReasoningEffort=False → 不可控）
        "thinking_controllable": False,
        "budget": {
            "token_cap_max": settings.SESSION_MODEL_TOKEN_CAP_MAX,
            "token_cap_default": settings.SESSION_MODEL_TOKEN_CAP_DEFAULT,
        },
    }


@router.put("/sessions/{session_id}/model")
async def put_session_model(
    session_id: str,
    req: ModelPreferenceUpdate,
    _conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
):
    """设置/恢复 preference（CAS + owner + 目录重验证）。

    写入只持久「目录项 identity + 请求档位」—— 实际生效永远以 turn 期
    set_model→get_state 回读为准（本端点不触碰 Pi，绝不宣称已生效）。
    """
    from app.core.config import settings

    # 目录重验证：explicit 必须引用当前管理员目录内可选项（M07/M08/M12）。
    if req.mode == "explicit":
        if not req.model_id:
            raise HTTPException(status_code=400, detail="explicit preference requires model_id")
        try:
            session_catalog.validate_model_id(req.model_id)
        except session_catalog.CatalogItemIdError:
            raise HTTPException(status_code=400, detail="invalid model id")
        item = session_catalog.find_item(req.model_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail="model is not in the administered catalog",
            )
        if not item.available:
            raise HTTPException(
                status_code=409,
                detail=f"model unavailable: {item.unavailable_reason}",
            )
    elif req.mode != "inherit":
        raise HTTPException(status_code=400, detail="mode must be inherit or explicit")

    # 预算上限钳制：只能在 admin 上限内收紧（不得抬高全局授权）。
    cap = req.session_token_cap
    if cap is not None:
        cap = int(cap)
        if cap <= 0:
            cap = None
        admin_max = int(settings.SESSION_MODEL_TOKEN_CAP_MAX or 0)
        if cap is not None and admin_max > 0 and cap > admin_max:
            raise HTTPException(
                status_code=409,
                detail=f"session token cap exceeds the admin limit ({admin_max})",
            )
        if cap is not None and admin_max <= 0:
            raise HTTPException(
                status_code=409,
                detail="session token caps are not enabled by the administrator",
            )

    thinking = req.thinking_level
    if thinking is not None:
        from app.services.chat.model_runtime.session_model_prefs import THINKING_LEVELS

        if thinking not in THINKING_LEVELS:
            raise HTTPException(status_code=400, detail="invalid thinking level")
        # review-P3 修复：当前端点契约（compat.supportsReasoningEffort=false）
        # 不支持档位控制 —— 存储「请求档位」只会让消费者误以为会生效。明确
        # 拒绝，待端点确证支持后再放开（M14）。
        raise HTTPException(
            status_code=409,
            detail=(
                "thinking level control is not supported by the current "
                "endpoint contract; reasoning output may occur but a specific "
                "level cannot be requested"
            ),
        )

    try:
        pref = await set_pref(
            db,
            session_id,
            mode=req.mode,
            catalog_item_id=req.model_id if req.mode == "explicit" else None,
            thinking_level=thinking if req.mode == "explicit" else None,
            session_token_cap=cap,
            expected_revision=req.expected_revision,
        )
    except RevisionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "REVISION_CONFLICT",
                "message": "preference was updated concurrently; refresh and retry",
                "current_revision": (await get_pref(db, session_id)).revision,
            },
        ) from exc

    return {
        "session_id": session_id,
        "preference": {
            "mode": pref.mode,
            "model_id": pref.catalog_item_id,
            "thinking_level": pref.thinking_level,
            "session_token_cap": pref.session_token_cap,
            "revision": pref.revision,
        },
        # 本端点不应用 Pi —— 生效发生在下一 turn 边界（active turn 不打断，
        # 其结束即应用点）
        "effective_when": (
            "active_turn_end" if _active_turn_pending(session_id) else "next_turn"
        ),
        "pending_next_turn": _active_turn_pending(session_id),
        "thinking_controllable": False,
    }


@router.get("/sessions/{session_id}/usage")
async def get_session_usage(
    session_id: str,
    _conv: Conversation = Depends(require_owned_session),
    db: AsyncSession = Depends(get_async_db),
):
    """会话用量：累计 + 最近 turn + coverage/completeness（诚实分层）。"""
    return await session_usage_summary(db, session_id)
