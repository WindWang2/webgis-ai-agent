"""图层数据子系统契约模型（V9 契约基石，ADR-0138）。

对应 `app/api/routes/layer.py` 的 JSON 端点。
二进制端点（PNG/MVT/零拷贝数据面）不走 response_model，理由见
tests/unit/api_contract/_contract_util.py EXCLUSIONS。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class LayerDescriptorResponse(BaseModel):
    """GET /layers/descriptor/{ref_id} —— 轻量图层元数据描述符。

    存储快路径与在线回退路径归一为同一形状（ref_id/feature_count/
    mvt_capable …）；证据面与 app/schemas/ref_descriptor.py 同源。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "ref_id": "ref:abc123",
                    "session_id": "sess-123",
                    "feature_count": 1200,
                    "point_count": 1200,
                    "geometry_types": ["Point"],
                    "bbox": [120.0, 30.0, 121.0, 31.5],
                    "mvt_capable": True,
                    "raster_capable": False,
                    "estimated_bytes": 204800,
                    "filterable_fields": ["name"],
                    "field_schema": {"name": {"type": "string"}},
                    "field_schema_complete": True,
                }
            ]
        }
    )

    ref_id: str
    session_id: str
    feature_count: int
    point_count: int
    geometry_types: list[str] = []
    bbox: Optional[list[float]] = None
    mvt_capable: bool = False
    raster_capable: bool = False
    estimated_bytes: Optional[int] = None
    filterable_fields: Optional[list[str]] = None
    field_schema: Optional[dict[str, Any]] = None
    field_schema_complete: bool = True
    crs: Optional[str] = None


class LayerTypesResponse(BaseModel):
    """GET /layer-types —— 支持的图层/分析类型元数据。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "layer_types": [
                        {
                            "type": "vector",
                            "description": "矢量图层",
                            "formats": ["shapefile", "geojson", "gpx", "kml"],
                        }
                    ],
                    "analysis_types": [{"type": "buffer", "description": "缓冲区分析"}],
                }
            ]
        }
    )

    layer_types: list[dict[str, Any]]
    analysis_types: list[dict[str, Any]]
