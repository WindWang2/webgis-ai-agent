"""空间反幻觉与地理红线安全守护引擎（ADR-0195）。

公共 API：
- SpatialGuardrails / get_guardrails / guardrails_enabled —— 编排网关；
- detect_pair / auto_flip —— 经纬度倒置检测与自愈；
- classify_point / validate_facility_point —— 陆地掩膜三区制校验；
- verify_code / resolve_name / assert_valid_code —— 行政区划校验；
- GuardrailVerdict / GuardrailIssue / GuardLevel / DefenseMode —— 裁决契约；
- GeographicImpossibilityError 等错误族。

离线纪律：仅标准库；无网络 IO；重依赖（numpy/geopandas）零接触。
"""
from app.services.spatial_guardrails.admin_division_verifier import (
    AdminCheckReport,
    assert_valid_code,
    resolve_name,
    verify_code,
)
from app.services.spatial_guardrails.errors import (
    FabricatedAdminDivisionError,
    GeographicImpossibilityError,
    GeofenceRedlineViolationError,
    LatLonInversionError,
    SpatialGuardrailError,
    TopologyImplausibilityError,
)
from app.services.spatial_guardrails.guardrail_middleware import (
    SpatialGuardrails,
    get_guardrails,
)
from app.services.spatial_guardrails.landmask_validator import (
    SurfaceZone,
    classify_point,
    validate_facility_point,
)
from app.services.spatial_guardrails.latlon_inversion_detector import (
    InversionReport,
    auto_flip,
    detect_pair,
)
from app.services.spatial_guardrails.types import (
    DefenseMode,
    GuardLevel,
    GuardrailConfig,
    GuardrailIssue,
    GuardrailVerdict,
    guardrails_enabled,
)

__all__ = [
    "AdminCheckReport",
    "DefenseMode",
    "FabricatedAdminDivisionError",
    "GeographicImpossibilityError",
    "GeofenceRedlineViolationError",
    "GuardLevel",
    "GuardrailConfig",
    "GuardrailIssue",
    "GuardrailVerdict",
    "InversionReport",
    "LatLonInversionError",
    "SpatialGuardrailError",
    "SpatialGuardrails",
    "SurfaceZone",
    "TopologyImplausibilityError",
    "assert_valid_code",
    "auto_flip",
    "classify_point",
    "detect_pair",
    "get_guardrails",
    "guardrails_enabled",
    "resolve_name",
    "validate_facility_point",
    "verify_code",
]
