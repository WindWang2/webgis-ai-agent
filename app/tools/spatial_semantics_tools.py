"""空间语义只读工具面（GOAL 01 · M4 Pi 发现层）。

``inspect_spatial_semantics``：FC/ref → typed SpatialSemanticContract 投影
（tier 2、确定性、只读）。Agent 在执行度量敏感工具前可先问"这份数据在
哪个 CRS、什么单位、什么口径"—— 判定可解释、可验证、可回退。

注册为独立模块（app/tools/__init__.py 单行追加），不触碰既有工具文件。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class InspectSpatialSemanticsArgs(BaseModel):
    geojson: Any = Field(
        ..., description="输入 GeoJSON FeatureCollection 或数据引用(ref:xxx)")
    scope_bbox: Optional[list] = Field(
        None, description="可选：请求范围 [minx,miny,maxx,maxy]（AOI 对账用）")
    scope_crs: str = Field(
        "EPSG:4326", max_length=32,
        description="scope_bbox 的 CRS（默认 EPSG:4326）")
    scope_name: str = Field(
        "", max_length=64, description="可选：范围名（如 成都）")


def register_spatial_semantics_tools(registry: ToolRegistry) -> None:
    """注册空间语义只读工具（tier 2：分析/数据语境按需载入）。"""

    @tool(
        registry,
        tier=2, domains=["analysis", "dataset"], name="inspect_spatial_semantics",
        capabilities=["spatial_semantics"],
        description=(
            "空间语义契约检视（只读、确定性）：输入数据 → typed 判定"
            "『这份数据的 CRS 是什么、怎么来的（declared/rfc7946_default/assumed/"
            "unknown）、坐标结构是否度样、轴序是否疑似倒置、该用什么计算空间"
            "（UTM 单带/极方位/测地）、尺度与精度如何』。"
            "\n何时用：执行 buffer/spatial_aggregate/spatial_join/zonal_stats 等"
            "度量敏感工具前；对『坐标系不明/单位可疑』数据先检视再决策；"
            "分析结论的 CRS/单位口径需要证据链时。"
            "\n返回：spatial_semantics 块（source_crs/crs_source/crs_class/"
            "axis_order/calculation_mode/working_crs/scale_level/reason_codes）——"
            "绝不猜测：未声明的非度样数据如实标 unknown + declare_crs 建议。"
            "\n何时不适用：改数据（用 data_preparation 工具面）；坐标转换执行"
            "（用 transform_coordinates/reproject_coordinates）。"
        ),
        args_model=InspectSpatialSemanticsArgs,
        side_effect="deterministic_compute",
        network=False,
        deterministic=True,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("空间语义", "CRS", "坐标系", "单位", "preflight", "契约", "crs"),
        output_semantic_type="stats",
        result_size_policy="inline_small",
        crs_semantics="inspect_only",
        unit_semantics="report_only",
        failure_modes=("invalid_args", "missing_data"),
        examples=(
            "这份学校点位的坐标系是什么、能直接做 500 米缓冲吗",
            "检查该图层数据的 CRS/轴序/尺度",
        ),
        anti_examples=(
            "把 BD-09 转 WGS84 —— 那是坐标转换，用 transform_coordinates",
        ),
        summary=(
            "空间语义契约检视（只读）：CRS 来源四态/坐标结构/轴序嫌疑/计算空间"
            "推荐（UTM/极区/测地）/尺度精度/中国偏移帧风险，typed reason codes。"
            "度量敏感工具执行前的第一步；未知不猜。"
        ),
    )
    async def inspect_spatial_semantics(
        geojson: Any,
        scope_bbox: Optional[list] = None,
        scope_crs: str = "EPSG:4326",
        scope_name: str = "",
    ) -> dict:
        from app.lib.geo_processor.core import safe_parse as safe_parse_geojson
        from app.services.gis_harness.spatial_semantics import (
            ScopeEvidence,
            SpatialOperationSpec,
            preflight_for_feature_collection,
        )

        data = safe_parse_geojson(geojson)
        if not isinstance(data, dict):
            raise ValueError(
                "invalid GeoJSON input: could not parse a FeatureCollection")
        scope = None
        if scope_bbox and len(scope_bbox) == 4:
            scope = ScopeEvidence(
                name=scope_name, bbox=[float(v) for v in scope_bbox],
                bbox_crs=scope_crs or "EPSG:4326", source="user")
        result = preflight_for_feature_collection(
            data, scope=scope,
            spec=SpatialOperationSpec(operation="generic"))
        block = result.disclosure()
        block["contract_version"] = 1
        block["dataset"] = {
            "feature_count": result.contract.feature_count,
            "geometry_type": result.contract.geometry_type,
            "longitude_convention": result.contract.longitude_convention,
        }
        return {
            "success": True,
            "summary": (
                f"CRS={block.get('source_crs') or '未知'}"
                f"（{block.get('crs_source')}）/ 计算={block.get('calculation_mode')}"
                f" {block.get('working_crs') or ''}".strip()),
            "spatial_semantics": block,
        }
