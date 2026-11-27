"""Survey sampling frame —— 分类栅格 → 版本化抽样框（WP2）。

把分类栅格变成受版本约束的概率抽样总体框：

- **总体**：框内全部有效像元（NoData/掩膜外排除并披露），按地图类别
  分层；类别必须⊆类别字典（漂移 typed 拒绝）；
- **面积**：逐像元**测地面积核查**（WGS84 椭球，跨框多点采样）——
  层内像元面积相对变化 > 容差（默认 1%）⇒ typed 拒绝并建议等面积
  CRS 重投影（禁止把经纬度像元数直接当面积，goal WP2 硬约束）；
  通过后 A_h = N_h × ā（ā = 平均像元测地面积），W_h = A_h/A；
- **单元索引**：每层按行主序枚举有效像元（确定性；同源同 digest ⇒
  同索引 ⇒ 同坐标），uint32 线性索引数组（预算内）；
- **分块读**：按行块窗口读（J4 大栅格不整体载入），块间
  ``checkpoint()`` 响应取消——取消即中止，**不产半成品帧**；
- **不可变快照**：payload（源指纹/CRS/形状/计数/面积/排除）canonical
  digest；源文件内容漂移 ⇒ payload 指纹不符 ⇒ 框 stale（估计失效）。

预算：``MAX_FRAME_UNITS``（默认 2×10⁷）保护内存（uint32 ≈ 80MB 峰值
+ 行块读缓存）；超限 typed 拒绝并给出收窄 AOI/粗化响应单元的建议。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.lib.cancellation import checkpoint
from app.lib.data.fingerprints import canonical_fingerprint
from app.lib.gis.scientific_errors import DegenerateData, ResourceScaleMismatch
from app.lib.survey_sampling.design import StratumSpec

logger = logging.getLogger(__name__)

#: 帧单元总量预算（uint32 索引 ≈ 80MB 峰值；保护内存/RSS 基线）
MAX_FRAME_UNITS = 20_000_000

#: 行块读高度（像元行；分块读不整体载入）
BLOCK_ROWS = 2048

#: 像元面积均匀性容差（跨框采样点测地面积 max/min − 1；1% 默认）
PIXEL_AREA_TOLERANCE = 0.01

#: 像元面积核查采样点（bbox 均匀网格，≥ 角点覆盖）
_AREA_PROBE_GRID = 5


@dataclass
class SurveyFrame:
    """冻结抽样框：payload（可序列化/可 digest）+ 索引数组（内存态）。"""

    payload: Dict[str, Any]
    #: 每层有效像元线性索引（行主序；uint32/int64）——内存态，不序列化
    index_arrays: Dict[str, np.ndarray]
    raster_width: int
    raster_height: int
    transform: Sequence[float]           # GDAL order [a,b,c,d,e,f]
    crs: str
    digest: str = ""

    def __post_init__(self) -> None:
        if self.digest:
            return
        self.digest = canonical_fingerprint(self.payload)

    def unit_coordinates(
        self, stratum_id: str, unit_index: int,
    ) -> Tuple[float, float, float, float]:
        """(层, 层内序号) → 像元中心 (x, y, lon, lat)。确定性。"""
        arr = self.index_arrays.get(stratum_id)
        if arr is None or unit_index < 0 or unit_index >= len(arr):
            raise IndexError(
                f"unit_index {unit_index} out of range for stratum "
                f"{stratum_id!r} (frame units {0 if arr is None else len(arr)})")
        lin = int(arr[unit_index])
        row, col = divmod(lin, self.raster_width)
        x, y = pixel_center_xy(self.transform, col, row)
        lon, lat = to_wgs84(self.crs, x, y)
        return float(x), float(y), float(lon), float(lat)

    @property
    def total_units(self) -> int:
        return int(sum(len(a) for a in self.index_arrays.values()))


def pixel_center_xy(
    transform: Sequence[float], col: int, row: int,
) -> Tuple[float, float]:
    """GDAL order affine [a,b,c,d,e,f]：角点 (col,row) → 中心 +0.5。"""
    a, b, c, d, e, f = (float(v) for v in transform[:6])
    x = a * (col + 0.5) + b * (row + 0.5) + c
    y = d * (col + 0.5) + e * (row + 0.5) + f
    return x, y


_TRANSFORMER_CACHE: Dict[str, Any] = {}


def to_wgs84(crs: str, x: float, y: float) -> Tuple[float, float]:
    """框 CRS → WGS84 经纬度（pyproj Transformer 缓存；转换失败 typed 拒绝）。"""
    key = str(crs)
    tr = _TRANSFORMER_CACHE.get(key)
    if tr is None:
        from pyproj import Transformer

        tr = Transformer.from_crs(key, "EPSG:4326", always_xy=True)
        _TRANSFORMER_CACHE[key] = tr
    lon, lat = tr.transform(x, y)
    return float(lon), float(lat)


def _pixel_geodesic_areas(
    transform: Sequence[float], crs: str,
    width: int, height: int,
) -> Tuple[float, float, float]:
    """跨框采样像元测地面积 → (均值, max/min−1 相对变化)。

    像元四角 → WGS84 → Geod.polygon_area_perimeter（椭球面积）。
    相对变化 > 容差由调用方 typed 拒绝（等面积假设不成立 ⇒ 面积权错）。
    """
    from pyproj import Geod

    if not crs:
        raise DegenerateData(
            "raster CRS is unknown — pixel area cannot be verified",
            correction_hint="declare a CRS on the classification raster "
                            "(equal-area projections preferred)",
        )
    geod = Geod(ellps="WGS84")
    a, b, c, d, e, f = (float(v) for v in transform[:6])

    def _corner_xy(col: float, row: float) -> Tuple[float, float]:
        return a * col + b * row + c, d * col + e * row + f

    xs = np.linspace(0, max(width - 1, 0), _AREA_PROBE_GRID)
    ys = np.linspace(0, max(height - 1, 0), _AREA_PROBE_GRID)
    areas: List[float] = []
    for rc in xs:
        for rr in ys:
            col0 = float(min(rc, width - 1))
            row0 = float(min(rr, height - 1))
            corners_xy = [
                _corner_xy(col0, row0), _corner_xy(col0 + 1, row0),
                _corner_xy(col0 + 1, row0 + 1), _corner_xy(col0, row0 + 1),
            ]
            lons_lats = [to_wgs84(crs, x, y) for x, y in corners_xy]
            lons = [p[0] for p in lons_lats]
            lats = [p[1] for p in lons_lats]
            area, _perim = geod.polygon_area_perimeter(lons, lats)
            area = abs(float(area))
            if area > 0 and math.isfinite(area):
                areas.append(area)
    if not areas:
        raise DegenerateData(
            "pixel geodesic area probe produced no finite areas — check "
            "raster CRS/transform",
            correction_hint="verify the classification raster CRS",
        )
    mean_area = float(np.mean(areas))
    spread = (max(areas) - min(areas)) / mean_area
    return mean_area, float(spread)


def build_frame(
    reader,                          # RasterReader（已 open；调用方持有）
    *,
    band: int,
    class_dictionary: Dict[int, str],    # 类别值 → 类名（值域全量）
    nodata: Optional[float],
    crs: str,
    tolerance: float = PIXEL_AREA_TOLERANCE,
) -> SurveyFrame:
    """构建抽样框（分块读 + 取消检查点；取消 ⇒ 无半成品帧）。

    ``class_dictionary``：值→名映射（类别字典版本由调用方携带进 payload）。
    非 NoData 且值在字典内的像元入框；其余排除并披露（按原因计数）。
    """
    meta = reader.metadata()
    width, height = int(meta.width), int(meta.height)
    if band < 1 or band > int(meta.count):
        raise ValueError(f"band {band} out of range (1..{meta.count})")
    nd = nodata if nodata is not None else meta.nodata

    total_cells = width * height
    if total_cells > MAX_FRAME_UNITS * 4:
        raise ResourceScaleMismatch(
            f"raster {width}x{height} = {total_cells:,} cells exceeds the "
            f"frame scan guard {MAX_FRAME_UNITS * 4:,}",
            estimated=f"{total_cells:,} cells",
            limit=f"≤{MAX_FRAME_UNITS * 4:,} cells (scan)",
            correction_hint="narrow the AOI window or coarsen the response "
                            "unit before building a survey frame",
        )

    counts: Dict[int, int] = {v: 0 for v in class_dictionary}
    excluded = {"nodata": 0, "outside_dictionary": 0}
    idx_lists: Dict[int, List[np.ndarray]] = {v: [] for v in class_dictionary}

    dtype = np.dtype(meta.dtype)
    if dtype.itemsize > 8:
        raise DegenerateData(
            f"classification band dtype {meta.dtype} is not a discrete "
            "class encoding",
            correction_hint="use an integer class band",
        )
    # 类值必须能被带 dtype 精确表示（uint8 带 300 会静默回绕 → 错层）
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        for value in class_dictionary:
            if not (info.min <= int(value) <= info.max):
                raise DegenerateData(
                    f"class value {value} not representable in band dtype "
                    f"{meta.dtype}",
                    correction_hint="align the class dictionary with the "
                                    "raster's value range",
                )
    else:
        for value in class_dictionary:
            if float(value) != int(value):
                raise DegenerateData(
                    f"class value {value} is not an integer code "
                    f"(band dtype {meta.dtype})",
                    correction_hint="class dictionaries use integer codes",
                )
    for row0 in range(0, height, BLOCK_ROWS):
        checkpoint()  # 取消即中止（J4：无半成品帧）
        rows = min(BLOCK_ROWS, height - row0)
        block = reader.read_window((0, row0, width, rows), band=band)
        block = np.asarray(block).reshape(rows, width)
        known_mask = np.zeros((rows, width), dtype=bool)
        for value, sid in class_dictionary.items():
            m = block == np.dtype(block.dtype).type(value)
            hits = int(m.sum())
            counts[value] += hits
            known_mask |= m
            if hits:
                lin = (np.nonzero(m)[0] + row0) * width + np.nonzero(m)[1]
                idx_lists[value].append(lin.astype(np.uint32
                                                   if total_cells < 2**32
                                                   else np.int64))
        if nd is not None:
            nd_mask = block == np.dtype(block.dtype).type(nd)
            excluded["nodata"] += int(nd_mask.sum())
        else:
            excluded["nodata"] += int((~known_mask & ~_valid_all(block)).sum())
        excluded["outside_dictionary"] += int(
            (~known_mask & ~_is_nodata(block, nd)).sum())

    index_arrays: Dict[str, np.ndarray] = {}
    strata: List[Dict[str, Any]] = []
    empty_classes = []
    for value, sid in class_dictionary.items():
        if idx_lists[value]:
            index_arrays[sid] = np.concatenate(idx_lists[value])
        else:
            index_arrays[sid] = np.zeros(0, dtype=np.uint32)
        if counts[value] == 0:
            empty_classes.append(sid)
        strata.append({"stratum_id": sid, "class_value": int(value),
                       "N_units": int(counts[value])})
    if empty_classes:
        raise DegenerateData(
            f"class(es) {empty_classes[:8]} have zero valid pixels in the "
            "frame — empty strata cannot support population inference",
            correction_hint="remove empty classes from the dictionary or "
                            "check the classification raster extent/mask",
        )

    total_units = int(sum(counts.values()))
    if total_units > MAX_FRAME_UNITS:
        raise ResourceScaleMismatch(
            f"frame units {total_units:,} exceed budget {MAX_FRAME_UNITS:,}",
            estimated=f"{total_units:,} units (~{total_units * 4 // 2**20}MB "
                      "index)",
            limit=f"≤{MAX_FRAME_UNITS:,} units",
            correction_hint="narrow the AOI or coarsen the response unit "
                            "(e.g. sample every k-th pixel block as the unit)",
        )
    if total_units == 0:
        raise DegenerateData(
            "frame has zero valid units (all NoData/outside dictionary)",
            correction_hint="check classification raster and class dictionary",
        )

    mean_area, spread = _pixel_geodesic_areas(
        meta.transform or [], crs or "", width, height)
    if spread > tolerance:
        raise DegenerateData(
            f"pixel area varies by {spread:.2%} across the frame "
            f"(tolerance {tolerance:.1%}) — count-weighted stratification "
            "would misstate area",
            correction_hint="reproject the classification raster onto an "
                            "equal-area CRS (e.g. EPSG:6933 / Albers) before "
                            "building the survey frame",
            guidance=(
                "equal-area projections keep pixel area constant",
                "re-run frame build after reprojection",
                "geographic/mercator grids are never equal-area",
            ),
        )

    per_stratum = {}
    for s in strata:
        area_h = s["N_units"] * mean_area
        per_stratum[s["stratum_id"]] = {
            "N_units": s["N_units"],
            "area": round(area_h, 6),
            "class_value": s["class_value"],
        }
    total_area = total_units * mean_area
    payload = {
        "frame_version": "survey-frame/1.0.0",
        "source": {
            "uri": str(reader.uri) if hasattr(reader, "uri") else "",
            #: reader 平面内容指纹（RasterMetadata.fingerprint，32 hex）；
            #: 源变更检测（估计 stale 语义）的事实源。注意 RasterSource 的
            #: content_fingerprint 是同 digest 的 16 hex 截断（前缀比较）。
            "content_fingerprint": str(meta.fingerprint) if meta.fingerprint else None,
            "width": width, "height": height,
            "dtype": str(meta.dtype), "band": int(band),
            "nodata": nd,
        },
        "crs": crs,
        "class_dictionary": {str(v): class_dictionary[v]
                             for v in sorted(class_dictionary)},
        "strata": sorted(
            per_stratum.items()),                      # 排序稳定 digest
        "total_units": total_units,
        "total_area": round(total_area, 6),
        "pixel_area_mean": round(mean_area, 9),
        "pixel_area_spread": round(spread, 9),
        "pixel_area_tolerance": tolerance,
        "excluded": excluded,
    }
    frame = SurveyFrame(
        payload=payload, index_arrays=index_arrays,
        raster_width=width, raster_height=height,
        transform=meta.transform or [], crs=crs,
    )
    payload["frame_digest"] = frame.digest
    return frame


def _is_nodata(block: np.ndarray, nd: Optional[float]) -> np.ndarray:
    if nd is None:
        return np.zeros(block.shape, dtype=bool)
    return block == np.dtype(block.dtype).type(nd)


def _valid_all(block: np.ndarray) -> np.ndarray:
    return np.ones(block.shape, dtype=bool)


def frame_strata_specs(frame: SurveyFrame) -> List[StratumSpec]:
    """payload → StratumSpec 列表（供设计/估计复用；排序稳定）。"""
    specs = []
    for sid, info in frame.payload["strata"]:
        specs.append(StratumSpec(
            stratum_id=sid, N_units=int(info["N_units"]),
            area=float(info["area"])))
    return specs
