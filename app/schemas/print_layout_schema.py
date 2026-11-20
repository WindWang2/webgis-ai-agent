"""G34 纸面版式工作台 HTTP 响应契约（ADR-0138 response_model 基线）。

逐字段镜像 ``app.services.print_layout_authoring`` 各回执的运行时形状；
propose/apply/templates 共用一张回执（success/blocked/committed 多形态）。
所有模型 ``extra="allow"``：未知字段原样透传，保证挂模前后响应体逐字节
一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PrintLayoutCapabilitiesResponse(BaseModel):
    """能力面（字体/纸张/词表/预算/单位 corpus；有界，可缓存）。"""

    model_config = ConfigDict(extra="allow")

    success: bool
    schema_version: str
    render_path: str
    paper_profiles: Dict[str, Any] = Field(default_factory=dict)
    fonts: Any = None
    font_asset_version: str = ""
    font_ids: List[str] = Field(default_factory=list)
    component_kinds: List[str] = Field(default_factory=list)
    text_bindings: List[str] = Field(default_factory=list)
    operations: List[str] = Field(default_factory=list)
    budgets: Dict[str, Any] = Field(default_factory=dict)
    units: Dict[str, Any] = Field(default_factory=dict)
    default_plan: Dict[str, Any] = Field(default_factory=dict)


class PrintLayoutInspectResponse(BaseModel):
    """检查回执（not_configured / blocked / configured 三形态）。"""

    model_config = ConfigDict(extra="allow")

    success: bool
    status: str
    schema_version: Optional[str] = None
    note: Optional[str] = None
    code: Optional[str] = None
    locks: Dict[str, Any] = Field(default_factory=dict)
    font_asset_version: Optional[str] = None
    mapspec_revision: Optional[int] = None
    plan: Optional[Dict[str, Any]] = None
    issues: List[Dict[str, Any]] = Field(default_factory=list)
    validation: Optional[Dict[str, Any]] = None
    numbering: Optional[Dict[str, Any]] = None
    planner_sync: Optional[Dict[str, Any]] = None


class PrintLayoutPagesResponse(BaseModel):
    """真实待导出页清单（planner 消费面）。"""

    model_config = ConfigDict(extra="allow")

    success: bool
    status: str
    planner_page_count: int = 0
    page_budget: int = 0
    page_count: int = 0
    toc: List[Dict[str, Any]] = Field(default_factory=list)
    pages: List[Dict[str, Any]] = Field(default_factory=list)
    disabled_pages: List[Dict[str, Any]] = Field(default_factory=list)
    binding_changed: bool = False
    exceeds_budget: bool = False


class PrintLayoutProposalReceipt(BaseModel):
    """propose / apply / templates 共用回执。

    dry_run 仅 propose；mutation_revision/plan_fingerprint/binding_drift/
    rebased_from 仅 apply；scope 仅 templates 的 scope 动作。
    """

    model_config = ConfigDict(extra="allow")

    success: bool
    status: Optional[str] = None
    code: str = ""
    message: Optional[str] = None
    correction_hint: Optional[str] = None
    committed: Optional[bool] = None
    dry_run: Optional[bool] = None
    issues: List[Dict[str, Any]] = Field(default_factory=list)
    changes: List[Dict[str, Any]] = Field(default_factory=list)
    skipped_locked: List[Any] = Field(default_factory=list)
    locks: Optional[Dict[str, Any]] = None
    adopted_values: Optional[Dict[str, Any]] = None
    validation: Optional[Dict[str, Any]] = None
    mutation_revision: Optional[int] = None
    schema_version: Optional[str] = None
    plan_fingerprint: Optional[str] = None
    binding_drift: Optional[bool] = None
    rebased_from: Optional[int] = None
    scope: Optional[Dict[str, Any]] = None


class PrintLayoutPreviewResponse(BaseModel):
    """纸面预览回执（+ 可选 SVG 字符串字段，非二进制下发）。"""

    model_config = ConfigDict(extra="allow")

    success: bool
    status: Optional[str] = None
    code: Optional[str] = None
    committed: Optional[bool] = None
    page_id: Optional[str] = None
    dpi: Optional[int] = None
    paper_mm: Optional[Dict[str, Any]] = None
    sample_px: Optional[Dict[str, Any]] = None
    receipt: Optional[Dict[str, Any]] = None
    svg_sha256: Optional[str] = None
    svg_bytes: Optional[int] = None
    svg: Optional[str] = None
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    font_status: List[Dict[str, Any]] = Field(default_factory=list)
    diagnostics: Optional[List[Any]] = None


class PrintLayoutExportResponse(BaseModel):
    """导出提交回执（refs-only；不返回整页像素或字体字节）。"""

    model_config = ConfigDict(extra="allow")

    success: bool
    status: Optional[str] = None
    code: Optional[str] = None
    format: Optional[str] = None
    dpi: Optional[int] = None
    bytes: Optional[int] = None
    download_url: Optional[str] = None
    manifest_url: Optional[str] = None
    filename: Optional[str] = None
    checksum: Optional[str] = None
    print_layout: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
