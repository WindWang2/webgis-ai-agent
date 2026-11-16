"""G45 坐标操作域 — 路由/工具共用的 pydantic 契约（G39 raster_sampling 同款）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SourceSpec(BaseModel):
    """源锚（G18 DeliverySourceRef 的子集；授权解析复用同一入口）。"""

    kind: str = Field(description="session_ref | fabric_parquet | project_artifact")
    session_id: Optional[str] = None
    ref: Optional[str] = None
    project_id: Optional[str] = None
    artifact_id: Optional[str] = None
    revision_no: Optional[int] = None
    table_ref: Optional[str] = None

    model_config = {"extra": "forbid"}


class AoiSpec(BaseModel):
    """单一连续 bbox（度；west<east 等语义校验在 candidates.normalize_aoi）。"""

    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)

    model_config = {"extra": "forbid"}


class CoordinateOperationPlanInput(BaseModel):
    """计划输入（作者态；锁定候选经独立 lock 路径，不在此处）。"""

    title: str = Field(default="", max_length=200)
    source: SourceSpec
    target_crs: str = Field(min_length=2, max_length=256)
    aoi: Optional[AoiSpec] = None
    #: none | explicit | source_bounds_approximate（近似 AOI 不冒充全体域内证明）
    aoi_derivation: str = Field(default="none", pattern=
                                "^(none|explicit|source_bounds_approximate)$")
    #: preserve_unconverted（Z 原值保留 + 回执明示）| reject（含 Z 即 typed 拒绝）
    z_policy: str = Field(
        default="preserve_unconverted",
        pattern="^(preserve_unconverted|reject)$")
    #: keep（空几何保留 + 独立计数）—— 目前唯一政策，显式声明
    empty_geometry_policy: str = Field(default="keep", pattern="^keep$")
    #: report（唯一支持值）：域外发现经预览 findings 与执行非有限
    #: all-or-nothing 如实披露 —— fail 政策未实现，契约不承诺（评审 P2-3）。
    out_of_area_policy: str = Field(default="report", pattern="^report$")
    project_id: Optional[str] = None

    model_config = {"extra": "forbid"}


class DescribeSourceBody(BaseModel):
    source: SourceSpec


class ListOperationsBody(BaseModel):
    source: SourceSpec
    target_crs: str = Field(min_length=2, max_length=256)
    aoi: Optional[AoiSpec] = None


class PlanBody(BaseModel):
    plan: CoordinateOperationPlanInput


class UpdatePlanBody(PlanBody):
    expected_revision: int = Field(ge=1)


class LockBody(BaseModel):
    candidate_id: str = Field(min_length=8, max_length=64)
    expected_revision: int = Field(ge=1)


class PreviewBody(BaseModel):
    expected_revision: int = Field(ge=1)
    sample_rows: int = Field(default=25, ge=1, le=50)


class ExecuteBody(BaseModel):
    expected_revision: int = Field(ge=1)
    background: bool = True
