"""G39 采样内核 — 源像元 / bilinear / 重归一（纯 NumPy，无 I/O）。

纪律（WP05/WP06）：
- ``source_pixel`` 与 G20 probe 同口径：``~transform`` 反算后 ``floor``
  命中源像元（边界 tie = 左/上像元），不插值；
- ``bilinear`` 是**插值估计**：保留四个支撑像元与权重（可核验事实），
  绝不称原像元观测；分类波段 typed 拒绝；
- 严格策略：任一支撑无效 → 样本无效（masked_support）；显式
  ``bilinear_renormalized`` 才允许有效权重重归一，且必须披露 support_ratio；
- 源范围外 → outside_extent，不钳到边缘像元；
- scale/offset **恰一次**：只用声明值（0/负 scale、0 offset 合法），
  ``scale_declared=False`` 时才回落 1/0；换算在 raw 域插值之后做一次；
- 每波段独立有效性（nodata/mask/NaN/Inf 分型），不用 band 1 代替；
- raw 整数超 2^53 时以精确十进制串保真（不默认 float 丢位）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.services.raster_sampling.contracts import (
    INVALID_INSUFFICIENT_SUPPORT,
    INVALID_MASKED,
    INVALID_MASKED_SUPPORT,
    INVALID_NODATA,
    INVALID_NON_FINITE,
    INVALID_OUTSIDE_EXTENT,
    INTERPOLATING_METHODS,
    METHOD_BILINEAR,
    METHOD_SOURCE_PIXEL,
    RasterSamplingError,
    UNIT_STATUS_DECLARED,
    UNIT_STATUS_UNKNOWN,
    VALUE_DOMAIN_INTERPOLATED,
    VALUE_DOMAIN_PHYSICAL,
    VALUE_DOMAIN_RAW,
)

#: renorm 模式的最低有效支撑占比（低于此值不重归一 —— 半孔插值噪声过大）
MIN_RENORM_SUPPORT_RATIO = 0.25
#: JS 安全整数边界：|int| 超出则 float64 不再无损，改用精确十进制串
_JS_SAFE_INTEGER = 2 ** 53


@dataclass
class BandFacts:
    """单波段采样事实（来自真实元数据或用户声明；dtype 不猜语义）。"""

    index: int
    label: str = ""
    dtype: str = ""
    unit: str = ""
    scale: float = 1.0
    offset: float = 0.0
    scale_declared: bool = False
    offset_declared: bool = False
    semantic: str = "undeclared"       # categorical | continuous | undeclared
    semantic_source: str = "undeclared"
    nodata: Optional[float] = None
    supplement_source: str = ""        # band_meta | supplemented | fallback

    @property
    def unit_status(self) -> str:
        return UNIT_STATUS_DECLARED if self.unit else UNIT_STATUS_UNKNOWN

    def validate_method(self, method: str) -> None:
        if method in INTERPOLATING_METHODS:
            if self.semantic == "categorical":
                raise RasterSamplingError(
                    "METHOD_INCOMPATIBLE",
                    f"band {self.index} is categorical — interpolation would "
                    "fabricate blended classes; use method='source_pixel'",
                    field="method",
                    correction_hint="Keep class integrity with source_pixel "
                                    "sampling, or declare the band continuous "
                                    "if it truly is.")
            if self.semantic != "continuous":
                raise RasterSamplingError(
                    "METHOD_INCOMPATIBLE",
                    f"band {self.index} has undeclared semantics — "
                    "interpolation needs an explicit continuous declaration "
                    "(metadata tag or user declaration; dtype is not "
                    "evidence)",
                    field="method",
                    correction_hint="Declare the band continuous "
                                    "(band_semantics) or use source_pixel.")


def raw_exact_if_needed(raw: Any) -> Optional[str]:
    """整数 raw 超 JS 安全范围 → 精确十进制串（无损编码）。"""
    if isinstance(raw, (bool,)):
        return None
    if isinstance(raw, (int, np.integer)):
        v = int(raw)
        return str(v) if abs(v) > _JS_SAFE_INTEGER else None
    return None


def apply_physical_once(raw: float, facts: BandFacts) -> Optional[float]:
    """physical = raw×scale + offset，恰一次（声明值可为 0/负）。"""
    if raw is None:
        return None
    scale = facts.scale if facts.scale_declared else 1.0
    offset = facts.offset if facts.offset_declared else 0.0
    value = float(raw) * scale + offset
    return value if math.isfinite(value) else None


def classify_raw(
    raw: Any, masked: bool, facts: BandFacts
) -> Tuple[bool, Optional[str]]:
    """原始域有效性分型（nodata / masked / 非有限元）。"""
    if masked:
        return False, INVALID_MASKED
    try:
        fv = float(raw)
    except (TypeError, ValueError):
        return False, INVALID_NON_FINITE
    if not math.isfinite(fv):
        return False, INVALID_NON_FINITE
    if facts.nodata is not None and fv == float(facts.nodata):
        return False, INVALID_NODATA
    return True, None


def _pixel_invalid_reason(
    data_arr: np.ndarray, valid_arr: np.ndarray,
    r: int, c: int, facts: BandFacts,
    mask_arr: Optional[np.ndarray] = None,
) -> Tuple[bool, Optional[str], Optional[float]]:
    """读单个像元：有效性 + 原值（含 int64 精确通道）。

    ``valid_arr`` 是折叠后的有效位（mask+有限+非 nodata）；``mask_arr``
    在场时用于把「mask 洞」与「nodata/非有限元」分型 —— 折叠位数组会把
    三者混成 masked，破坏 invalid_by_reason 的真实分类。
    """
    raw = data_arr[r, c]
    masked = (not bool(valid_arr[r, c])) if mask_arr is None else bool(mask_arr[r, c])
    # GDAL 的 masked read 会把「声明的 nodata」折进 mask 通道 —— 原值等于
    # 声明 sentinel 时按更具体的 nodata 分型（mask 洞 ≠ nodata，S08）。
    try:
        raw_f = float(raw)
    except (TypeError, ValueError):
        raw_f = float("nan")
    if (facts.nodata is not None and math.isfinite(raw_f)
            and raw_f == float(facts.nodata)):
        return False, INVALID_NODATA, raw
    ok, reason = classify_raw(raw, masked, facts)
    return ok, reason, raw


def sample_source_pixel(
    data_arr: np.ndarray,
    valid_arr: np.ndarray,
    row: Optional[int],
    col: Optional[int],
    facts: BandFacts,
    *,
    shape: Tuple[int, int],
    mask_arr: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """源像元采样（与 probe 同口径：floor 命中；outside 不钳位）。"""
    h, w = shape
    if row is None or col is None or not (0 <= row < h and 0 <= col < w):
        return {"valid": False, "reason": INVALID_OUTSIDE_EXTENT,
                "raw": None, "physical": None, "supports": []}
    ok, reason, raw = _pixel_invalid_reason(data_arr, valid_arr, row, col,
                                            facts, mask_arr)
    if not ok:
        return {"valid": False, "reason": reason, "raw": None,
                "physical": None, "supports": []}
    physical = apply_physical_once(raw, facts)
    return {
        "valid": True, "reason": None,
        "raw": raw, "physical": physical, "supports": [],
    }


def sample_bilinear(
    data_arr: np.ndarray,
    valid_arr: np.ndarray,
    row_f: float,
    col_f: float,
    facts: BandFacts,
    *,
    shape: Tuple[int, int],
    mode: str = METHOD_BILINEAR,
    mask_arr: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """双线性插值（像元中心栅格）。

    支撑像元 = 围绕 (row_f−0.5, col_f−0.5) 的 2×2 邻域；权重 = 双线性
    分数权。严格模式（``bilinear``）：任一支撑无效 → masked_support；
    ``bilinear_renormalized``：有效支撑权重重归一，披露 support_ratio，
    占比低于 MIN_RENORM_SUPPORT_RATIO → insufficient_support。
    站位在栅格内但支撑越界（边缘半像元内）→ masked_support（不钳位）。
    """
    h, w = shape
    # 站位中心必须在栅格索引域内（col_f∈[0,w), row_f∈[0,h)）
    if not (0.0 <= col_f < w and 0.0 <= row_f < h):
        return {"valid": False, "reason": INVALID_OUTSIDE_EXTENT,
                "raw": None, "physical": None, "supports": [],
                "support_ratio": None}
    c0 = int(math.floor(col_f - 0.5))
    r0 = int(math.floor(row_f - 0.5))
    dc = (col_f - 0.5) - c0
    dr = (row_f - 0.5) - r0
    corners = ((r0, c0, (1 - dr) * (1 - dc)),
               (r0, c0 + 1, (1 - dr) * dc),
               (r0 + 1, c0, dr * (1 - dc)),
               (r0 + 1, c0 + 1, dr * dc))
    supports: List[Dict[str, float]] = []
    weights_valid: List[float] = []
    values_valid: List[float] = []
    total_w = 0.0
    required_invalid = False  # 权重>0 的支撑无效（零权支撑不影响估计）
    for r, c, wgt in corners:
        in_grid = 0 <= r < h and 0 <= c < w
        if in_grid:
            ok, _reason, raw = _pixel_invalid_reason(data_arr, valid_arr,
                                                     r, c, facts, mask_arr)
        else:
            ok = False
        supports.append({
            "row": float(r), "col": float(c), "weight": float(wgt),
            "valid": 1.0 if ok else 0.0,
        })
        total_w += wgt
        if wgt > 0.0 and not ok:
            required_invalid = True
        if ok and in_grid:
            weights_valid.append(wgt)
            values_valid.append(float(raw))
    if mode == METHOD_BILINEAR:
        if required_invalid:
            return {"valid": False, "reason": INVALID_MASKED_SUPPORT,
                    "raw": None, "physical": None, "supports": supports,
                    "support_ratio": sum(weights_valid) / max(total_w, 1e-12)}
        est = sum(v * w for v, w in zip(values_valid, weights_valid))
        physical = apply_physical_once(est, facts)
        return {"valid": True, "reason": None, "raw": None,
                "physical": physical, "interpolated": est,
                "supports": supports, "support_ratio": 1.0}
    # renormalized：有效权重重归一（显式不同方法）
    valid_w = sum(weights_valid)
    ratio = valid_w / total_w if total_w > 0 else 0.0
    if valid_w <= 0.0:
        return {"valid": False, "reason": INVALID_MASKED,
                "raw": None, "physical": None, "supports": supports,
                "support_ratio": 0.0}
    if ratio < MIN_RENORM_SUPPORT_RATIO:
        return {"valid": False, "reason": INVALID_INSUFFICIENT_SUPPORT,
                "raw": None, "physical": None, "supports": supports,
                "support_ratio": ratio}
    est = sum(v * w for v, w in zip(values_valid, weights_valid)) / valid_w
    physical = apply_physical_once(est, facts)
    return {"valid": True, "reason": None, "raw": None,
            "physical": physical, "interpolated": est,
            "supports": supports, "support_ratio": ratio}


def world_to_grid(
    transform: Sequence[float], x: float, y: float
) -> Tuple[float, float]:
    """仿射反算（支持旋转/非方形像元）：世界 → (col_f, row_f)。"""
    a, b, c, d, e, f = (float(transform[0]), float(transform[1]),
                        float(transform[2]), float(transform[3]),
                        float(transform[4]), float(transform[5]))
    # 正变换 [x;y] = [a b; d e]·[col;row] + [c;f] —— 解 2×2 线性方程
    det = a * e - b * d
    if abs(det) < 1e-18:
        raise RasterSamplingError(
            "SAMPLING_FAILED", "degenerate affine transform (singular grid)",
            field="source")
    dx, dy = x - c, y - f
    col_f = (e * dx - b * dy) / det
    row_f = (-d * dx + a * dy) / det
    return col_f, row_f


def _resolve_nodata(source: Any, meta: Dict[str, Any]) -> Optional[float]:
    """nodata sentinel：band_meta 优先，缺席时回退数据集 descriptor（G20 的
    band_meta 不携带 nodata —— 数据集级声明经 descriptor 传播）。"""
    raw = meta.get("nodata")
    if raw is None:
        descriptor = getattr(source, "descriptor", None)
        raw = getattr(descriptor, "nodata", None) if descriptor else None
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def band_facts_from_source(
    source: Any,
    band: int,
    *,
    declared_semantics: Optional[str] = None,
    declared_label: str = "",
    declared_unit: str = "",
) -> BandFacts:
    """RasterSource.band_meta → BandFacts（语义：用户声明 > 元数据 > 未声明）。"""
    meta: Dict[str, Any] = source.band(int(band))
    dataset_semantic = ""
    tags = getattr(source, "dataset_tags", None) or {}
    cls = str(tags.get("classification") or "").strip().lower()
    if cls in ("categorical", "continuous"):
        dataset_semantic = cls
    semantic = "undeclared"
    semantic_source = "undeclared"
    if declared_semantics in ("categorical", "continuous"):
        semantic = declared_semantics
        semantic_source = "declared"
    elif dataset_semantic:
        semantic = dataset_semantic
        semantic_source = "metadata"
    unit = declared_unit or str(meta.get("unit") or "")
    label = declared_label or str(meta.get("label") or f"band {band}")
    scale_declared = bool(meta.get("scale_declared"))
    offset_declared = bool(meta.get("offset_declared"))
    try:
        scale = float(meta.get("scale") if scale_declared else 1.0)
    except (TypeError, ValueError):
        scale, scale_declared = 1.0, False
    try:
        offset = float(meta.get("offset") if offset_declared else 0.0)
    except (TypeError, ValueError):
        offset, offset_declared = 0.0, False
    return BandFacts(
        index=int(band), label=label, dtype=str(meta.get("dtype") or ""),
        unit=unit, scale=scale, offset=offset,
        scale_declared=scale_declared, offset_declared=offset_declared,
        semantic=semantic, semantic_source=semantic_source,
        nodata=_resolve_nodata(source, meta),
        supplement_source=str(meta.get("supplement_source") or
                              ("band_meta" if meta.get("label") else "fallback")),
    )


def supplement_band_facts(
    ds: Any, band: int, base: BandFacts
) -> BandFacts:
    """band_meta 覆盖外的波段：直读该波段真实头（dtype/scale/offset/unit/
    description/nodata）。只补选中的波段（有界），不从 band 1 推断。"""
    try:
        dtype = str(ds.dtypes[band - 1])
    except Exception:  # noqa: BLE001
        dtype = base.dtype
    scales = list(getattr(ds, "scales", None) or [])
    offsets = list(getattr(ds, "offsets", None) or [])
    units = list(getattr(ds, "units", None) or [])
    descriptions = list(getattr(ds, "descriptions", None) or [])
    tags: Dict[str, str] = {}
    try:
        tags = {str(k).lower(): v for k, v in (ds.tags(band) or {}).items()}
    except Exception:  # noqa: BLE001
        tags = {}

    def _flag(values: Sequence[Any], idx: int, neutral: float) -> Tuple[float, bool]:
        if idx < len(values):
            try:
                v = float(values[idx])
                return v, v != neutral
            except (TypeError, ValueError):
                return neutral, False
        return neutral, False

    scale, scale_declared = _flag(scales, band - 1, 1.0)
    offset, offset_declared = _flag(offsets, band - 1, 0.0)
    if "scale" in tags:
        try:
            scale, scale_declared = float(tags["scale"]), True
        except (TypeError, ValueError):
            pass
    if "offset" in tags:
        try:
            offset, offset_declared = float(tags["offset"]), True
        except (TypeError, ValueError):
            pass
    unit = (tags.get("units") or tags.get("unit")
            or (units[band - 1] if band - 1 < len(units) else "")
            or base.unit)
    description = ""
    if band - 1 < len(descriptions) and descriptions[band - 1]:
        description = str(descriptions[band - 1])
    label = description or f"band {band}"
    nodata = base.nodata
    try:
        nodatavals = list(getattr(ds, "nodatavals", None) or [])
        if band - 1 < len(nodatavals) and nodatavals[band - 1] is not None:
            nodata = float(nodatavals[band - 1])  # 逐波段 sentinel
        elif ds.nodata is not None:
            nodata = float(ds.nodata)
    except Exception:  # noqa: BLE001
        pass
    return BandFacts(
        index=band, label=label, dtype=dtype, unit=str(unit or ""),
        scale=scale, offset=offset,
        scale_declared=scale_declared, offset_declared=offset_declared,
        semantic=base.semantic, semantic_source=base.semantic_source,
        nodata=nodata, supplement_source="supplemented",
    )


# ── 值域封装（SampleRow 填充） ────────────────────────────────────────

def value_facts_to_row_fields(
    facts: BandFacts, method: str, outcome: Dict[str, Any]
) -> Dict[str, Any]:
    """kernel 结果 → SampleRow 值字段（三域分明，恰好一次换算）。"""
    if not outcome.get("valid"):
        return {
            "raw_value": None, "physical_value": None,
            "interpolated_value": None,
            "value_domain": _domain_for(facts, method),
            "valid": False,
            "invalid_reason": outcome.get("reason"),
        }
    raw = outcome.get("raw")
    interpolated = outcome.get("interpolated")
    physical = outcome.get("physical")
    exact = raw_exact_if_needed(raw)
    if method == METHOD_SOURCE_PIXEL:
        domain = VALUE_DOMAIN_PHYSICAL if facts.scale_declared or facts.offset_declared else VALUE_DOMAIN_RAW
    else:
        domain = VALUE_DOMAIN_INTERPOLATED
    return {
        "raw_value": (float(raw) if raw is not None and math.isfinite(float(raw)) else None),
        "raw_exact": exact,
        "physical_value": physical,
        "interpolated_value": (float(interpolated)
                               if interpolated is not None else None),
        "value_domain": domain,
        "valid": True,
        "invalid_reason": None,
    }


def _domain_for(facts: BandFacts, method: str) -> str:
    if method == METHOD_SOURCE_PIXEL:
        if facts.scale_declared or facts.offset_declared:
            return VALUE_DOMAIN_PHYSICAL
        return VALUE_DOMAIN_RAW
    return VALUE_DOMAIN_INTERPOLATED
