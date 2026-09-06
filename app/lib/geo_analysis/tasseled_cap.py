"""Tasseled Cap 变换 —— 传感器系数注册表（Foundation V2 · A6）。

Kauth-Thomas 型冠层变换：亮度（土壤背景/总体反射率轴）、绿度
（植被活力）、湿度（冠层/土壤水分）三轴，各传感器一行系数。

系数注册表（角色序固定为 blue/green/red/nir/swir1/swir2；出处 id ∈
method_references 词表）：

- ``landsat5_tm``（crist_cicone1984）：TM 1/2/3/4/5/7 经典六行系数；
- ``landsat8_oli``（baig2014）：OLI B2-B7，**at-satellite 反射率**
  推导；
- ``sentinel2``（shi_xu2019）：S2 B2/B3/B4/B8/B11/B12，
  **at-satellite 反射率**推导。

诚实边界：

- 波段解析**只走语义角色**（blue/green/red/nir/swir1/swir2 六角色
  全需），缺任一角色 → UnsupportedBandSemantics（绝不按位置猜测）；
- ``reflectance_domain`` 参数只做**披露**（"surface"/"at_satellite"）：
  baig2014/shi_xu2019 系数在 at-satellite 反射率上推导，crist_cicone1984
  基于 TM 反射率因子——域错配不改公式，但误差进假设披露（不静默）；
- 输出 = Σ coef·band（线性组合，逐像元）；任一角色像元无效 → 该像元
  三轴全 NaN（无角色级稀释）；
- 每像元贡献分解（coef·band）只作证据摘要（全局均值贡献），完整面
  不搬运。
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from app.lib.gis.scientific_errors import (
    UnsupportedBandSemantics,
    UnsupportedMethod,
)

__all__ = [
    "TASSELED_CAP_SENSORS",
    "TASSELED_CAP_COMPONENTS",
    "TASSELED_CAP_ROLE_ORDER",
    "TASSELED_CAP_COEFFICIENTS",
    "tasseled_cap",
]

TASSELED_CAP_SENSORS = ("landsat5_tm", "landsat8_oli", "sentinel2")
TASSELED_CAP_COMPONENTS = ("brightness", "greenness", "wetness")
TASSELED_CAP_ROLE_ORDER = ("blue", "green", "red", "nir", "swir1", "swir2")

# 角色序固定 (blue, green, red, nir, swir1, swir2)；行 = 分量轴。
TASSELED_CAP_COEFFICIENTS: Dict[str, Dict[str, List[float]]] = {
    "landsat5_tm": {
        # Crist & Cicone 1984（TM 1/2/3/4/5/7 → 六角色序）
        "brightness": [0.3037, 0.2793, 0.4743, 0.5585, 0.5082, 0.1863],
        "greenness": [-0.2848, -0.2435, -0.5436, 0.7243, 0.0840, -0.1800],
        "wetness": [0.1509, 0.1973, 0.3279, 0.3406, -0.7112, -0.4572],
    },
    "landsat8_oli": {
        # Baig et al. 2014（OLI B2-B7，at-satellite reflectance 推导）
        "brightness": [0.3029, 0.2786, 0.4733, 0.5599, 0.5080, 0.1872],
        "greenness": [-0.2941, -0.2430, -0.5424, 0.7276, 0.0713, -0.1608],
        "wetness": [0.1511, 0.1973, 0.3283, 0.3407, -0.7117, -0.4559],
    },
    "sentinel2": {
        # Shi & Xu 2019（S2 B2/B3/B4/B8/B11/B12，at-satellite reflectance）
        "brightness": [0.3327, 0.3637, 0.5621, 0.5728, 0.3813, 0.2423],
        "greenness": [-0.2203, -0.2120, -0.4910, 0.7812, 0.0317, -0.1958],
        "wetness": [0.1029, 0.1117, 0.3239, 0.6585, -0.4064, -0.5194],
    },
}

_REFLECTANCE_DOMAINS = ("surface", "at_satellite")


def resolve_tasseled_roles(band_map: Dict[str, object]) -> tuple:
    """校验 band_map 覆盖六语义角色（缺任一 → UnsupportedBandSemantics）。

    本层没有任何按波段位置的回退猜测——与 spectral.validate_band_map
    同一拒绝哲学，但所需角色集是 Tasseled Cap 固定六角色。
    """
    provided = set(band_map or {})
    missing = [r for r in TASSELED_CAP_ROLE_ORDER if r not in provided]
    if missing:
        raise UnsupportedBandSemantics(
            f"Tasseled Cap 需要波段语义角色 {list(TASSELED_CAP_ROLE_ORDER)}；"
            f"缺失: {missing}。波段角色必须显式命名（band_map），"
            "本层不按波段位置猜测",
            correction_hint=(
                "提供 {'blue','green','red','nir','swir1','swir2'} 六角色"
                "数组（按传感器波段表显式映射）"),
        )
    return TASSELED_CAP_ROLE_ORDER


def tasseled_cap(
    bands: Dict[str, np.ndarray],
    *,
    sensor: str,
    reflectance_domain: str = "at_satellite",
    nodata: Optional[float] = None,
) -> Dict[str, object]:
    """Tasseled Cap 三轴变换（亮度/绿度/湿度；线性组合逐像元）。

    Args:
        bands: 语义角色 → 2D 数组（六角色全需；多余角色忽略）。
        sensor: ``landsat5_tm`` / ``landsat8_oli`` / ``sentinel2``。
        reflectance_domain: ``surface`` / ``at_satellite``（披露用——
            系数推导域错配不静默，进 meta 假设披露）。
        nodata: 标量哨兵值；NaN/Inf 自动视为无效。

    Returns:
        dict: components（{brightness/greenness/wetness: 2D 数组，
        任一角色无效的像元 → NaN}）、coefficients（分量 → 角色 → 系数，
        证据/审计用）、meta（公式 + 域披露）。
    """
    sensor_key = (sensor or "").lower()
    if sensor_key not in TASSELED_CAP_COEFFICIENTS:
        raise UnsupportedMethod(
            f"未知传感器 '{sensor}'；已注册: "
            f"{list(TASSELED_CAP_COEFFICIENTS)}（新传感器系数需显式注册，"
            "绝不默认套用他传感器系数）",
            correction_hint="从 TASSELED_CAP_SENSORS 选择，或先注册该"
                            "传感器的发表系数行",
        )
    domain_key = (reflectance_domain or "at_satellite").lower()
    if domain_key not in _REFLECTANCE_DOMAINS:
        raise ValueError(
            f"reflectance_domain 必须是 {_REFLECTANCE_DOMAINS} 之一，"
            f"got {reflectance_domain!r}")
    roles = resolve_tasseled_roles(bands)

    arrays: Dict[str, np.ndarray] = {}
    shape: Optional[tuple] = None
    for role in roles:
        arr = np.asarray(bands[role], dtype=float)
        if arr.ndim != 2:
            raise ValueError(
                f"bands[{role!r}] 必须是 2D 数组，got ndim={arr.ndim}")
        if shape is None:
            shape = arr.shape
        elif arr.shape != shape:
            raise ValueError(
                f"bands 各角色形状不一致：{role}={arr.shape} vs {shape}")
        arrays[role] = arr
    assert shape is not None

    invalid = np.zeros(shape, dtype=bool)
    for role in roles:
        arr = arrays[role]
        bad = ~np.isfinite(arr)
        if nodata is not None:
            bad |= arr == float(nodata)
        invalid |= bad

    coef_rows = TASSELED_CAP_COEFFICIENTS[sensor_key]
    components: Dict[str, np.ndarray] = {}
    for comp in TASSELED_CAP_COMPONENTS:
        row = coef_rows[comp]
        acc = np.zeros(shape, dtype=float)
        for coef, role in zip(row, roles):
            acc += coef * arrays[role]
        components[comp] = np.where(invalid, np.nan, acc)

    # 每分量全局平均贡献（有界证据摘要：Σ|coef·band| 均值占比）。
    contribution: Dict[str, Dict[str, float]] = {}
    for comp in TASSELED_CAP_COMPONENTS:
        row = coef_rows[comp]
        abs_total = 0.0
        per_role: Dict[str, float] = {}
        for coef, role in zip(row, roles):
            contrib = float(
                np.nanmean(np.abs(coef * np.where(invalid, np.nan,
                                                  arrays[role]))))
            per_role[role] = round(contrib, 6)
            abs_total += contrib
        contribution[comp] = {
            r: round(v / abs_total, 6) if abs_total > 0 else 0.0
            for r, v in per_role.items()
        }

    disclosure = (
        f"系数出处随 sensor 声明（{sensor_key}）；reflectance_domain="
        f"{domain_key}（仅披露——baig2014/shi_xu2019 于 at-satellite "
        "反射率推导，域错配需自行评估）；任一角色像元无效 → 三轴全 NaN")
    meta: Dict[str, object] = {
        "sensor": sensor_key,
        "reflectance_domain": domain_key,
        "roles_used": list(roles),
        "formula": ("；".join(
            f"{c} = Σ coef·band（{sensor_key} 系数行）"
            for c in TASSELED_CAP_COMPONENTS)),
        "disclosure": disclosure,
    }
    return {
        "components": components,
        "coefficients": {c: dict(zip(roles, coef_rows[c]))
                         for c in TASSELED_CAP_COMPONENTS},
        "contribution": contribution,
        "roles_used": list(roles),
        "meta": meta,
    }
