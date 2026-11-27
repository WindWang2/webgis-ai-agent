"""survey_sampling schema — 调查工作台 HTTP 面模型（入参边界/出参形状）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class FrameBuildIn(BaseModel):
    source_ref: str = Field(min_length=1, max_length=128)
    band: int = Field(1, ge=1, le=64)
    class_dictionary: Dict[int, str] = Field(
        ..., description="类别值→类名（冻结进框 digest）")


class SurveyFrameResponse(BaseModel):
    success: bool = True
    payload: Dict[str, Any]


class FrameListResponse(BaseModel):
    success: bool = True
    frames: List[Dict[str, Any]]


class DesignPreviewIn(BaseModel):
    frame_digest: str = Field(min_length=8, max_length=80)
    strategy: str = Field("equal", pattern="^(equal|proportional|neyman)$")
    n_total: Optional[int] = Field(None, ge=2, le=200000)
    target_se_oa: Optional[float] = Field(None, gt=0.0, lt=1.0)
    prior_precision: Optional[Dict[str, float]] = None


class DesignPreviewResponse(BaseModel):
    success: bool = True
    preview: Dict[str, Any]


class SurveyCreateIn(BaseModel):
    frame_digest: str = Field(min_length=8, max_length=80)
    objective: str = Field(min_length=1, max_length=200)
    target_time: str = Field("", max_length=32)
    strategy: str = Field("equal", pattern="^(equal|proportional|neyman)$")
    n_total: Optional[int] = Field(None, ge=2, le=200000)
    target_se_oa: Optional[float] = Field(None, gt=0.0, lt=1.0)
    prior_precision: Optional[Dict[str, float]] = None
    seed: int = Field(42, ge=0, le=2**31 - 1)
    backup_per_stratum: int = Field(0, ge=0, le=500)
    blind_labelling: bool = True
    max_nonresponse_rate: float = Field(0.2, gt=0.0, lt=1.0)
    notes: str = Field("", max_length=280)


class SurveyCreateResponse(BaseModel):
    success: bool = True
    survey_id: str
    design_digest: str
    frame_digest: str
    allocation: Dict[str, int]
    backup_per_stratum: int
    sample_count: int
    main_count: int


class SurveyDetailResponse(BaseModel):
    success: bool = True
    survey: Dict[str, Any]


class SurveyListResponse(BaseModel):
    success: bool = True
    surveys: List[Dict[str, Any]]


class ProgressResponse(BaseModel):
    success: bool = True
    progress: Dict[str, Any]


class RecordIn(BaseModel):
    sample_id: str = Field(min_length=8, max_length=64)
    status: str = Field(..., max_length=24)
    reference_class: Optional[str] = Field(None, max_length=64)
    evidence_type: str = Field("other", max_length=32)
    evidence_note: str = Field("", max_length=160)
    quality: str = Field("medium", max_length=16)
    interpreter: str = Field("", max_length=64)
    observed_at: str = Field("", max_length=32)
    note: str = Field("", max_length=280)
    expected_revision: Optional[int] = Field(None, ge=0)


class RecordResponse(BaseModel):
    success: bool = True
    result: Dict[str, Any]


class ImportIn(BaseModel):
    rows: List[Dict[str, Any]] = Field(..., min_length=1, max_length=5000)
    expected_revision: Optional[int] = Field(None, ge=0)


class ImportResponse(BaseModel):
    success: bool = True
    result: Dict[str, Any]


class AdjudicateIn(BaseModel):
    sample_id: str = Field(min_length=8, max_length=64)
    reference_class: str = Field(min_length=1, max_length=64)
    note: str = Field("", max_length=280)
    expected_revision: Optional[int] = Field(None, ge=0)


class EstimateResponse(BaseModel):
    success: bool = True
    estimate: Dict[str, Any]


class EstimatesListResponse(BaseModel):
    success: bool = True
    estimates: List[Dict[str, Any]]
