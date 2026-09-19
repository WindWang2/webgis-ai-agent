"""Geofence 红线围栏与抓取预算（ADR-0195 D8）。

两层围栏：
- 区域围栏：RedlineRegistry（内嵌空表 + env JSON 可注入），bbox/半径与
  围栏相交即按策略 BLOCK；
- 预算围栏：单次请求 bbox 面积上限（默认 ~500km×500km），超限 BLOCK。
围栏判定保守（ring bbox 相交即算命中），宁可误拦可疑抓取也不放行越界。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

from shapely.geometry import Polygon, box

from app.services.spatial_guardrails.errors import (
    GeofenceRedlineViolationError,
)
from app.services.spatial_guardrails.geo_index import (
    EARTH_DEG_KM_LAT,
    EARTH_DEG_KM_LNG,
)
from app.services.spatial_guardrails.types import (
    CODE_BBOX_AREA_BUDGET_EXCEEDED,
    CODE_GEOFENCE_REDLINE_VIOLATION,
    DefenseMode,
    GuardLevel,
    GuardrailIssue,
)

# 受围栏管制的动作词表；"coarse"/"metadata" 类低敏动作放行
RESTRICTED_ACTIONS = {"fetch", "crawl", "scrape", "download", "query_detail"}


@dataclass
class RedlineZone:
    name: str
    ring: list[tuple[float, float]]
    policy: str  # "no_fetch" | "coarse_only"

    def to_dict(self) -> dict:
        return {"name": self.name, "policy": self.policy}


@dataclass
class RedlineRegistry:
    zones: list[RedlineZone] = field(default_factory=list)

    @classmethod
    def from_json(cls, path: str) -> "RedlineRegistry":
        """加载运维注入的围栏清单：
        {"zones": [{"name", "ring": [[lng,lat]...], "policy"}]}"""
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        zones = [
            RedlineZone(
                name=str(z.get("name", "unnamed")),
                ring=[(float(p[0]), float(p[1])) for p in z.get("ring", [])],
                policy=str(z.get("policy", "no_fetch")),
            )
            for z in payload.get("zones", [])
        ]
        return cls(zones=[z for z in zones if len(z.ring) >= 3])

    def _hit(self, bbox: tuple[float, float, float, float]) -> RedlineZone | None:
        """bbox 与围栏环的完整相交判定（GIS-104）。

        旧实现只查「bbox 四角是否落在环内」+「环 bbox 是否整体包含于
        bbox」，会漏掉 bbox 横穿围栏内部（两角在外、围栏延伸出 bbox 上下）
        的场景。改为 shapely 矩形-多边形求交，任意重叠即命中（保守）。
        """
        query = box(bbox[0], bbox[1], bbox[2], bbox[3])
        for zone in self.zones:
            if len(zone.ring) < 3:
                continue
            try:
                zone_poly = Polygon(zone.ring)
                if not zone_poly.is_valid:
                    zone_poly = zone_poly.buffer(0)
            except Exception:  # noqa: BLE001 — 退化环跳过，不阻断其余围栏
                continue
            if query.intersects(zone_poly):
                return zone
        return None

    def check_bbox(self, bbox: list[float], *, action: str = "fetch") -> None:
        if action.lower() not in RESTRICTED_ACTIONS:
            return
        box = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        hit = self._hit(box)
        if hit is not None:
            raise GeofenceRedlineViolationError(
                GuardrailIssue(
                    level=GuardLevel.L2_GEOGRAPHIC_BOUNDS,
                    mode=DefenseMode.BLOCK,
                    code=CODE_GEOFENCE_REDLINE_VIOLATION,
                    message=(
                        f"请求范围 bbox={bbox} 命中红线围栏「{hit.name}」"
                        f"（policy={hit.policy}），动作 {action} 被拒绝"
                    ),
                    location="$",
                    evidence={"bbox": list(bbox), "action": action,
                              "zone": hit.to_dict()},
                )
            )

    def check_radius(
        self, lng: float, lat: float, radius_km: float, *, action: str = "fetch"
    ) -> None:
        """以查询点为中心构造外接 bbox 后走 check_bbox。

        GIS-104：经度半宽按 cos(lat) 收缩（旧实现漏了 cos(lat)，中纬度
        会过度放大经度范围 → 误拦）。
        """
        if action.lower() not in RESTRICTED_ACTIONS:
            return
        cos_lat = max(math.cos(math.radians(lat)), 1e-6)
        d_lng = radius_km / max(EARTH_DEG_KM_LNG * cos_lat, 1e-9)
        dlat = radius_km / EARTH_DEG_KM_LAT
        bbox = [lng - d_lng, lat - dlat, lng + d_lng, lat + dlat]
        self.check_bbox(bbox, action=action)


def check_area_budget(
    bbox: list[float], *, max_km2: float = 250_000.0
) -> None:
    """bbox 面积预算：超限抛 GeofenceRedlineViolationError。"""
    minx, miny, maxx, maxy = (float(v) for v in bbox)
    dlng_km = abs(maxx - minx) * EARTH_DEG_KM_LNG * math.cos(math.radians((miny + maxy) / 2.0))
    dlat_km = abs(maxy - miny) * EARTH_DEG_KM_LAT
    area = dlng_km * dlat_km
    if area > max_km2:
        raise GeofenceRedlineViolationError(
            GuardrailIssue(
                level=GuardLevel.L2_GEOGRAPHIC_BOUNDS,
                mode=DefenseMode.BLOCK,
                code=CODE_BBOX_AREA_BUDGET_EXCEEDED,
                message=(
                    f"请求 bbox={bbox} 面积约 {area:.0f}km²，超出单次抓取预算 "
                    f"{max_km2:.0f}km² —— 请缩小范围后重试"
                ),
                location="$",
                evidence={"bbox": list(bbox), "area_km2": round(area, 1),
                          "budget_km2": max_km2},
            )
        )
