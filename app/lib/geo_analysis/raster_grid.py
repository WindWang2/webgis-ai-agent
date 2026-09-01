"""Raster Grid Contract —— 网格身份、对齐裁决与虚拟对齐读取（Runtime V3）。

把「两个栅格能不能直接逐像元运算」从三处各自的 ad-hoc 判断
（raster_calculator 的 crs/transform/shape 等值、spatial_tasks 的
``_grids_pixel_aligned``、TemporalRasterEngine 的 ``_validate_alignment``）
收编为一个**纯 metadata** 契约模块：

- ``RasterGridProfile`` —— 栅格网格身份（CRS + 仿射 6 参数 + 宽高 +
  nodata/dtype/band 数）。``from_dataset()`` 只读头信息，零像元 IO。
- ``grids_align()`` —— 严格逐像元可运算判定：CRS、transform、宽高必须
  全部一致（仅 width==width / height==height 绝不构成 aligned）。
- ``RasterAlignmentDecision`` —— 纯决策对象（不是数据状态）：
  aligned / needs_resample / needs_reproject / incompatible + 目标网格 +
  重采样方法 + 理由。
- ``aligned_reader()`` —— B → A 网格的虚拟对齐读取（WarpedVRT）：窗口读
  即对齐读，不落地临时重投影栅格；GCP 源不支持 VRT 时回退显式窗口
  reproject。
- ``iter_bounded_windows()`` —— 从内存预算推导的有界窗口迭代器（优先
  源文件自然 block，块超预算时细分），取代固定 512×512 拍脑袋值。

对齐策略（确定性，ADR-0089）：
- 第一个栅格（调用方的 A / reference）是基准网格，B → A；
- 连续量默认 bilinear，分类量默认 nearest（分类图禁 bilinear）；
- 足迹无交集 → incompatible（结构化拒绝，不产空垃圾栅格）。
"""
from __future__ import annotations

import logging
import math
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 网格身份 ────────────────────────────────────────────────────────

# transform 六参数的相等容差：与 TemporalRasterEngine._validate_alignment
# 同口径（rtol 1e-6 / atol 1e-9）。仿射参数是浮点记账值，逐位相等过严。
_TRANSFORM_RTOL = 1e-6
_TRANSFORM_ATOL = 1e-9
# 分辨率比较容差（相对）：10m vs 10.0000001m 视为同分辨率。
_RES_RTOL = 1e-6


def _transform_tuple(transform) -> Tuple[float, float, float, float, float, float]:
    if hasattr(transform, "a") and hasattr(transform, "f"):
        return (
            float(transform.a), float(transform.b), float(transform.c),
            float(transform.d), float(transform.e), float(transform.f),
        )
    if isinstance(transform, (tuple, list)):
        vals = [float(v) for v in transform[:6]]
        if len(vals) < 6:
            raise ValueError(f"Expected 6 transform coefficients, got {len(transform)}")
        return (vals[0], vals[1], vals[2], vals[3], vals[4], vals[5])
    return (
        float(transform.a), float(transform.b), float(transform.c),
        float(transform.d), float(transform.e), float(transform.f),
    )



def _transforms_equal(t1, t2) -> bool:
    a, b = _transform_tuple(t1), _transform_tuple(t2)
    for x, y in zip(a, b):
        if not math.isclose(x, y, rel_tol=_TRANSFORM_RTOL, abs_tol=_TRANSFORM_ATOL):
            return False
    return True


def _crs_equal(crs1, crs2) -> bool:
    """CRS 相等：双方都缺、或 rasterio CRS 归一化相等（EPSG:4326 == WGS84）。"""
    if crs1 is None and crs2 is None:
        return True
    if crs1 is None or crs2 is None:
        return False
    try:
        from rasterio.crs import CRS

        return CRS.from_user_input(crs1) == CRS.from_user_input(crs2)
    except Exception:  # noqa: BLE001 — 不可解析的 CRS 字符串退化为串比较
        return str(crs1) == str(crs2)


@dataclass(frozen=True)
class RasterGridProfile:
    """栅格网格身份（头信息投影；零像元 IO）。"""

    width: int
    height: int
    crs: Optional[str]
    transform: Tuple[float, float, float, float, float, float]
    dtype: str = ""
    nodata: Optional[float] = None
    band_count: int = 1
    bounds: Optional[Tuple[float, float, float, float]] = None
    has_gcps: bool = False
    # VRT 对齐时源 nodata 缺席下的足迹哨兵（见 aligned_reader）。
    source_path: str = ""

    @classmethod
    def from_dataset(cls, src, source_path: str = "") -> "RasterGridProfile":
        """从打开的 rasterio 数据集投影网格身份（只读头，不读像元）。"""
        return cls(
            width=int(src.width),
            height=int(src.height),
            crs=str(src.crs) if src.crs else None,
            transform=_transform_tuple(src.transform),
            dtype=str(src.dtypes[0]) if src.dtypes else "",
            nodata=(float(src.nodata) if src.nodata is not None else None),
            band_count=int(src.count),
            bounds=(
                (float(src.bounds.left), float(src.bounds.bottom),
                 float(src.bounds.right), float(src.bounds.top))
                if src.bounds else None
            ),
            has_gcps=bool(getattr(src, "gcps", None) and src.gcps[0]),
            source_path=source_path,
        )

    @property
    def resolution_x(self) -> float:
        return abs(self.transform[0])

    @property
    def resolution_y(self) -> float:
        return abs(self.transform[4])

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "crs": self.crs,
            "transform": list(self.transform),
            "resolution_x": self.resolution_x,
            "resolution_y": self.resolution_y,
            "dtype": self.dtype,
            "nodata": self.nodata,
            "band_count": self.band_count,
            "bounds": list(self.bounds) if self.bounds else None,
        }


def _bounds_overlap(
    b1: Optional[Tuple[float, float, float, float]],
    b2: Optional[Tuple[float, float, float, float]],
) -> bool:
    """两足迹是否有交集（None 边界视为未知 → 不据此刻死，交给变换）。"""
    if b1 is None or b2 is None:
        return True
    return not (b1[2] <= b2[0] or b2[2] <= b1[0] or b1[3] <= b2[1] or b2[3] <= b1[1])


def same_georeferencing(
    ref: RasterGridProfile, other: RasterGridProfile
) -> Tuple[bool, str]:
    """仅 CRS + 仿射一致（不含宽高）——窗口可交叠类运算（如差值统计窗口
    取两栅格 bounds 交集）用这个弱判定；逐像元运算必须用 ``grids_align``。"""
    if not _crs_equal(ref.crs, other.crs):
        return False, f"crs mismatch: {ref.crs} vs {other.crs}"
    if not _transforms_equal(ref.transform, other.transform):
        return False, (
            f"transform mismatch: {tuple(ref.transform)} vs {tuple(other.transform)}"
        )
    return True, "same crs/transform"


def grids_align(
    ref: RasterGridProfile, other: RasterGridProfile
) -> Tuple[bool, str]:
    """ref 与 other 是否共享同一像元网格（可直接逐像元运算）。

    返回 (aligned, reason)。规则：CRS、仿射六参数、宽高全部一致。
    仅形状相等（width/height 各自相等）不构成 aligned —— 分辨率/原点
    不同时逐像元运算比较的是错位采样。
    """
    if not _crs_equal(ref.crs, other.crs):
        return False, f"crs mismatch: {ref.crs} vs {other.crs}"
    if ref.width != other.width or ref.height != other.height:
        return False, (
            f"shape mismatch: {ref.width}x{ref.height} vs "
            f"{other.width}x{other.height}"
        )
    if not _transforms_equal(ref.transform, other.transform):
        return False, (
            "transform mismatch: "
            f"{tuple(ref.transform)} vs {tuple(other.transform)}"
        )
    return True, "identical crs/transform/shape"


# ── 对齐裁决 ────────────────────────────────────────────────────────


class RasterAlignmentError(ValueError):
    """不可对齐（无交集/不可判定网格）——结构化拒绝，绝不静默产空栅格。"""

    def __init__(self, message: str, decision: Optional["RasterAlignmentDecision"] = None):
        super().__init__(message)
        self.message = message
        self.decision = decision


@dataclass(frozen=True)
class RasterAlignmentDecision:
    """对齐决策（纯 metadata / decision object，不是数据状态）。

    status 词表：
    - ``aligned``          —— 同网格，直接逐像元运算；
    - ``needs_resample``   —— 同 CRS、不同分辨率/原点/形状，重采样 B → A；
    - ``needs_reproject``  —— 不同 CRS（含重投影，通常也伴随重采样）；
    - ``incompatible``     —— 足迹无交集等结构化拒绝。
    """

    status: str
    reason: str = ""
    target_crs: Optional[str] = None
    target_transform: Optional[Tuple[float, ...]] = None
    target_width: int = 0
    target_height: int = 0
    resampling: str = "bilinear"
    reference_bounds: Optional[Tuple[float, float, float, float]] = None
    other_bounds: Optional[Tuple[float, float, float, float]] = None

    @property
    def aligned(self) -> bool:
        return self.status == "aligned"

    @property
    def incompatible(self) -> bool:
        return self.status == "incompatible"

    @property
    def reprojected(self) -> bool:
        return self.status == "needs_reproject"

    @property
    def resampled(self) -> bool:
        return self.status in ("needs_resample", "needs_reproject")

    def to_dict(self) -> dict:
        """有界披露（quality evidence 用）：决策事实，不含像元数据。"""
        return {
            "status": self.status,
            "reason": self.reason[:160],
            "target_crs": self.target_crs,
            "target_transform": (
                [round(v, 12) for v in self.target_transform]
                if self.target_transform else None
            ),
            "target_width": self.target_width,
            "target_height": self.target_height,
            "resampling": self.resampling,
            "reprojected": self.reprojected,
            "resampled": self.resampled,
        }


# 分类量哨兵：分类栅格（土地利用等）对齐时禁 bilinear（产生不存在的
# 混合类）。由调用方按数据语义传入 categorical=True/False。
CATEGORICAL_RESAMPLING = "nearest"
CONTINUOUS_RESAMPLING = "bilinear"


def _overlaps_in_ref_crs(
    ref: RasterGridProfile, other: RasterGridProfile
) -> Tuple[bool, str]:
    """足迹交集判定（在 ref 的 CRS 空间里）。

    同 CRS → 直接数值比较；不同 CRS → 先做一次廉价的 bounds 重投影
    （纯元数据运算，零像元 IO）再比较 —— 两个不同投影空间的原生 bounds
    直接比大小会把所有跨 CRS 对误判成无交集。任何不可判定（bounds 缺失/
    变换失败/退化）→ 视为有交集，交给后续 warp 产出全 nodata（保守方向：
    宁可产出显式空结果也不错误拒绝合法对齐）。
    """
    if ref.bounds is None or other.bounds is None:
        return True, ""
    b2 = other.bounds
    if not _crs_equal(ref.crs, other.crs):
        try:
            from rasterio.warp import transform_bounds

            b2 = transform_bounds(other.crs, ref.crs, *b2, densify_pts=21)
        except Exception as e:  # noqa: BLE001
            return True, f"bounds transform undecidable ({e})"
    if b2[2] <= ref.bounds[0] or b2[0] >= ref.bounds[2] or \
            b2[3] <= ref.bounds[1] or b2[1] >= ref.bounds[3]:
        return False, "raster footprints do not overlap"
    return True, ""


def decide_alignment(
    ref: RasterGridProfile,
    other: RasterGridProfile,
    *,
    categorical: bool = False,
    resampling: Optional[str] = None,
) -> RasterAlignmentDecision:
    """确定性对齐裁决：A（ref）是基准网格，B（other）→ A。

    - 默认重采样：连续量 bilinear / 分类量 nearest（显式传入优先）；
    - 同 CRS 不同网格 → needs_resample；
    - 不同 CRS → needs_reproject（目标 CRS 恒为 A 的 CRS）；
    - 足迹（换算到 A 的 CRS 后）无交集 → incompatible。
    """
    method = resampling or (
        CATEGORICAL_RESAMPLING if categorical else CONTINUOUS_RESAMPLING
    )
    target = dict(
        target_crs=ref.crs,
        target_transform=ref.transform,
        target_width=ref.width,
        target_height=ref.height,
        resampling=method,
        reference_bounds=ref.bounds,
        other_bounds=other.bounds,
    )
    overlaps, no_overlap_reason = _overlaps_in_ref_crs(ref, other)
    if not overlaps:
        return RasterAlignmentDecision(
            status="incompatible", reason=no_overlap_reason, **target
        )
    aligned, reason = grids_align(ref, other)
    if aligned:
        return RasterAlignmentDecision(status="aligned", reason=reason, **target)
    if not _crs_equal(ref.crs, other.crs):
        return RasterAlignmentDecision(
            status="needs_reproject",
            reason=reason,
            **target,
        )
    return RasterAlignmentDecision(
        status="needs_resample",
        reason=reason,
        **target,
    )


# ── 虚拟对齐读取（B → A）────────────────────────────────────────────


def _fallback_fill_nodata(profile: RasterGridProfile) -> float:
    """源未声明 nodata 时，VRT 足迹外填充哨兵（#931 语义：B 外 = 无效）。

    float → NaN（浮点天然无效值）；整型 → dtype 最小值（与真实数据的
    碰撞概率远低于整型 0 —— 旧实现正是用 0 且依赖整幅 footprint 掩膜）。
    """
    import numpy as np

    if np.dtype(profile.dtype).kind == "f":
        return float("nan")
    return float(np.iinfo(np.dtype(profile.dtype)).min)


@contextmanager
def aligned_reader(
    path: str,
    decision: RasterAlignmentDecision,
    band: int = 1,
):
    """按对齐决策打开 B，使其能以 A 的网格窗口读取。

    - aligned：直接开原文件（零成本）；
    - needs_resample / needs_reproject：``WarpedVRT`` 虚拟重投影 —— 窗口读
      即对齐读，不落地临时栅格、不整幅进内存；
    - GCP 源不支持 VRT 路径时回退原文件（调用方按显式 reproject 处理，
      与 raster_calculator 旧行为兼容的兜底）。

    yield (dataset, effective_nodata)。effective_nodata 是**对齐后**数据的
    无效哨兵：源 nodata 优先；缺席时为足迹哨兵（VRT 填充值）。
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    src = rasterio.open(path)
    try:
        if decision.aligned:
            eff = src.nodata if src.nodata is not None else None
            yield src, eff
            return

        if decision.incompatible:
            raise RasterAlignmentError(
                "cannot align rasters: " + decision.reason, decision
            )

        profile = RasterGridProfile.from_dataset(src)
        fill = (
            profile.nodata
            if profile.nodata is not None
            else _fallback_fill_nodata(profile)
        )
        resampling = Resampling[decision.resampling]
        vrt = WarpedVRT(
            src,
            crs=decision.target_crs,
            transform=_as_affine(decision.target_transform),
            width=decision.target_width,
            height=decision.target_height,
            resampling=resampling,
            nodata=fill,
        )
        try:
            yield vrt, fill
        finally:
            vrt.close()
    finally:
        src.close()


def _as_affine(transform_tuple: Optional[Tuple[float, ...]]):
    from affine import Affine

    if transform_tuple is None:
        raise RasterAlignmentError("alignment decision carries no target transform")
    a, b, c, d, e, f = transform_tuple[:6]
    return Affine(a, b, c, d, e, f)



# ── 有界窗口迭代 ────────────────────────────────────────────────────

# 窗口循环峰值工作集估计（字节/像元）：窗口化计算普遍同时持有
# 2 个输入窗口 + 2 个 nodata 掩膜 + 表达式中间/结果数组（float64 记账），
# ≈ 8 个 8 字节数组。预算换算用它，而不是固定窗口边长。
WORKING_BYTES_PER_CELL = 64
# 窗口边长上限护栏：即便预算很大，超大单窗口也会放大 GDAL warp 的
# 单次工作集与最坏情况延迟；2k 边（≈16M cells ≈ 1GB 工作集）封顶。
_MAX_WINDOW_SIDE = 2048
_MIN_WINDOW_SIDE = 64


def window_side_from_budget(memory_mb: Optional[int] = None) -> int:
    """内存预算 → 方形窗口边长（确定性推导，无 magic number）。

    cells = budget_bytes / WORKING_BYTES_PER_CELL，side = isqrt(cells)，
    再夹到 [_MIN, _MAX] 护栏。默认 256MB → side ≈ 2048（与护栏重合）；
    32MB → 724；显式记录在 ADR-0089。
    """
    if memory_mb is None:
        try:
            from app.core.config import settings

            memory_mb = settings.RASTER_PROCESSING_MEMORY_MB
        except Exception:  # noqa: BLE001 — 配置缺席按保守默认
            memory_mb = 256
    cells = max(1, int(memory_mb) * 1024 * 1024 // WORKING_BYTES_PER_CELL)
    side = int(math.isqrt(cells))
    return max(_MIN_WINDOW_SIDE, min(_MAX_WINDOW_SIDE, side))


def iter_bounded_windows(
    width: int,
    height: int,
    *,
    window_side: Optional[int] = None,
    src=None,
) -> Iterator["object"]:
    """有界窗口迭代：优先源文件自然 block（block_windows），否则固定网格。

    - src 给定且是 tiled GTiff、且单块 ≤ 预算 → ``src.block_windows()``：
      窗口与磁盘块对齐，I/O 自然；
    - 其余（strip 文件 / 单块巨阵）→ 按 ``window_side`` 固定网格切分，
      内存 O(side²) 不随源块大小膨胀。
    """
    from rasterio.windows import Window

    if window_side is None:
        window_side = window_side_from_budget()

    if src is not None:
        try:
            blocks = list(src.block_windows(1))
            max_block = max(
                (w.width * w.height for _, w in blocks), default=0
            )
            budget_cells = window_side * window_side
            if blocks and 0 < max_block <= budget_cells:
                yield from (w for _, w in blocks)
                return
        except Exception:  # noqa: BLE001 — block 枚举失败退固定网格
            pass

    for row0 in range(0, height, window_side):
        for col0 in range(0, width, window_side):
            yield Window(
                col0, row0,
                min(window_side, width - col0),
                min(window_side, height - row0),
            )
