"""G45 坐标操作域 — 路由/工具共用的 pydantic 契约（G39 raster_sampling 同款）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


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


# ── HTTP 响应契约（ADR-0138 response_model 基线）──────────────────────────
#
# 逐字段镜像 ``app.services.coordinate_operations`` 各回执的运行时形状
# （源卡 / CRS 描述 / 候选清单 / 计划视图 / 预览 / run 视图 / 恢复收敛）；
# 自由子文档（CRS 描述、候选 listing、几何画像、收据）以 Dict[str, Any]
# 透传。所有响应模型 ``extra="allow"``：未知字段原样透传，保证挂模前后
# 响应体逐字节一致。


class CoordinateOpsDescribeSourceResponse(BaseModel):
    """源卡 + 权威 CRS 描述（freeze.resolve_eligible_source 投影）。"""

    model_config = ConfigDict(extra="allow")

    kind: str = ""
    label: str = ""
    row_count: int = 0
    content_fingerprint: str = ""
    content_revision: Optional[int] = None
    artifact_revision_no: Optional[int] = None
    crs: str = ""
    crs_evidence: Dict[str, Any] = Field(default_factory=dict)
    geometry: Optional[Dict[str, Any]] = None
    fields: List[Dict[str, Any]] = Field(default_factory=list)
    completeness: Dict[str, Any] = Field(default_factory=dict)
    crs_description: Dict[str, Any] = Field(default_factory=dict)
    units: Dict[str, Any] = Field(default_factory=dict)


class CoordinateOpsCrsLookupResponse(BaseModel):
    """目标 CRS 查询/描述（service.lookup_crs 形状）。"""

    model_config = ConfigDict(extra="allow")

    input: str = ""
    identity: str = ""
    name: str = ""
    type_name: str = ""
    axis_official: Any = None
    carrier_convention: Any = None
    dimension_supported: bool = True
    dimension_unsupported_reason: Optional[str] = None
    area_of_use: Optional[Dict[str, Any]] = None
    units: Dict[str, Any] = Field(default_factory=dict)
    canonical_sha256: str = ""


class CoordinateOpsListOperationsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: Dict[str, Any] = Field(default_factory=dict)
    target: Dict[str, Any] = Field(default_factory=dict)
    listing: Any = None


class CoordinateOperationPlanView(BaseModel):
    """计划行视图（service._plan_view 形状）。"""

    model_config = ConfigDict(extra="allow")

    plan_id: str
    session_id: str
    project_id: Optional[str] = None
    title: str = ""
    status: str = "draft"
    revision: int = 1
    plan: Dict[str, Any] = Field(default_factory=dict)
    normalized_digest: str = ""
    source_fingerprint: Optional[str] = None
    selected_candidate_id: Optional[str] = None
    operation: Optional[Dict[str, Any]] = None
    preview: Optional[Dict[str, Any]] = None
    preview_digest: Optional[str] = None
    preview_stale: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CoordinateOperationPlanListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    plans: List[CoordinateOperationPlanView] = Field(default_factory=list)


class CoordinateOperationPlanArchiveResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    plan_id: str
    status: str
    revision: int


class CoordinateOperationPlanPreviewResponse(BaseModel):
    """有界预览回执（service.preview_plan 形状）。"""

    model_config = ConfigDict(extra="allow")

    plan_id: str
    plan_revision: int
    plan_digest: str = ""
    sample_rule: str = ""
    sample_rows_requested: int = 0
    sample_rows_transformed: int = 0
    operation: Optional[Dict[str, Any]] = None
    coordinate_pairs: List[Dict[str, Any]] = Field(default_factory=list)
    source_extent: Optional[List[float]] = None
    target_extent: Optional[List[float]] = None
    findings: Dict[str, Any] = Field(default_factory=dict)
    area_of_use_check: str = ""
    accuracy_note: str = ""
    preview_is_not_full_execution: bool = True


class CoordinateOperationRunView(BaseModel):
    """run 视图（service._run_view 形状 + 执行附加键）。

    execute 的幂等复用短路只带 run_id/status/receipt/output_* 子集，故
    除 run_id/status 外全部可选。
    """

    model_config = ConfigDict(extra="allow")

    run_id: str
    status: str
    plan_id: Optional[str] = None
    job_id: Optional[int] = None
    error_code: Optional[str] = None
    output_ref: Optional[str] = None
    output_rows: Optional[int] = None
    receipt: Optional[Dict[str, Any]] = None
    plan_revision: Optional[int] = None
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    poll: Optional[str] = None
    idempotent_reuse: Optional[bool] = None


class CoordinateOperationRunListItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    plan_id: str
    status: str
    job_id: Optional[int] = None
    error_code: Optional[str] = None
    output_ref: Optional[str] = None
    output_rows: Optional[int] = None
    created_at: Optional[str] = None
    receipt_summary: Optional[Dict[str, Any]] = None


class CoordinateOperationRunListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    runs: List[CoordinateOperationRunListItem] = Field(default_factory=list)


class CoordinateOperationRunResultsResponse(BaseModel):
    """结果分页（outputs.result_page 形状）。"""

    model_config = ConfigDict(extra="allow")

    run_id: str
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    total_rows: int = 0
    cursor: int = 0
    next_cursor: Optional[int] = None
    page_size: int = 0
    crs: Optional[Union[str, Dict[str, Any]]] = None
    output_kind: str = ""


class CoordinateOperationRunCancelResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    status: str
    already_terminal: Optional[bool] = None


class CoordinateOperationDisplayRefResponse(BaseModel):
    """WGS84 显示副本重铸回执（_mint_display_copy 投影）。"""

    model_config = ConfigDict(extra="allow")

    state: str
    ref: Optional[str] = None
    rows: Optional[int] = None
    reason: Optional[str] = None


class CoordinateOperationRecoverResponse(BaseModel):
    """孤儿收敛回执（service.recover 形状）。"""

    model_config = ConfigDict(extra="allow")

    converged: List[Dict[str, Any]] = Field(default_factory=list)
