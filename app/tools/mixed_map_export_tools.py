"""G26 混合地图导出工具（webgis_map_export）—— UI/Agent 统一编译路径的
Pi 面。

冻结会话权威 MapSpec 修订 → 矢量 + 数值栅格 + 标注 + 图例/整饰的完整地图
（PNG/SVG/PDF）。与 raster-only 的 ``webgis_raster_expression_export`` 并存
（专用快照不迁移）；「完整地图」语义由 blocking/allow_partial 显式选择。

纪律：dispatcher 注入 session_id（模型不可指定他人会话）；owner 校验在
resolve_source 层执行（trusted_dispatch 服务端内部读 + scoped path 三重
校验）；refs-only 回执（下载 URL + manifest 摘要，不携带图像载荷）。
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


class MapExportArgs(BaseModel):
    """webgis_map_export 参数模型（future annotations + 显式 args_model）。"""

    format: str = Field(
        "pdf", pattern="^(png|svg|pdf)$",
        description="导出格式：pdf=出版链（多帧/图册能力）；svg=自包含离线矢量"
        "（栅格以 data URI 内嵌）；png=服务端栅格化（依赖宿主 rsvg 能力）",
    )
    expected_revision: Optional[int] = Field(
        None, ge=0,
        description="CAS 基线（可选）：与当前 MapSpec 修订不符 → superseded 拒绝",
    )
    partial_policy: str = Field(
        "blocking", pattern="^(blocking|allow_partial)$",
        description="blocking（默认）：会遗漏图层时拒绝（完整地图不放宽）；"
        "allow_partial：显式选择并返回完整 omitted_layers 清单",
    )
    target_dpi: Optional[int] = Field(
        None, ge=72, le=600,
        description="PDF 渲染 DPI（72-600；生效值随 manifest 披露；缺省 300）",
    )
    session_id: str = Field(default="", description="由 dispatcher 注入的会话 id")


def _error_payload(exc) -> dict:
    return {
        "success": False,
        "code": exc.code,
        "message": exc.message,
        **({"details": exc.details} if getattr(exc, "details", None) else {}),
        "correction_hint": (
            "Re-check the frozen revision (mapspec revision may have moved), "
            "choose partial_policy='allow_partial' for an explicit omission "
            "manifest, or use png/svg when the PDF engine is unavailable."
        ) if exc.code in ("CAS_SUPERSEDED", "EXPORT_BLOCKED_INCOMPLETE",
                          "VECTOR_PDF_UNAVAILABLE", "VECTOR_PDF_BUSY") else "",
    }


def register_mixed_map_export_tools(registry: ToolRegistry) -> None:
    """注册 webgis_map_export（G26 统一混合地图导出）。"""

    @tool(
        registry,
        name="webgis_map_export",
        domains=["cartography", "raster"],
        tier=2,
        args_model=MapExportArgs,
        description=(
            "把当前会话**冻结的一个 MapSpec 修订**导出为完整混合地图"
            "（矢量边界 + 数值栅格 + 标注 + 图例/标题/比例尺统一编译）："
            "pdf=出版链（真实图像对象 + 可提取文本，支持多帧/产品页），"
            "svg=自包含离线矢量（栅格 data URI 内嵌，不依赖 session），"
            "png=服务端栅格化。"
            "\n何时用：用户要「导出/出版整张地图」且图上有数值栅格层"
            "（GeoTIFF 表达）；raster-only 快照请继续用 "
            "webgis_raster_expression_export。"
            "\n关键语义：栅格按冻结表达地理对齐嵌入（不居中贴图）；"
            "默认 blocking —— 有层无法呈现时拒绝而非报成功；"
            "expected_revision 可做 CAS 防并发混修订。"
            "\n不做：外部底图瓦片不嵌入（许可不明 → 需 allow_partial 并披露）；"
            "GPKG/GeoJSON 数据交付用数据成果工具。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="medium",
        tags=("导出", "出版", "混合地图", "矢量+栅格"),
        output_semantic_type="reference",
        result_size_policy="ref_offload",
        produced_refs=("artifact",),
        required_context=("map_state",),
        failure_modes=(
            "invalid_args", "superseded", "unsupported_format",
            "blocked_incomplete", "engine_unavailable",
        ),
    )
    async def webgis_map_export(
        format: str = "pdf",
        expected_revision: Optional[int] = None,
        partial_policy: str = "blocking",
        target_dpi: Optional[int] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        from app.services.mixed_map_export import (
            MixedMapExportError,
            export_mixed_map,
            persist_mixed_export,
        )

        try:
            artifact = await export_mixed_map(
                session_id,
                fmt=format,
                expected_revision=expected_revision,
                trusted_dispatch=True,
                target_dpi=target_dpi if target_dpi is not None else 300,
                partial_policy=partial_policy,
            )
        except MixedMapExportError as exc:
            return _error_payload(exc)
        delivered = await persist_mixed_export(
            session_id, artifact, principal=f"session:{session_id}")
        if not delivered.get("success"):
            return delivered
        manifest = delivered["manifest"]
        return {
            "success": True,
            "filename": delivered["filename"],
            "download_url": delivered["download_url"],
            "manifest_url": delivered["manifest_url"],
            "format": format,
            "media_type": delivered["media_type"],
            "bytes": delivered["bytes"],
            "mapspec_revision": manifest.get("mapspec_revision"),
            "partial": manifest.get("partial", False),
            "omitted_layers": manifest.get("omitted_layers", []),
            "raster_layers": [
                {k: p.get(k) for k in ("layer_id", "style_id", "data_revision",
                                       "overlap", "embedded", "width", "height")}
                for p in (manifest.get("raster_layers") or [])[:12]
            ],
            "renderer": manifest.get("renderer", {}),
            "checksum": manifest.get("checksum"),
        }
