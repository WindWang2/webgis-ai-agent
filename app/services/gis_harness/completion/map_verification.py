"""Final Map Verification（Goal §九）—— finalize 前的最终地图状态裁决。

V3 强制闭环的最后一段：既有 finalizer（validate → repair → revalidate +
render observation）之上，聚合三组此前的验证缺口 —— **图层顺序**、
**结果越界**、**陈旧覆盖层** —— 并产出机器可读的最终裁决：

    verified                    成图面全绿（complete + 观察验证通过 + 无 V3 发现）
    verified_with_degradation   成图但带披露（stale/unknown 观察、警告级发现）
    failed                      结果层缺失 / 渲染缺口 / 不可修复 error
    unknown                     无可验证的成图面（无计划图层）

红线：

- 复用既有 validator 族证据（不重复实现图层/组件/语义校验）；
- V3 发现全部 warning 级增值披露 —— 不推翻既有 status 语义，只参与
  final_map_status 聚合（零回归风险）；
- stale overlay 只对「死 ref / 已被 supersede 的 ref」图层告警（确定性、
  可证伪），绝不触碰用户手动添加的有效图层（用户表达优先红线）；
- 全部确定性：同输入同裁决。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from .contracts import (
    FINAL_MAP_DEGRADED,
    FINAL_MAP_FAILED,
    FINAL_MAP_UNKNOWN,
    FINAL_MAP_VERIFIED,
    F_EXTENT_MISMATCH,
    F_LAYER_ORDER,
    F_STALE_OVERLAY,
    RENDER_ISSUES,
    RENDER_NOT_APPLICABLE,
    RENDER_STALE,
    RENDER_UNKNOWN,
    RENDER_VERIFIED,
    RESULT_LAYER_ROLES,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_NEEDS_REPAIR,
    STATUS_PENDING,
    MapCompletionFinding,
    _spec_layers,
)

logger = logging.getLogger(__name__)


def _planned_layers(chapter: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [ly for ly in (chapter.get("map_layers") or [])
            if isinstance(ly, dict) and ly.get("layer_id")]


def _bbox_intersects(a: List[float], b: List[float]) -> bool:
    """[w, s, e, n] 相交判定（确定性；退化 bbox 视为不相交）。"""
    try:
        return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])
    except (IndexError, TypeError):
        return False


def _observation_viewport_bbox(observation: Optional[Dict[str, Any]]) -> Optional[List[float]]:
    """render observation 的 viewport → bbox（前端上报形态，缺省 None）。

    observation.viewport 形如 {"bbox": [...]} 或 {"center":[lng,lat],
    "zoom": z}；仅 bbox 形态可确定性判定（zoom→bbox 换算是前端真相，
    服务端不重复实现）。
    """
    if not isinstance(observation, dict):
        return None
    viewport = observation.get("viewport")
    if not isinstance(viewport, dict):
        return None
    bbox = viewport.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            return [float(x) for x in bbox]
        except (TypeError, ValueError):
            return None
    return None


def _check_layer_order(
    chapter: Dict[str, Any], mapspec: Dict[str, Any],
) -> List[MapCompletionFinding]:
    """结果层必须绘制在上下文（reference）层之上 —— spec 数组序即绘制序。"""
    findings: List[MapCompletionFinding] = []
    spec_order = {str(ly.get("id") or ""): i
                  for i, ly in enumerate(_spec_layers(mapspec))}
    planned = _planned_layers(chapter)
    result_idx = [
        (str(ly.get("layer_id")), spec_order.get(str(ly.get("layer_id"))))
        for ly in planned
        if str(ly.get("role") or "") in RESULT_LAYER_ROLES
        and ly.get("enabled") is not False
    ]
    context_idx = [
        (str(ly.get("layer_id")), spec_order.get(str(ly.get("layer_id"))))
        for ly in planned
        if str(ly.get("role") or "") == "reference"
        and ly.get("enabled") is not False
    ]
    for rid, ri in result_idx:
        if ri is None:
            continue
        for cid, ci in context_idx:
            if ci is not None and ci is not None and ri < ci:
                findings.append(MapCompletionFinding(
                    code=F_LAYER_ORDER,
                    severity="warning",
                    target=rid,
                    detail=f"result layer '{rid[:48]}' renders below context "
                           f"layer '{cid[:48]}'",
                ))
                break
    return findings


def _check_extent(
    result_bbox: Optional[List[float]],
    observation: Optional[Dict[str, Any]],
) -> List[MapCompletionFinding]:
    """结果 bbox 与观察视口相交性（仅双方事实在场时判定）。"""
    if not result_bbox or not isinstance(observation, dict):
        return []
    viewport_bbox = _observation_viewport_bbox(observation)
    if viewport_bbox is None:
        return []
    if _bbox_intersects(result_bbox, viewport_bbox):
        return []
    return [MapCompletionFinding(
        code=F_EXTENT_MISMATCH,
        severity="warning",
        target="viewport",
        detail="result bbox does not intersect the observed viewport",
    )]


def _check_stale_overlays(
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
    descriptors: Optional[Dict[str, Optional[dict]]],
) -> List[MapCompletionFinding]:
    """陈旧覆盖层检测（保守红线：只告警死 ref / superseded ref 图层）。

    判定（确定性）：spec 图层不在当前计划图层内，且其 source ref 满足
    之一 —— (a) ref 不在 ref store（被 TTL/LRU 驱逐或属上个任务）；
    (b) descriptor.status ∈ {superseded, expired, stale}。用户添加的
    有效图层（ref 存活 + 非 superseded）永不误伤。
    """
    findings: List[MapCompletionFinding] = []
    planned_ids = {str(ly.get("layer_id") or "") for ly in _planned_layers(chapter)}
    descriptors = descriptors if isinstance(descriptors, dict) else {}
    for ly in _spec_layers(mapspec):
        lid = str(ly.get("id") or "")
        if not lid or lid in planned_ids:
            continue
        src = str(ly.get("source") or "")
        if not src:
            continue
        desc = descriptors.get(src)
        if desc is None:
            # source 无 descriptor：可能 basemap / xyz（无 ref 语义）—— 只
            # 在 descriptor 字典显式登记过该 source 时才判死 ref，避免误伤
            continue
        status = str((desc or {}).get("status") or "")
        if status in ("superseded", "expired", "stale"):
            findings.append(MapCompletionFinding(
                code=F_STALE_OVERLAY,
                severity="warning",
                target=lid,
                detail=f"non-planned layer '{lid[:48]}' shows {status} data "
                       "(previous task leftover)",
            ))
    return findings


def collect_final_map_findings(
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
    *,
    descriptors: Optional[Dict[str, Optional[dict]]] = None,
    result_bbox: Optional[List[float]] = None,
    render_observation: Optional[Dict[str, Any]] = None,
) -> List[MapCompletionFinding]:
    """V3 缺口检查（顺序 / 越界 / 陈旧覆盖层）—— warning 级增值披露。"""
    planned = _planned_layers(chapter)
    if not planned:
        return []
    findings: List[MapCompletionFinding] = []
    findings.extend(_check_layer_order(chapter, mapspec))
    findings.extend(_check_extent(result_bbox, render_observation))
    findings.extend(_check_stale_overlays(chapter, mapspec, descriptors))
    return findings


def aggregate_final_map_status(
    *,
    has_planned_layers: bool,
    base_status: str,
    render_status: str,
    v3_findings: List[MapCompletionFinding],
) -> str:
    """最终裁决聚合（确定性；在 result.status 定格后调用）。"""
    if not has_planned_layers:
        return FINAL_MAP_UNKNOWN
    if base_status == STATUS_FAILED or render_status == RENDER_ISSUES:
        return FINAL_MAP_FAILED
    if (base_status == STATUS_COMPLETE
            and render_status in (RENDER_VERIFIED, RENDER_NOT_APPLICABLE)
            and not v3_findings):
        return FINAL_MAP_VERIFIED
    if base_status in (STATUS_COMPLETE, STATUS_NEEDS_REPAIR):
        return FINAL_MAP_DEGRADED
    return FINAL_MAP_UNKNOWN


def verify_final_map(
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
    *,
    descriptors: Optional[Dict[str, Optional[dict]]] = None,
    render_status: str = RENDER_UNKNOWN,
    base_status: str = STATUS_PENDING,
    result_bbox: Optional[List[float]] = None,
    render_observation: Optional[Dict[str, Any]] = None,
) -> Tuple[str, List[MapCompletionFinding]]:
    """最终地图状态裁决（收集 + 聚合；测试/独立调用便利入口）。"""
    planned = _planned_layers(chapter)
    v3_findings = collect_final_map_findings(
        chapter, mapspec, descriptors=descriptors,
        result_bbox=result_bbox, render_observation=render_observation)
    status = aggregate_final_map_status(
        has_planned_layers=bool(planned),
        base_status=base_status,
        render_status=render_status,
        v3_findings=v3_findings,
    )
    return status, v3_findings


__all__ = [
    "verify_final_map",
    "FINAL_MAP_VERIFIED",
    "FINAL_MAP_DEGRADED",
    "FINAL_MAP_FAILED",
    "FINAL_MAP_UNKNOWN",
]
