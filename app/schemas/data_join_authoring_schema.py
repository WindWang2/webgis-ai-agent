"""G38 数据关联工作台 HTTP 响应契约（ADR-0138 response_model 基线）。

逐字段镜像 ``app.services.data_join_authoring`` 各回执的运行时形状（计划
行视图 / run 视图 / 预览诊断 / 结果分页 / 渲染投影 / 恢复收敛）；自由子
文档（计划 JSON / 收据 / 证据卡）以 Dict[str, Any] 透传。所有模型
``extra="allow"``：未知字段原样透传，保证挂模前后响应体逐字节一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class DataJoinPlanView(BaseModel):
    """计划行视图（service._plan_row_to_view 形状）。"""

    model_config = ConfigDict(extra="allow")

    plan_id: str
    session_id: str
    project_id: Optional[str] = None
    title: str = ""
    status: str = "draft"
    revision: int = 1
    normalized_digest: str = ""
    left_fingerprint: Optional[str] = None
    right_fingerprint: Optional[str] = None
    preview: Optional[Dict[str, Any]] = None
    preview_digest: Optional[str] = None
    plan: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DataJoinDescribeSourceResponse(BaseModel):
    """源卡回执（授权快照 evidence 的 HTTP 投影）。"""

    model_config = ConfigDict(extra="allow")

    session_id: str
    source: Dict[str, Any] = Field(default_factory=dict)
    row_count: int = 0
    sample_fields: List[Dict[str, Any]] = Field(default_factory=list)


class DataJoinPlanListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    plans: List[DataJoinPlanView] = Field(default_factory=list)
    count: int = 0


class DataJoinPlanPreviewResponse(BaseModel):
    """预览回执（ready / blocked 双形态同模型；blocked_* 仅阻断时出现）。"""

    model_config = ConfigDict(extra="allow")

    plan: DataJoinPlanView
    status: str
    blocked_code: Optional[str] = None
    blocked: Optional[Dict[str, Any]] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    sample_rows: Optional[List[Dict[str, Any]]] = None
    sample_row_count: Optional[int] = None
    duration_ms: int = 0


class DataJoinRunView(BaseModel):
    """run 视图（service._run_view 形状 + 执行/检查附加键）。

    execute 的幂等复用短路只带 run_id/status/receipt/output_* 子集，故
    除 run_id/status 外全部可选。
    """

    model_config = ConfigDict(extra="allow")

    run_id: str
    status: str
    plan_id: Optional[str] = None
    session_id: Optional[str] = None
    plan_revision: Optional[int] = None
    plan_digest: Optional[str] = None
    job_id: Optional[int] = None
    error_code: Optional[str] = None
    receipt: Optional[Dict[str, Any]] = None
    output_ref: Optional[str] = None
    output_sha256: Optional[str] = None
    output_rows: Optional[int] = None
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    poll: Optional[str] = None
    idempotent_reuse: Optional[bool] = None
    content_invalidated: Optional[bool] = None


class DataJoinRunSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    plan_id: str
    status: str
    output_rows: Optional[int] = None
    error_code: Optional[str] = None
    created_at: Optional[str] = None


class DataJoinRunListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    runs: List[DataJoinRunSummary] = Field(default_factory=list)
    count: int = 0


class DataJoinResultRow(BaseModel):
    model_config = ConfigDict(extra="allow")

    properties: Dict[str, Any] = Field(default_factory=dict)
    geometry_summary: Optional[Dict[str, Any]] = None


class DataJoinRunResultsResponse(BaseModel):
    """结果分页（outputs.result_page 形状 + output_ref）。"""

    model_config = ConfigDict(extra="allow")

    run_id: str
    rows: List[DataJoinResultRow] = Field(default_factory=list)
    total_rows: int = 0
    cursor: int = 0
    next_cursor: Optional[int] = None
    page_size: int = 0
    crs: Optional[Union[str, Dict[str, Any]]] = None
    output_kind: str = ""
    output_ref: Optional[str] = None


class DataJoinRunCancelResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    status: Optional[str] = None
    note: Optional[str] = None
    job_id: Optional[int] = None
    cancel_requested: Optional[bool] = None


class DataJoinRenderRefResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    render_ref: str
    rows: int = 0


class DataJoinRecoverResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    completed: List[str] = Field(default_factory=list)
    failed: List[str] = Field(default_factory=list)
    skipped_active: int = 0
