"""build_spatial_preflight —— 统一 preflight 入口（GOAL 01 · M2 汇聚点）。

一次调用完成 inspect → resolve → validate 三段，返回 typed
``SpatialPreflightResult``。工具适配层（M4）只消费本函数 —— 禁止各工具
复制 CRS/单位判定逻辑（BASELINE G1 的根治）。

契约纪律：

- **不改写工具原始参数**：preflight 是判定面不是变换面；确定性安全变换
  的执行仍走既有执行层（transform_geojson / buffer_smart 等），preflight
  只给建议与授权分级；
- **descriptor 级开销**：evidence 提取 O(≤2000 要素 / ≤200 采样)，
  50k feature 与 5k feature 的 preflight 成本同为有界（M7 预算测试锁定）；
- **LLM 安全**：``disclosure`` 是进 prompt/evidence 的有界投影 ——
  绝无坐标采样/几何全量。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel

from app.services.gis_harness.spatial_semantics.contract import (
    ScopeEvidence,
    SpatialSemanticContract,
)
from app.services.gis_harness.spatial_semantics.inspect_semantics import (
    DatasetSpatialEvidence,
    evidence_from_feature_collection,
    inspect_spatial_contract,
)
from app.services.gis_harness.spatial_semantics.resolve import (
    CalculationSpace,
    apply_calculation_space,
    resolve_calculation_space,
)
from app.services.gis_harness.spatial_semantics.validate import (
    SpatialOperationSpec,
    SpatialOperationVerdict,
    validate_spatial_operation,
)

PREFLIGHT_SCHEMA_VERSION = 1


class SpatialPreflightResult(BaseModel):
    """preflight 结果（contract + space + verdict + 有界披露投影）。"""

    schema_version: int = PREFLIGHT_SCHEMA_VERSION
    contract: SpatialSemanticContract
    calculation: CalculationSpace
    verdict: SpatialOperationVerdict

    @property
    def status(self) -> str:
        return self.verdict.status

    @property
    def is_executable(self) -> bool:
        return self.verdict.status in (
            "pass", "pass_with_disclosure", "degraded_with_disclosure")

    def disclosure(self) -> Dict[str, Any]:
        """有界披露投影（进工具结果 evidence / prompt；无几何）。"""
        return {
            "schema_version": self.schema_version,
            "status": self.verdict.status,
            "source_crs": self.contract.source_crs,
            "crs_source": self.contract.crs_source,
            "crs_class": self.contract.crs_class,
            "axis_order": self.contract.axis_order,
            "geometry_type": self.contract.geometry_type,
            "feature_count": self.contract.feature_count,
            "calculation_mode": self.calculation.mode,
            "working_crs": self.calculation.working_crs,
            "working_crs_basis": self.calculation.working_crs_basis,
            "distortion_estimate": (
                round(self.calculation.distortion_estimate, 4)
                if self.calculation.distortion_estimate is not None else None),
            "datum_normalization": list(self.contract.datum_normalization),
            "scale_level": self.contract.scale_level,
            "spatial_precision": self.contract.spatial_precision,
            "scope_overlap": self.contract.scope_overlap,
            "reason_codes": [
                {"code": rc.code, "field": rc.field, "detail": rc.detail}
                for rc in self.verdict.reason_codes
            ],
            "repair_suggestions": [
                {
                    "action": r.action,
                    "authorization": r.authorization,
                    "transform": r.transform,
                    "detail": r.detail,
                }
                for r in self.verdict.repair_suggestions
            ],
            "blocked_fields": list(self.verdict.blocked_fields),
        }


def build_spatial_preflight(
    evidence: DatasetSpatialEvidence,
    *,
    scope: Optional[ScopeEvidence] = None,
    spec: Optional[SpatialOperationSpec] = None,
    purpose: Optional[str] = None,
) -> SpatialPreflightResult:
    """evidence (+scope) + 操作声明 → 统一 preflight 结果。

    ``purpose`` 缺省按 spec.operation 推导（topology 族 → topology；
    其余 → distance 最严口径）。
    """
    spec = spec or SpatialOperationSpec()
    if purpose is None:
        purpose = ("topology" if spec.operation in ("spatial_join", "overlay",
                                                    "topology")
                   else "distance")
    contract = inspect_spatial_contract(evidence, scope=scope)
    space = resolve_calculation_space(contract, purpose=purpose)
    contract = apply_calculation_space(contract, space)
    verdict = validate_spatial_operation(contract, space, spec)
    return SpatialPreflightResult(
        contract=contract, calculation=space, verdict=verdict)


def preflight_for_feature_collection(
    fc: Any,
    *,
    ref: str = "",
    fingerprint: str = "",
    scope: Optional[ScopeEvidence] = None,
    spec: Optional[SpatialOperationSpec] = None,
    purpose: Optional[str] = None,
) -> SpatialPreflightResult:
    """FC 直达入口（工具适配层最常用形态）。"""
    evidence = evidence_from_feature_collection(fc, ref=ref, fingerprint=fingerprint)
    return build_spatial_preflight(
        evidence, scope=scope, spec=spec, purpose=purpose)


def evidence_block_for_tool_result(
    result: SpatialPreflightResult,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """preflight → 工具结果 evidence 的 ``spatial_semantics`` 键（M4/M5 接线）。

    与 disclosure 同构 + 调用方补充的计算口径（distance_m/density_unit 等）。
    """
    block = result.disclosure()
    if extra:
        for k, v in extra.items():
            block[str(k)[:48]] = v
    return block


__all__ = [
    "PREFLIGHT_SCHEMA_VERSION",
    "SpatialPreflightResult",
    "build_spatial_preflight",
    "preflight_for_feature_collection",
    "evidence_block_for_tool_result",
]
