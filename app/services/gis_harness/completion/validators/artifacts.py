"""artifact 存活/绑定 validator（artifacts facet）— ADR-0081 / ADR-0091。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contracts import (
    F_ARTIFACT_EXPIRED,
    F_ARTIFACT_MISSING,
    F_EMPTY_RESULT,
    MapCompletionFinding,
)


def validate_artifacts(
    chapter: Dict[str, Any],
    descriptors: Dict[str, Optional[dict]],
) -> List[MapCompletionFinding]:
    """artifact 校验：required artifact 已绑定、ref 存活、空结果有明确语义。

    registry-driven（review 6 P1）：只有产出**空间 feature set** 的能力才
    要求 bound_ref —— `stats_table`（spatial_stats/point_profile 等）、
    `od_matrix`、raster 家族（heatmap 栅格走独立通道，geojson_ref 恒空）
    的完成证据是工具成功本身，不落 FC ref；对它们强求 bound_ref 会把
    常规 recipe 路径误判为 failed。
    """
    findings: List[MapCompletionFinding] = []
    rows = [
        r
        for r in list(chapter.get("data_requirements") or [])
        + list(chapter.get("analysis_steps") or [])
        if isinstance(r, dict)
        and str(r.get("status") or "") in ("available", "done")
        and not bool(r.get("optional"))
    ]
    seen: set[str] = set()
    for row in rows:
        cap = str(row.get("capability") or "?")
        if cap in seen:
            continue
        seen.add(cap)
        policy = _capability_fc_ref_policy(cap)
        if policy == "none":
            continue
        ref = str(row.get("bound_ref") or "")
        if not ref:
            if policy == "required":
                findings.append(
                    MapCompletionFinding(
                        code=F_ARTIFACT_MISSING,
                        severity="error",
                        target=cap,
                        detail="capability marked complete without a bound artifact ref",
                    )
                )
            # optional（raster 通道）：无 FC ref 是合法完成形态 —— 完成证据
            # 是工具成功 + 已挂载的栅格图层（validate_layers 覆盖后者）。
            continue
        desc = descriptors.get(ref)
        if desc is None:
            if ref not in descriptors:
                continue  # unknown（探测失败）：未知 ≠ 过期，跳过不判
            findings.append(
                MapCompletionFinding(
                    code=F_ARTIFACT_EXPIRED,
                    severity="error",
                    target=cap,
                    detail=f"bound ref {ref[:48]} not present in session store",
                )
            )
            continue
        count = desc.get("feature_count")
        if isinstance(count, int) and count == 0:
            findings.append(
                MapCompletionFinding(
                    code=F_EMPTY_RESULT,
                    severity="warning",
                    target=cap,
                    detail=f"artifact {ref[:48]} has zero features — nothing to map",
                )
            )
    return findings


# 非空间输出类型：完成证据 = 工具成功（无 FC ref 可绑；见 dispatch 的
# geojson_ref 语义）。raster 家族走 heatmap 栅格通道，同样不落 FC ref。
_NON_SPATIAL_ARTIFACT_TYPES = {"stats_table", "od_matrix", "raster_surface", "terrain_surface"}
# FC ref 可选的能力：density_surface 有两条产物通道 —— vector 通道落
# FC ref、raster 渲染通道（density.visual.heatmap → heatmap_data 工具）
# 刻意不落 geojson_ref（产物是 ref:heatmap-* / ref:raster/*）。对它强求
# bound_ref 会把成功挂载的栅格热力图误判成 artifact_missing → 假 failed
# （review C-1）；绑了 ref 时仍照常校验存在性/空结果。
_OPTIONAL_FC_REF_TYPES = {"density_surface"}


def _capability_fc_ref_policy(capability: str) -> str:
    """该能力的 FC ref 策略：required / optional / none。

    - required：输出含空间 feature set 且无 raster 旁路 → 必须绑定 ref；
    - optional：存在 raster/栅格产物通道 → ref 有则校验、无则不判缺失；
    - none：纯非空间输出（stats/od/raster 家族）→ 完成证据 = 工具成功。
    """
    try:
        from app.lib.gis.capability_registry import get_capability_registry

        desc = get_capability_registry().get(capability)
        if desc is None:
            return "required"  # 未知能力保守要求（缺 ref 时由 finding 披露）
        outputs = [str(t) for t in (desc.output_artifact_types or [])]
        if not outputs:
            return "required"
        if any(t in _OPTIONAL_FC_REF_TYPES for t in outputs):
            return "optional"
        if all(t in _NON_SPATIAL_ARTIFACT_TYPES for t in outputs):
            return "none"
        return "required"
    except Exception:  # noqa: BLE001 — registry 读失败保守要求
        return "required"
