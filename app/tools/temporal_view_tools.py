"""时态视图控制工具族（temporal_view_*，GOAL 03 M3）。

薄工具面：参数校验 + 会话上下文 + 服务层转发；全部副作用经
``apply_gis_mutation``（MapSpec CAS）落 ``temporal`` 分支，Pi 与 legacy
同走 ToolRegistry/ToolDispatchService，无第二执行入口。

科学诚实语义（与 service 层一致）：
- bind 拒绝单期数据（single_period_unsupported）—— 绝不伪造动画；
- 帧序列只含真实观测桶 —— seek 到不存在的期数是 typed 拒绝；
- user_pinned 后 agent 不动光标（user-wins）。
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class TemporalViewBindArgs(BaseModel):
    ref_id: str = Field(..., min_length=1, max_length=128,
                        description="会话数据 ref（时序数据真相源）")
    layer_id: Optional[str] = Field(None, max_length=128,
                                    description="绑定的地图图层 id（缺省=ref 别名）")
    time_field: Optional[str] = Field(None, max_length=64,
                                      description="时间字段名（缺省=自动探测）")
    granularity: Optional[str] = Field(None,
                                       description="帧粒度 year|quarter|month|week|day|hour（缺省=按表示自然精度）")
    timezone: Optional[str] = Field(None, max_length=64,
                                    description="IANA 时区（缺省 UTC；锚定数据而非用户墙钟）")
    semantics: str = Field("event", description="event|snapshot|valid_time（缺省自动推断）")
    expected_revision: Optional[int] = Field(None, ge=0, description="CAS 基线（可选）")
    force: bool = Field(False, description="已绑定时强制重绑（重建帧与游标）")


class TemporalViewControlArgs(BaseModel):
    action: str = Field(..., description="seek|step|play|pause|rate|unpin")
    frame_id: Optional[str] = Field(None, max_length=64,
                                    description="seek 目标帧 id（如 f-2023）")
    steps: Optional[int] = Field(None, ge=-512, le=512,
                                 description="step 步数（±N；越界钳制并披露）")
    rate: Optional[float] = Field(None, ge=0.25, le=8.0,
                                  description="播放速率（0.25-8.0）")
    expected_revision: Optional[int] = Field(None, ge=0)


class TemporalViewCompareArgs(BaseModel):
    frame_id_a: Optional[str] = Field(None, max_length=64,
                                      description="A 期帧 id（缺省=当前游标）")
    frame_id_b: str = Field(..., min_length=1, max_length=64, description="B 期帧 id")
    value_field: Optional[str] = Field(None, max_length=64,
                                       description="固定分级取值字段（缺省=不做固定分级）")
    k: int = Field(5, ge=2, le=9, description="等距分级段数")
    expected_revision: Optional[int] = Field(None, ge=0)


class TemporalViewUnbindArgs(BaseModel):
    expected_revision: Optional[int] = Field(None, ge=0)


def register_temporal_view_tools(registry: ToolRegistry) -> None:
    """注册时态视图控制工具族（Pi 与 legacy 同面）。"""

    @tool(
        registry,
        name="temporal_view_bind",
        capabilities=["temporal_bind"],
        description=(
            "把会话图层绑定为时序视图：探测时间字段/表示/粒度，从真实数据推导"
            "帧序列（绝不虚构期数），写入 MapSpec temporal 契约并启用前端时间轴。"
            "单期数据会被诚实拒绝（single_period_unsupported）。"
        ),
        tier=2,
        domains=["temporal", "map"],
        args_model=TemporalViewBindArgs,
        side_effect="state_mutation",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="medium",
        scale_class="medium",
        output_semantic_type="json",
        result_size_policy="inline_small",
        tags=("时序", "时间轴", "播放", "动态制图", "temporal", "bind"),
        failure_modes=("missing_data", "invalid_args", "single_period", "already_bound"),
    )
    async def temporal_view_bind(
        ref_id: str,
        layer_id: Optional[str] = None,
        time_field: Optional[str] = None,
        granularity: Optional[str] = None,
        timezone: Optional[str] = None,
        semantics: str = "event",
        expected_revision: Optional[int] = None,
        force: bool = False,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.temporal_products import service as tp

        try:
            return await tp.bind_temporal_view(
                session_id, ref_id, layer_id=layer_id, time_field=time_field,
                granularity=granularity, timezone=timezone, semantics=semantics,
                expected_revision=expected_revision, force=force,
            )
        except Exception as e:  # noqa: BLE001 — 工具面统一错误契约
            logger.error("[temporal_view_bind] failed: %s", e, exc_info=True)
            return {"status": "error", "code": "internal_error", "message": str(e)}

    @tool(
        registry,
        name="temporal_view_control",
        capabilities=["temporal_control"],
        description=(
            "控制时序视图播放：seek（定位帧）/step（±N 步）/play/pause/rate/"
            "unpin。帧 id 必须来自真实帧序列；用户手动固定游标（user_pinned）后"
            "Agent 的光标操作被拒绝（user-wins）。"
        ),
        tier=2,
        domains=["temporal", "map"],
        args_model=TemporalViewControlArgs,
        side_effect="state_mutation",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="medium",
        output_semantic_type="json",
        result_size_policy="inline_small",
        tags=("时序", "播放控制", "暂停", "seek", "temporal"),
        failure_modes=("not_bound", "unknown_frame", "user_pinned", "invalid_args"),
    )
    async def temporal_view_control(
        action: str,
        frame_id: Optional[str] = None,
        steps: Optional[int] = None,
        rate: Optional[float] = None,
        expected_revision: Optional[int] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.temporal_products import service as tp

        try:
            return await tp.control_temporal_view(
                session_id, action, frame_id=frame_id, steps=steps, rate=rate,
                expected_revision=expected_revision,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[temporal_view_control] failed: %s", e, exc_info=True)
            return {"status": "error", "code": "internal_error", "message": str(e)}

    @tool(
        registry,
        name="temporal_view_compare",
        capabilities=["temporal_compare"],
        description=(
            "两期对照：选择 A/B 两个真实帧，可选跨帧共享固定分级（shared "
            "breaks，union 窗等距）—— 颜色范围不随帧漂移，差异只来自数据。"
            "取值域退化时诚实披露并不固定分级。"
        ),
        tier=2,
        domains=["temporal", "map"],
        args_model=TemporalViewCompareArgs,
        side_effect="state_mutation",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="medium",
        scale_class="medium",
        output_semantic_type="json",
        result_size_policy="inline_small",
        tags=("时序", "两期对比", "固定分级", "change", "temporal"),
        failure_modes=("not_bound", "unknown_frame", "same_frame", "degenerate_range"),
    )
    async def temporal_view_compare(
        frame_id_a: Optional[str] = None,
        frame_id_b: str = "",
        value_field: Optional[str] = None,
        k: int = 5,
        expected_revision: Optional[int] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.temporal_products import service as tp

        try:
            return await tp.compare_temporal_view(
                session_id, frame_id_a, frame_id_b or None,
                value_field=value_field, k=k, expected_revision=expected_revision,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[temporal_view_compare] failed: %s", e, exc_info=True)
            return {"status": "error", "code": "internal_error", "message": str(e)}

    @tool(
        registry,
        name="temporal_view_unbind",
        capabilities=["temporal_bind"],
        description="解绑时序视图（移除 MapSpec temporal 分支；时间轴与帧过滤随之消失）。",
        tier=2,
        domains=["temporal", "map"],
        args_model=TemporalViewUnbindArgs,
        side_effect="state_mutation",
        deterministic=False,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="medium",
        output_semantic_type="json",
        result_size_policy="inline_small",
        tags=("时序", "解绑", "temporal"),
        failure_modes=("not_bound",),
    )
    async def temporal_view_unbind(
        expected_revision: Optional[int] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.temporal_products import service as tp

        try:
            return await tp.unbind_temporal_view(
                session_id, expected_revision=expected_revision)
        except Exception as e:  # noqa: BLE001
            logger.error("[temporal_view_unbind] failed: %s", e, exc_info=True)
            return {"status": "error", "code": "internal_error", "message": str(e)}

    @tool(
        registry,
        name="temporal_view_status",
        capabilities=["temporal_control"],
        description=(
            "读取时序视图状态：契约（帧序列/游标/播放/对照/质量旗标）+ 当前帧"
            "where 过滤与指纹（地图/图表/表格同窗锚点）。只读。"
        ),
        tier=2,
        domains=["temporal", "map"],
        side_effect="cacheable_read",
        deterministic=True,
        network=False,
        latency_class="fast",
        memory_class="light",
        scale_class="medium",
        output_semantic_type="json",
        result_size_policy="inline_small",
        tags=("时序", "状态", "temporal"),
        failure_modes=("not_bound",),
    )
    async def temporal_view_status(session_id: str = "") -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.temporal_products import service as tp

        try:
            return await tp.get_temporal_view(session_id)
        except Exception as e:  # noqa: BLE001
            logger.error("[temporal_view_status] failed: %s", e, exc_info=True)
            return {"status": "error", "code": "internal_error", "message": str(e)}
