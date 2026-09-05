"""Typed spectral science —— 波段语义角色 + 光谱指数族（ADR-0099 VNext）。

与 ``app/services/rs/band_math.INDEX_FORMULAS`` 的关系：band_math 是
Sentinel-2 在线路径的执行事实源（read-only，不复用其内部约定）；
本模块是**类型化波段语义层**——波段必须按语义角色（red/nir/swir1/...）
显式命名，绝不按栅格波段位置猜测。缺角色 = ``UnsupportedBandSemantics``
（列出所需角色并提示 band_map），不是默认 B04=red 这类隐式映射。

诚实性契约（进证据块 / 测试锁定）：

- 公式族 ``INDEX_FAMILY`` 每项携带出处 id（``method_references.py`` 词表
  内，或空串=诚实无出处）、理论值域、人类可读公式串；
- 零分母 → NaN（与 band_math golden 语义一致，不产 inf/0）；
- 输入先做线性定标（``scale_factors``，如 DN/10000→反射率）再进公式；
- 超出理论值域的输出**只报告不钳制**（``out_of_range_fraction``）——
  值域违背是未定标 DN 输入的信号，静默 clamp 会掩盖定标错误；
- NODATA 由调用方以掩膜显式传入（或 NaN 自动识别），输出 NaN。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.lib.gis.scientific_errors import UnsupportedBandSemantics

__all__ = [
    "BAND_ROLES",
    "BandRole",
    "ROLE_ORDER",
    "INDEX_FAMILY",
    "SpectralIndexSpec",
    "validate_band_map",
    "compute_spectral_index",
    "roles_in_canonical_order",
]


# ── 波段语义角色注册表 ────────────────────────────────────────────────

@dataclass(frozen=True)
class BandRole:
    """一个波段语义角色：光学反射率 / 热红外 / SAR 极化后向散射。"""

    role: str
    kind: str                        # optical / thermal / sar
    valid_range: Optional[Tuple[float, float]]   # None = 无封闭值域（SAR dB / 亮温 K）
    description: str


# 固定语义序（CVA 角色序、文档生成共用；勿按字典序漂移）。
ROLE_ORDER: Tuple[str, ...] = (
    "blue", "green", "red", "red_edge", "nir", "swir1", "swir2", "thermal",
    "vv", "vh", "hh", "hv",
)

BAND_ROLES: Dict[str, BandRole] = {
    r.role: r
    for r in [
        BandRole("blue", "optical", (0.0, 1.0), "蓝光反射率（叶绿素吸收副翼、气溶胶）"),
        BandRole("green", "optical", (0.0, 1.0), "绿光反射率（MNDWI/GNDVI 项）"),
        BandRole("red", "optical", (0.0, 1.0), "红光反射率（叶绿素强吸收，NDVI/SAVI 分母项）"),
        BandRole("red_edge", "optical", (0.0, 1.0), "红边反射率（叶绿素含量敏感，Sentinel-2 B05-B07）"),
        BandRole("nir", "optical", (0.0, 1.0), "近红外反射率（植被高反射平台，多数指数分子项）"),
        BandRole("swir1", "optical", (0.0, 1.0), "短波红外 1（水体吸收、建筑高反射，NDWI/MNDWI/NDBI 项）"),
        BandRole("swir2", "optical", (0.0, 1.0), "短波红外 2（燃烧疤痕敏感，NBR 项）"),
        BandRole(
            "thermal", "thermal", None,
            "热红外（亮温 K 或发射率语义，非反射率——无 0-1 值域约束）"),
        BandRole(
            "vv", "sar", None,
            "SAR VV 极化后向散射（dB 或线性 σ⁰/γ⁰——可为负值，无封闭值域）"),
        BandRole(
            "vh", "sar", None,
            "SAR VH 交叉极化后向散射（体散射/植被结构敏感；dB 或线性）"),
        BandRole("hh", "sar", None, "SAR HH 同极化后向散射（冻结地表/水体区分；dB 或线性）"),
        BandRole("hv", "sar", None, "SAR HV 交叉极化后向散射（森林生物量敏感；dB 或线性）"),
    ]
}

OPTICAL_ROLES: Tuple[str, ...] = tuple(
    r for r in ROLE_ORDER if BAND_ROLES[r].kind == "optical")
SAR_ROLES: Tuple[str, ...] = tuple(
    r for r in ROLE_ORDER if BAND_ROLES[r].kind == "sar")


def roles_in_canonical_order(roles: Sequence[str]) -> List[str]:
    """按 ROLE_ORDER 稳定排序（未知角色按字典序垫底，保持确定性）。"""
    known = [r for r in ROLE_ORDER if r in set(roles)]
    extra = sorted(set(roles) - set(ROLE_ORDER))
    return known + extra


# ── 公式族（出处 id ⊆ method_references.py 词表；空串 = 诚实无出处）──

def _safe_div(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    """零分母 → NaN（golden 语义；绝不产 inf 或伪 0）。"""
    num = np.asarray(num, dtype=float)
    den = np.asarray(den, dtype=float)
    return np.divide(
        num, den,
        out=np.full(np.broadcast(num, den).shape, np.nan, dtype=float),
        where=den != 0,
    )


@dataclass(frozen=True)
class SpectralIndexSpec:
    """一个光谱指数：所需角色 + numpy 公式 + 理论值域 + 出处。"""

    index_id: str
    required_roles: Tuple[str, ...]
    formula: Callable[..., np.ndarray]      # 参数序 = required_roles 序
    formula_text: str                       # 人类可读公式（进证据块）
    valid_range: Tuple[float, float]
    reference: str                          # method_references id 或 ""


INDEX_FAMILY: Dict[str, SpectralIndexSpec] = {
    s.index_id: s
    for s in [
        SpectralIndexSpec(
            "ndvi", ("red", "nir"),
            lambda red, nir: _safe_div(nir - red, nir + red),
            "(NIR − Red) / (NIR + Red)",
            (-1.0, 1.0), "rouse1974",
        ),
        SpectralIndexSpec(
            "gndvi", ("green", "nir"),
            lambda green, nir: _safe_div(nir - green, nir + green),
            "(NIR − Green) / (NIR + Green)",
            (-1.0, 1.0), "",   # Gitelson 1996 不在 method_references 词表 → 诚实留空
        ),
        SpectralIndexSpec(
            "savi", ("red", "nir"),
            lambda red, nir: 1.5 * _safe_div(nir - red, nir + red + 0.5),
            "((NIR − Red) · (1 + L)) / (NIR + Red + L), L = 0.5",
            (-1.0, 1.0), "huete1988",
        ),
        SpectralIndexSpec(
            # MSAVI2 (Qi et al. 1994)：SAVI 家族的自适应 L 推导；出处不在
            # 词表（huete1988 只覆盖 SAVI/EVI 本体）→ 留空，谱系在公式串披露。
            "msavi", ("red", "nir"),
            lambda red, nir: np.where(
                (2.0 * np.asarray(nir, dtype=float) + 1.0) ** 2
                - 8.0 * (np.asarray(nir, dtype=float) - np.asarray(red, dtype=float)) >= 0,
                (2.0 * np.asarray(nir, dtype=float) + 1.0
                 - np.sqrt(np.maximum(
                     (2.0 * np.asarray(nir, dtype=float) + 1.0) ** 2
                     - 8.0 * (np.asarray(nir, dtype=float)
                              - np.asarray(red, dtype=float)), 0.0))) / 2.0,
                np.nan,
            ),
            "MSAVI2 = (2·NIR + 1 − sqrt((2·NIR + 1)² − 8·(NIR − Red))) / 2",
            (-1.0, 1.0), "",
        ),
        SpectralIndexSpec(
            "ndwi", ("nir", "swir1"),
            lambda nir, swir1: _safe_div(nir - swir1, nir + swir1),
            "(NIR − SWIR1) / (NIR + SWIR1)  [植被水分]",
            (-1.0, 1.0), "gao1996",
        ),
        SpectralIndexSpec(
            "mndwi", ("green", "swir1"),
            lambda green, swir1: _safe_div(green - swir1, green + swir1),
            "(Green − SWIR1) / (Green + SWIR1)  [开放水体]",
            (-1.0, 1.0), "xu2006",
        ),
        SpectralIndexSpec(
            "ndbi", ("nir", "swir1"),
            lambda nir, swir1: _safe_div(swir1 - nir, swir1 + nir),
            "(SWIR1 − NIR) / (SWIR1 + NIR)  [建筑指数]",
            (-1.0, 1.0), "zha_woodcock2003",
        ),
        SpectralIndexSpec(
            # NDMI 与 Gao-NDWI 公式同形、语义命名不同（植被水分监测惯称）；
            # 正典出处 (Wilson & Sader 2002) 不在词表 → 留空。
            "ndmi", ("nir", "swir1"),
            lambda nir, swir1: _safe_div(nir - swir1, nir + swir1),
            "(NIR − SWIR1) / (NIR + SWIR1)  [植被水分，NDMI 惯称]",
            (-1.0, 1.0), "",
        ),
        SpectralIndexSpec(
            "nbr", ("nir", "swir2"),
            lambda nir, swir2: _safe_div(nir - swir2, nir + swir2),
            "(NIR − SWIR2) / (NIR + SWIR2)  [燃烧比]",
            (-1.0, 1.0), "key_benson2006",
        ),
        SpectralIndexSpec(
            # EVI 的 +1 与 −7.5·B 项只在反射率单位下物理成立（#382）；
            # #537：全零波段（S2 L2A nodata 惯例）保持 NaN，不产伪 0。
            "evi", ("blue", "red", "nir"),
            lambda blue, red, nir: np.where(
                (np.asarray(blue, dtype=float) == 0)
                & (np.asarray(red, dtype=float) == 0)
                & (np.asarray(nir, dtype=float) == 0),
                np.nan,
                2.5 * _safe_div(
                    np.asarray(nir, dtype=float) - np.asarray(red, dtype=float),
                    (np.asarray(nir, dtype=float) + 6.0 * np.asarray(red, dtype=float)
                     - 7.5 * np.asarray(blue, dtype=float) + 1.0),
                ),
            ),
            "2.5 · (NIR − Red) / (NIR + 6·Red − 7.5·Blue + 1)",
            (-1.0, 2.5), "huete1988",
        ),
    ]
}


# ── 角色解析（绝不按位置猜测）────────────────────────────────────────

def validate_band_map(index_id: str, band_map: Dict[str, object]) -> Tuple[str, ...]:
    """校验 ``band_map`` 是否覆盖 ``index_id`` 所需的语义角色。

    Returns:
        required_roles（INDEX_FAMILY 声明序）。

    Raises:
        UnsupportedBandSemantics: 指数未知或角色缺失 —— 错误体列出所需
        角色并提示显式 band_map；本层**没有**任何按波段位置的回退猜测。
    """
    spec = INDEX_FAMILY.get((index_id or "").lower())
    if spec is None:
        raise UnsupportedBandSemantics(
            f"未知光谱指数 '{index_id}'；可用: {sorted(INDEX_FAMILY)}",
            correction_hint="从 INDEX_FAMILY 支持的指数中选择，或先扩展公式族",
        )
    provided = set(band_map or {})
    missing = [r for r in spec.required_roles if r not in provided]
    if missing:
        raise UnsupportedBandSemantics(
            f"指数 '{spec.index_id}' 需要波段语义角色 {list(spec.required_roles)}；"
            f"缺失: {missing}。波段角色必须显式命名（band_map），"
            "本层不按波段位置猜测",
            correction_hint=(
                "提供 band_map，如 {'red': ..., 'nir': ...}；"
                f"本指数至少需要 {missing}"),
        )
    return spec.required_roles


# ── 主入口 ───────────────────────────────────────────────────────────

def compute_spectral_index(
    arrays: Dict[str, np.ndarray],
    index_id: str,
    *,
    scale_factors: Optional[Dict[str, float]] = None,
    nodata: Optional[np.ndarray] = None,
) -> Dict[str, object]:
    """按类型化语义角色计算光谱指数（定标 → 公式 → 诚实值域报告）。

    Args:
        arrays: 角色 → 2D 数组（键必须命中指数所需角色；多余角色忽略）。
        index_id: INDEX_FAMILY id（大小写不敏感）。
        scale_factors: 角色 → 线性定标除数（如 Sentinel-2 L2A DN 10000）。
            先除定标再进公式（EVI/SAVI 的常数项只在反射率单位下成立）。
        nodata: 布尔掩膜（True = 无效像元）；数组内 NaN/Inf 自动视为无效。

    Returns:
        dict: array（float，nodata/零分母 → NaN）、roles_used、
        scale_factors_applied、valid_pixel_fraction（有限值/总像元）、
        out_of_range_fraction（超理论值域/有限值——只报告不钳制）、
        valid_range、formula（人类可读串）、reference（出处 id 或 ""）。
    """
    spec = INDEX_FAMILY.get((index_id or "").lower())
    if spec is None:
        raise UnsupportedBandSemantics(
            f"未知光谱指数 '{index_id}'；可用: {sorted(INDEX_FAMILY)}",
            correction_hint="从 INDEX_FAMILY 支持的指数中选择",
        )
    validate_band_map(spec.index_id, arrays)

    roles = spec.required_roles
    scale_factors = dict(scale_factors or {})
    scaled: List[np.ndarray] = []
    scale_applied: Dict[str, float] = {}
    for role in roles:
        arr = np.asarray(arrays[role], dtype=float)
        factor = scale_factors.get(role)
        if factor is not None:
            if not (np.isfinite(factor) and factor > 0):
                raise ValueError(
                    f"scale_factors[{role!r}] 必须为正有限数，got {factor!r}")
            arr = arr / float(factor)
            scale_applied[role] = float(factor)
        scaled.append(arr)

    out = np.asarray(spec.formula(*scaled), dtype=float)

    invalid = ~np.isfinite(out)
    for arr in scaled:
        invalid |= ~np.isfinite(arr)
    if nodata is not None:
        invalid |= np.asarray(nodata, dtype=bool)
    out = np.where(invalid, np.nan, out)

    total = int(out.size)
    finite = np.isfinite(out)
    n_finite = int(np.sum(finite))
    lo, hi = spec.valid_range
    out_of_range = finite & ((out < lo) | (out > hi))
    n_out = int(np.sum(out_of_range))

    return {
        "array": out,
        "index_id": spec.index_id,
        "roles_used": list(roles),
        "scale_factors_applied": scale_applied,
        "valid_pixel_fraction": (n_finite / total) if total else 0.0,
        "out_of_range_fraction": (n_out / n_finite) if n_finite else 0.0,
        "valid_range": (float(lo), float(hi)),
        "formula": spec.formula_text,
        "reference": spec.reference,
        "disclosure": (
            "超出理论值域的输出仅报告（out_of_range_fraction），未钳制；"
            "值域违背通常是未定标 DN 输入的信号"),
    }
