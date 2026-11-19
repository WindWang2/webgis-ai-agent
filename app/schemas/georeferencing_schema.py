"""Typed contracts, capability budgets, and error codes for G48 GCP Georeferencing."""
from __future__ import annotations

import math
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


def validate_positive_finite_resolution(v: Optional[float]) -> Optional[float]:
    """Ensure optional target resolution in meters is finite and strictly positive."""
    if v is None:
        return None
    val = float(v)
    if not math.isfinite(val) or val <= 0.0:
        raise ValueError("target_resolution_m must be finite and greater than 0")
    return val


class GeorefErrorCode(str, Enum):
    """Stable domain error codes exposed via API and Pi tool receipts."""

    SOURCE_UNSUPPORTED_FORMAT = "SOURCE_UNSUPPORTED_FORMAT"
    SOURCE_TOO_LARGE = "SOURCE_TOO_LARGE"
    SOURCE_DECODE_FAILED = "SOURCE_DECODE_FAILED"
    SOURCE_EXIF_FAILED = "SOURCE_EXIF_FAILED"
    SOURCE_CORRUPT_OR_TAMPERED = "SOURCE_CORRUPT_OR_TAMPERED"
    UNSUPPORTED_TARGET_CRS = "UNSUPPORTED_TARGET_CRS"
    INSUFFICIENT_GCPS = "INSUFFICIENT_GCPS"
    INVALID_GCP_COORDS = "INVALID_GCP_COORDS"
    RANK_DEFICIENT = "RANK_DEFICIENT"
    SINGULAR_AFFINE = "SINGULAR_AFFINE"
    OUTPUT_BUDGET_EXCEEDED = "OUTPUT_BUDGET_EXCEEDED"
    CAS_CONFLICT = "CAS_CONFLICT"
    PREVIEW_STALE = "PREVIEW_STALE"
    JOB_CANCELLED = "JOB_CANCELLED"
    JOB_FAILED = "JOB_FAILED"
    ARTIFACT_NOT_READY = "ARTIFACT_NOT_READY"
    MAPSPEC_BIND_CONFLICT = "MAPSPEC_BIND_CONFLICT"
    FORBIDDEN_OR_NOT_FOUND = "FORBIDDEN_OR_NOT_FOUND"


# ── Capability Matrix & Hard Resource Budgets ────────────────────────────────

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_DECODE_PIXELS = 32_000_000
MAX_DECODE_SIDE = 12_000
MAX_OUTPUT_PIXELS = 64_000_000
MAX_UNCOMPRESSED_OUTPUT_BYTES = 512 * 1024 * 1024
MAX_PREVIEW_SIDE = 1280
MAX_GCPS_PER_PLAN = 500

SUPPORTED_IMAGE_FORMATS = ("PNG", "JPEG", "TIFF")
SUPPORTED_DTYPES_BY_FORMAT: Dict[str, List[str]] = {
    "PNG": ["uint8"],
    "JPEG": ["uint8"],
    "TIFF": ["uint8", "uint16_gray"],
}


class GeorefDomainError(Exception):
    """Structured domain exception carrying HTTP status, stable code, and message."""

    def __init__(
        self,
        code: GeorefErrorCode,
        message: str,
        *,
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_payload(self) -> Dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }


# ── Core Domain Models ───────────────────────────────────────────────────────


class ExistingGeorefMetadata(BaseModel):
    has_existing_crs: bool = False
    existing_crs: Optional[str] = None
    has_existing_transform: bool = False
    has_embedded_gcps: bool = False
    embedded_gcp_count: int = 0
    warnings: List[str] = Field(default_factory=list)


class GeorefSourceRecord(BaseModel):
    source_id: str
    session_id: str
    filename: str
    detected_format: str
    width: int
    height: int
    bands: int
    dtype: str
    color_mode: str
    has_alpha: bool
    exif_orientation: int = 1
    exif_applied_transform: str = "none"
    exif_overridden: bool = False
    original_blob_key: str
    normalized_blob_key: str
    normalized_path: str
    original_sha256: str
    normalized_sha256: str
    byte_size: int
    observations: List[str] = Field(default_factory=list)
    existing_georef: ExistingGeorefMetadata = Field(default_factory=ExistingGeorefMetadata)
    created_at: str


class GeorefCoordProvenance(BaseModel):
    source_crs: str
    raw_x: float
    raw_y: float
    target_crs: str
    map_x: float
    map_y: float
    axis_order: str = "always_xy"
    transform_note: str = ""
    warnings: List[str] = Field(default_factory=list)


class GcpPoint(BaseModel):
    gcp_id: str = Field(..., min_length=1, max_length=64)
    label: str = Field(default="", max_length=120)
    px: float
    py: float
    map_x: float
    map_y: float
    role: Literal["fit", "check"] = "fit"
    enabled: bool = True
    note: str = Field(default="", max_length=300)
    provenance: Optional[GeorefCoordProvenance] = None


class AffineCoefficients(BaseModel):
    a0: float
    a1: float
    a2: float
    b0: float
    b1: float
    b2: float
    px_scale_x: float
    px_scale_y: float
    rotation_deg: float
    shear_deg: float
    determinant: float
    is_reflection: bool = False


class PointResidual(BaseModel):
    gcp_id: str
    label: str
    role: Literal["fit", "check"]
    enabled: bool
    px: float
    py: float
    map_x: float
    map_y: float
    pred_x: Optional[float] = None
    pred_y: Optional[float] = None
    dx: Optional[float] = None
    dy: Optional[float] = None
    radial_error: Optional[float] = None


class AffineFitMetrics(BaseModel):
    status: Literal["valid", "insufficient", "degenerate", "invalid"]
    fit_count: int = 0
    check_count: int = 0
    disabled_count: int = 0
    fit_rmse: Optional[float] = None
    fit_max_error: Optional[float] = None
    check_rmse: Optional[float] = None
    check_max_error: Optional[float] = None
    zero_residual_unverified: bool = False
    condition_number: Optional[float] = None
    rank: int = 0
    hull_coverage_ratio: float = 0.0
    low_coverage_warning: bool = False
    extrapolation_warning: bool = False
    outlier_gcp_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    coefficients: Optional[AffineCoefficients] = None
    residuals: List[PointResidual] = Field(default_factory=list)


class FrozenExecutionSnapshot(BaseModel):
    frozen_at: str
    revision: int
    source_id: str
    original_sha256: str
    normalized_sha256: str
    target_crs: str
    resampling: Literal["bilinear", "nearest"]
    target_resolution_m: Optional[float] = None
    generate_cog: bool = False
    ignore_exif: bool = False
    affine: AffineCoefficients
    fit_gcps: List[GcpPoint]
    check_gcps: List[GcpPoint]
    out_width: int
    out_height: int
    dst_transform: List[float]
    bounds_target_crs: List[float]
    footprint_wgs84: Optional[Dict[str, Any]] = None
    bounds_wgs84: Optional[List[float]] = None
    estimated_output_pixels: int
    estimated_output_bytes: int


class GeorefArtifactRecord(BaseModel):
    artifact_id: str
    plan_id: str
    execution_id: str
    job_id: Optional[str] = None
    session_id: str
    format: Literal["GeoTIFF"] = "GeoTIFF"
    cog_requested: bool = False
    cog_verified: bool = False
    cog_checks: Dict[str, Any] = Field(default_factory=dict)
    target_crs: str
    width: int
    height: int
    bands: int
    dtype: str
    has_alpha: bool
    nodata_mode: str
    sha256: str
    byte_size: int
    blob_key: str
    file_path: str
    raster_ref: str
    bounds_target_crs: List[float]
    bounds_wgs84: List[float]
    footprint_wgs84: Dict[str, Any]
    created_at: str
    mapspec_layer_id: Optional[str] = None
    mapspec_revision: Optional[int] = None


class GeorefDraftPlan(BaseModel):
    plan_id: str
    session_id: str
    source_id: str
    name: str = "Georeference Plan"
    revision: int = 1
    transform_model: Literal["affine_2d"] = "affine_2d"
    target_crs: str = "EPSG:3857"
    resampling: Literal["bilinear", "nearest"] = "bilinear"
    target_resolution_m: Optional[float] = None
    generate_cog: bool = False
    ignore_exif: bool = False
    gcps: List[GcpPoint] = Field(default_factory=list)
    fit_metrics: Optional[AffineFitMetrics] = None
    preview_digest: Optional[str] = None
    preview_stale: bool = True
    preview_footprint_wgs84: Optional[Dict[str, Any]] = None
    preview_bounds_wgs84: Optional[List[float]] = None
    preview_path: Optional[str] = None
    active_job_id: Optional[str] = None
    active_execution_id: Optional[str] = None
    frozen_snapshot: Optional[FrozenExecutionSnapshot] = None
    latest_artifact: Optional[GeorefArtifactRecord] = None
    superseded_artifacts: List[GeorefArtifactRecord] = Field(default_factory=list)
    mapspec_bind_status: Optional[str] = None
    created_at: str
    updated_at: str


# ── API Request / Response Models ────────────────────────────────────────────


class CreatePlanIn(BaseModel):
    session_id: str = Field(..., min_length=8, max_length=128)
    source_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(default="Georeference Plan", max_length=120)
    target_crs: str = Field(default="EPSG:3857", min_length=3, max_length=128)
    resampling: Literal["bilinear", "nearest"] = "bilinear"
    target_resolution_m: Optional[float] = Field(default=None, gt=0.0)
    generate_cog: bool = False

    @field_validator("target_resolution_m")
    @classmethod
    def _finite_target_res(cls, v: Optional[float]) -> Optional[float]:
        return validate_positive_finite_resolution(v)


class GcpInputItem(BaseModel):
    gcp_id: Optional[str] = Field(default=None, max_length=64)
    label: str = Field(default="", max_length=120)
    px: float
    py: float
    map_x: Optional[float] = None
    map_y: Optional[float] = None
    wgs84_lon: Optional[float] = None
    wgs84_lat: Optional[float] = None
    role: Literal["fit", "check"] = "fit"
    enabled: bool = True
    note: str = Field(default="", max_length=300)

    @field_validator("px", "py")
    @classmethod
    def _finite_px(cls, v: float) -> float:
        import math

        if not math.isfinite(v):
            raise ValueError("Pixel coordinate must be finite")
        return float(v)


class DraftPlanUpdateIn(BaseModel):
    expected_revision: int = Field(..., ge=1)
    name: Optional[str] = Field(default=None, max_length=120)
    target_crs: Optional[str] = Field(default=None, min_length=3, max_length=128)
    resampling: Optional[Literal["bilinear", "nearest"]] = None
    target_resolution_m: Optional[float] = None
    clear_target_resolution: bool = False
    generate_cog: Optional[bool] = None
    gcps: Optional[List[GcpInputItem]] = Field(default=None, max_length=MAX_GCPS_PER_PLAN)

    @field_validator("target_resolution_m")
    @classmethod
    def _finite_target_res(cls, v: Optional[float]) -> Optional[float]:
        return validate_positive_finite_resolution(v)


class ImportGcpsIn(BaseModel):
    expected_revision: int = Field(..., ge=1)
    format: Literal["json", "csv"] = "json"
    content: str = Field(..., min_length=1, max_length=1_000_000)
    replace_existing: bool = True
    coords_are_wgs84: bool = False


class PreviewPlanIn(BaseModel):
    expected_revision: Optional[int] = Field(default=None, ge=1)


class ExecutePlanIn(BaseModel):
    expected_revision: int = Field(..., ge=1)
    background: bool = True
    idempotency_key: Optional[str] = Field(default=None, max_length=128)
    project_id: Optional[str] = Field(default=None, max_length=128)


class BindLayerIn(BaseModel):
    expected_mapspec_revision: int = Field(..., ge=0)
    layer_id: Optional[str] = Field(default=None, max_length=128)
    layer_name: Optional[str] = Field(default=None, max_length=120)
    opacity: float = Field(default=0.85, ge=0.0, le=1.0)


class GeorefSourceResponse(BaseModel):
    source: GeorefSourceRecord
    image_url: str


class GeorefSourceListResponse(BaseModel):
    session_id: str
    sources: List[GeorefSourceRecord]


class GeorefPlanResponse(BaseModel):
    plan: GeorefDraftPlan
    source: Optional[GeorefSourceRecord] = None


class GeorefPlanListResponse(BaseModel):
    session_id: str
    plans: List[GeorefDraftPlan]


class GeorefPreviewResponse(BaseModel):
    plan: GeorefDraftPlan
    preview_available: bool
    preview_image_url: Optional[str] = None
    out_width: Optional[int] = None
    out_height: Optional[int] = None
    estimated_output_pixels: Optional[int] = None
    estimated_output_bytes: Optional[int] = None
    warnings: List[str] = Field(default_factory=list)


class GeorefExecuteResponse(BaseModel):
    plan_id: str
    job_id: str
    execution_id: str
    status: str
    background: bool
    idempotent_reuse: bool = False
    artifact: Optional[GeorefArtifactRecord] = None
    poll_url: str


class GeorefJobStatusResponse(BaseModel):
    plan_id: str
    job_id: Optional[str] = None
    execution_id: Optional[str] = None
    status: str
    stage: str = ""
    progress: int = 0
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    artifact: Optional[GeorefArtifactRecord] = None
    plan: GeorefDraftPlan


class GeorefBindLayerResponse(BaseModel):
    bound: bool
    partial_success: bool = False
    layer_id: Optional[str] = None
    mapspec_revision: Optional[int] = None
    current_mapspec_revision: Optional[int] = None
    error_code: Optional[str] = None
    message: str = ""
    artifact: Optional[GeorefArtifactRecord] = None
    mapspec: Optional[Dict[str, Any]] = None


class GeorefDeleteResponse(BaseModel):
    deleted: bool
    source_id: str
    released_blobs: int = 0


# ── Response views: server-path stripping (issue #2194) ─────────────────────
#
# 领域记录内部合法携带 normalized_path/file_path/preview_path（下载、清理、
# 预览读图都依赖它们）；但 HTTP 响应绝不回显服务器文件系统路径
# （对照 raster_authoring 的既定做法）。以下视图返回剥离后的副本，
# 存储与文件操作始终使用原始记录。


def source_public_view(src: GeorefSourceRecord) -> GeorefSourceRecord:
    """API 响应视图：剥离 ``normalized_path``。"""
    return src.model_copy(update={"normalized_path": ""})


def artifact_public_view(art: Optional[GeorefArtifactRecord]) -> Optional[GeorefArtifactRecord]:
    """API 响应视图：剥离 ``file_path``。"""
    if art is None:
        return None
    return art.model_copy(update={"file_path": ""})


def plan_public_view(plan: GeorefDraftPlan) -> GeorefDraftPlan:
    """API 响应视图：剥离 ``preview_path`` 与嵌套 artifact 的 ``file_path``。"""
    return plan.model_copy(update={
        "preview_path": None,
        "latest_artifact": artifact_public_view(plan.latest_artifact),
        "superseded_artifacts": [
            artifact_public_view(a) for a in plan.superseded_artifacts
        ],
    })
