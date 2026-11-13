"""G27 尺度表达策略工具面 —— Agent 可发现 / 可执行 / 可审计。

- ``webgis_inspect_scale_portrayal``（只读）：读取**真实 effective policy**
  （与 UI 同一解析结果，非 stub）、有界解释、typed 诊断与字段证据；
- ``webgis_apply_scale_portrayal``（受控写）：preview 零突变 / commit 走
  既有 ``UpsertLayerIntent`` + ``apply_gis_mutation`` CAS 单事务与 undo 栈，
  只改指定 layer，不全图重置。

不注册 ``webgis_execute``、不升级 Pi vendor；工具经既有 ToolRegistry
发现/tier/session 守卫进入，返回 refs-only 紧凑回执。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class InspectArgs(BaseModel):
    layer: str = Field(..., max_length=128,
                       description="目标图层 id（或唯一后缀）")
    observed_zoom: Optional[float] = Field(
        None, ge=0, le=24,
        description="浏览器当前 zoom（observed，只读；不写 desired state）")
    crs: str = Field("EPSG:3857", max_length=32,
                     description="导出/物理比例尺 CRS；非 Web Mercator 不套固定公式")
    target_dpi: float = Field(
        96.0, ge=1, le=600,
        description="输出 DPI —— 只影响采样与尺寸，不改变可见层裁决")


class ApplyArgs(BaseModel):
    layer: str = Field(..., max_length=128, description="目标图层 id")
    policy: Optional[Dict[str, Any]] = Field(
        None,
        description="完整 scale policy 投影（version/mode/可选 pin/字段引用）；"
                    "None = 恢复自动（移除该层策略）")
    mode: Literal["preview", "commit"] = Field(
        "preview", description="preview=零突变预览；commit=CAS 落库")
    expected_revision: Optional[int] = Field(
        None, ge=0, description="CAS 基线；并发修改会使操作 superseded")
    operation_id: Optional[str] = Field(
        None, max_length=64, description="幂等键；重试传同 id")
    note: Optional[str] = Field(None, max_length=160, description="归因摘要")


def register_scale_portrayal_tools(registry: ToolRegistry) -> None:
    """注册尺度表达策略两工具（只读×1 + 受控写×1）。"""

    @tool(
        registry,
        name="webgis_inspect_scale_portrayal",
        capabilities=["cartography_scale_portrayal_inspect"],
        domains=["mapspec", "cartography"],
        tier=2,
        args_model=InspectArgs,
        description=(
            "只读检查一个图层当前有效的多尺度表达策略：effective scale、"
            "policy version、当前档位、隐藏原因、用户覆盖、typed 诊断、"
            "显示策略披露与 source revision。"
            "\n何时用：用户问『缩小后为什么有些标注/符号不见了』『当前按什么尺度显示』，"
            "或 apply 前确认策略与字段证据。"
            "\n何时不用：要改表达用 webgis_apply_scale_portrayal；要看 GOAL 04 "
            "综合档案用 webgis_inspect_map_scale_profile。"
        ),
        side_effect="cacheable_read",
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("多尺度", "尺度表达", "可解释", "scale portrayal"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=("invalid_args", "target_not_found", "no_mapspec"),
    )
    async def webgis_inspect_scale_portrayal(
        layer: str,
        observed_zoom: Optional[float] = None,
        crs: str = "EPSG:3857",
        target_dpi: float = 96.0,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.scale_portrayal.service import (
            ScalePortrayalError,
            inspect_scale_portrayal,
        )
        try:
            return await inspect_scale_portrayal(
                session_id, layer,
                observed_zoom=observed_zoom, crs=crs, target_dpi=target_dpi)
        except ScalePortrayalError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - 工具错误契约
            logger.warning("[inspect_scale_portrayal] failed", exc_info=True)
            return {"error": f"inspect failed: {exc}"}

    @tool(
        registry,
        name="webgis_apply_scale_portrayal",
        capabilities=["cartography_scale_portrayal_apply"],
        domains=["mapspec", "cartography"],
        tier=2,
        args_model=ApplyArgs,
        description=(
            "按尺度调整指定图层的表达策略：启用自动 LOD（标注密度/字号随缩放分档）、"
            "固定密度（人工锁 topRatio）、恢复自动，或提供具体 policy 投影。"
            "preview 零突变预览；commit 经 CAS 单事务落 MapSpec 并可 undo。"
            "只修改指定 layer，不改数据、不改分析结果/统计 count、不动用户隐藏与 pin。"
            "\n何时不用：不要用它做全图改色或重新分类；'更清楚一点' 必须先读 "
            "webgis_inspect_scale_portrayal 再提出具体层/具体策略变化。"
        ),
        side_effect="state_mutation",
        deterministic=False,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=("多尺度", "尺度表达", "局部覆盖", "CAS"),
        output_semantic_type="text",
        result_size_policy="inline_small",
        failure_modes=(
            "invalid_args", "target_not_found", "no_mapspec",
            "unknown_policy", "superseded",
        ),
    )
    async def webgis_apply_scale_portrayal(
        layer: str,
        policy: Optional[Dict[str, Any]] = None,
        mode: str = "preview",
        expected_revision: Optional[int] = None,
        operation_id: Optional[str] = None,
        note: Optional[str] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"error": "Missing session_id context"}
        from app.services.gis_harness.scale_portrayal.service import (
            ScalePortrayalError,
            apply_scale_portrayal,
        )
        try:
            return await apply_scale_portrayal(
                session_id, layer,
                policy=policy,
                mode=mode,
                expected_revision=expected_revision,
                operation_id=operation_id,
                note=note or "",
                origin="agent",
                actor="webgis_apply_scale_portrayal",
            )
        except ScalePortrayalError as exc:
            return {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - 工具错误契约
            logger.warning("[apply_scale_portrayal] failed", exc_info=True)
            return {"error": f"apply failed: {exc}"}
