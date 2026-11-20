"""G30 技能作业 HTTP 响应契约（ADR-0138 response_model 基线）。

逐字段镜像 ``app.services.gis_harness.skill_jobs`` 目录/详情/prepare/运行
视图/预设回执的运行时形状；目录条目与 run_record 等自由子文档以
Dict[str, Any] 透传。所有模型 ``extra="allow"``：未知字段原样透传，保证
挂模前后响应体逐字节一致。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class SkillJobsCatalogResponse(BaseModel):
    """分类型目录（GIS 技能 + md 指南 + 扩展 candidate；有界）。"""

    model_config = ConfigDict(extra="allow")

    schema_version: str
    skills: List[Dict[str, Any]] = Field(default_factory=list)
    md_guides: List[Dict[str, Any]] = Field(default_factory=list)
    extension_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    counts: Dict[str, Any] = Field(default_factory=dict)


class SkillJobSkillDetailResponse(BaseModel):
    """单技能详情（目录条目 + 完成证据 + 质量义务投影）。"""

    model_config = ConfigDict(extra="allow")

    asset_type: str = "gis_skill"
    id: str
    name: str = ""
    description: str = ""
    domain: str = ""
    version: str = ""
    runnability: str = ""
    runnability_reasons: List[str] = Field(default_factory=list)
    completion_evidence: List[str] = Field(default_factory=list)
    quality_obligations: List[Dict[str, Any]] = Field(default_factory=list)


class SkillJobPrepareResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    prepared_id: str
    skill: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = ""
    preview: Dict[str, Any] = Field(default_factory=dict)


class SkillJobPresetPrepareResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    prepared_id: str
    preset: Dict[str, Any] = Field(default_factory=dict)
    skill: Dict[str, Any] = Field(default_factory=dict)
    preview: Dict[str, Any] = Field(default_factory=dict)
    param_sources: List[Dict[str, Any]] = Field(default_factory=list)


class SkillJobRunView(BaseModel):
    """运行视图（runner.get_skill_job_view 形状；run_record 存在时扩展）。"""

    model_config = ConfigDict(extra="allow")

    job_id: str
    kind: str = "skill_job"
    status: str
    progress: Optional[float] = None
    message: str = ""
    cancellable: bool = False
    retryable: bool = False
    active: bool = False
    attempt: int = 1
    session_id: Optional[str] = None
    project_id: Optional[str] = None
    error: Optional[str] = None
    result_ref: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    run_record: Optional[Dict[str, Any]] = None
    skill: Dict[str, Any] = Field(default_factory=dict)
    evidence_covered: List[Any] = Field(default_factory=list)
    evidence_not_covered: List[Any] = Field(default_factory=list)
    artifact_refs: Dict[str, Any] = Field(default_factory=dict)
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    skill_id: str = ""
    skill_version: str = ""


class SkillJobRunsListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    runs: List[SkillJobRunView] = Field(default_factory=list)


class SkillJobCancelResponse(BaseModel):
    """cancel 双形态：不可取消 = {job_id,status,cancelled,message}；
    已请求 = {job_id, cancel_requested}。"""

    model_config = ConfigDict(extra="allow")

    job_id: str
    status: Optional[str] = None
    cancelled: Optional[bool] = None
    message: Optional[str] = None
    cancel_requested: Optional[bool] = None


class SkillJobPresetView(BaseModel):
    """预设行视图（presets._row_to_view 形状；参数值有界投影）。"""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str = ""
    description: str = ""
    skill_id: str = ""
    skill_version: str = ""
    skill_digest: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)
    bindings_template: Dict[str, Any] = Field(default_factory=dict)
    project_id: str = ""
    creator_id: str = ""


class SkillJobPresetsListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    presets: List[SkillJobPresetView] = Field(default_factory=list)


class SkillJobPresetDeleteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    deleted: bool
