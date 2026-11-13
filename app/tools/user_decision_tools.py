"""运行中用户决策工具面（G29 / WP05+WP06）。

``webgis_user_decision`` 是**生产工具**（非测试专用）：当用户明确要求
「作图前让我核对标题/来源说明」或任务缺少一个只有用户能补的事实时，模型
调用本工具发起一次有界原生交互。封套由服务端签名铸造（发不出发起不由
模型文本决定）；扩展校验签名后经真实 ``ctx.ui`` 弹出，用户回答由服务端
按保存的选项表/类型校验反解，结果作为工具内容回到同一 agent run。

纪律：

- **不授权**：本工具只采集「用户决定」这一输入事实；不提升任何工具权限，
  不替代 owner/scope/write 门禁（任务 §3）。
- **有界**：kind 白名单、标题/正文/选项/超时全部受限；per-turn 待答封套
  上限（见 pi_interaction_envelope），超限返回错误引导改文本收口。
- **无副作用**：发起前绝不执行依赖该决定的写入（由调用方工具遵守）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class UserDecisionArgs(BaseModel):
    kind: str = Field(
        description="交互类型：select（选项）/ confirm（确认）/ input（单行）/ editor（多行）",
    )
    title: str = Field(min_length=2, max_length=120, description="问题标题（用户可见）")
    message: str = Field(default="", max_length=2000, description="说明/正文（confirm/editor 草稿语境）")
    options: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="select 的候选：[{display, value}]；display 用户可见，value 为稳定引用（如 ref:xxx）",
    )
    prefill: str = Field(default="", max_length=6000, description="editor 预填草稿（如当前标题草案）")
    placeholder: str = Field(default="", max_length=200, description="input 占位提示")
    timeout_s: Optional[int] = Field(
        default=None, ge=10, le=900, description="等待上限（秒）；缺省用宿主策略"
    )
    target: str = Field(
        default="other",
        max_length=40,
        description="业务对象：mapspec_title / attribution / dataset_choice / other",
    )


def register_user_decision_tools(registry: ToolRegistry) -> None:
    """注册运行中用户决策工具。"""

    @tool(
        registry,
        name="webgis_user_decision",
        capabilities=["workspace_state_inspection"],
        description=(
            "在同一运行中的任务里向用户提出一个有界问题并等待回答。适用于："
            "用户明确要求在产出前核对标题/来源说明（kind=confirm/input/editor，"
            "target=mapspec_title/attribution）；或存在多个同名/同义合法数据版本"
            "需要用户裁决（kind=select，options 带 ref）。回答将由用户在界面提交，"
            "结果原样回到本任务——不要同时在文本里臆测答案。"
        ),
        args_model=UserDecisionArgs,
        tier=1,
        domains=["mapspec", "dataset"],
        side_effect="pure",
        deterministic=False,
        latency_class="slow",
        memory_class="light",
        scale_class="small",
        tags=("用户决策", "运行中交互", "核对", "同名数据", "G29"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        summary=(
            "在同一运行中的任务里向用户提一个有界问题并等待回答（原生交互）。"
            "用户要求核对标题/来源说明时用 confirm/input/editor + target="
            "mapspec_title/attribution；同名数据二义时用 select + ref 值选项。"
        ),
        examples=(
            "用户要求作图前核对标题——发起 editor 让用户改标题草案",
            "存在两个同名人口数据版本——发起 select 让用户选数据",
        ),
        anti_examples=(
            "任务事实齐全且用户未要求核对——不要凭空追加审批卡",
        ),
    )
    async def webgis_user_decision(
        kind: str,
        title: str,
        message: str = "",
        options: Optional[list[dict[str, Any]]] = None,
        prefill: str = "",
        placeholder: str = "",
        timeout_s: Optional[int] = None,
        target: str = "other",
        session_id: Optional[str] = None,
    ) -> dict:
        from app.services.chat.pi_interaction_envelope import (
            mint_envelope,
        )

        token, error = mint_envelope(
            tool_name="webgis_user_decision",
            kind=str(kind or "").strip(),
            title=str(title or "").strip(),
            message=str(message or ""),
            options=options if isinstance(options, list) else None,
            prefill=str(prefill or ""),
            placeholder=str(placeholder or ""),
            timeout_ms=(int(timeout_s * 1000) if timeout_s else None),
            business={"target": str(target or "other")[:40]},
        )
        if error:
            reasons = {
                "ENVELOPE_NO_TURN_CONTEXT": (
                    "当前调用不在可交互的任务上下文中（非运行中 turn）。"
                    "请在最终回复中以文本形式向用户提问。"
                ),
                "ENVELOPE_TURN_CAP": (
                    "本回合待答问题已达上限。请先处理已提出的问题，"
                    "或在本轮结束后的文本中提问。"
                ),
            }
            return {
                "success": False,
                "code": error,
                "message": reasons.get(error, f"决策请求被拒绝（{error}）。"),
            }
        out: Dict[str, Any] = {
            "success": True,
            "summary": f"已向用户发起运行中决策（{kind}）：{title}",
            "webgis_interaction_request": token,
        }
        return out
