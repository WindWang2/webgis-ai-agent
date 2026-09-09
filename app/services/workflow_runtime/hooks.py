"""Workflow Runtime V5 —— chat/session 集成挂钩（全部 fail-open）。

边界纪律（架构 §8 [R1-M5/C3]）：

- 挂钩**绝不**阻断/改变既有会话行为：任何异常只记日志；
- 会话锁外执行（apply_tool_result 在锁释放后调用本模块）—— 绝不延长
  会话锁持锁时间；
- `GIS_WORKFLOW_RUNTIME` 开关统一门控（默认开；附加事实，关闭零回归）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.services.workflow_runtime.service import (
    get_service,
    runtime_enabled,
)

logger = logging.getLogger(__name__)


async def owner_scope_for_session(session_id: str) -> str:
    """会话 → owner 域（Conversation.user_id → geocompute 同哈希纪律）。

    信任缝：本挂钩只被既有会话所有权门（require_owned_session /
    authorize_session_write）之后的路径调用 —— 会话 id 即已授权身份。
    匿名会话（user_id NULL）落 "anonymous" 域。
    """
    try:
        from sqlalchemy import select

        from app.core.database import SessionLocal
        from app.models.db_model import Conversation

        with SessionLocal() as db:
            row = db.execute(
                select(Conversation.user_id).where(
                    Conversation.id == session_id)
            ).first()
        uid = row[0] if row else None
    except Exception:  # noqa: BLE001 — 身份解析失败按匿名（fail closed）
        uid = None
    from app.services.geocompute.executor import owner_scope_for as _osf

    return _osf({"user_id": uid} if uid else None, None)


async def attach_plan_safe(
    session_id: str, *, query: str, recipe_id: str,
    owner_scope: Optional[str] = None,
    profile: Optional[Dict[str, Any]] = None,
) -> None:
    """计划合成后：注册包 + 实例化 + 预绑数据角色（fail-open）。"""
    if not runtime_enabled() or not session_id or not recipe_id:
        return
    if not owner_scope:
        owner_scope = await owner_scope_for_session(session_id)
    try:
        svc = get_service()
        inst = await svc.attach_session_plan(
            session_id, owner_scope=owner_scope, query=query,
            recipe_id=recipe_id, profile=profile)
        if inst is not None:
            logger.info(
                "[WorkflowRuntime] attached instance %s session=%s pkg=%s",
                inst.get("instance_id"), session_id, recipe_id)
    except Exception:  # noqa: BLE001 — 附加事实绝不阻断规划
        logger.info("[WorkflowRuntime] attach_plan failed session=%s",
                    session_id, exc_info=True)


async def record_tool_result_safe(
    session_id: str, *, tool_name: str, geojson_ref: Optional[str],
    owner_scope: Optional[str] = None,
) -> None:
    """工具结果后：capability → 节点完成证据（fail-open，会话锁外）。"""
    if not runtime_enabled() or not session_id:
        return
    if not owner_scope:
        owner_scope = await owner_scope_for_session(session_id)
    try:
        from app.services.session_plan import (
            capabilities_hit_by_tool,
            load_session_plan,
        )

        plan = await load_session_plan(session_id)
        if plan is None or not getattr(plan, "gis_chapter", None):
            return
        hits = capabilities_hit_by_tool(plan, tool_name)
        if not hits:
            return
        svc = get_service()
        for capability in hits[:4]:
            await svc.record_tool_result(
                session_id, capability=capability,
                bound_ref=str(geojson_ref or ""),
                owner_scope=owner_scope, status="complete")
    except Exception:  # noqa: BLE001 — 证据回写绝不倒灌工具路径
        logger.info("[WorkflowRuntime] record_tool_result failed session=%s",
                    session_id, exc_info=True)


async def record_style_change_safe(
    session_id: str, *, owner_scope: Optional[str] = None,
    target: str = "",
) -> None:
    """MapSpec 样式突变后：style 维变更（fail-open；科学零触碰）。"""
    if not runtime_enabled() or not session_id:
        return
    if not owner_scope:
        owner_scope = await owner_scope_for_session(session_id)
    try:
        svc = get_service()
        await svc.record_style_change(
            session_id, owner_scope=owner_scope, target=target)
    except Exception:  # noqa: BLE001
        logger.info("[WorkflowRuntime] record_style_change failed "
                    "session=%s", session_id, exc_info=True)
