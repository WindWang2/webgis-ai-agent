"""G42 Agent 工具面 —— goal repair 预览/启动/状态/取消（Pi 同链）。

信任边界：

- preview/status/cancel 语义与 REST 面同一服务层（无第二实现）；
- **start 零等待**：回调内只绑定 attempt（幂等）即返回 —— 动作执行体是
  独立任务，经统一 ToolDispatchService 派发（不嵌套持 wave 槽，R37；
  不递归 prompt、不另持 ``pi-writer:{session}``）；
- owner 身份经 JobOrigin/runtime context 归一（模型不可指定他人会话；
  dispatcher 注入 session_id）；
- acknowledged_losses 由调用方显式携带（交付损失确认是用户决策，工具
  不代答）；缺席 → 该动作 typed 阻断（零提交）。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _runtime_ids() -> tuple:
    """(session_id, turn_id, tool_call_id, owner) —— 当前回调上下文。"""
    from app.lib.runtime.context import current_runtime_context
    from app.services.jobs.context import current_origin

    ctx = current_runtime_context()
    origin = current_origin()
    sid = str(getattr(ctx, "session_id", "") or "") if ctx else ""
    turn_id = str(getattr(ctx, "turn_id", "") or "") if ctx else ""
    tool_call_id = str(getattr(ctx, "tool_call_id", "") or "") if ctx else ""
    owner = {
        "user_id": getattr(origin, "owner_id", None) if origin else None,
        "owner_token":
            getattr(origin, "owner_token", None) if origin else None,
        "org_id": getattr(origin, "org_id", None) if origin else None,
        "project_id":
            getattr(origin, "project_id", None) if origin else None,
    }
    return sid, turn_id, tool_call_id, owner


def _owner_ctx(session_id: str):
    from app.services.gis_harness.goal_repair_execution.actions import (
        RepairOwnerContext,
    )

    _sid, turn_id, tool_call_id, owner = _runtime_ids()
    return RepairOwnerContext(
        session_id=session_id,
        user_id=owner.get("user_id"),
        org_id=owner.get("org_id"),
        project_id=owner.get("project_id"),
        owner_token=owner.get("owner_token"),
        turn_id=turn_id,
        tool_call_id=tool_call_id,
    )


def _typed_error(exc: Exception) -> dict:
    code = getattr(exc, "code", "GOAL_REPAIR_ERROR")
    return {
        "success": False,
        "code": str(code)[:48],
        "message": str(getattr(exc, "message", exc))[:300],
        "detail": getattr(exc, "detail", None) or {},
    }


class GoalRepairStartArgs(BaseModel):
    """webgis_goal_repair_start 参数（future annotations + 显式 args_model）。"""

    action_ids: Optional[List[str]] = Field(
        default=None, max_length=8,
        description="预览返回的可执行动作 id 子集；缺省 = 全部可执行动作")
    acknowledged_losses: Optional[List[str]] = Field(
        default=None, max_length=8,
        description="preview 披露的交付格式损失确认码全集（用户决策；缺省=该动作阻断）")
    session_id: str = Field(default="", description="由 dispatcher 注入的会话 id")


def register_goal_repair_tools(registry: ToolRegistry) -> None:
    """注册 goal repair 工具族（G42）。"""

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="webgis_goal_repair_preview",
        description=(
            "查看当前会话目标补全预览：未兑现的原需求 → 服务端核实的有限"
            "修复动作（可执行/需用户确认损失/需用户资料/不支持），零副作用。"
            "\n何时用：任务完成卡 verdict 非 satisfied 且存在 goal_repair_plan"
            " —— 先预览再让用户/你决定补全范围。"
            "\n返回：goal_id/verdict/plan_version/逐动作绑定（entry 为真实"
            "执行入口、ack_codes 为需确认的格式损失）与既有 attempt 状态。"
        ),
        side_effect="cacheable_read",
        deterministic=True,
        network=False,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=("GIS", "目标", "补全", "修复", "预览"),
        output_semantic_type="object",
        result_size_policy="bounded",
    )
    async def webgis_goal_repair_preview(session_id: str = "") -> dict:
        from app.services.gis_harness.goal_repair_execution import (
            build_repair_preview,
        )

        sid = session_id or _runtime_ids()[0]
        if not sid:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        try:
            preview = await build_repair_preview(
                sid, ctx=_owner_ctx(sid))
        except Exception as e:  # noqa: BLE001 — typed 拒绝统一回执
            return _typed_error(e)
        return {"success": True, **preview}

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="webgis_goal_repair_start",
        description=(
            "启动一次有界目标补全（幂等：同 goal 活跃 attempt 复用）。"
            "动作经统一派发执行既有生产工具（地图导出/数据交付/修复），"
            "收口后由既有终验重新裁决；本工具**立即返回**（不等待动作），"
            "用 webgis_goal_repair_status 轮询。"
            "\n红线：不放宽原需求；交付损失确认（acknowledged_losses）是"
            "用户决策 —— 未携带则该动作 typed 阻断（零提交）。"
        ),
        args_model=GoalRepairStartArgs,
        side_effect="state_mutation",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("GIS", "目标", "补全", "执行", "attempt"),
        capabilities=["plan_workflow_orchestration"],
        output_semantic_type="object",
        result_size_policy="bounded",
    )
    async def webgis_goal_repair_start(
        action_ids: Optional[List[str]] = None,
        acknowledged_losses: Optional[List[str]] = None,
        session_id: str = "",
    ) -> dict:
        from app.services.gis_harness.goal_repair_execution import (
            start_repair_attempt,
        )

        sid = session_id or _runtime_ids()[0]
        if not sid:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        try:
            # Pi 回调入口：当前 turn 即合法上下文（session_busy_check=False；
            # 执行体独立任务，不嵌套持槽、不递归 prompt、不另持 writer）。
            view = await start_repair_attempt(
                sid,
                selected_action_ids=action_ids,
                acknowledged_losses=acknowledged_losses,
                ctx=_owner_ctx(sid),
                entry="pi_tool",
                session_busy_check=False,
            )
        except Exception as e:  # noqa: BLE001 — typed 拒绝统一回执
            return _typed_error(e)
        out = {"success": True, **view}
        if view.get("status") in ("running", "pending", "queued"):
            out["note"] = ("attempt 已启动（零等待）：用 "
                           "webgis_goal_repair_status(attempt_id=...) 轮询；"
                           "收口后终验自动重跑")
        return out

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="webgis_goal_repair_status",
        description=(
            "查询目标补全 attempt 状态（attempt_id 定位；缺省取会话最近）。"
            "返回逐动作回执（状态/证据 ref/下载 URL）与重终验前后 verdict ——"
            "浏览器刷新后的回看权威。"
        ),
        side_effect="cacheable_read",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("GIS", "目标", "补全", "状态"),
        output_semantic_type="object",
        result_size_policy="bounded",
    )
    async def webgis_goal_repair_status(
        attempt_id: str = "", session_id: str = "",
    ) -> dict:
        from app.services.gis_harness.goal_repair_execution import (
            get_attempt_status,
        )
        from app.services.gis_harness.goal_repair_execution.store import (
            find_attempts,
        )

        sid = session_id or _runtime_ids()[0]
        if not sid:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        if attempt_id:
            # 归属校验先于 get_attempt_status（其惰性对账会写他方会话状态
            # —— review P2：跨会话 attempt 一律 404 语义拒绝，零副作用）。
            try:
                from app.services.gis_harness.goal_repair_execution.store \
                    import ensure_attempt as _ensure

                pre = _ensure(attempt_id)
                if str(pre.get("session_id") or "") != sid:
                    return {"success": False, "code": "ATTEMPT_NOT_FOUND",
                            "message": "attempt 不属于当前会话"}
                view = await get_attempt_status(attempt_id)
            except Exception as e:  # noqa: BLE001
                return _typed_error(e)
            return {"success": True, **view}
        attempts = find_attempts(sid, limit=1)
        if not attempts:
            return {"success": False, "code": "ATTEMPT_NOT_FOUND",
                    "message": "会话尚无补全 attempt（先 preview/start）"}
        view = attempts[0]
        try:
            view = await get_attempt_status(view["attempt_id"])
        except Exception as e:  # noqa: BLE001
            return _typed_error(e)
        return {"success": True, **view}

    @tool(
        registry,
        tier=2,
        domains=["meta"],
        name="webgis_goal_repair_cancel",
        description=(
            "取消目标补全 attempt（两阶段协作取消；幂等）。已提交的不可撤销"
            "副作用（如已生成的导出文件）如实保留并在回执披露，不虚假回滚。"
        ),
        side_effect="state_mutation",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("GIS", "目标", "补全", "取消"),
        output_semantic_type="object",
        result_size_policy="bounded",
    )
    async def webgis_goal_repair_cancel(
        attempt_id: str = "", session_id: str = "",
    ) -> dict:
        from app.services.gis_harness.goal_repair_execution import (
            cancel_repair_attempt,
        )

        sid = session_id or _runtime_ids()[0]
        if not sid:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        if not attempt_id:
            from app.services.gis_harness.goal_repair_execution.store import (
                find_attempts,
            )

            attempts = find_attempts(sid or "", limit=1)
            if not attempts or not attempts[0].get("active"):
                return {"success": False, "code": "ATTEMPT_NOT_FOUND",
                        "message": "会话无活跃补全 attempt"}
            attempt_id = str(attempts[0]["attempt_id"])
        # 归属校验先于取消（review P1：跨会话破坏性动作不可先执行后拒绝）。
        try:
            from app.services.gis_harness.goal_repair_execution.store \
                import ensure_attempt as _ensure

            pre = _ensure(attempt_id)
            if str(pre.get("session_id") or "") != sid:
                return {"success": False, "code": "ATTEMPT_NOT_FOUND",
                        "message": "attempt 不属于当前会话"}
            view = await cancel_repair_attempt(
                attempt_id, ctx=_owner_ctx(sid or "unknown"))
        except Exception as e:  # noqa: BLE001
            return _typed_error(e)
        return {"success": True, **view}
