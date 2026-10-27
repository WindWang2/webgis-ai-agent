"""3D measurement science（G3D03 / ADR-0274 —— 三维量测契约实现层）。

本模块补齐全库此前不存在的**确定性地表三维量测**：

- ``terrain_profile``：沿折线 bilinear 采样地形剖面（水平距离/高程/逐段
  坡度/累计爬升与下降/极值）；
- ``line_of_sight``：两点通视判定 + 首遮挡点 + 视线剖面；地球曲率与
  大气折射为**可选模型**（默认关闭并披露）；
- ``surface_distance``：同一折线上的水平（网格米制）/ 三维直线斜距 /
  地表（沿面采样）距离、高差、累计爬升，附半步加密收敛指示；
- ``surface_area``：相邻像元中心间两三角面 3D 面积和 vs 投影面积（粗糙比）。

垂直基准治理（ADR-0274 核心）：

- ``resolve_vertical_reference`` 把「DEM 高程」与「外部覆盖值（绝对高程
  override）」的垂直基准/单位显式化 —— 声明的 datum 不一致 →
  ``VERTICAL_REFERENCE_MISMATCH``（拒绝混算，不静默换算；本仓库不内置
  geoid 模型，跨 datum 换算一律拒绝）；``z_units`` 词表 {m, ft}，ft 以
  精确 0.3048 折算米并记 transformation；datum="unknown" 允许**单源**
  运算（DEM + 离地偏移量），输出 quality_flags 披露。

职责边界与 terrain.py 同（CONTRACT_BACKBONE §1）：纯 NumPy/标量数学，
不读文件、不写 artifact、不挂证据块；nodata = ``dem == nodata`` 与
NaN/±Inf（与 band_math #712 口径一致）；meta 为确定性纯文本/数值事实
（无时间戳）；采样循环按段边界 ``checkpoint()`` 协作取消。

离散化诚实披露：地表距离/面积是**采样近似**（平面/线性表面上精确 —— 见
tests/science_oracles/data/terrain.json 的解析曲面 cases）；收敛指示
``convergence.delta_rel`` 只报告经验差，不宣称解析误差界。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.lib.cancellation import checkpoint
from app.lib.geo_analysis.terrain import (
    _bilinear_sample,
    _cell_to_world,
    _guard_cells,
    _prepare,
    _validate_cell_sizes,
    _world_to_cell,
)
from app.lib.gis.scientific_errors import (
    DegenerateData,
    NoValidObservations,
    ResourceScaleMismatch,
    VerticalReferenceMismatch,
    VerticalUnitMismatch,
)

__all__ = [
    "VERTICAL_DATUM_VOCABULARY",
    "Z_UNIT_VOCABULARY",
    "EARTH_RADIUS_M",
    "REFRACTION_COEFFICIENT",
    "resolve_vertical_reference",
    "terrain_profile",
    "line_of_sight",
    "surface_distance",
    "surface_area",
]

# ── 垂直基准契约（ADR-0274）─────────────────────────────────────────

#: 垂直基准闭合词表。GeoTIFF 不携带垂直元数据 —— 声明来自调用方，
#: 缺省 "unknown" 是诚实缺省（允许单源运算，禁止跨 datum 混算）。
VERTICAL_DATUM_VOCABULARY = frozenset(
    {
        "ellipsoid_height",
        "geoid_height_egm96",
        "geoid_height_egm2008",
        "orthometric_local",
        "unknown",
    }
)

#: DEM 高程存储单位词表（ft = **国际英尺** 0.3048 m 精确定义；US survey
#: foot ≈0.30480061 m 不在词表 —— survey-foot 数据须先换算再声明 ft）。
Z_UNIT_VOCABULARY = frozenset({"m", "ft"})

#: WGS84 平均地球半径（IUGG 平均半径 R1 = (2a+b)/3）。
EARTH_RADIUS_M = 6371008.8
#: 标准大气地面折射系数（测量学口径 k=0.1307，R_eff=R/(1−k)；仅
#: curvature="earth_refracted" 使用并随结果披露）。
REFRACTION_COEFFICIENT = 0.1307

# ── 规模护栏（estimate-before-allocate）─────────────────────────────

MAX_PROFILE_SAMPLES = 2_000_000
MAX_CONVERGENCE_SAMPLES = 1_000_000
MAX_VERTICES = 1024
#: 输出剖面表的最大点数（超出时均匀抽稀，统计量仍基于全采样）。
MAX_OUTPUT_SAMPLES = 8192

# 遮挡判定的 grazing 容差（米）：视线与地形相切（clearance ∈ [−tol, 0)）
# 记为可见 —— 与 viewshed 的相切可见口径一致（角度容差换算到距离量级）。
_LOS_CLEARANCE_TOL_M = 1e-9


def resolve_vertical_reference(
    vertical_datum: str = "unknown",
    z_units: str = "m",
    override_vertical_datum: Optional[str] = None,
) -> Dict[str, Any]:
    """解析垂直基准/单位声明 → 换算因子 + 披露事实。

    规则（ADR-0274 §2）：

    - ``vertical_datum`` / ``z_units`` 必须在闭合词表内（ValueError 家族的
      类型化错误：datum 非法 → ``VerticalReferenceMismatch``；单位非法 →
      ``VerticalUnitMismatch``）；
    - ``override_vertical_datum``（绝对高程覆盖值的声明）给出且与 DEM
      datum 不同 → ``VERTICAL_REFERENCE_MISMATCH``：本仓库无 geoid 模型，
      跨 datum 换算一律拒绝，绝不静默混算；
    - datum="unknown" 允许单源运算，返回 ``flags=["vertical_datum_unknown"]``
      供结果 quality_flags 披露。

    返回 dict：``{vertical_datum, z_units, z_to_m, flags, transformations}``
    （确定性、JSON 安全）。
    """
    datum = str(vertical_datum)
    if datum not in VERTICAL_DATUM_VOCABULARY:
        raise VerticalReferenceMismatch(
            f"vertical_datum {datum!r} is not in the closed vocabulary "
            f"{sorted(VERTICAL_DATUM_VOCABULARY)}",
            correction_hint="declare one of the supported vertical datums "
            "or omit it (defaults to the honest 'unknown')",
            ref={"adr": "docs/adr/0274-3d-measurement-contract.md"},
        )
    units = str(z_units)
    if units not in Z_UNIT_VOCABULARY:
        raise VerticalUnitMismatch(
            f"z_units {units!r} is not in the closed vocabulary "
            f"{sorted(Z_UNIT_VOCABULARY)}",
            correction_hint="declare DEM elevation units as 'm' or 'ft'",
        )
    flags: List[str] = []
    transformations: List[str] = []
    if override_vertical_datum is not None:
        ov = str(override_vertical_datum)
        if ov not in VERTICAL_DATUM_VOCABULARY:
            raise VerticalReferenceMismatch(
                f"override_vertical_datum {ov!r} is not in the closed vocabulary "
                f"{sorted(VERTICAL_DATUM_VOCABULARY)}",
                correction_hint="declare one of the supported vertical datums",
            )
        if ov != datum:
            raise VerticalReferenceMismatch(
                f"absolute z overrides are declared {ov!r} but the DEM is "
                f"declared {datum!r} — mixing heights across vertical datums "
                f"is refused (no geoid transformation is bundled)",
                correction_hint="re-express the overrides in the DEM's vertical "
                "datum (external geoid model), or declare both "
                "sources with the same datum",
                ref={"adr": "docs/adr/0274-3d-measurement-contract.md"},
            )
    z_to_m = 1.0 if units == "m" else 0.3048
    if units == "ft":
        transformations.append("z_units ft → m (×0.3048 exact international foot)")
    if datum == "unknown":
        flags.append("vertical_datum_unknown")
    return {
        "vertical_datum": datum,
        "z_units": units,
        "z_to_m": z_to_m,
        "flags": flags,
        "transformations": transformations,
    }


# ── 折线采样原语（profile / surface_distance / line_of_sight 共用）───


def _vertex_cells(
    vertices: Sequence[Sequence[float]],
    transform: Sequence[float],
) -> Tuple[np.ndarray, np.ndarray]:
    """世界坐标顶点 → (cols, rows) 像元中心索引数组（含基本形状校验）。"""
    pts = [(float(v[0]), float(v[1])) for v in vertices]
    cols = np.empty(len(pts), dtype=np.float64)
    rows = np.empty(len(pts), dtype=np.float64)
    for i, (x, y) in enumerate(pts):
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(f"vertex {i} has non-finite coordinates ({x!r}, {y!r})")
        cols[i], rows[i] = _world_to_cell(transform, x, y)
    return cols, rows


def _sample_polyline(
    z: np.ndarray,
    valid: np.ndarray,
    transform: Sequence[float],
    cols: np.ndarray,
    rows: np.ndarray,
    *,
    cy: float,
    cx: float,
    step_m: float,
) -> Dict[str, Any]:
    """沿折线按 ~step_m 重采样（每段 n=ceil(len/step) 等分子步）。

    保证：顶点恰好是采样点（段边界不重复计入）；水平累计距离在网格米制
    空间（cy/cx 已按 cos(lat) 政策换算 —— 调用方负责）；z 为 bilinear
    采样（任一角无效 → NaN）。段边界 checkpoint()。
    """
    h, w = z.shape
    n_seg = len(cols) - 1
    # 先估算总采样数（分配前拒绝）
    total = 1
    seg_n = np.empty(n_seg, dtype=np.int64)
    seg_len = np.empty(n_seg, dtype=np.float64)
    for i in range(n_seg):
        dm = math.hypot((cols[i + 1] - cols[i]) * cx, (rows[i + 1] - rows[i]) * cy)
        seg_len[i] = dm
        n = 1 if dm == 0.0 else max(1, int(math.ceil(dm / step_m)))
        seg_n[i] = n
        total += n  # 段尾点 = 下一段首点，只计一次
    if total > MAX_PROFILE_SAMPLES:
        raise ResourceScaleMismatch(
            f"terrain.measurement: polyline sampling needs ~{total} samples "
            f"(cap {MAX_PROFILE_SAMPLES}) at sampling_step={step_m:.3f} m",
            estimated=f"{total} samples",
            limit=f"{MAX_PROFILE_SAMPLES} samples",
            correction_hint="increase sampling_step_m or simplify the line",
        )

    sample_col: List[np.ndarray] = []
    sample_row: List[np.ndarray] = []
    sample_d: List[np.ndarray] = []
    dist_acc = 0.0
    for i in range(n_seg):
        checkpoint()  # 段边界协作取消（ADR-0052 + science-v4 chunk_boundary）
        n = int(seg_n[i])
        if seg_len[i] == 0.0:
            continue
        t = np.arange(n, dtype=np.float64) / n
        sample_col.append(cols[i] + (cols[i + 1] - cols[i]) * t)
        sample_row.append(rows[i] + (rows[i + 1] - rows[i]) * t)
        sample_d.append(dist_acc + seg_len[i] * t)
        dist_acc += float(seg_len[i])
    # 末顶点收尾
    sample_col.append(np.array([cols[-1]]))
    sample_row.append(np.array([rows[-1]]))
    sample_d.append(np.array([dist_acc]))

    cols_s = np.concatenate(sample_col)
    rows_s = np.concatenate(sample_row)
    d_s = np.concatenate(sample_d)
    zs = _bilinear_sample(z, valid, cols_s, rows_s)
    xs, ys = _cell_to_world(transform, cols_s, rows_s)
    inside = (cols_s >= 0) & (cols_s <= w - 1) & (rows_s >= 0) & (rows_s <= h - 1)
    # 越界采样点（折线伸出 DEM）与 bilinear 无效同口径记 NaN（skip 政策
    # 剔除 / error 政策类型化失败，由上层语义决定）。
    zs = np.where(inside, zs, np.nan)
    return {
        "xs": xs,
        "ys": ys,
        "d": d_s,
        "z": zs,
        "n_samples": int(cols_s.size),
        "horizontal_m": float(d_s[-1]),
        "n_invalid": int(np.count_nonzero(~np.isfinite(zs))),
    }


def _interval_stats(
    d: np.ndarray,
    z: np.ndarray,
) -> Dict[str, float]:
    """相邻有效采样对上的地表距离/爬升/下降（跨无效点的区间剔除）。"""
    ok = np.isfinite(z)
    pair = ok[:-1] & ok[1:]
    if not pair.any():
        return {
            "surface_distance_m": 0.0,
            "ascent_m": 0.0,
            "descent_m": 0.0,
            "covered_horizontal_m": 0.0,
            "n_valid_pairs": 0,
            "n_dropped_pairs": int(ok.size - 1),
        }
    dh = d[1:] - d[:-1]
    dz = z[1:] - z[:-1]
    dh, dz = dh[pair], dz[pair]
    seg3d = np.hypot(dh, dz)
    return {
        "surface_distance_m": float(seg3d.sum()),
        "ascent_m": float(np.maximum(dz, 0.0).sum()),
        "descent_m": float(np.maximum(-dz, 0.0).sum()),
        # 有效采样对覆盖的水平长度（skip 政策跨洞剔除后的同口径水平量）：
        # surface ≥ covered_horizontal 在任何政策下无条件成立。
        "covered_horizontal_m": float(dh.sum()),
        "n_valid_pairs": int(pair.sum()),
        "n_dropped_pairs": int(pair.size - pair.sum()),
    }


def _invalid_fraction_guard(
    n_invalid: int,
    n_samples: int,
    nodata_policy: str,
    max_invalid_fraction: float,
) -> List[str]:
    """nodata/ErrorPolicy 语义：error → 类型化失败；skip → 披露 + 超阈失败。"""
    flags: List[str] = []
    if n_invalid == 0:
        return flags
    frac = n_invalid / max(n_samples, 1)
    if nodata_policy == "error":
        raise NoValidObservations(
            f"{n_invalid}/{n_samples} sampled points are nodata/out-of-bounds "
            f"(nodata_policy='error'); the measurement would be silently "
            f"wrong if computed over the gap",
            correction_hint="repair/warp the DEM, or pass nodata_policy='skip' "
            "to exclude invalid samples with disclosure",
        )
    flags.append(f"nodata_samples_skipped:{n_invalid}")
    if frac > max_invalid_fraction:
        raise NoValidObservations(
            f"invalid sample fraction {frac:.3f} exceeds max_invalid_fraction "
            f"{max_invalid_fraction} — remaining coverage is too sparse for a "
            f"trustworthy measurement",
            correction_hint="repair the DEM or raise max_invalid_fraction "
            "explicitly if sparse coverage is acceptable",
        )
    flags.append(f"nodata_fraction:{frac:.4f}")
    return flags


# ── 1. 地形剖面 ──────────────────────────────────────────────────────


def terrain_profile(
    dem: np.ndarray,
    cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    vertices: Sequence[Sequence[float]],
    transform: Sequence[float],
    sampling_step_m: Optional[float] = None,
    nodata: Optional[float] = None,
    nodata_policy: str = "error",
    max_invalid_fraction: float = 0.5,
    z_units: str = "m",
    vertical_datum: str = "unknown",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """沿折线地形剖面（bilinear 采样）。

    返回 (profile, meta)：

    - ``profile``：逐采样点 ``{x, y, z_m, distance_m, slope}``（slope =
      相邻有效对的 dz/d_horizontal；无效点为 None —— 保留位置供前端标记
      nodata 洞）+ 汇总统计（horizontal/surface/ascent/descent/min/max/
      start/end）。采样点 > ``MAX_OUTPUT_SAMPLES`` 时均匀抽稀输出表
      （统计仍基于全采样），meta 披露 ``output_downsampled``。
    - ``meta``：采样步长（请求值与有效值）、样本数、nodata 政策、垂直
      基准/单位事实（resolve_vertical_reference 输出）。

    数值口径：线性表面（平面）上 bilinear 精确 → surface/ascent 解析精确
    （oracle terrain.json 平面 cases）；曲面为采样近似（披露）。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    vref = resolve_vertical_reference(vertical_datum, z_units)
    if nodata_policy not in ("error", "skip"):
        raise ValueError(
            f"nodata_policy must be 'error' or 'skip' (got {nodata_policy!r})"
        )
    if len(vertices) < 2:
        raise ValueError(f"profile needs at least 2 vertices (got {len(vertices)})")
    if len(vertices) > MAX_VERTICES:
        raise ValueError(
            f"profile accepts at most {MAX_VERTICES} vertices (got {len(vertices)})"
        )
    step = float(sampling_step_m) if sampling_step_m is not None else 0.5 * (cy + cx)
    if not (step > 0 and math.isfinite(step)):
        raise ValueError(
            f"sampling_step_m must be a positive finite number (got {step!r})"
        )

    z, valid = _prepare(dem, nodata)
    _guard_cells(z.shape, "terrain.profile")
    z = z * vref["z_to_m"]
    cols, rows = _vertex_cells(vertices, transform)
    s = _sample_polyline(z, valid, transform, cols, rows, cy=cy, cx=cx, step_m=step)
    if s["horizontal_m"] <= 0.0:
        raise DegenerateData(
            "profile polyline has zero horizontal length (all vertices coincide)"
        )

    quality_flags = list(vref["flags"])
    quality_flags += _invalid_fraction_guard(
        s["n_invalid"], s["n_samples"], nodata_policy, max_invalid_fraction
    )
    iv = _interval_stats(s["d"], s["z"])
    if iv["n_dropped_pairs"]:
        quality_flags.append(
            f"surface_stats_exclude_nodata_intervals:{iv['n_dropped_pairs']}"
        )
    if int(np.count_nonzero(np.isfinite(s["z"]))) < 2:
        raise NoValidObservations(
            "fewer than 2 valid samples — no interval statistics (and no "
            "valid LineString) are possible under this nodata policy"
        )

    zs_f = s["z"]
    finite = np.isfinite(zs_f)
    slope = np.full(zs_f.size, np.nan)
    ok = finite[:-1] & finite[1:]
    slope[1:][ok] = (zs_f[1:] - zs_f[:-1])[ok] / np.maximum(
        (s["d"][1:] - s["d"][:-1])[ok], 1e-12
    )

    stride = max(1, math.ceil(zs_f.size / MAX_OUTPUT_SAMPLES))
    downsampled = stride > 1
    if downsampled:
        quality_flags.append(f"output_downsampled:stride={stride}")

    def _pt(i: int) -> Dict[str, Any]:
        zi = float(zs_f[i])
        sl = float(slope[i]) if math.isfinite(slope[i]) else None
        return {
            "x": round(float(s["xs"][i]), 6),
            "y": round(float(s["ys"][i]), 6),
            "z_m": round(zi, 4) if math.isfinite(zi) else None,
            "distance_m": round(float(s["d"][i]), 4),
            "slope": round(sl, 6) if sl is not None else None,
        }

    stats = {
        "horizontal_m": round(s["horizontal_m"], 4),
        "surface_distance_m": round(iv["surface_distance_m"], 4),
        "ascent_m": round(iv["ascent_m"], 4),
        "descent_m": round(iv["descent_m"], 4),
        "covered_horizontal_m": round(iv["covered_horizontal_m"], 4),
        "z_min_m": round(float(np.min(zs_f[finite])), 4) if finite.any() else None,
        "z_max_m": round(float(np.max(zs_f[finite])), 4) if finite.any() else None,
        "z_start_m": round(float(zs_f[0]), 4) if finite[0] else None,
        "z_end_m": round(float(zs_f[-1]), 4) if finite[-1] else None,
    }
    meta = {
        "sampling_step_requested_m": step,
        "sampling_step_effective_m": round(
            s["horizontal_m"] / max(s["n_samples"] - 1, 1), 6
        ),
        "sample_count": s["n_samples"],
        "output_point_count": len(range(0, s["n_samples"], stride)),
        "invalid_sample_count": s["n_invalid"],
        "nodata_policy": nodata_policy,
        "vertical_datum": vref["vertical_datum"],
        "z_units": vref["z_units"],
        "z_unit_transformations": vref["transformations"],
        "quality_flags": quality_flags,
        "method": "bilinear sampling on cell centers; surface stats are exact "
        "on planar surfaces, sampled approximation otherwise; "
        "surface >= covered_horizontal holds unconditionally, "
        "surface >= horizontal only when no nodata intervals are dropped",
    }
    profile = {
        "statistics": stats,
        "points": [_pt(i) for i in range(0, s["n_samples"], stride)],
    }
    return profile, meta


# ── 2. 通视线（LOS）─────────────────────────────────────────────────


def line_of_sight(
    dem: np.ndarray,
    cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    observer_xy: Sequence[float],
    target_xy: Sequence[float],
    transform: Sequence[float],
    observer_height: float = 2.0,
    target_height: float = 0.0,
    observer_z: Optional[float] = None,
    target_z: Optional[float] = None,
    override_vertical_datum: Optional[str] = None,
    curvature: str = "none",
    sampling_step_m: Optional[float] = None,
    nodata: Optional[float] = None,
    nodata_policy: str = "error",
    z_units: str = "m",
    vertical_datum: str = "unknown",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """两点通视（LOS）：遮挡判定 + 首遮挡点 + 视线剖面。

    模型：

    - 观察眼高 = (observer_z 覆盖值或 DEM bilinear 采样) + observer_height
      （离地偏移恒为米；绝对覆盖值取 DEM z_units、须同 datum —— 声明
      不一致 → ``VERTICAL_REFERENCE_MISMATCH``）；
    - ``curvature``：``"none"``（平面视线，默认）| ``"earth"``（球面
      R=6371008.8 m）| ``"earth_refracted"``（k=0.1307，R_eff=R/(1−k)）。
      曲率以地形抬升 bulge(s)=s(D−s)/(2R_eff) 计入地形侧（球面弧在
      弦之上，中间地形相对平面基线被抬高 —— 标准无线电 LOS 口径；
      折射以有效地球半径放大折减 bulge）；
    - 遮挡 = 首个 clearance = sight(s) − terrain_eff(s) < −1e-9 m 的采样
      点（相切视为可见，与 viewshed 口径一致）；几何 LOS 对称
      （角色互换连同各自高度判定不变；高度本身改变视线与可见性 —— meta 披露）；
    - nodata：``"error"`` 遇无效采样即类型化失败；``"skip"`` 剔除无效
      采样并在 quality_flags 披露（判定仅覆盖有效段）。

    返回 (los, meta)：los 含 visible / obstruction（或 None）/
    min_clearance_m（及位置）/ 端点事实 / 剖面采样（视线与地形两条曲线 +
    遮挡点），meta 含曲率模型披露与采样事实。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    vref = resolve_vertical_reference(vertical_datum, z_units, override_vertical_datum)
    if nodata_policy not in ("error", "skip"):
        raise ValueError(
            f"nodata_policy must be 'error' or 'skip' (got {nodata_policy!r})"
        )
    if curvature not in ("none", "earth", "earth_refracted"):
        raise ValueError(
            f"curvature must be one of none|earth|earth_refracted (got {curvature!r})"
        )
    if observer_height < 0 or target_height < 0:
        raise ValueError("observer_height/target_height must be >= 0")
    step = float(sampling_step_m) if sampling_step_m is not None else 0.5 * (cy + cx)
    if not (step > 0 and math.isfinite(step)):
        raise ValueError(
            f"sampling_step_m must be a positive finite number (got {step!r})"
        )

    z, valid = _prepare(dem, nodata)
    _guard_cells(z.shape, "terrain.line_of_sight")
    z = z * vref["z_to_m"]

    cols, rows = _vertex_cells([observer_xy, target_xy], transform)
    s = _sample_polyline(z, valid, transform, cols, rows, cy=cy, cx=cx, step_m=step)
    total_d = s["horizontal_m"]
    if total_d <= 0.0:
        raise DegenerateData("observer and target coincide (zero-length sight line)")

    quality_flags = list(vref["flags"])
    if observer_z is not None or target_z is not None:
        quality_flags.append("absolute_z_override_applied")
    quality_flags += _invalid_fraction_guard(
        s["n_invalid"], s["n_samples"], nodata_policy, max_invalid_fraction=0.5
    )

    zs = s["z"]
    finite = np.isfinite(zs)
    # 端点必须在有效覆盖内（观察点/目标落 nodata → 通视无定义）
    if not finite[0] or not finite[-1]:
        bad = "observer" if not finite[0] else "target"
        raise NoValidObservations(
            f"{bad} endpoint falls on nodata/out-of-bounds cells — LOS is "
            f"undefined without its anchor elevation"
        )

    z_a = float(zs[0]) if observer_z is None else float(observer_z) * vref["z_to_m"]
    z_b = float(zs[-1]) if target_z is None else float(target_z) * vref["z_to_m"]
    eye_a = z_a + float(observer_height)
    eye_b = z_b + float(target_height)

    d = s["d"]
    sight = eye_a + (eye_b - eye_a) * (d / total_d)
    terrain = zs.copy()
    curvature_meta: Dict[str, Any] = {
        "model": "none",
        "refraction_coefficient": None,
        "effective_earth_radius_m": None,
    }
    if curvature != "none":
        k = REFRACTION_COEFFICIENT if curvature == "earth_refracted" else 0.0
        r_eff = EARTH_RADIUS_M / (1.0 - k)
        terrain = terrain + d * (total_d - d) / (2.0 * r_eff)
        curvature_meta = {
            "model": f"sphere R={EARTH_RADIUS_M}m + refraction k={k}"
            if k
            else f"sphere R={EARTH_RADIUS_M}m",
            "refraction_coefficient": k,
            "effective_earth_radius_m": round(r_eff, 3),
            "bulge_formula": "s*(D-s)/(2*R_eff) added to terrain",
        }

    clearance = sight - terrain
    # 遮挡点 = 首个 clearance < −tol 的采样（含端点；端点 clearance = 眼高
    # ≥ 0，实际不可能触发）。skip 政策下无效采样不参与判定。
    blocked_mask = finite & (clearance < -_LOS_CLEARANCE_TOL_M)
    visible = not bool(blocked_mask.any())
    obstruction: Optional[Dict[str, Any]] = None
    if not visible:
        i = int(np.flatnonzero(blocked_mask)[0])
        obstruction = {
            "x": round(float(s["xs"][i]), 6),
            "y": round(float(s["ys"][i]), 6),
            "distance_from_observer_m": round(float(d[i]), 4),
            "terrain_z_m": round(float(zs[i]), 4),
            "terrain_z_effective_m": round(float(terrain[i]), 4),
            "sight_z_m": round(float(sight[i]), 4),
            "clearance_m": round(float(clearance[i]), 4),
        }
    sub = np.flatnonzero(finite)
    if sub.size:
        min_clearance = float(clearance[sub[int(np.argmin(clearance[sub]))]])
        min_clearance_d: Optional[float] = float(d[sub[int(np.argmin(clearance[sub]))]])
    else:
        min_clearance = float("nan")
        min_clearance_d = None

    stats = {
        "visible": visible,
        "obstruction": obstruction,
        "min_clearance_m": round(min_clearance, 4)
        if math.isfinite(min_clearance)
        else None,
        "min_clearance_distance_m": round(min_clearance_d, 4)
        if min_clearance_d is not None
        else None,
        "horizontal_m": round(total_d, 4),
        "slope_distance_m": round(math.hypot(total_d, eye_b - eye_a), 4),
        "eye_z_observer_m": round(eye_a, 4),
        "eye_z_target_m": round(eye_b, 4),
        "terrain_z_observer_m": round(z_a, 4),
        "terrain_z_target_m": round(z_b, 4),
    }
    stride = max(1, math.ceil(zs.size / MAX_OUTPUT_SAMPLES))
    if stride > 1:
        quality_flags.append(f"output_downsampled:stride={stride}")

    def _p(i: int, zi: Optional[float]) -> Dict[str, Any]:
        out = {
            "x": round(float(s["xs"][i]), 6),
            "y": round(float(s["ys"][i]), 6),
            "distance_m": round(float(d[i]), 4),
            "z_m": round(zi, 4) if zi is not None and math.isfinite(zi) else None,
        }
        return out

    terrain_curve = [_p(i, float(zs[i])) for i in range(0, zs.size, stride)]
    sight_curve = [_p(i, float(sight[i])) for i in range(0, zs.size, stride)]
    meta = {
        "sampling_step_requested_m": step,
        "sample_count": s["n_samples"],
        "nodata_policy": nodata_policy,
        "max_invalid_fraction": 0.5,
        "vertical_datum": vref["vertical_datum"],
        "z_units": vref["z_units"],
        "z_unit_transformations": vref["transformations"],
        "curvature": curvature_meta,
        "grazing_tolerance_m": _LOS_CLEARANCE_TOL_M,
        "visibility_symmetry": "swapping observer/target roles together with "
        "their heights preserves the verdict (same 3D segment); the heights "
        "themselves do change the sight line and can change visibility",
        "quality_flags": quality_flags,
        "method": "sampled sight-line vs bilinear terrain (~1 cell step); tangent "
        "counts as visible",
    }
    los = {
        "statistics": stats,
        "terrain_profile": terrain_curve,
        "sight_profile": sight_curve,
    }
    return los, meta


# ── 3. 地表距离 ─────────────────────────────────────────────────────


def surface_distance(
    dem: np.ndarray,
    cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    vertices: Sequence[Sequence[float]],
    transform: Sequence[float],
    sampling_step_m: Optional[float] = None,
    nodata: Optional[float] = None,
    nodata_policy: str = "error",
    max_invalid_fraction: float = 0.5,
    z_units: str = "m",
    vertical_datum: str = "unknown",
    include_samples: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """折线的水平 / 三维直线斜距 / 地表（沿面采样）距离 + 高差 + 爬升。

    三种距离同一网格米制空间（cy/cx 已按 cos(lat) 政策换算，调用方
    负责），保证不变量 surface ≥ horizontal（解析平面精确；oracle）。
    附半步加密收敛指示 ``convergence.delta_rel``（|D(step)−D(step/2)|/D；
    采样数超 ``MAX_CONVERGENCE_SAMPLES`` 时跳过并披露 —— 不做无依据的
    解析误差界宣称）。斜距为折线首末 3D 端点的直线弦长。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    vref = resolve_vertical_reference(vertical_datum, z_units)
    if nodata_policy not in ("error", "skip"):
        raise ValueError(
            f"nodata_policy must be 'error' or 'skip' (got {nodata_policy!r})"
        )
    if len(vertices) < 2:
        raise ValueError(
            f"surface_distance needs at least 2 vertices (got {len(vertices)})"
        )
    if len(vertices) > MAX_VERTICES:
        raise ValueError(
            f"surface_distance accepts at most {MAX_VERTICES} vertices (got {len(vertices)})"
        )
    step = float(sampling_step_m) if sampling_step_m is not None else 0.5 * (cy + cx)
    if not (step > 0 and math.isfinite(step)):
        raise ValueError(
            f"sampling_step_m must be a positive finite number (got {step!r})"
        )

    z, valid = _prepare(dem, nodata)
    _guard_cells(z.shape, "terrain.surface_distance")
    z = z * vref["z_to_m"]
    cols, rows = _vertex_cells(vertices, transform)
    s = _sample_polyline(z, valid, transform, cols, rows, cy=cy, cx=cx, step_m=step)
    if s["horizontal_m"] <= 0.0:
        raise DegenerateData("measurement polyline has zero horizontal length")

    quality_flags = list(vref["flags"])
    quality_flags += _invalid_fraction_guard(
        s["n_invalid"], s["n_samples"], nodata_policy, max_invalid_fraction
    )
    iv = _interval_stats(s["d"], s["z"])
    if iv["n_dropped_pairs"]:
        quality_flags.append(
            f"surface_stats_exclude_nodata_intervals:{iv['n_dropped_pairs']}"
        )
    if int(np.count_nonzero(np.isfinite(s["z"]))) < 2:
        raise NoValidObservations(
            "fewer than 2 valid samples — no interval statistics (and no "
            "valid LineString) are possible under this nodata policy"
        )

    zs = s["z"]
    finite = np.isfinite(zs)
    z_start = float(zs[0]) if finite[0] else None
    z_end = float(zs[-1]) if finite[-1] else None

    convergence: Optional[Dict[str, Any]] = None
    if s["n_samples"] * 2 <= MAX_CONVERGENCE_SAMPLES:
        s2 = _sample_polyline(
            z, valid, transform, cols, rows, cy=cy, cx=cx, step_m=step / 2.0
        )
        iv2 = _interval_stats(s2["d"], s2["z"])
        d1 = iv["surface_distance_m"]
        d2 = iv2["surface_distance_m"]
        delta_rel = abs(d2 - d1) / d1 if d1 > 0 else 0.0
        convergence = {
            "half_step_m": round(step / 2.0, 6),
            "surface_distance_half_step_m": round(d2, 4),
            "delta_rel": round(delta_rel, 8),
        }
    else:
        quality_flags.append("convergence_check_skipped:sample_budget")

    stats: Dict[str, Any] = {
        "horizontal_m": round(s["horizontal_m"], 4),
        "surface_distance_m": round(iv["surface_distance_m"], 4),
        "ascent_m": round(iv["ascent_m"], 4),
        "descent_m": round(iv["descent_m"], 4),
        "covered_horizontal_m": round(iv["covered_horizontal_m"], 4),
        "z_start_m": round(z_start, 4) if z_start is not None else None,
        "z_end_m": round(z_end, 4) if z_end is not None else None,
    }
    if z_start is not None and z_end is not None:
        stats["z_diff_m"] = round(z_end - z_start, 4)
        stats["slope_distance_m"] = round(
            math.hypot(s["horizontal_m"], z_end - z_start), 4
        )
    else:
        stats["z_diff_m"] = None
        stats["slope_distance_m"] = None
    meta = {
        "sampling_step_requested_m": step,
        "sample_count": s["n_samples"],
        "nodata_policy": nodata_policy,
        "vertical_datum": vref["vertical_datum"],
        "z_units": vref["z_units"],
        "z_unit_transformations": vref["transformations"],
        "quality_flags": quality_flags,
        "method": "polyline resampled at ~step, bilinear z; surface distance is "
        "exact on planar surfaces, a sampled lower-bound-style "
        "approximation otherwise; surface >= covered_horizontal holds "
        "unconditionally, surface >= horizontal only when no nodata intervals "
        "are dropped; the convergence indicator converges to the bilinear "
        "interpolant, not the underlying terrain",
        "convergence": convergence,
    }
    if include_samples:
        stride = max(1, math.ceil(zs.size / MAX_OUTPUT_SAMPLES))
        meta["sample_points"] = [
            {
                "x": round(float(s["xs"][i]), 6),
                "y": round(float(s["ys"][i]), 6),
                "z_m": round(float(zs[i]), 4) if math.isfinite(zs[i]) else None,
                "distance_m": round(float(s["d"][i]), 4),
            }
            for i in range(0, zs.size, stride)
        ]
        meta["sample_point_stride"] = stride
    return stats, meta


# ── 4. 地表面积 ─────────────────────────────────────────────────────


def surface_area(
    dem: np.ndarray,
    cell_size: float,
    cell_size_x: Optional[float] = None,
    *,
    mask: Optional[np.ndarray] = None,
    nodata: Optional[float] = None,
    max_invalid_fraction: float = 0.5,
    z_units: str = "m",
    vertical_datum: str = "unknown",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """真实地表面积（相邻像元中心间两三角面 3D 面积和）vs 投影面积。

    对偶网格 facet：每 4 个相邻像元中心构成一个 quad，拆 SW-SE-NE 与
    SW-NE-NW 两三角面（米制空间 cx/cy）。**不发明角点**——像元中心高程
    是唯一观测，平面上全域精确（含边界；oracle）；代价是最后一行/列
    中心不构成 facet（meta 披露）。``mask`` 为布尔 (h, w)（True = 计入，
    quad 需 4 像元全在 mask；缺省全 True —— 工具层由多边形栅格化生成）。
    nodata quad 剔除并披露 invalid_fraction，超 ``max_invalid_fraction``
    类型化失败。不变量：surface ≥ planar（粗糙比 ≥ 1）。
    """
    cy, cx = _validate_cell_sizes(cell_size, cell_size_x)
    vref = resolve_vertical_reference(vertical_datum, z_units)
    z, finite = _prepare(dem, nodata)
    _guard_cells(z.shape, "terrain.surface_area")
    z = z * vref["z_to_m"]
    h, w = z.shape
    if mask is None:
        mask_b = np.ones((h, w), dtype=bool)
    else:
        mask_b = np.asarray(mask, dtype=bool)
        if mask_b.shape != (h, w):
            raise ValueError(f"mask shape {mask_b.shape} does not match DEM {(h, w)}")

    quad_mask = mask_b[:-1, :-1] & mask_b[:-1, 1:] & mask_b[1:, :-1] & mask_b[1:, 1:]
    quad_finite = finite[:-1, :-1] & finite[:-1, 1:] & finite[1:, :-1] & finite[1:, 1:]
    candidate = int(np.count_nonzero(quad_mask))
    usable_quads = quad_mask & quad_finite
    usable = int(np.count_nonzero(usable_quads))
    if candidate == 0:
        raise NoValidObservations(
            "no candidate quads for surface area (mask/DEM leaves no 2x2 neighborhood)"
        )
    if usable == 0:
        raise NoValidObservations(
            "no usable quads for surface area (every candidate quad touches "
            "nodata cells)"
        )
    invalid_fraction = 1.0 - usable / candidate
    quality_flags = list(vref["flags"])
    if invalid_fraction > 0:
        quality_flags.append(f"invalid_quad_fraction:{invalid_fraction:.4f}")
    if invalid_fraction > max_invalid_fraction:
        raise NoValidObservations(
            f"invalid quad fraction {invalid_fraction:.3f} exceeds "
            f"max_invalid_fraction {max_invalid_fraction} — area over the "
            f"remaining quads would misrepresent the region",
            correction_hint="repair the DEM, shrink the region to valid "
            "coverage, or raise max_invalid_fraction explicitly",
        )

    # 两三角面（米制空间，quad 中心观测）：T1=(SW,SE,NE) u=(cx,0,z1)
    # v=(cx,cy,z2)；T2=(SW,NE,NW) u=(cx,cy,a) v=(0,cy,b)
    z1 = z[:-1, 1:] - z[:-1, :-1]  # SE − SW
    z2 = z[1:, 1:] - z[:-1, :-1]  # NE − SW
    b = z[1:, :-1] - z[:-1, :-1]  # NW − SW
    a = z2
    t1 = 0.5 * np.sqrt((cy * z1) ** 2 + (cx * (z1 - z2)) ** 2 + (cx * cy) ** 2)
    t2 = 0.5 * np.sqrt((cy * (b - a)) ** 2 + (cx * b) ** 2 + (cx * cy) ** 2)
    surface = float((t1 + t2)[usable_quads].sum())
    planar = usable * cx * cy
    stats = {
        "planar_area_m2": round(planar, 4),
        "surface_area_m2": round(surface, 4),
        "roughness_ratio": round(surface / planar, 8),
        "usable_quads": usable,
        "candidate_quads": candidate,
        "boundary_centers_without_facet": int(2 * (h + w) - 4)
        if (h > 1 and w > 1)
        else 0,
    }
    meta = {
        "vertical_datum": vref["vertical_datum"],
        "z_units": vref["z_units"],
        "z_unit_transformations": vref["transformations"],
        "quality_flags": quality_flags,
        "method": "two-triangle facets between adjacent cell centers (dual "
        "grid; no synthetic corner elevations); exact on planar "
        "surfaces everywhere, discretization otherwise; outermost "
        "cell centers form no facet; with a mask, quads follow cell-center "
        "in/out membership, so planar_area_m2 approximates the polygon by "
        "whole quads (boundary half-cells excluded)",
    }
    return stats, meta
