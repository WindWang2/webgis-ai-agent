"""Lakehouse REST schemas — Spatial Lakehouse V6 (ADR-0118).

有界请求/响应模型（所有 session 域端点强制所有权守卫；响应直接暴露
服务层 dict 的诚实形态 —— 包括 durable 披露与剪枝证据）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class VectorScanRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    ref: str = Field(min_length=1, max_length=256)
    bbox: List[float] = Field(min_items=4, max_items=4)
    columns: Optional[List[str]] = None
    max_rows: int = Field(default=50_000, ge=1, le=200_000)


class CubeTimeSource(BaseModel):
    time: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=1024)


class CubeBuildRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="cube", max_length=256)
    window_side: Optional[int] = Field(default=None, ge=1, le=8192)
    time_sources: List[CubeTimeSource] = Field(min_items=1, max_items=512)


class CubeWindowRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    ref: str = Field(min_length=1, max_length=256)
    time: Optional[List[int]] = Field(default=None, min_items=2, max_items=2)
    y: Optional[List[int]] = Field(default=None, min_items=2, max_items=2)
    x: Optional[List[int]] = Field(default=None, min_items=2, max_items=2)


class CubeRevisionUpdate(BaseModel):
    band: str = Field(min_length=1, max_length=64)
    time_index: int = Field(ge=0)
    # 时间片源（与构建同语义：路径 / ref:fabric-parquet/*）
    source: str = Field(min_length=1, max_length=1024)


class CubeRevisionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    ref: str = Field(min_length=1, max_length=256)
    title: str = Field(default="cube revision", max_length=256)
    updates: List[CubeRevisionUpdate] = Field(min_items=1, max_items=64)


class ObjectVerifyRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=128)
    project_id: Optional[str] = Field(default=None, max_length=128)

# ── V7（ADR-0119）：遥感 cube / labeled 选择 / 发布 / catalog / GC ──────


class RSCubeSource(BaseModel):
    time: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=1024)
    role: str = Field(min_length=1, max_length=32)
    band: Optional[str] = Field(default=None, max_length=64)
    polarization: Optional[str] = Field(default=None, max_length=16)


class RSCubeBuildRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    title: str = Field(default="rs cube", max_length=256)
    sources: List[RSCubeSource] = Field(min_items=1, max_items=512)


class LabeledWindowRequest(BaseModel):
    """labeled 选择窗口读（标签 / bbox / 索引切片至少一种）。"""

    session_id: str = Field(min_length=1, max_length=128)
    ref: str = Field(min_length=1, max_length=256)
    time: Optional[List[str]] = Field(default=None, max_items=64)
    band: Optional[List[str]] = Field(default=None, max_items=64)
    polarization: Optional[List[str]] = Field(default=None, max_items=16)
    vertical: Optional[List[str]] = Field(default=None, max_items=64)
    bbox: Optional[List[float]] = Field(default=None, min_items=4, max_items=4)
    index_slices: Optional[Dict[str, List[int]]] = Field(
        default=None,
        description="dim -> [start, stop]（显式索引切片，优先于标签/bbox）",
    )
    max_cells: int = Field(default=8_000_000, ge=1, le=8_000_000)


class PublishRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    object_ids: List[str] = Field(min_items=1, max_items=200)
    tags: Optional[List[str]] = Field(default=None, max_items=32)


class RevokeRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    object_ids: List[str] = Field(min_items=1, max_items=200)


class CatalogSearchQuery(BaseModel):
    owner_type: str = Field(pattern="^(session|project)$")
    owner_id: str = Field(min_length=1, max_length=128)
    kind: Optional[str] = Field(default=None, max_length=32)
    bbox: Optional[List[float]] = Field(default=None, min_items=4, max_items=4)
    time_from: Optional[str] = Field(default=None, max_length=64)
    time_to: Optional[str] = Field(default=None, max_length=64)
    tags: Optional[List[str]] = Field(default=None, max_items=16)
    producer: Optional[str] = Field(default=None, max_length=64)
    include_revoked: bool = False
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ObjectScrubRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=128)
    project_id: Optional[str] = Field(default=None, max_length=128)
    mode: str = Field(default="sample", pattern="^(sample|full)$")
    sample_k: int = Field(default=8, ge=1, le=64)
    etag_check: bool = True


class GCPlanRequest(BaseModel):
    grace_hours: float = Field(default=72.0, ge=0.0, le=24 * 30)
    session_id: str = Field(min_length=1, max_length=128)


class GCExecuteRequest(BaseModel):
    plan: Dict[str, Any]
    session_id: str = Field(min_length=1, max_length=128)
