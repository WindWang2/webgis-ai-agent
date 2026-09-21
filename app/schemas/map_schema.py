"""地图制图/导出子系统契约模型（V9 契约基石，ADR-0138）。

从 `app/api/routes/map.py` 内联迁出；路由文件只 import。
二进制下发端点（GET /export/download/{filename}）不走 response_model，
理由见 tests/unit/api_contract/_contract_util.py EXCLUSIONS。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class VectorPdfRequest(BaseModel):
    """POST /export/vector-pdf 请求体（V6，ADR-0120 W8：publication 矢量 PDF）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "mapspec": {"version": 2, "components": []},
                    "title": "流域专题图",
                    "target_dpi": 300,
                }
            ]
        }
    )

    mapspec: dict
    title: Optional[str] = None
    """PDF 文档元数据标题（图面标题由 spec 标题组件驱动 —— user-wins）。"""
    target_dpi: Optional[int] = None
    """渲染 DPI（V7 可选；72-600，越界钳制，生效值随响应披露；缺省 300
    = 既有输出不变）。"""
    session_id: Optional[str] = None
    """ADR-0211：导出血缘/回执记录目标会话（可选）。**绝不参与 ref 载体源
    水合** —— 源内联安全决策不变；会话属主校验失败 → 仅跳过 lineage。"""

    # 安全决策（R2-M3/M8）：不提供 sessionId 水合 —— ref 载体源由调用方
    # 内联后提交（前端 exporter 内存中已持有数据）；未内联 → 400 typed 拒绝。


class GeoJSONExportRequest(BaseModel):
    """POST /export/geojson 请求体。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "geojson": {
                        "type": "FeatureCollection",
                        "features": [],
                    },
                    "filename": "aoi_export",
                }
            ]
        }
    )

    geojson: Any
    filename: str = "export"


class ExportLineageInfo(BaseModel):
    """ADR-0211：导出血缘记录摘要（additive；缺席 = 未记录，如无 session）。"""

    ref: str
    artifact_recorded: bool = False
    receipt_recorded: bool = False
    format: str = ""


class MapExportResponse(BaseModel):
    """POST /export 返回（Canvas 合成图持久化结果）。"""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "success": True,
                    "filename": "map_export_1757548800_ab12cd34ef56.png",
                    "url": "/api/v1/export/download/map_export_1757548800_ab12cd34ef56.png",
                    "message": "地图制品已成功保存",
                }
            ]
        },
    )

    success: bool
    filename: str
    url: str
    message: str
    render_diagnostics: Optional[dict[str, Any]] = None
    lineage: Optional[ExportLineageInfo] = None
    """ADR-0211：导出成品血缘（ref:export/*）与回执落章摘要。"""


class ExportDiagnosticsResponse(BaseModel):
    """GET /export/diagnostics/{filename} 返回（sidecar 内容透传）。"""

    model_config = ConfigDict(extra="allow")

    success: bool
    filename: str
    diagnostics: Optional[list[Any]] = None


class VectorPdfExportResponse(BaseModel):
    """POST /export/vector-pdf 返回。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "success": True,
                    "filename": "map_vector_1757548800_ab12cd34ef56.pdf",
                    "url": "/api/v1/export/download/map_vector_1757548800_ab12cd34ef56.pdf",
                    "format": "pdf",
                    "vector": True,
                    "pages": 1,
                    "frames_rendered": 1,
                    "frames_skipped": 0,
                    "target_dpi": 300,
                    "render_diagnostics": [],
                    "schema_disclosures": [],
                    "message": "矢量 PDF 已生成（文本可选中检索）",
                }
            ]
        }
    )

    success: bool
    filename: str
    url: str
    format: str
    vector: bool
    pages: int
    frames_rendered: int
    frames_skipped: int
    target_dpi: Optional[int] = None
    render_diagnostics: Optional[list[Any]] = None
    schema_disclosures: Optional[list[Any]] = None
    message: str
    lineage: Optional[ExportLineageInfo] = None
    """ADR-0211：导出成品血缘（ref:export/*）与回执落章摘要（additive）。"""


class PdfExportResponse(BaseModel):
    """POST /export/pdf 返回（reportlab 栅格 PDF）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "success": True,
                    "filename": "map_export_1757548800_ab12cd34ef56.pdf",
                    "url": "/api/v1/export/download/map_export_1757548800_ab12cd34ef56.pdf",
                    "format": "pdf",
                    "message": "专题底图 PDF 已成功生成",
                }
            ]
        }
    )

    success: bool
    filename: str
    url: str
    format: str
    message: str


class GeoJSONExportResponse(BaseModel):
    """POST /export/geojson 返回。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "filename": "aoi_export_ab12cd34ef56.geojson",
                    "url": "/api/v1/export/download/aoi_export_ab12cd34ef56.geojson",
                    "format": "geojson",
                    "message": "GeoJSON 导出成功 (2048 bytes)",
                }
            ]
        }
    )

    filename: str = Field(...)
    url: str
    format: str
    message: str
