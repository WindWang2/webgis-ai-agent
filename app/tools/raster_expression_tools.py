"""G20 栅格表达 Pi 工具面（与 UI 共用 ``raster_authoring.service``）。

纪律：
- 工具只接受 ref / layer / 显式样式 / 少量点，不接受任意本地路径，也不让
  LLM 拼内部 tile URL；
- 结果只含修订、样式摘要、图例、输出引用与有界采样，不塞影像数组、
  直方图全桶或点样本全集；
- 写工具声明 ``state_mutation`` + ``map_mutations`` + ``required_context``，
  走既有 ToolDispatchService 的 owner/权限/写入/结果门禁；
- 不改 G12 的 Pi pin / RPC / extension / native surface。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.raster_authoring.errors import error_payload
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

_MAX_TOOL_BANDS = 6
_MAX_TOOL_POINTS = 8


class DescribeArgs(BaseModel):
    ref_id: str = Field(..., min_length=1, max_length=128,
                        description="已登记的 owner-scoped 数值栅格 ref id")
    histogram_bands: Optional[List[int]] = Field(
        None, max_length=6,
        description="只用于内部统计口径披露；回执不返回直方图桶")


class ApplyArgs(BaseModel):
    ref_id: str = Field(..., min_length=1, max_length=128)
    layer_id: str = Field(..., min_length=1, max_length=200,
                          description="目标 MapSpec 图层 id")
    expression: Dict[str, Any] = Field(
        ..., description="raster_expression.v1：mode/bands/stretch/colormap/"
                         "opacity/resampling/classification")
    expected_revision: int = Field(..., ge=0, description="MapSpec CAS 基线")
    mode: str = Field("commit", description="commit=权威提交；preview=零突变预览")


class ProbeArgs(BaseModel):
    ref_id: str = Field(..., min_length=1, max_length=128)
    points: List[Any] = Field(..., min_length=1, max_length=8,
                              description="[[x,y],...] 或 [{x,y,id}]；最多 8 点")
    crs: str = Field("EPSG:4326", max_length=64)
    bands: Optional[List[int]] = Field(None, max_length=8,
                                       description="最多 8 个 1-based 波段")
    data_revision: Optional[str] = Field(
        None, max_length=128, description="绑定表达时的数据修订；过期返回 stale")


class ExportArgs(BaseModel):
    expected_revision: Optional[int] = Field(
        None, ge=0, description="冻结的 MapSpec 修订；不匹配返回 conflict")
    format: str = Field("png", description="png=真实位图；svg=离线嵌入；pdf=显式拒绝")


def register_raster_expression_tools(registry: ToolRegistry) -> None:
    """注册 4 个 G20 工具（只读×2 + 受控写 + 成果输出）。"""
    from app.services.raster_authoring import (
        commit_expression,
        describe_raster,
        export_snapshot,
        probe_raster,
        validate_expression,
    )
    from app.services.raster_authoring.delivery import persist_snapshot_artifact
    from app.schemas.raster_spec import RasterExpressionError

    async def _guard(coro):
        try:
            return await coro
        except RasterExpressionError as exc:
            return error_payload(exc)
        except Exception:  # noqa: BLE001 - 工具错误契约
            logger.warning("[raster_expression] tool failed", exc_info=True)
            return {
                "success": False, "code": "RENDER_FAILED",
                "message": "raster expression operation failed",
                "correction_hint": "Check the ref id and MapSpec revision.",
            }

    @tool(
        registry,
        name="webgis_raster_expression_describe",
        domains=["raster", "cartography"],
        tier=2,
        args_model=DescribeArgs,
        description=(
            "只读查看已登记数值栅格的真实波段清单（1-based、dtype、可证明的"
            "单位/scale/offset）、有界统计口径与受支持的默认表达。波段角色没有"
            "可靠元数据时只称第 N 波段，绝不猜红/近红外/地表温度。"
            "\n何时用：用户问『这幅影像有哪些波段』『数值范围是多少』，或设置"
            "表达之前。\n何时不用：要点位原始值用 webgis_raster_expression_probe；"
            "要改颜色/波段用 webgis_raster_expression_apply。"
        ),
        side_effect="cacheable_read",
        # 内部会话数据源，不访问外部网络（registry 完整度闸要求显式声明）。
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="medium",
        scale_class="medium",
        tags=("栅格", "波段", "元数据", "raster bands"),
        output_semantic_type="text",
        result_size_policy="bounded",
        failure_modes=("invalid_args", "missing_data", "permission_denied"),
    )
    async def webgis_raster_expression_describe(
        ref_id: str,
        histogram_bands: Optional[List[int]] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(describe_raster(
            session_id, ref_id, histogram_bands=histogram_bands,
            trusted_dispatch=True,
        ))
        if not result.get("success"):
            return result
        bands = result.get("bands") or []
        return {
            "success": True,
            "ref_id": ref_id,
            "data_revision": result["data_revision"],
            "band_count": result["band_count"],
            "bands": [
                {k: b.get(k) for k in (
                    "index", "label", "description", "dtype", "unit", "scale",
                    "offset", "role", "vmin", "vmax",
                )}
                for b in bands[:_MAX_TOOL_BANDS]
            ],
            "bands_truncated": len(bands) > _MAX_TOOL_BANDS,
            "crs": result.get("crs"),
            "nodata": result.get("nodata"),
            "statistics_provenance": result.get("statistics"),
            "defaults": result.get("defaults"),
            "disclosures": result.get("disclosures"),
            "capabilities": result.get("capabilities"),
        }

    @tool(
        registry,
        name="webgis_raster_expression_apply",
        domains=["raster", "cartography", "mapspec"],
        tier=2,
        args_model=ApplyArgs,
        description=(
            "把单波段/RGB 组合、拉伸端点、色带、重采样、透明度提交到权威 MapSpec"
            "（与 UI 面板同一领域服务、同一次 CAS 提交语义）。回执含数据修订、"
            "表达版本 style_id、统计采样口径与图例；提交后真实瓦片像素随之改变，"
            "刷新/撤销可恢复。\n何时用：『把第 4/3/2 波段显示出来』『换色带』"
            "『用明确的拉伸范围』。\n何时不用：只要读点位值用 "
            "webgis_raster_expression_probe。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="medium",
        tags=("栅格", "表达", "MapSpec", "提交"),
        output_semantic_type="text",
        result_size_policy="bounded",
        required_context=("map_state",),
        map_mutations=("style_layer",),
        failure_modes=("invalid_args", "superseded", "missing_data", "validation_error"),
    )
    async def webgis_raster_expression_apply(
        ref_id: str,
        layer_id: str,
        expression: Dict[str, Any],
        expected_revision: int,
        mode: str = "commit",
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        if mode not in ("commit", "preview"):
            return error_payload(RasterExpressionError(
                "SCHEMA_VERSION_UNSUPPORTED", "mode must be commit|preview", field="mode",
            ))
        if mode == "preview":
            result = await _guard(validate_expression(
                session_id, ref_id, expression, layer_id=layer_id,
                trusted_dispatch=True,
            ))
        else:
            result = await _guard(commit_expression(
                session_id, ref_id, layer_id, expression,
                expected_revision=expected_revision,
                origin="agent", actor="pi:raster_expression_apply",
                trusted_dispatch=True,
            ))
        if not result.get("success"):
            return result
        return {
            "success": True,
            "committed": result["committed"],
            "state": result["state"],
            # 与 UI 不同：工具回执必须保持有界（不放完整 MapSpec 进上下文）。
            # live map 由 apply_gis_mutation 的既有 MapSpec 事件通道 reconcile，
            # mapspec_revision 供调用方核对世代。
            "reconcile_via": "mapspec_event",
            "layer_id": result["layer_id"],
            "mapspec_revision": result["mapspec_revision"],
            "data_revision": result["data_revision"],
            "expression_version": result["expression_version"],
            "style_id": result["style_id"],
            "statistics_provenance": result["statistics_provenance"],
            "expression": result["projection"] if "projection" in result else {
                "mode": result["expression"]["mode"],
                "bands": result["expression"]["bands"],
                "colormap": result["expression"]["colormap"],
                "resampling": result["expression"]["resampling"],
                "classification": result["expression"]["classification"],
            },
            "legend": result["legend"],
        }

    @tool(
        registry,
        name="webgis_raster_expression_probe",
        domains=["raster", "analysis"],
        tier=2,
        args_model=ProbeArgs,
        description=(
            "从源栅格直接读取被坐标命中的源像元原始值（窗口/像元读取，不从显示"
            " PNG 反推），带 CRS 转换、行列、nodata/mask、scale/offset 与单位；"
            "缩放后的插值值不存在于本工具，原值与 scaled 值分栏。"
            "\n何时用：『告诉我点击位置各波段的原始值和单位』。"
        ),
        side_effect="cacheable_read",
        # 内部会话数据源，不访问外部网络（registry 完整度闸要求显式声明）。
        network=False,
        deterministic=True,
        latency_class="medium",
        memory_class="light",
        scale_class="small",
        tags=("栅格", "像元", "检视", "pixel probe"),
        output_semantic_type="text",
        result_size_policy="bounded",
        failure_modes=("invalid_args", "no_crs", "outside_extent", "masked_pixel"),
    )
    async def webgis_raster_expression_probe(
        ref_id: str,
        points: List[Any],
        crs: str = "EPSG:4326",
        bands: Optional[List[int]] = None,
        data_revision: Optional[str] = None,
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        result = await _guard(probe_raster(
            session_id, ref_id, points[:_MAX_TOOL_POINTS],
            crs=crs, bands=(bands[:_MAX_TOOL_BANDS] if bands else None),
            expected_data_revision=data_revision,
            trusted_dispatch=True,
        ))
        if not result.get("success"):
            return result
        return {
            "success": True,
            "ref_id": ref_id,
            "data_revision": result["data_revision"],
            "method": result["method"],
            "interpolated": False,
            "statuses": [
                {
                    "id": p["id"], "status": p["status"],
                    "row": p.get("row"), "col": p.get("col"),
                    "values": p.get("values", []),
                }
                for p in result["points"]
            ],
            "truncated": result.get("truncated") or len(points) > _MAX_TOOL_POINTS,
        }

    @tool(
        registry,
        name="webgis_raster_expression_export",
        domains=["raster", "cartography"],
        tier=2,
        args_model=ExportArgs,
        description=(
            "把当前单幅地图的**一个冻结 MapSpec 修订**连同同一套栅格样式、图例与"
            "表达说明导出为真实可读 PNG（或离线可读 SVG，位图以 data URI 嵌入，"
            "不依赖过期 session URL）。导出期间继续改样式不会混用两个提交。"
            "PDF 明确拒绝而非静默丢层。"
        ),
        side_effect="state_mutation",
        network=False,
        deterministic=False,
        latency_class="slow",
        memory_class="medium",
        scale_class="medium",
        tags=("栅格", "导出", "单幅", "expression snapshot"),
        output_semantic_type="reference",
        result_size_policy="ref_offload",
        produced_refs=("artifact",),
        required_context=("map_state",),
        failure_modes=("invalid_args", "superseded", "unsupported_format"),
    )
    async def webgis_raster_expression_export(
        expected_revision: Optional[int] = None,
        format: str = "png",
        session_id: str = "",
    ) -> dict:
        if not session_id:
            return {"success": False, "code": "SOURCE_UNAVAILABLE",
                    "message": "Missing session_id context"}
        if format not in ("png", "svg", "pdf"):
            return error_payload(RasterExpressionError(
                "EXPORT_UNSUPPORTED", f"unsupported export format {format!r}", field="format",
            ))
        if format == "pdf":
            # 在渲染前拒绝，不做一遍注定失败的 2048² 快照计算。
            return error_payload(RasterExpressionError(
                "EXPORT_UNSUPPORTED",
                "PDF does not embed numeric raster snapshots; use png or svg",
                field="format",
            ))
        artifact = await _guard(export_snapshot(
            session_id, expected_revision=expected_revision, fmt=format,
            trusted_dispatch=True,
        ))
        if "frozen" not in artifact:  # 领域错误回执，不落盘
            return artifact
        delivered = await _guard(persist_snapshot_artifact(
            session_id, artifact, principal=f"session:{session_id}",
        ))
        if not delivered.get("success"):
            return delivered
        return {
            "success": True,
            "filename": delivered["filename"],
            "download_url": delivered["download_url"],
            "media_type": delivered["media_type"],
            "bytes": delivered["bytes"],
            "frozen_revision": delivered["frozen_revision"],
            "frozen_style_ids": delivered["frozen_style_ids"],
            "omitted_layers": delivered["omitted_layers"],
            "embedded": delivered["embedded"],
            "limits": delivered["limits"],
        }
