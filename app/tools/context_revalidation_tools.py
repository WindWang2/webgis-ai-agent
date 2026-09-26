"""情境重验证 / mission 续接工具入口（ADR-0215，薄包装层）。

把 app/services/gis_context 的 evidence-backed revalidation 与跨 session
mission 续接能力暴露给 LLM。真正的检查、裁决、CAS 写回全部在
``gis_context.revalidation`` / ``gis_context.hotpath`` 内完成 —— 工具调用
方只能**指名** claim / 决策文本 / mission id，任何叙事性描述都不参与判定
（LLM 不可自说自话升级，失败一律返回带 reason code 的 rejected 回执）。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolExecutionPolicy, ToolRegistry

logger = logging.getLogger(__name__)


class ContextRevalidationArgs(BaseModel):
    """指名式重验证请求：claim id 清单 + 决策原文清单（均可选）。"""

    claim_ids: List[str] = Field(
        default_factory=list,
        max_length=8,
        description="要重新验证的 claim id 清单（工作上下文中的 stale findings）。",
    )
    reaffirm_texts: List[str] = Field(
        default_factory=list,
        max_length=4,
        description="要重新确认的决策/假设**原文**（必须与已记录文本完全一致）。",
    )


class ContextBindMissionArgs(BaseModel):
    """跨 session 续接请求：显式 mission id（不做模糊匹配）。"""

    mission_id: str = Field(
        min_length=4,
        max_length=64,
        description="要续接的 mission id（msn-*）。",
    )


def register_context_revalidation_tools(registry: ToolRegistry):
    """注册 ADR-0215 情境工具：webgis_context_revalidate / webgis_context_bind_mission。"""

    @registry.tool(
        name="webgis_context_revalidate",
        capabilities=["gis_context_revalidation"],
        tier=2,
        description=(
            "证据驱动的上下文重验证：把被失效（stale）的结论用**新证据**恢复为 current。\n"
            "适用场景：(1) 任务目标/数据未变，但 AOI/CRS/时间段曾漂移过，部分结论被标记为需复核，"
            "重新计算后需要恢复它们；(2) 某条假设在新的观察下被再次确认为成立。\n"
            "**纪律**：调用方只能提供 claim id 与决策原文；引擎会重新读取 claim、"
            "复核数据集指纹、重跑确定性验证器 —— 描述性文字不参与判定。"
            "每次调用返回带 reason code 的回执（restored / rejected + 原因），"
            "被拒绝时状态不会改变。\n"
            "**约束**：claim_ids ≤ 8；reaffirm_texts ≤ 4；同一次任务会话内需要先有"
            "重算/重验的事实依据，纯叙事请求会被拒绝。"
        ),
        args_model=ContextRevalidationArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["情境", "重验证", "失效恢复", "revalidation", "工作上下文"],
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=["invalid_args", "missing_data"],
    )
    async def webgis_context_revalidate(
        claim_ids: Optional[List[str]] = None,
        reaffirm_texts: Optional[List[str]] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        try:
            from app.services.gis_context.hotpath import request_revalidation

            return await request_revalidation(
                session_id,
                claim_ids=claim_ids or [],
                reaffirm_texts=reaffirm_texts or [],
            )
        except Exception as exc:  # noqa: BLE001 — 工具面失败不外溢堆栈
            logger.warning("[gis_context] revalidate tool failed: %s", type(exc).__name__)
            return {"ok": False, "reason": f"tool_error:{type(exc).__name__}"[:48]}

    @registry.tool(
        name="webgis_context_bind_mission",
        capabilities=["gis_context_revalidation"],
        tier=2,
        description=(
            "跨会话续接制图任务：把当前会话显式绑定到一个已存在的 mission，"
            "恢复其工作上下文（已接受的基准 / 已验证结论 / 用户手动编辑）。\n"
            "适用场景：用户说「继续之前的制图任务 msn-xxxx」或在新会话中要求接续既有工作。\n"
            "**安全边界**：org 不匹配的 mission 不可见；终态 mission 会被拒绝（其上下文已清空）；"
            "project 不匹配会拒绝渲染。绑定成功后，后续回合的 [GIS_CONTEXT] 卡自动携带该"
            "mission 的上下文（stale 结论只会以失效原因出现，不会伪装成 current）。"
        ),
        args_model=ContextBindMissionArgs,
        execution_policy=ToolExecutionPolicy.INLINE,
        side_effect="state_mutation",
        data_mutations=["session_state"],
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=["情境", "mission", "续接", "绑定", "跨会话"],
        output_semantic_type="json",
        result_size_policy="inline_small",
        failure_modes=["invalid_args", "missing_data"],
    )
    async def webgis_context_bind_mission(
        mission_id: str = "",
        session_id: Optional[str] = None,
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        if not mission_id:
            return {"ok": False, "reason": "invalid_args"}
        try:
            from app.services.gis_context.hotpath import bind_session_mission

            ok, reason = await bind_session_mission(
                session_id, mission_id,
            )
            return {"ok": ok, "reason": reason}
        except Exception as exc:  # noqa: BLE001 — 工具面失败不外溢堆栈
            logger.warning("[gis_context] bind_mission tool failed: %s", type(exc).__name__)
            return {"ok": False, "reason": f"tool_error:{type(exc).__name__}"[:48]}


__all__ = ["register_context_revalidation_tools"]
