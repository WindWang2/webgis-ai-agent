"""Lakehouse REST schemas — Spatial Lakehouse V6 (ADR-0118).

有界请求/响应模型（所有 session 域端点强制所有权守卫；响应直接暴露
服务层 dict 的诚实形态 —— 包括 durable 披露与剪枝证据）。
"""
from __future__ import annotations

from typing import List, Optional

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


class ObjectVerifyRequest(BaseModel):
    session_id: Optional[str] = Field(default=None, max_length=128)
    project_id: Optional[str] = Field(default=None, max_length=128)
