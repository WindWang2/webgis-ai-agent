"""Geometry repair disclosure —— 几何修复的唯一披露实现（science-v4 W3）。

修复 NEVER silent：全仓库的几何有效性门都应走本模块，修复动作必须产出
可观测记录（数量 / 方法 / 面积变化），strict 模式下直接类型化拒绝。

- 无效几何 + ``strict=True`` → ``InvalidGeometry``（科学词表，ValueError 系，
  correction_hint 通道）；
- 无效几何 + ``strict=False`` → ``shapely.make_valid`` 修复 + 逐要素记录
  （method / is_valid_reason / 面积前后）；
- 修复后仍无效 / 为空 → ``InvalidGeometry``（修复不是无限自救）。

消费方：zonal_statistics（W3 门）、spatial_aggregate（W3 披露）、
to_utm_gdf_with_note（W3 修复计数）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

import shapely
from shapely.geometry.base import BaseGeometry

from app.lib.gis.scientific_errors import InvalidGeometry


@dataclass
class GeometryRepairRecord:
    """单个几何的修复记录（进结果行 / 披露通道）。"""

    index: int
    method: str = "make_valid"
    reason: str = ""  # shapely is_valid_reason（无效原因）
    area_before: Optional[float] = None
    area_after: Optional[float] = None

    @property
    def area_delta(self) -> Optional[float]:
        if self.area_before is None or self.area_after is None:
            return None
        return self.area_after - self.area_before

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "method": self.method,
            "reason": self.reason[:120],
            "area_before": self.area_before,
            "area_after": self.area_after,
            "area_delta": self.area_delta,
        }


@dataclass
class GeometryRepairReport:
    """一批几何的修复汇总（never-silent 的可观测面）。"""

    checked: int = 0
    invalid: int = 0
    repaired: int = 0
    failed: int = 0
    records: List[GeometryRepairRecord] = field(default_factory=list)

    @property
    def area_delta_total(self) -> Optional[float]:
        deltas = [r.area_delta for r in self.records if r.area_delta is not None]
        return sum(deltas) if deltas else None

    def to_dict(self) -> dict:
        return {
            "checked": self.checked,
            "invalid": self.invalid,
            "repaired": self.repaired,
            "failed": self.failed,
            "area_delta_total": self.area_delta_total,
            "records_truncated": len(self.records) > 32,
            "records": [r.to_dict() for r in self.records[:32]],
        }


def ensure_valid_geometry(
    geom: BaseGeometry,
    *,
    index: int = 0,
    strict: bool = True,
    context: str = "geometry",
) -> Tuple[BaseGeometry, Optional[GeometryRepairRecord]]:
    """有效性门：valid 原样通过；invalid 按 strict 拒绝或修复+记录。

    返回 ``(geometry, record)``；record 为 None 表示无需修复。
    """
    if geom is None:
        raise InvalidGeometry(
            f"{context}[{index}]: geometry is None",
            correction_hint="provide a valid geometry (null geometry is not analyzable)",
        )
    if geom.is_valid:
        return geom, None

    reason = ""
    try:
        reason = shapely.is_valid_reason(geom)
    except Exception:  # noqa: BLE001 — reason 是披露增强，非必要条件
        reason = ""

    if strict:
        raise InvalidGeometry(
            f"{context}[{index}]: invalid geometry ({reason or 'topologically invalid'})",
            correction_hint=(
                "fix the input geometry (ST_MakeValid / buffer(0) offline) or "
                "re-run with strict=False to auto-repair with disclosure"
            ),
        )

    fixed = shapely.make_valid(geom)
    if fixed.is_empty or not fixed.is_valid:
        raise InvalidGeometry(
            f"{context}[{index}]: make_valid could not repair geometry "
            f"({reason or 'topologically invalid'})",
            correction_hint="repair the source geometry offline; automatic repair failed",
        )
    area_before = _area_of(geom)
    area_after = _area_of(fixed)
    return fixed, GeometryRepairRecord(
        index=index, method="make_valid", reason=reason,
        area_before=area_before, area_after=area_after,
    )


def repair_geometry_sequence(
    geoms: Iterable[BaseGeometry],
    *,
    strict: bool = True,
    context: str = "geometry",
) -> Tuple[List[BaseGeometry], GeometryRepairReport]:
    """一批几何统一走 ensure_valid_geometry，产出汇总报告。"""
    out: List[BaseGeometry] = []
    report = GeometryRepairReport()
    for i, g in enumerate(geoms):
        report.checked += 1
        fixed, rec = ensure_valid_geometry(g, index=i, strict=strict, context=context)
        if rec is not None:
            report.invalid += 1
            report.repaired += 1
            report.records.append(rec)
        out.append(fixed)
    return out, report


def _area_of(geom: BaseGeometry) -> Optional[float]:
    try:
        if geom.is_empty:
            return 0.0
        return float(abs(geom.area))
    except Exception:  # noqa: BLE001 — 无面积概念（点/线）如实返回 None
        return None
