"""本地地理数据子系统契约模型（V9 契约基石，ADR-0138）。

对应 `app/api/routes/local_data.py`。返回体是「GeoJSON FeatureCollection
或错误披露」双形态，模型用 extra="allow" 保持透传不裁剪，已知键显式声明。
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class _ErrorDisclosure(BaseModel):
    """工具语义错误（HTTP 200 承载的 soft error，与 HTTPException 区分）。"""

    error: str
    correction_hint: Optional[str] = None


class AdminBoundaryResponse(BaseModel):
    """GET /admin/{level}/boundary —— FeatureCollection 或错误披露。"""

    model_config = ConfigDict(extra="allow")

    error: Optional[str] = None
    correction_hint: Optional[str] = None
    type: Optional[str] = None
    features: Optional[list[dict[str, Any]]] = None
    count: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None


class AdminChildrenResponse(BaseModel):
    """GET /admin/children —— FeatureCollection 或错误披露。"""

    model_config = ConfigDict(extra="allow")

    error: Optional[str] = None
    correction_hint: Optional[str] = None
    type: Optional[str] = None
    features: Optional[list[dict[str, Any]]] = None
    count: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None


class OsmCatalogRow(BaseModel):
    """OSM 主题目录行。"""

    model_config = ConfigDict(extra="allow")

    theme: str
    description: Optional[str] = None


class OsmCatalogResponse(BaseModel):
    """GET /osm/catalog —— 已 ingest 主题目录。"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "themes": [{"theme": "roads", "description": "道路网"}],
                    "note": "查询用 query_local_osm(theme, bbox, ...)。",
                }
            ]
        },
        extra="allow",
    )

    themes: list[dict[str, Any]] = []
    note: Optional[str] = None


class OsmFeaturesResponse(BaseModel):
    """GET /osm/features —— FeatureCollection（含 count/bbox）或错误披露。"""

    model_config = ConfigDict(extra="allow")

    error: Optional[str] = None
    correction_hint: Optional[str] = None
    type: Optional[str] = None
    features: Optional[list[dict[str, Any]]] = None
    count: Optional[int] = None
    bbox: Optional[list[float]] = None
    truncated: Optional[bool] = None
    note: Optional[str] = None
