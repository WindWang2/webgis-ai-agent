"""G37：Pi/agent 可读的会话模型运行配置工具（WP11，只读）。

三个只读工具，经既有 ToolRegistry→dispatch 面向 Pi（``webgis_execute``
动态面，G35 纪律：不进 NATIVE_TOOL_NAMES/default_active）：

- ``webgis_model_catalog``：管理员允许目录 + 不可用原因 + 能力来源；
- ``webgis_session_model_status``：当前会话 preference/目标解析/最近
  turn 实际生效回执（requested/effective/responseModel 分列）；
- ``webgis_session_budget_status``：会话累计用量 + 预算投影 + coverage。

红线（M34）：只读工具绝不升级权限 —— 不接受任何「改 allowlist/换供
应商/写凭证」参数；模型选择变化只能来自用户经正式 API 的已验证请求。
``session_id`` 由 dispatch 注入（服务器所有，调用方传入值被剥离）。
"""
from __future__ import annotations

import logging

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def register_session_model_tools(registry: ToolRegistry):
    @tool(
        registry,
        tier=2, domains=["runtime"],
        name="webgis_model_catalog",
        description=(
            "列出本会话可选择的、管理员允许的 AI 模型目录（只读）。"
            "每项含能力（context_window/tool_calling/reasoning/cost_class，"
            "未知即 unknown）、可用状态与不可用原因、是否默认继承项。"
            "✅ 用于：回答『有哪些模型可选/为什么某模型不可选/默认是哪个』。"
            "\n❌ 不能用于：添加模型、修改凭证或切换模型 —— 用户请在会话"
            "设置中操作。"
        ),
        param_descriptions={},
        side_effect="pure",
        network=False,
        deterministic=True,
    )
    async def model_catalog_tool(session_id: str = "") -> dict:
        from app.services.chat.model_runtime import session_catalog

        catalog = session_catalog.build_catalog()
        return {
            "catalog_generation": session_catalog.catalog_generation(),
            "thinking_controllable": False,
            "items": [item.public_payload() for item in catalog],
            "note": (
                "capability fields come from administered descriptors; "
                "unknown means honestly unknown, not a vendor maximum"
            ),
        }

    @tool(
        registry,
        tier=2, domains=["runtime"],
        name="webgis_session_model_status",
        description=(
            "当前会话的模型运行状态（只读）：用户选择了什么、目录解析是否"
            "通过、最近一个 turn 实际采用的模型（get_state 回读真值）与"
            "思考档 requested/effective。"
            "✅ 用于：回答『我的选择生效了吗/这轮用的哪个模型』。"
            "\n❌ 不能用于：替用户切换模型。"
        ),
        param_descriptions={},
        side_effect="pure",
        network=False,
        deterministic=True,
    )
    async def session_model_status_tool(session_id: str = "") -> dict:
        if not session_id:
            return {"error": "session context unavailable"}
        from sqlalchemy import select

        from app.core.database import async_db_session
        from app.models.db_model import ChatTurnRuntime
        from app.services.chat.model_runtime.session_model_service import (
            resolve_session_target,
        )

        async with async_db_session() as db:
            target = await resolve_session_target(db, session_id)
            receipt = (
                await db.execute(
                    select(ChatTurnRuntime)
                    .where(ChatTurnRuntime.session_id == session_id)
                    .order_by(ChatTurnRuntime.created_at.desc(), ChatTurnRuntime.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            pref = target["pref"]
            return {
                "preference": {
                    "mode": pref.mode,
                    "model_id": pref.catalog_item_id,
                    "thinking_level": pref.thinking_level,
                    "revision": pref.revision,
                },
                "target_ok": target["ok"],
                "target_reason": target["reason_code"] or None,
                "target_detail": target["reason_detail"] or None,
                "last_turn": (
                    {
                        "turn_id": receipt.turn_id,
                        "status": receipt.status,
                        "requested_model": receipt.requested_model,
                        "effective_model": receipt.effective_model,
                        "response_model": receipt.response_model,
                        "thinking_requested": receipt.thinking_requested,
                        "thinking_effective": receipt.thinking_effective,
                        "complete": receipt.complete,
                    }
                    if receipt is not None else None
                ),
            }

    @tool(
        registry,
        tier=2, domains=["runtime"],
        name="webgis_session_budget_status",
        description=(
            "当前会话的 token 用量与预算投影（只读）：累计 input/output/"
            "cache 分项、最近 turn 用量、预算上限/剩余、费用来源"
            "（provider-reported/estimated/unknown）与覆盖范围。"
            "✅ 用于：回答『这轮花了多少/还剩多少/为什么被预算拒绝』。"
            "\n❌ 不能用于：调整预算上限 —— 用户在会话设置中自设（不得超"
            "管理员上限）。"
        ),
        param_descriptions={},
        side_effect="pure",
        network=False,
        deterministic=True,
    )
    async def session_budget_status_tool(session_id: str = "") -> dict:
        if not session_id:
            return {"error": "session context unavailable"}
        from app.core.database import async_db_session
        from app.services.chat.model_runtime.session_model_prefs import (
            effective_session_token_cap,
        )
        from app.services.chat.model_runtime.usage_ledger import (
            session_usage_summary,
        )

        async with async_db_session() as db:
            summary = await session_usage_summary(db, session_id, limit_turns=5)
            cap = await effective_session_token_cap(db, session_id)
            totals = summary["totals"]
            summary["budget"] = {
                "token_cap": cap,
                "remaining": (
                    None if cap is None else max(0, cap - totals["total"])
                ),
            }
            return summary
