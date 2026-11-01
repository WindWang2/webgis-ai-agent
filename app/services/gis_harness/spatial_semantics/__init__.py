"""SpatialSemantics —— 空间语义与量纲可信契约（GOAL 01，schema v1）。

实例层契约：一次数据 × 范围 × 操作的 CRS/单位/分母/范围语义绑定，
贯穿 Pi GIS Harness、空间分析工具、ref 证据与 MapSpec。

公开面（消费方只 import 本包入口，不深入子模块）：

- 契约：``SpatialSemanticContract`` / ``ScopeEvidence``
- 证据：``DatasetSpatialEvidence`` / ``evidence_from_feature_collection``
- 判定：``build_spatial_preflight`` / ``preflight_for_feature_collection``
  / ``validate_spatial_operation`` / ``SpatialOperationSpec``
- 单位：``parse_distance_expression`` / ``convert_area`` / ``compose_density_unit``
- 披露：``SpatialPreflightResult.disclosure`` / ``evidence_block_for_tool_result``

纪律见各模块 docstring；权威边界（BASELINE §1）：CRS 分类在 crs_safety、
字段量纲在 measurement、算法要求在 method_use_contract、中国偏移帧数学在
utils/coord_transform —— 本包组合它们，不复制。
"""
from app.services.gis_harness.spatial_semantics.contract import (
    SPATIAL_SEMANTIC_CONTRACT_VERSION,
    ContractReasonCode,
    ScopeEvidence,
    SpatialSemanticContract,
)
from app.services.gis_harness.spatial_semantics.inspect_semantics import (
    DatasetSpatialEvidence,
    evidence_for_china_offset_risk,
    evidence_from_feature_collection,
    inspect_spatial_contract,
)
from app.services.gis_harness.spatial_semantics.preflight import (
    PREFLIGHT_SCHEMA_VERSION,
    SpatialPreflightResult,
    build_spatial_preflight,
    evidence_block_for_tool_result,
    preflight_for_feature_collection,
)
from app.services.gis_harness.spatial_semantics.resolve import (
    CalculationSpace,
    apply_calculation_space,
    resolve_calculation_space,
)
from app.services.gis_harness.spatial_semantics.units import (
    AREA_UNIT_DISPLAY,
    AREA_UNIT_FACTORS,
    compose_density_unit,
    convert_area,
    convert_density,
    normalize_distance_params,
    parse_distance_expression,
)
from app.services.gis_harness.spatial_semantics.validate import (
    RepairSuggestion,
    SpatialOperationSpec,
    SpatialOperationVerdict,
    validate_spatial_operation,
)
from app.services.gis_harness.spatial_semantics.vocabulary import (
    SSC_REASON_CODES,
    reason_code_metadata,
)

__all__ = [
    "SPATIAL_SEMANTIC_CONTRACT_VERSION",
    "PREFLIGHT_SCHEMA_VERSION",
    "SpatialSemanticContract",
    "ScopeEvidence",
    "ContractReasonCode",
    "DatasetSpatialEvidence",
    "evidence_from_feature_collection",
    "inspect_spatial_contract",
    "evidence_for_china_offset_risk",
    "CalculationSpace",
    "resolve_calculation_space",
    "apply_calculation_space",
    "SpatialOperationSpec",
    "SpatialOperationVerdict",
    "RepairSuggestion",
    "validate_spatial_operation",
    "SpatialPreflightResult",
    "build_spatial_preflight",
    "preflight_for_feature_collection",
    "evidence_block_for_tool_result",
    "parse_distance_expression",
    "normalize_distance_params",
    "convert_area",
    "convert_density",
    "compose_density_unit",
    "AREA_UNIT_FACTORS",
    "AREA_UNIT_DISPLAY",
    "SSC_REASON_CODES",
    "reason_code_metadata",
]
