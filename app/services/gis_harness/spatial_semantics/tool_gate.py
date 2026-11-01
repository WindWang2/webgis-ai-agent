"""tool_gate —— 工具层 preflight 适配门（GOAL 01 · M4）。

工具适配统一走这里（禁止各工具复制 CRS/单位判定）：

- ``preflight_tool_input``：执行前判定。``blocked`` / ``requires_clarification``
  → 抛 :class:`SpatialSemanticBlocked`（typed 错误：稳定码 + 修复建议），
  工具层转标准 ValueError（既有错误折叠通道）—— **绝不改写成成功**；
  ``pass`` / ``pass_with_disclosure`` / ``degraded_with_disclosure`` → 返回
  preflight 结果，``attach_semantics_evidence`` 把有界披露并入工具响应。
- 双运行时一致（M6）：Pi（registry.dispatch）与 legacy（直接函数调用）都
  过同一 gate —— 判定只依赖输入本身，与入口无关。

修复建议不自动执行（决策 D4）：确定性安全变换由调用方显式授权后另行调用
transform 工具；本 gate 只判定与披露。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.gis_harness.spatial_semantics.contract import ScopeEvidence
from app.services.gis_harness.spatial_semantics.preflight import (
    SpatialPreflightResult,
    preflight_for_feature_collection,
)
from app.services.gis_harness.spatial_semantics.validate import (
    SpatialOperationSpec,
)


class SpatialSemanticBlocked(Exception):
    """preflight 拒绝 —— typed 错误（稳定码 + 修复建议 + 字段名）。

    工具层捕获后 raise ValueError(str(exc))：走既有 std_error_response
    折叠（错误路径不新增第二套通道）。
    """

    def __init__(self, preflight: SpatialPreflightResult) -> None:
        self.preflight = preflight
        codes = "|".join(rc.code for rc in preflight.verdict.reason_codes[:6])
        repairs = "/".join(
            f"{r.action}({r.authorization})"
            for r in preflight.verdict.repair_suggestions[:3])
        blocked_fields = ",".join(preflight.verdict.blocked_fields[:4])
        parts = [
            f"spatial_semantics:{preflight.verdict.status}:{codes}",
        ]
        if blocked_fields:
            parts.append(f"fields={blocked_fields}")
        detail = "; ".join(
            f"{rc.code}:{rc.detail}" for rc in preflight.verdict.reason_codes[:3])
        if detail:
            parts.append(detail[:200])
        if repairs:
            parts.append(f"repairs={repairs}")
        super().__init__(" — ".join(parts))


def preflight_tool_input(
    fc: Any,
    *,
    operation: str,
    distance: Optional[float] = None,
    distance_unit: Optional[str] = None,
    denominator_kind: Optional[str] = None,
    requested_density_denominator: Optional[str] = None,
    scope: Optional[ScopeEvidence] = None,
    purpose: Optional[str] = None,
    ref: str = "",
) -> SpatialPreflightResult:
    """执行前统一判定（blocked/requires_clarification → 抛 typed 错误）。"""
    spec = SpatialOperationSpec(
        operation=operation,
        distance=distance,
        distance_unit=distance_unit,
        denominator_kind=denominator_kind,
        requested_density_denominator=requested_density_denominator,
    )
    result = preflight_for_feature_collection(
        fc, ref=ref, scope=scope, spec=spec, purpose=purpose)
    if result.verdict.status in ("blocked", "requires_clarification"):
        raise SpatialSemanticBlocked(result)
    return result


def attach_semantics_evidence(
    response: Dict[str, Any],
    preflight: SpatialPreflightResult,
    **extra: Any,
) -> Dict[str, Any]:
    """把 preflight 披露并入工具响应（``spatial_semantics`` 键，有界）。

    只增不改：既有键（scientific_evidence/normalization 等）原样保留；
    evidence 消费方（dispatch 血缘 / MapSpec 编译 / QA 面）按需读取。
    """
    if not isinstance(response, dict):
        return response
    block = preflight.disclosure()
    for k, v in extra.items():
        block[str(k)] = v
    response["spatial_semantics"] = block
    return response


__all__ = [
    "SpatialSemanticBlocked",
    "preflight_tool_input",
    "attach_semantics_evidence",
]
