"""G39 块读取调度 — 一次打开、分组批读、预算与取消（WP07）。

纪律：
- 每源**一次打开**贯穿整个执行（受控生命周期），绝不每样本重开文件；
- 站位按 (source, block) 分组：同块站位合并为一次窗口读（5,000 点同块
  = 1 次读，不是 5,000 次，也不是整幅大图）；
- 预算先于分配：值单元、单窗口读字节、每源最低块解码成本（非分块巨
  条带只请求一点也按真实条带 I/O 预估，S18）；
- 每批 checkpoint() 协作取消 + 源 stat 复核（mtime_ns/size/ino）——
  跨批改写（含改回原样的 ABA）一律 SOURCE_MUTATED_DURING_READ，已产
  内容丢弃，绝不发布混合样本（S34）；
- 读取原生全分辨率（不用 overview 冒充科学样本）；
- 记账：打开次数、触块数、窗口读次数、解码字节、批次数、站位数。
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.lib.cancellation import checkpoint
from app.services.raster_sampling import kernel
from app.services.raster_sampling.contracts import (
    INVALID_TRANSFORM_FAILED,
    MAX_BATCH_STATIONS,
    MAX_STRIP_DECODE_BYTES,
    MAX_VALUE_CELLS,
    METHOD_SOURCE_PIXEL,
    RasterSamplingError,
    SampleRow,
    SourceBinding,
)
from app.services.raster_sampling.stations import StationSet

#: 单窗口读字节上限（超预算时沿站位切半，单站位 1×1 恒可读）
_MAX_WINDOW_READ_BYTES = 64 * 1024 * 1024


@dataclass
class ReadMetrics:
    opens: int = 0
    blocks_touched: int = 0
    window_reads: int = 0
    batches: int = 0
    decoded_bytes: int = 0
    stations_processed: int = 0
    block_shapes: Dict[str, List[int]] = field(default_factory=dict)
    is_tiled: Dict[str, bool] = field(default_factory=dict)
    overview_used: bool = False

    def to_payload(self) -> Dict[str, Any]:
        return {
            "opens": self.opens,
            "blocks_touched": self.blocks_touched,
            "window_reads": self.window_reads,
            "batches": self.batches,
            "decoded_bytes": self.decoded_bytes,
            "stations_processed": self.stations_processed,
            "block_shapes": self.block_shapes,
            "is_tiled": self.is_tiled,
            "overview_used": self.overview_used,
        }


@dataclass
class SourceGrid:
    """执行期源网格事实（一次打开后采集；批读与核算依据）。"""

    binding: SourceBinding
    path: str
    crs: Optional[str]
    transform: Tuple[float, float, float, float, float, float]
    width: int
    height: int
    block_shape: Tuple[int, int]
    is_tiled: bool
    band_facts: Dict[int, kernel.BandFacts]
    stat_snapshot: Dict[str, Any]
    grid_cols: Optional[np.ndarray] = None
    grid_rows: Optional[np.ndarray] = None
    transform_failed: Optional[np.ndarray] = None

    @property
    def itemsize_est(self) -> int:
        sizes = [np.dtype(f.dtype).itemsize if f.dtype else 8
                 for f in self.band_facts.values()]
        return max(sizes) if sizes else 8

    def min_block_bytes(self) -> int:
        """单块最低解码成本：块面积 × 最大 itemsize × 波段数。"""
        bh, bw = self.block_shape
        if bh <= 0 or bw <= 0:
            bh, bw = self.height, self.width  # 未报告块：整幅最坏情形
        area = min(bh, self.height) * min(bw, self.width)
        return int(area) * self.itemsize_est * len(self.band_facts)


def stat_fingerprint(path: str) -> Dict[str, Any]:
    """源文件代次事实（dev/ino/mtime_ns/size —— ABA 检测基础）。"""
    st = os.stat(path)
    return {"dev": st.st_dev, "ino": st.st_ino,
            "mtime_ns": st.st_mtime_ns, "size": st.st_size}


def same_generation(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return (a.get("mtime_ns") == b.get("mtime_ns")
            and a.get("size") == b.get("size")
            and a.get("ino") == b.get("ino")
            and a.get("dev") == b.get("dev"))


def collect_source_grid(
    binding: SourceBinding, source: Any, ds: Any, method: str
) -> SourceGrid:
    """在**已打开**的数据集上采集网格事实 + 逐波段 facts（含超摘要补读）。"""
    if int(ds.count) < max(int(b) for b in binding.bands):
        raise RasterSamplingError(
            "BAND_OUT_OF_RANGE",
            f"source {binding.ref_id} has {ds.count} bands; plan binds "
            f"band {max(int(b) for b in binding.bands)}",
            field="bands", details={"band_count": int(ds.count)})
    try:
        bs = ds.block_shapes[0]
        block_shape = (int(bs[0]), int(bs[1]))
    except Exception:  # noqa: BLE001
        block_shape = (0, 0)
    is_tiled = bool(getattr(ds, "is_tiled", False))
    band_facts: Dict[int, kernel.BandFacts] = {}
    for band in binding.bands:
        band = int(band)
        declared_sem = binding.band_semantics.get(str(band))
        base = kernel.band_facts_from_source(
            source, band,
            declared_semantics=declared_sem,
            declared_label=binding.band_labels.get(str(band), ""),
            declared_unit=binding.band_units.get(str(band), ""))
        meta_covered = any(int(m.get("index")) == band
                           for m in (source.band_meta or []))
        if not meta_covered:
            # band_meta（≤64 波段）未覆盖的选中波段：直读真实头，
            # 不从 band 1 推断（S12）
            base = kernel.supplement_band_facts(ds, band, base)
            if declared_sem in ("categorical", "continuous"):
                base.semantic = declared_sem  # type: ignore[assignment]
                base.semantic_source = "declared"
            if binding.band_labels.get(str(band)):
                base.label = binding.band_labels[str(band)]
            if binding.band_units.get(str(band)):
                base.unit = binding.band_units[str(band)]
        band_facts[band] = base
        base.validate_method(method)
    t = ds.transform
    return SourceGrid(
        binding=binding, path=source.path,
        crs=(str(ds.crs) if ds.crs else None),
        transform=(float(t.a), float(t.b), float(t.c),
                   float(t.d), float(t.e), float(t.f)),
        width=int(ds.width), height=int(ds.height),
        block_shape=block_shape, is_tiled=is_tiled,
        band_facts=band_facts,
        stat_snapshot=stat_fingerprint(source.path),
    )


def estimate_execution_cost(
    grids: Sequence[SourceGrid], station_count: int
) -> Dict[str, Any]:
    """分配前成本预估（值单元 / 最低块解码；超限 typed 阻断）。"""
    total_bands = sum(len(g.band_facts) for g in grids)
    value_cells = station_count * total_bands
    if value_cells > MAX_VALUE_CELLS:
        raise RasterSamplingError(
            "BUDGET_EXCEEDED",
            f"execution would produce {value_cells} station×band value cells "
            f"(cap {MAX_VALUE_CELLS}) — reduce stations or band selection",
            field="budget", status_code=413,
            correction_hint="Split the plan, thin stations (larger step), or "
                            "select fewer bands.",
            details={"stations": station_count, "total_bands": total_bands})
    per_source: Dict[str, int] = {}
    for g in grids:
        cost = g.min_block_bytes()
        per_source[g.binding.ref_id] = cost
        if cost > MAX_STRIP_DECODE_BYTES:
            raise RasterSamplingError(
                "BUDGET_EXCEEDED",
                f"source {g.binding.ref_id} decodes in blocks of ~"
                f"{cost / 1e6:.0f} MB (strip/untiled layout) — a single "
                f"point still costs a full block read "
                f"(cap {MAX_STRIP_DECODE_BYTES // 1e6} MB)",
                field="sources", status_code=413,
                correction_hint="Materialize the raster through the existing "
                                "conversion path (e.g. COG tiling) before "
                                "sampling.",
                details={"block_shape": list(g.block_shape),
                         "min_block_bytes": cost})
    return {
        "stations": station_count,
        "total_bands": total_bands,
        "value_cells": value_cells,
        "min_block_bytes_per_source": per_source,
    }


def _transform_stations(station_set: StationSet, grid: SourceGrid) -> None:
    """站位坐标（站位 CRS）→ 源网格 col/row（批向量变换；失败逐站分型）。"""
    xs = np.array([s.x for s in station_set.stations], dtype=np.float64)
    ys = np.array([s.y for s in station_set.stations], dtype=np.float64)
    n = len(xs)
    crs_from = station_set.crs
    if not grid.crs:
        # 源无 CRS：地理采样 typed 阻断（站位列保留 transform_failed）
        grid.transform_failed = np.ones(n, dtype=bool)
        grid.grid_cols = grid.grid_rows = None
        return
    if str(crs_from).lower().startswith("source:"):
        ref = str(crs_from).split(":", 1)[1]
        if ref != grid.binding.ref_id:
            grid.transform_failed = np.ones(n, dtype=bool)
            grid.grid_cols = grid.grid_rows = None
            return
        src_xs, src_ys = xs, ys
    else:
        try:
            from rasterio.warp import transform as rio_transform

            src_xs, src_ys = rio_transform(
                crs_from, grid.crs, xs.tolist(), ys.tolist())
            src_xs = np.asarray(src_xs, dtype=np.float64)
            src_ys = np.asarray(src_ys, dtype=np.float64)
        except Exception:  # noqa: BLE001 — 单源变换失败不拖垮其它源
            grid.transform_failed = np.ones(n, dtype=bool)
            grid.grid_cols = grid.grid_rows = None
            return
    a, b, c, d, e, f = grid.transform
    det = a * e - b * d
    if abs(det) < 1e-18:
        grid.transform_failed = np.ones(n, dtype=bool)
        grid.grid_cols = grid.grid_rows = None
        return
    dx = src_xs - c
    dy = src_ys - f
    cols = (e * dx - b * dy) / det
    rows = (-d * dx + a * dy) / det
    grid.grid_cols = cols
    grid.grid_rows = rows
    grid.transform_failed = ~(np.isfinite(cols) & np.isfinite(rows))


def _needed_pixels(col_f: float, row_f: float, method: str
                   ) -> Tuple[int, int, int, int]:
    """站位需要的像元范围 (row0, row1, col0, col1)（含 bilinear 2×2）。"""
    if method == METHOD_SOURCE_PIXEL:
        col, row = int(math.floor(col_f)), int(math.floor(row_f))
        return row, row, col, col
    c0 = int(math.floor(col_f - 0.5))
    r0 = int(math.floor(row_f - 0.5))
    return r0, r0 + 1, c0, c0 + 1


def _read_window(
    ds: Any, bands: Sequence[int], row0: int, row1: int, col0: int, col1: int,
    grid: SourceGrid, metrics: ReadMetrics,
) -> Tuple[Dict[int, np.ndarray], Dict[int, np.ndarray], Tuple[int, int, int, int]]:
    """越界裁剪后的窗口读（masked=True，原生全分辨率）。"""
    from rasterio.windows import Window

    r0 = max(0, row0)
    r1 = min(grid.height - 1, row1)
    c0 = max(0, col0)
    c1 = min(grid.width - 1, col1)
    win_h, win_w = r1 - r0 + 1, c1 - c0 + 1
    est = win_h * win_w * grid.itemsize_est * len(bands)
    if est > _MAX_WINDOW_READ_BYTES:
        raise RasterSamplingError(
            "BUDGET_EXCEEDED",
            f"single window read ~{est / 1e6:.0f} MB exceeds the "
            f"{_MAX_WINDOW_READ_BYTES // 1e6} MB window cap",
            field="budget", status_code=413)
    data = ds.read(indexes=[int(b) for b in bands],
                   window=Window(c0, r0, win_w, win_h), masked=True)
    metrics.window_reads += 1
    out_mask: Dict[int, np.ndarray] = {}
    # 记账按真实块布局：窗口触达的块面积 × itemsize × 波段数（条带源
    # 1×1 窗口也按整行块计 —— 不以「返回一个像元」掩盖整条带 I/O）
    bh, bw = grid.block_shape
    if bh <= 0 or bw <= 0:
        bh, bw = grid.height, grid.width
    blocks_r = (r1 // bh) - (r0 // bh) + 1
    blocks_c = (c1 // bw) - (c0 // bw) + 1
    block_bytes = (min(blocks_r * bh, grid.height)
                   * min(blocks_c * bw, grid.width)
                   * grid.itemsize_est * len(bands))
    metrics.decoded_bytes += block_bytes
    out_data: Dict[int, np.ndarray] = {}
    out_valid: Dict[int, np.ndarray] = {}
    for i, b in enumerate(bands):
        arr = np.ma.getdata(data[i])
        mask = np.ma.getmaskarray(data[i])
        facts = grid.band_facts[int(b)]
        valid = (~mask) & np.isfinite(arr)
        if facts.nodata is not None:
            valid &= arr != facts.nodata
        out_data[int(b)] = arr
        out_valid[int(b)] = valid
        out_mask[int(b)] = mask  # 分型依据：mask 洞 ≠ nodata ≠ 非有限元
    return out_data, out_valid, (r0, r1, c0, c1), out_mask


def _station_rows(
    station: Any, grid: SourceGrid, idx: int, method: str,
    station_crs: str,
    data: Optional[Dict[int, np.ndarray]],
    valid: Optional[Dict[int, np.ndarray]],
    window: Tuple[int, int, int, int],
    masks: Optional[Dict[int, np.ndarray]] = None,
) -> List[SampleRow]:
    """单站位 × 该源全部波段 → 样本行。"""
    r0, _r1, c0, _c1 = window
    binding = grid.binding
    failed = (grid.transform_failed is not None
              and bool(grid.transform_failed[idx]))
    shape = ((int(next(iter(data.values())).shape[0]),
              int(next(iter(data.values())).shape[1]))
             if data else (0, 0))
    outcomes: Dict[int, Dict[str, Any]] = {}
    pixel: Tuple[Optional[int], Optional[int]] = (None, None)
    if failed:
        base = {"valid": False, "reason": INVALID_TRANSFORM_FAILED,
                "raw": None, "physical": None, "supports": []}
        outcomes = {int(b): base for b in binding.bands}
    else:
        col_f = float(grid.grid_cols[idx])
        row_f = float(grid.grid_rows[idx])
        in_raster = 0.0 <= col_f < grid.width and 0.0 <= row_f < grid.height
        if method == METHOD_SOURCE_PIXEL:
            col, row = int(math.floor(col_f)), int(math.floor(row_f))
            pixel = (row, col) if in_raster else (None, None)
            if not in_raster:
                base = {"valid": False, "reason": "outside_extent",
                        "raw": None, "physical": None, "supports": []}
                outcomes = {int(b): base for b in binding.bands}
            else:
                for b in binding.bands:
                    facts = grid.band_facts[int(b)]
                    local_r, local_c = row - r0, col - c0
                    if 0 <= local_r < shape[0] and 0 <= local_c < shape[1]:
                        outcomes[int(b)] = kernel.sample_source_pixel(
                            data[int(b)], valid[int(b)], local_r, local_c,
                            facts, shape=shape,
                            mask_arr=(masks or {}).get(int(b)))
                    else:
                        outcomes[int(b)] = {"valid": False,
                                            "reason": "outside_extent",
                                            "raw": None, "physical": None,
                                            "supports": []}
        else:
            pixel = ((int(math.floor(col_f)), int(math.floor(row_f)))
                     if in_raster else (None, None))
            for b in binding.bands:
                facts = grid.band_facts[int(b)]
                outcomes[int(b)] = kernel.sample_bilinear(
                    data[int(b)], valid[int(b)], row_f - r0, col_f - c0,
                    facts, shape=shape, mode=method,
                    mask_arr=(masks or {}).get(int(b)))
    rows: List[SampleRow] = []
    for b in binding.bands:
        facts = grid.band_facts[int(b)]
        outcome = outcomes[int(b)]
        fields = kernel.value_facts_to_row_fields(facts, method, outcome)
        rows.append(SampleRow(
            sample_id=station.sample_id,
            station_index=station.station_index,
            parent_locator=station.parent_locator,
            source_ref=binding.ref_id,
            band=int(b),
            band_label=facts.label,
            station_x=station.x, station_y=station.y,
            station_crs=station_crs,
            part_id=station.part_id,
            chainage_m=station.chainage_m,
            source_x=(float(grid.grid_cols[idx])
                      if grid.grid_cols is not None else 0.0),
            source_y=(float(grid.grid_rows[idx])
                      if grid.grid_rows is not None else 0.0),
            row=pixel[0], col=pixel[1],
            unit=facts.unit,
            unit_status=facts.unit_status,
            method=method,
            support_pixels=[
                {"row": s["row"], "col": s["col"],
                 "weight": round(float(s["weight"]), 9),
                 "valid": bool(s["valid"])}
                for s in outcome.get("supports", [])
            ],
            support_ratio=(outcome.get("support_ratio")
                           if method != METHOD_SOURCE_PIXEL else None),
            data_revision=binding.data_revision,
            **fields,
        ))
    return rows


def _group_by_block(
    indices: Sequence[int], grid: SourceGrid, method: str
) -> Dict[Tuple[int, int], List[int]]:
    """站位索引按块分组（bilinear 邻像元并入同组窗口）。"""
    bh, bw = grid.block_shape
    if bh <= 0 or bw <= 0:
        bh, bw = grid.height, grid.width  # 未报告块：整体单组（受预算闸）
    groups: Dict[Tuple[int, int], List[int]] = {}
    for i in indices:
        if grid.transform_failed is not None and bool(grid.transform_failed[i]):
            groups.setdefault((-1, -1), []).append(i)
            continue
        col_f = float(grid.grid_cols[i])
        row_f = float(grid.grid_rows[i])
        r0, _r1, c0, _c1 = _needed_pixels(col_f, row_f, method)
        key = (max(0, r0) // bh, max(0, c0) // bw)
        groups.setdefault(key, []).append(i)
    return groups


def _batched(items: List[int], size: int) -> List[List[int]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _verify_generation(grid: SourceGrid) -> None:
    """批间源代次复核（跨批改写/ABA → typed 失败，弃已产内容）。"""
    now = stat_fingerprint(grid.path)
    if not same_generation(grid.stat_snapshot, now):
        raise RasterSamplingError(
            "SOURCE_MUTATED_DURING_READ",
            f"source {grid.binding.ref_id} changed on disk during sampling "
            "(generation check failed) — mixed samples were discarded",
            field="sources", status_code=409,
            correction_hint="Re-resolve the raster source and re-execute the "
                            "plan against a stable revision.",
        )


def _read_batch(
    ds: Any, grid: SourceGrid, batch: List[int],
    r0: int, r1: int, c0: int, c1: int,
    station_set: StationSet, method: str, metrics: ReadMetrics,
    progress: Any,
) -> List[SampleRow]:
    """批窗口读（超预算沿站位切半递归；单站位兜底恒可读）。"""
    est = ((r1 - r0 + 1) * (c1 - c0 + 1) * grid.itemsize_est
           * len(grid.band_facts))
    if est > _MAX_WINDOW_READ_BYTES and len(batch) > 1:
        mid = len(batch) // 2
        out: List[SampleRow] = []
        for half in (batch[:mid], batch[mid:]):
            bounds = [_needed_pixels(float(grid.grid_cols[i]),
                                     float(grid.grid_rows[i]), method)
                      for i in half]
            out.extend(_read_batch(
                ds, grid, half,
                min(b[0] for b in bounds), max(b[1] for b in bounds),
                min(b[2] for b in bounds), max(b[3] for b in bounds),
                station_set, method, metrics, progress))
        return out
    data, valid, window, masks = _read_window(
        ds, list(grid.band_facts.keys()), r0, r1, c0, c1, grid, metrics)
    rows: List[SampleRow] = []
    for i in batch:
        rows.extend(_station_rows(
            station_set.stations[i], grid, i, method, station_set.crs,
            data, valid, window, masks))
        progress.advance()  # 站位计数（total=站位数；值单元不进分母）
    return rows


def _run_source_pass(
    ds: Any, grid: SourceGrid, station_set: StationSet, method: str,
    metrics: ReadMetrics, progress: Any,
) -> List[SampleRow]:
    """单源全站位批读 + 采样（同步；由 execute 入口包 to_thread）。"""
    _transform_stations(station_set, grid)
    metrics.block_shapes[grid.binding.ref_id] = list(grid.block_shape)
    metrics.is_tiled[grid.binding.ref_id] = grid.is_tiled
    groups = _group_by_block(range(len(station_set.stations)), grid, method)
    metrics.blocks_touched += sum(1 for k in groups if k != (-1, -1))
    all_rows: List[SampleRow] = []
    # 变换失败站位：typed 无效行（无需读取，不拖垮其它源）
    for i in groups.get((-1, -1), []):
        all_rows.extend(_station_rows(
            station_set.stations[i], grid, i, method, station_set.crs,
            None, None, (0, 0, 0, 0), None))
        progress.advance()
    done = len(groups.get((-1, -1), []))
    for key in sorted(k for k in groups if k != (-1, -1)):
        for batch in _batched(groups[key], MAX_BATCH_STATIONS):
            # 每批：协作取消 + ABA 复核（跨批改写即失败）
            checkpoint()
            _verify_generation(grid)
            bounds = [_needed_pixels(float(grid.grid_cols[i]),
                                     float(grid.grid_rows[i]), method)
                      for i in batch]
            rows = _read_batch(
                ds, grid, batch,
                min(b[0] for b in bounds), max(b[1] for b in bounds),
                min(b[2] for b in bounds), max(b[3] for b in bounds),
                station_set, method, metrics, progress)
            all_rows.extend(rows)
            done += len(batch)
            metrics.stations_processed += len(batch)
            metrics.batches += 1
    return all_rows


class _ProgressCounter:
    """线程内进度计数（周期性回调；无回调时零开销）。"""

    def __init__(self, hook: Any = None, every: int = 2000,
                 total: int = 0) -> None:
        self._hook = hook
        self._every = max(1, every)
        self._count = 0
        self._total = max(0, int(total))

    def advance(self, n: int = 1) -> None:
        self._count += n
        if self._hook and self._count % self._every < n:
            try:
                self._hook(self._count, self._total)
            except TypeError:
                try:  # 兼容单参钩子
                    self._hook(self._count)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001 — 进度回调绝不影响执行
                pass


def _progress_every_hint(progress_hook: Any) -> int:
    """钩子可自带触发周期（测试注入用；默认 2000）。"""
    return int(getattr(progress_hook, "every", 2000) or 2000)


def execute_sampling(
    *,
    resolved_sources: Sequence[Any],
    bindings: Sequence[SourceBinding],
    station_set: StationSet,
    method: str,
    progress_hook: Any = None,
) -> Tuple[List[SampleRow], ReadMetrics, Dict[str, Any]]:
    """全源采样执行（一次打开/源；批读 + 预算 + 取消 + ABA 防护）。

    同步函数 —— 由 service 层经 ``asyncio.to_thread`` 驱动（取消经
    ``use_token`` 的 contextvars 传播到本线程）。返回（样本行按源分段，
    读取记账, 成本预估）。任一批 ABA 复核失败：整次执行作废（调用方
    负责不发布任何内容）。
    """
    metrics = ReadMetrics()
    progress = _ProgressCounter(progress_hook,
                                  every=_progress_every_hint(progress_hook),
                                  total=len(station_set.stations))
    from app.lib.geo_raster.env import open_raster, rasterio_env

    grids: List[SourceGrid] = []
    with rasterio_env():
        # 收集网格事实（每源一次 open；随块关闭）
        for binding, source in zip(bindings, resolved_sources):
            with open_raster(source.path) as ds:
                metrics.opens += 1
                grids.append(collect_source_grid(binding, source, ds, method))
        cost = estimate_execution_cost(grids, len(station_set.stations))
        all_rows: List[SampleRow] = []
        # 批读阶段：再开一次并保持打开（批间仅 stat 复核，不重开）
        for grid in grids:
            with open_raster(grid.path) as ds:
                all_rows.extend(_run_source_pass(
                    ds, grid, station_set, method, metrics, progress))
    return all_rows, metrics, cost
