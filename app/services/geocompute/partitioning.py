"""GeoCompute V8 空间感知分区（Phase D，ADR-0133 §4）。

纯函数/有界数据契约：把输入切成分区（plan）、把分区结果按明确的 seam
语义合并（merge）。本模块**不执行**任何算法 —— 执行仍是既有 ops 算子
（分区 job 就是带 ``_partition`` 参数的普通节点 job，幂等键天然互异），
栅格裁剪/镶嵌委托 ``app/lib/geo_analysis/raster_mosaic``（重依赖惰性）。

seam 语义（唯一真相，merge 与 plan 共同遵守）：
- **raster_grid**：源栅格按像素窗口网格切分；每个分区带 halo 窗口
  （``halo_px`` 外扩、钳在栅格界内）。分区 job 在 **halo 窗口**上计算，
  合并时只把 **core 窗口**写回输出网格（halo 裁除）；core 并集 = 全幅、
  互不重叠 → 输出确定性，CRS/transform 与源一致。
- **vector_grid**：bbox 网格；要素按**代表点**（几何 bbox 中心）唯一
  分配到格（无 halo 时零复制零丢失）；``halo_ratio`` 外扩的 halo bbox
  用于**邻域复制**（同一要素可进多个分区 job），合并按内容指纹去重 ——
  内容相同的要素塌缩为一个（诚实边界：真正的源内重复也被塌缩，
  语义上与「同内容要素」不可区分）。
- CRS：分区与合并只携带/回写 CRS 元数据，绝不重投影（重投影是显式
  reproject 节点的职责）。

自适应与偏斜：``adaptive_tile_count`` 按内存预算/行数下界收缩 tile 数
（小输入不值得 fan-out）；``detect_skew`` 在合并后给出偏斜报告（证据，
不自动重切 —— 重切是后续版本的工作）。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from app.services.geocompute.errors import NodeExecutionError
from app.services.geocompute.plan import PartitionSpec

#: 单节点分区数硬上界（与 spec 的 le=256 双保险；事件/证据基数有界）。
MAX_TILES = 256

#: 偏斜判定阈值：max/mean 超过它 → 报告（证据；不自动处置）。
SKEW_RATIO_THRESHOLD = 4.0

#: 已接线的可分区类别（诚实词表：声明 partition 但类别不在其中 →
#: 类型化 PARTITION_UNSUPPORTED，绝不静默按普通节点执行）。
PARTITIONABLE_CATEGORIES: frozenset[str] = frozenset({
    "filter",                    # 谓词过滤（逐要素）
    "vector_operation",          # buffer/clip/dissolve/overlay（逐要素/邻域）
    "raster_window_operation",   # raster_calculator/resample/windowed_band_index
})


def ensure_partition_supported(node: Any) -> None:
    """partition 声明 × 类别的支持性校验（执行期；typed 拒绝）。"""
    category = getattr(node, "category", None)
    value = getattr(category, "value", str(category))
    if value not in PARTITIONABLE_CATEGORIES:
        err = NodeExecutionError(
            f"partition declared on non-partitionable category '{value}' "
            f"(wired: {', '.join(sorted(PARTITIONABLE_CATEGORIES))})",
            retry_safe=False,
            details={"node_id": getattr(node, "node_id", ""),
                     "category": value, "reason": "PARTITION_UNSUPPORTED"},
        )
        err.code = "PARTITION_UNSUPPORTED"  # typed 语义码（证据/REST 投影）
        raise err


# ═════════════════════════════ 分区计划（纯数据 + 纯函数）═════════════════════════════


@dataclass(frozen=True)
class RasterPartition:
    """raster 分区（窗口元数据；像素单位，行/列 off 为源栅格坐标）。"""

    index: int
    #: 计算窗口（含 halo；worker 在此窗口上裁剪并执行）
    col_off: int
    row_off: int
    width: int
    height: int
    #: core 窗口（合并时写回的区域；halo 裁除的依据）
    core_col_off: int
    core_row_off: int
    core_width: int
    core_height: int

    def core_window(self) -> dict[str, int]:
        return {"col_off": self.core_col_off, "row_off": self.core_row_off,
                "width": self.core_width, "height": self.core_height}

    def window(self) -> dict[str, int]:
        return {"col_off": self.col_off, "row_off": self.row_off,
                "width": self.width, "height": self.height}


@dataclass(frozen=True)
class VectorPartition:
    """vector 分区（bbox 元数据；GeoJSON 坐标域）。"""

    index: int
    #: 分配 bbox（代表点落入此格 → 该格负责此要素）
    bbox: tuple[float, float, float, float]
    #: halo bbox（分配 bbox 外扩；邻域复制的过滤域）
    halo_bbox: tuple[float, float, float, float]

    def to_meta(self) -> dict[str, Any]:
        return {"index": self.index, "bbox": list(self.bbox),
                "halo_bbox": list(self.halo_bbox)}


@dataclass
class PartitionPlan:
    """一次 fan-out 的完整计划（CRS 元数据 + 分区集）。"""

    scheme: str
    crs: Optional[str]
    tiles: int
    parts: list[Any] = field(default_factory=list)
    #: 自适应收缩记录（target → actual；证据）
    adaptive_from: Optional[int] = None
    #: 计划附随元数据（raster 源头 header 等；合并侧消费）
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def parts_by_index(self) -> dict[int, Any]:
        return {p.index: p for p in self.parts}

    def summary(self) -> dict[str, Any]:
        return {"scheme": self.scheme, "tiles": self.tiles,
                "crs": self.crs, "adaptive_from": self.adaptive_from}


def _grid_dims(tiles: int, span_x: float, span_y: float) -> tuple[int, int]:
    """tile 数 → (cols, rows)：按跨度比例分配，保证 cols*rows ≥ tiles。

    确定性两候选搜索：cols 取 ``sqrt(tiles * span_x/span_y)`` 的
    floor/ceil 变体（X 越宽列越多），rows = ceil(tiles/cols)；选
    cols*rows 最小超集（平局取列少者）—— target 4 在方形域上恰得
    2×2，而不是 3×2=6 的浪费网格。
    """
    if tiles <= 1:
        return 1, 1
    sx = max(1e-12, float(span_x))
    sy = max(1e-12, float(span_y))
    ideal = math.sqrt(tiles * (sx / sy))
    best: tuple[int, int] | None = None
    for cols in {max(1, min(tiles, int(math.floor(ideal)))),
                 max(1, min(tiles, int(math.ceil(ideal))))}:
        rows = max(1, min(tiles, int(math.ceil(tiles / cols))))
        if best is None or (cols * rows, cols) < (best[0] * best[1], best[0]):
            best = (cols, rows)
    assert best is not None
    return best


def adaptive_tile_count(
    spec: PartitionSpec,
    *,
    est_total_mem_mb: Optional[float] = None,
    input_rows: Optional[int] = None,
) -> int:
    """自适应收缩 tile 数（确定性纯函数）。

    - ``per_tile_mem_budget_mb`` 给定且总内存估计已知 → tile 数 ≥
      ceil(total/budget)（往**上**取：单 tile 超预算就多切）；
    - ``input_rows`` 已知 → tile 数收缩到 ceil(rows / min_rows_per_tile)
      （往下：行数太少 fan-out 开销倒挂）；
    - 结果钳 [1, spec.target_tiles] ∩ [1, MAX_TILES]。
    """
    tiles = max(1, int(spec.target_tiles))
    if est_total_mem_mb and spec.per_tile_mem_budget_mb:
        need = int(math.ceil(est_total_mem_mb / spec.per_tile_mem_budget_mb))
        tiles = max(tiles, min(need, MAX_TILES))
    if input_rows is not None and spec.min_rows_per_tile > 0:
        allow = int(math.ceil(input_rows / spec.min_rows_per_tile))
        tiles = min(tiles, max(1, allow))
    return max(1, min(tiles, MAX_TILES))


def plan_raster(
    spec: PartitionSpec,
    *,
    width: int,
    height: int,
    crs: Optional[str] = None,
) -> PartitionPlan:
    """raster_grid 计划：像素窗口网格 + halo（钳界）。

    网格覆盖全幅；余数行列并入最后一列/行（core 并集恰为全幅）。
    """
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise NodeExecutionError(
            "raster partition requires positive raster dimensions",
            details={"width": width, "height": height},
        )
    cols, rows = _grid_dims(spec.target_tiles, float(width), float(height))
    halo = int(spec.halo_px)
    parts: list[RasterPartition] = []
    core_ws = max(1, math.ceil(width / cols))
    core_hs = max(1, math.ceil(height / rows))
    idx = 0
    for r in range(rows):
        for c in range(cols):
            core_col = c * core_ws
            core_row = r * core_hs
            if core_col >= width or core_row >= height:
                continue  # 网格溢出格（cols*rows > 需要时）跳过
            core_w = min(core_ws, width - core_col)
            core_h = min(core_hs, height - core_row)
            col_off = max(0, core_col - halo)
            row_off = max(0, core_row - halo)
            part_w = min(width - col_off, core_w + (core_col - col_off) + halo)
            part_h = min(height - row_off, core_h + (core_row - row_off) + halo)
            parts.append(RasterPartition(
                index=idx,
                col_off=col_off, row_off=row_off,
                width=part_w, height=part_h,
                core_col_off=core_col, core_row_off=core_row,
                core_width=core_w, core_height=core_h,
            ))
            idx += 1
    return PartitionPlan(scheme="raster_grid", crs=crs, tiles=len(parts),
                         parts=parts, adaptive_from=spec.target_tiles
                         if len(parts) != spec.target_tiles else None)


def plan_vector(
    spec: PartitionSpec,
    *,
    bbox: tuple[float, float, float, float],
    crs: Optional[str] = None,
    input_rows: Optional[int] = None,
) -> PartitionPlan:
    """vector_grid 计划：bbox 均分网格 + halo_ratio 外扩。"""
    minx, miny, maxx, maxy = (float(v) for v in bbox)
    if not (maxx > minx and maxy > miny):
        raise NodeExecutionError(
            "vector partition requires a positive-area input bbox",
            details={"bbox": [minx, miny, maxx, maxy]},
        )
    tiles = adaptive_tile_count(spec, input_rows=input_rows)
    cols, rows = _grid_dims(tiles, maxx - minx, maxy - miny)
    ratio = float(spec.halo_ratio)
    dx = (maxx - minx) / cols
    dy = (maxy - miny) / rows
    parts: list[VectorPartition] = []
    idx = 0
    for r in range(rows):
        for c in range(cols):
            x0 = minx + c * dx
            y0 = miny + r * dy
            x1 = x0 + dx if c < cols - 1 else maxx
            y1 = y0 + dy if r < rows - 1 else maxy
            hx = (x1 - x0) * ratio
            hy = (y1 - y0) * ratio
            parts.append(VectorPartition(
                index=idx,
                bbox=(x0, y0, x1, y1),
                halo_bbox=(x0 - hx, y0 - hy, x1 + hx, y1 + hy),
            ))
            idx += 1
    return PartitionPlan(scheme="vector_grid", crs=crs, tiles=len(parts),
                         parts=parts,
                         adaptive_from=spec.target_tiles
                         if len(parts) != spec.target_tiles else None)


# ═══════════════════════ 分配 / 切片（worker 侧执行输入）═══════════════════════


def representative_point(feature: dict[str, Any]) -> Optional[tuple[float, float]]:
    """要素代表点 = 几何 bbox 中心（确定性、廉价、与坐标序无关）。

    非 standard GeoJSON 几何 / 空坐标 → None（调用方决定丢弃或保留）。
    """
    geom = feature.get("geometry") if isinstance(feature, dict) else None
    if not isinstance(geom, dict):
        return None
    coords = geom.get("coordinates")

    def _walk(node: Any) -> Iterable[tuple[float, float]]:
        if isinstance(node, (list, tuple)) and node:
            if len(node) >= 2 and isinstance(node[0], (int, float)) \
                    and isinstance(node[1], (int, float)):
                yield (float(node[0]), float(node[1]))
            else:
                for child in node:
                    yield from _walk(child)

    xs: list[float] = []
    ys: list[float] = []
    for x, y in _walk(coords):
        xs.append(x)
        ys.append(y)
    if not xs:
        return None
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def in_bbox(pt: tuple[float, float], bbox: tuple[float, float, float, float],
             *, boundary: bool = True) -> bool:
    x, y = pt
    x0, y0, x1, y1 = bbox
    if boundary:
        return (x0 <= x <= x1) and (y0 <= y <= y1)
    return (x0 < x < x1) and (y0 < y < y1)


def assign_features(
    features: list[dict[str, Any]], partitions: list[VectorPartition]
) -> dict[int, list[dict[str, Any]]]:
    """代表点分配（无 halo 时零复制零丢失；代表点无法计算的要素**保留在
    分区 0** —— fail-open 丢弃会造成静默数据丢失，绝不）。

    返回 {partition_index: [feature, ...]}（缺省格为空表）。
    """
    out: dict[int, list[dict[str, Any]]] = {p.index: [] for p in partitions}
    if not partitions:
        return out
    for feature in features:
        pt = representative_point(feature)
        target: Optional[int] = None
        if pt is not None:
            for p in partitions:
                if in_bbox(pt, p.bbox):
                    target = p.index
                    break
        if target is None:
            target = partitions[0].index
        out[target].append(feature)
    return out


def slice_features_for_halo(
    features: list[dict[str, Any]], partition: VectorPartition
) -> list[dict[str, Any]]:
    """halo 过滤（worker 侧）：代表点 ∈ halo_bbox 的要素进入本分区 job。

    输入**已是**本分区分配结果（executor 先按 assign 切好）时，halo
    复制由 executor 对相邻格重放 assign 完成 —— 这里只做谓词过滤。
    """
    return [f for f in features
            if (pt := representative_point(f)) is None
            or in_bbox(pt, partition.halo_bbox)]


def feature_identity(feature: dict[str, Any]) -> str:
    """要素内容指纹（合并去重的身份键；sha1 前 16 —— 有界、确定性）。"""
    payload = json.dumps(feature, sort_keys=True, ensure_ascii=False,
                         default=str)
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def merge_vector_payloads(
    tile_payloads: list[dict[str, Any]],
    *,
    node_id: str,
) -> dict[str, Any]:
    """vector 合并：拼接 + 内容指纹去重（seam 语义见模块 docstring）。

    tile_payloads：各分区 job 的输出载荷（features）。输出 metadata 记录
    ``partition`` 合并证据（tiles/输入行/去重后行）。
    """
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    input_rows = 0
    for payload in tile_payloads:
        feats = payload.get("features") or []
        input_rows += len(feats)
        for f in feats:
            ident = feature_identity(f)
            if ident in seen:
                continue
            seen.add(ident)
            merged.append(f)
    meta = {"partition_merge": {
        "tiles": len(tile_payloads), "input_rows": input_rows,
        "rows": len(merged), "dedup_removed": input_rows - len(merged),
    }}
    # metadata 合并：分区 job 的业务 metadata（首 tile 基础上叠加）
    base_meta: dict[str, Any] = {}
    for payload in tile_payloads:
        m = payload.get("metadata")
        if isinstance(m, dict) and m:
            base_meta.update({k: v for k, v in m.items() if k != "partition_merge"})
            break
    base_meta.update(meta)
    return {"features": merged, "metadata": base_meta}


# ═════════════════════════════ 偏斜检测（证据）════════════════════════════


def detect_skew(row_counts: list[int]) -> Optional[dict[str, Any]]:
    """分区行数偏斜报告（max/mean > 阈值 → 报告；证据不处置）。

    全零/单分区/空 → None（无偏斜概念）。
    """
    counts = [int(c) for c in row_counts if c is not None]
    if len(counts) <= 1 or not any(counts):
        return None
    mean = sum(counts) / len(counts)
    if mean <= 0:
        return None
    peak = max(counts)
    ratio = peak / mean
    if ratio <= SKEW_RATIO_THRESHOLD:
        return None
    return {
        "ratio": round(ratio, 2),
        "peak_partition": counts.index(peak),
        "peak_rows": peak,
        "mean_rows": round(mean, 1),
        "counts": counts[:64],  # 有界证据
    }


# ═══════════════════════ raster 裁剪/镶嵌（lib 委托的薄接线）═══════════════════════


def crop_raster_window(
    raster_path: str, window: dict[str, int], out_path: str
) -> dict[str, Any]:
    """裁剪 halo 窗口（worker 侧 tile job 前置步骤；重依赖在 lib）。"""
    from app.lib.geo_analysis.raster_mosaic import crop_window

    return crop_window(raster_path, window, out_path)


def merge_raster_tiles(
    tiles: list[dict[str, Any]], *, out_path: str,
    width: int, height: int, crs: Optional[str],
    transform: Optional[list[float]],
) -> dict[str, Any]:
    """raster 合并（coordinator 侧）：core 窗口写回全幅网格。

    ``tiles``：[{path, core_window, ...}]（tile job 输出 metadata 携带）。
    """
    from app.lib.geo_analysis.raster_mosaic import mosaic_tiles

    return mosaic_tiles(
        tiles, out_path=out_path, width=width, height=height,
        crs=crs, transform=transform,
    )
