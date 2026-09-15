"""陆地掩膜校验：三区制海陆判定 + 设施类型地表常识（ADR-0195 D5）。

三区制消除假阳性：只有 OCEAN_CONFIDENT（距最近陆地 > ocean_confirm_km，
默认 150km）上的已知陆上设施才触发 GeographicImpossibilityError 硬阻断；
COASTAL_BAND / WATER_BODY 一律降级警示 —— 宁可 WARN 放行，绝不冤杀。
"""
from __future__ import annotations

from enum import Enum

from app.services.spatial_guardrails.errors import (
    GeographicImpossibilityError,
)
from app.services.spatial_guardrails.geo_index import (
    point_in_ring,
    ring_min_distance_km,
)
from app.services.spatial_guardrails.types import (
    CODE_COASTAL_BAND_UNCERTAIN,
    CODE_OCEAN_POINT_ON_LAND_FACILITY,
    CODE_SUSPICIOUS_OCEAN_POINT,
    CODE_WATER_BODY_FACILITY,
    DefenseMode,
    GuardLevel,
    GuardrailIssue,
)


class SurfaceZone(str, Enum):
    LAND = "LAND"
    COASTAL_BAND = "COASTAL_BAND"
    OCEAN_CONFIDENT = "OCEAN_CONFIDENT"
    WATER_BODY = "WATER_BODY"


# 陆上设施词表：这些类型出现在确信开阔水域 = 物理不可能
LAND_FACILITY_KINDS = {
    "school", "hospital", "building", "poi", "road", "factory", "station",
    "residential", "office", "park", "bridge", "airport", "landmark",
}
# 水上合法设施：出现在水域不算异常
WATER_FACILITY_KINDS = {"dock", "port", "buoy", "aquaculture", "vessel", "ship", "harbor"}


def classify_point(
    lng: float, lat: float, *, ocean_confirm_km: float = 150.0
) -> SurfaceZone:
    """点分区判定：LAND / WATER_BODY / COASTAL_BAND / OCEAN_CONFIDENT。"""
    from app.services.spatial_guardrails.data import (
        get_landmask_index,
        get_water_rings,
    )

    # 内陆水体优先于陆地环判定：大型水体（里海等）被陆环包围，
    # 命中水体必须先于"环内即陆地"结论（warn-only，不硬阻断）。
    for _name, ring in get_water_rings():
        if point_in_ring(lng, lat, ring):
            return SurfaceZone.WATER_BODY
    candidates = get_landmask_index().candidates_near(lng, lat)
    for ring in candidates:
        if point_in_ring(lng, lat, ring):
            return SurfaceZone.LAND
    nearest = min(
        (ring_min_distance_km(lng, lat, ring) for ring in candidates),
        default=float("inf"),
    )
    if nearest > ocean_confirm_km:
        return SurfaceZone.OCEAN_CONFIDENT
    return SurfaceZone.COASTAL_BAND


def _issue(level: GuardLevel, mode: DefenseMode, code: str, message: str,
           location: str, evidence: dict) -> GuardrailIssue:
    return GuardrailIssue(
        level=level, mode=mode, code=code, message=message,
        location=location, evidence=evidence,
    )


def validate_facility_point(
    lng: float,
    lat: float,
    facility_kind: str | None = None,
    *,
    ocean_confirm_km: float = 150.0,
    location: str = "$",
) -> list[GuardrailIssue]:
    """设施点物理常识校验。

    - OCEAN_CONFIDENT + 已知陆上设施 → raise GeographicImpossibilityError；
    - OCEAN_CONFIDENT + 未知类型 → SUSPICIOUS_OCEAN_POINT 警示；
    - WATER_BODY / COASTAL_BAND → 警示（放行）；
    - 返回警示 issue 列表（无警示返回空表）。
    """
    zone = classify_point(lng, lat, ocean_confirm_km=ocean_confirm_km)
    evidence = {"lng": lng, "lat": lat, "zone": zone.value,
                "facility_kind": facility_kind}
    if zone == SurfaceZone.OCEAN_CONFIDENT:
        kind = (facility_kind or "").lower()
        if kind in WATER_FACILITY_KINDS:
            return []
        if kind in LAND_FACILITY_KINDS:
            raise GeographicImpossibilityError(
                _issue(
                    GuardLevel.L2_GEOGRAPHIC_BOUNDS,
                    DefenseMode.BLOCK,
                    CODE_OCEAN_POINT_ON_LAND_FACILITY,
                    f"陆上设施（{kind}）落在距最近陆地超过 {ocean_confirm_km:.0f}km 的"
                    f"开阔水域 ({lng}, {lat})，物理上不可能 —— 疑似坐标幻觉或经纬度倒置",
                    location,
                    evidence,
                )
            )
        return [
            _issue(
                GuardLevel.L2_GEOGRAPHIC_BOUNDS,
                DefenseMode.WARN_DEGRADE,
                CODE_SUSPICIOUS_OCEAN_POINT,
                f"坐标 ({lng}, {lat}) 位于确信开阔水域，且设施类型未知 —— 请核实"
                "是否为坐标幻觉（水上合法设施如 buoy/vessel 可忽略本警示）",
                location,
                evidence,
            )
        ]
    if zone == SurfaceZone.WATER_BODY:
        return [
            _issue(
                GuardLevel.L3_LANDMASK_PLAUSIBILITY,
                DefenseMode.WARN_DEGRADE,
                CODE_WATER_BODY_FACILITY,
                f"设施点 ({lng}, {lat}) 落在大型内陆水体（粗掩膜）内 —— 若为码头/"
                "水上站点可忽略；若为普通陆上设施请核实坐标",
                location,
                evidence,
            )
        ]
    if zone == SurfaceZone.COASTAL_BAND:
        return [
            _issue(
                GuardLevel.L2_GEOGRAPHIC_BOUNDS,
                DefenseMode.WARN_DEGRADE,
                CODE_COASTAL_BAND_UNCERTAIN,
                f"坐标 ({lng}, {lat}) 位于粗掩膜近岸不确定带（不阻断）",
                location,
                evidence,
            )
        ]
    return []
