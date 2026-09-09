"""V6 空间统计（ADR-0118 W2）：确定性网格直方图 + 几何复杂度 + CRS。

「诚实有界」延伸（statistics.py 文化的延续）：
- 直方图计数可以是 **measured**（adapter/collector 提供逐格计数）或
  **assumption**（仅 extent×row_count 的均匀合成）—— basis 永远随行；
- 没有 extent 就没有直方图（绝不伪造全球 bbox）；
- 选择率返回 None 表示「不可估」，调用方回落常数路径。

依赖方向：federated → statistics（单向）；DatasetStatistics 以 **dict 形态**
存直方图（沿 QueryPlan.cost 的先例，避免 statistics → federated 反向依赖）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

#: 默认网格（8×8 = 64 格；结构性有界，绝不做逐格海量统计）。
DEFAULT_GRID_SIZE = 8
MAX_GRID_SIZE = 32

#: 几何复杂度默认（每要素平均顶点数；assumption basis）。
_GEOMETRY_TYPE_VERTEX_DEFAULTS: Dict[str, float] = {
    "point": 1.0,
    "multipoint": 4.0,
    "linestring": 20.0,
    "multilinestring": 30.0,
    "polygon": 60.0,
    "multipolygon": 120.0,
    "geometrycollection": 40.0,
}
_DEFAULT_VERTICES_UNKNOWN = 32.0


class SpatialGridHistogram(BaseModel):
    """确定性等分网格直方图（row-major，自 miny 行起）。"""

    model_config = ConfigDict(extra="forbid")

    grid_size: int = Field(ge=1, le=MAX_GRID_SIZE)
    extent: List[float] = Field(min_length=4, max_length=4)
    counts: List[float]  # len == grid_size²
    total_rows: Optional[float] = None
    basis: str = "assumption"  # measured | estimated | assumption

    def to_meta(self) -> Dict[str, Any]:
        return {
            "grid_size": self.grid_size,
            "extent": list(self.extent),
            "counts": list(self.counts),
            "total_rows": self.total_rows,
            "basis": self.basis,
        }

    @classmethod
    def from_meta(cls, meta: Any) -> Optional["SpatialGridHistogram"]:
        """dict → 直方图（形状不合法返回 None，绝不抛错阻断查询路径）。"""
        if not isinstance(meta, dict):
            return None
        try:
            grid = int(meta.get("grid_size", DEFAULT_GRID_SIZE))
            counts = meta.get("counts")
            extent = meta.get("extent")
            if not (1 <= grid <= MAX_GRID_SIZE):
                return None
            if not isinstance(counts, list) or len(counts) != grid * grid:
                return None
            if not isinstance(extent, list) or len(extent) != 4:
                return None
            clean_counts = [float(c) for c in counts]
            clean_extent = [float(x) for x in extent]
            if any(c != c or c < 0 for c in clean_counts):  # NaN/负数
                return None
            return cls(
                grid_size=grid,
                extent=clean_extent,
                counts=clean_counts,
                total_rows=(
                    float(meta["total_rows"])
                    if isinstance(meta.get("total_rows"), (int, float))
                    else sum(clean_counts)
                ),
                basis=str(meta.get("basis", "assumption")),
            )
        except (TypeError, ValueError):
            return None


def build_histogram(
    *,
    row_count: Optional[int],
    extent: Optional[List[float]],
    grid_size: int = DEFAULT_GRID_SIZE,
) -> SpatialGridHistogram:
    """均匀合成直方图（assumption basis；确定性：逐格均分，余数入前序格）。"""
    if (
        not extent
        or len(extent) != 4
        or not all(isinstance(x, (int, float)) for x in extent)
    ):
        raise ValueError("extent must be [minx, miny, maxx, maxy]")
    minx, miny, maxx, maxy = (float(x) for x in extent)
    if not (maxx > minx and maxy > miny):
        raise ValueError(f"degenerate extent {extent!r}")
    if not (1 <= grid_size <= MAX_GRID_SIZE):
        raise ValueError(f"grid_size out of bounds: {grid_size}")
    n_cells = grid_size * grid_size
    if isinstance(row_count, (int, float)) and row_count > 0:
        per = float(row_count) / n_cells
        counts = [per] * n_cells
        total: Optional[float] = float(row_count)
    else:
        counts = [0.0] * n_cells
        total = None
    return SpatialGridHistogram(
        grid_size=grid_size,
        extent=[minx, miny, maxx, maxy],
        counts=counts,
        total_rows=total,
        basis="assumption",
    )


def build_histogram_from_cells(
    *, counts: List[float], extent: List[float], basis: str = "measured"
) -> SpatialGridHistogram:
    """实测逐格计数直方图（长度必须成平方数；由 grid_size 推导）。"""
    n = len(counts)
    grid = 1
    while grid * grid < n:
        grid += 1
    if grid * grid != n:
        raise ValueError(f"counts length {n} is not a perfect square")
    if not (1 <= grid <= MAX_GRID_SIZE):
        raise ValueError(f"derived grid_size out of bounds: {grid}")
    hist = build_histogram(
        row_count=int(sum(counts)) or None, extent=extent, grid_size=grid
    )
    return SpatialGridHistogram(
        grid_size=grid,
        extent=hist.extent,
        counts=[float(c) for c in counts],
        total_rows=float(sum(counts)),
        basis=basis,
    )


def estimate_bbox_selectivity(
    hist: Optional[SpatialGridHistogram], bbox: Optional[List[float]]
) -> Optional[float]:
    """bbox 命中选择率：Σ(格内计数 × 重叠面积比) / 总计数。

    不可估（无直方图/退化 bbox/零计数）→ None（调用方回落常数）。
    """
    if hist is None or bbox is None or len(bbox) != 4:
        return None
    total = sum(hist.counts)
    if total <= 0:
        return None
    minx, miny, maxx, maxy = (float(x) for x in bbox)
    if maxx <= minx or maxy <= miny:
        return None
    ex_minx, ex_miny, ex_maxx, ex_maxy = hist.extent
    cell_w = (ex_maxx - ex_minx) / hist.grid_size
    cell_h = (ex_maxy - ex_miny) / hist.grid_size
    if cell_w <= 0 or cell_h <= 0:
        return None
    hit = 0.0
    for i, count in enumerate(hist.counts):
        if count <= 0:
            continue
        row, col = divmod(i, hist.grid_size)
        cx0 = ex_minx + col * cell_w
        cy0 = ex_miny + row * cell_h
        ox = min(maxx, cx0 + cell_w) - max(minx, cx0)
        oy = min(maxy, cy0 + cell_h) - max(miny, cy0)
        if ox <= 0 or oy <= 0:
            continue
        hit += count * ((ox * oy) / (cell_w * cell_h))
    return max(0.0, min(1.0, hit / total))


def geometry_complexity(stats: Optional[Any]) -> float:
    """每要素平均顶点数估计：measured(avg_vertices) > 几何类型默认。

    永远返回正数（成本模型的除数保护由调用方负责）。
    """
    measured = getattr(stats, "avg_vertices", None)
    if isinstance(measured, (int, float)) and measured > 0:
        return float(measured)
    gtype = str(getattr(stats, "geometry_type", None) or "").strip().lower()
    return _GEOMETRY_TYPE_VERTEX_DEFAULTS.get(gtype, _DEFAULT_VERTICES_UNKNOWN)


def histogram_to_meta(hist: SpatialGridHistogram) -> Dict[str, Any]:
    """直方图 → DatasetStatistics.spatial_histogram 的 dict 形态。"""
    return hist.to_meta()


def attach_spatial_stats(stats: Any, descriptor: Any) -> Any:
    """把空间统计收割进 DatasetStatistics（纯函数；返回新实例）。

    收割优先级：meta 实测直方图 → extent×row_count 均匀合成（assumption）。
    无 extent → 不产直方图（诚实 unknown）。
    """
    meta = getattr(descriptor, "metadata", None)
    if not isinstance(meta, dict):
        meta = {}
    updates: Dict[str, Any] = {}
    crs = meta.get("srs") or meta.get("crs")
    if isinstance(crs, str) and crs:
        updates["crs"] = crs
    avg_v = meta.get("avg_vertices")
    if isinstance(avg_v, (int, float)) and avg_v > 0:
        updates["avg_vertices"] = float(avg_v)
    hist: Optional[SpatialGridHistogram] = None
    raw_hist = meta.get("spatial_histogram")
    if isinstance(raw_hist, dict) and not raw_hist.get("extent"):
        # 实测逐格计数可以省略 extent（相对 dataset bbox 语义）—— 收割方
        # 显式补齐来源，from_meta 依旧不猜测。
        extent = meta.get("bbox") or getattr(descriptor, "bbox", None)
        if isinstance(extent, (list, tuple)) and len(extent) == 4:
            raw_hist = {**raw_hist, "extent": [float(x) for x in extent]}
    if raw_hist is not None:
        hist = SpatialGridHistogram.from_meta(raw_hist)
    if hist is None:
        extent = meta.get("bbox") or getattr(descriptor, "bbox", None)
        if isinstance(extent, (list, tuple)) and len(extent) == 4:
            row_count = meta.get("row_count") or getattr(
                descriptor, "feature_count", None
            )
            try:
                hist = build_histogram(row_count=row_count, extent=list(extent))
            except ValueError:
                hist = None
    if hist is not None:
        updates["spatial_histogram"] = hist.to_meta()
    if not updates:
        return stats
    return stats.model_copy(update=updates)


__all__ = [
    "SpatialGridHistogram",
    "DEFAULT_GRID_SIZE",
    "MAX_GRID_SIZE",
    "build_histogram",
    "build_histogram_from_cells",
    "estimate_bbox_selectivity",
    "geometry_complexity",
    "histogram_to_meta",
    "attach_spatial_stats",
]
