"""G20 色带单一解析面：瓦片像素、图例、导出快照共用同一 LUT。

历史上有两套互不相通的命名空间：
- cartography ``COLOR_PALETTES``（名称 → hex stops，``resolve_palette_colors`` 未知
  名静默回落 YlOrRd —— 对「未知色带必须报具体错误」是错的）；
- matplotlib ``colormaps[name]``（``app/schemas/raster_spec.py::colormap_rgb_lut``，
  未知名返回 None → 渲染器静默回落灰度）。

本模块把两者合并为一个**显式**解析表：别名映射 + 封闭词表 + 单 LUT 生成。
未知名字返回 None，由 contract 层转为 ``UNKNOWN_COLORMAP``。
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from app.lib.cartography.palettes import COLOR_PALETTES

#: cartography 名称 / 常见别名 → matplotlib 或 COLOR_PALETTES 规范名。
PALETTE_ALIASES = {
    "Viridis": "viridis",
    "Magma": "magma",
    "Inferno": "inferno",
    "Plasma": "plasma",
    "Cividis": "cividis",
    "Gray": "gray",
    "grey": "gray",
    "Greys": "gray",
    "RdYlGn": "RdYlGn",
    "RdBu": "RdBu",
    "PuOr": "PuOr",
    "YlOrRd": "YlOrRd",
    "Blues": "Blues",
    "Greens": "Greens",
    "Reds": "Reds",
    "Oranges": "Oranges",
    "Purples": "Purples",
    "Set1": "Set1",
    "Set2": "Set2",
    "Dark2": "Dark2",
    "Pastel1": "Pastel1",
    "Spectral": "Spectral",
    "coolwarm": "coolwarm",
    "turbo": "turbo",
    "jet": "jet",
    "terrain": "terrain",
    "viridis": "viridis",
    "plasma": "plasma",
    "inferno": "inferno",
    "magma": "magma",
    "cividis": "cividis",
    "gray": "gray",
}

#: matplotlib 连续色带词表（含 ``_r`` 反向）。离散/qualitative 名不在内：
#: 单波段连续量用离散色带表达物理量是错误语义。
_MATPLOTLIB_CONTINUOUS = (
    "viridis", "plasma", "inferno", "magma", "cividis", "gray",
    "coolwarm", "turbo", "jet", "terrain", "Spectral", "RdYlGn", "RdBu",
    "PuOr", "YlOrRd", "Blues", "Greens", "Reds", "Oranges", "Purples",
    "YlGnBu", "BuPu", "GnBu", "autumn", "bone", "copper", "hot", "hsv",
    "spring", "summer", "winter", "cool", "Wistia", "afmhot", "gist_heat",
)
MATPLOTLIB_COLORMAPS = frozenset(
    _MATPLOTLIB_CONTINUOUS + tuple(f"{n}_r" for n in _MATPLOTLIB_CONTINUOUS)
)

#: 面向 UI 的合法色带词表（稳定顺序，图例/面板同源）。
KNOWN_COLORMAPS = tuple(sorted(set(COLOR_PALETTES) | set(PALETTE_ALIASES) | MATPLOTLIB_COLORMAPS))

_LUT_CACHE: dict[tuple[str, int], np.ndarray] = {}
_LUT_CACHE_MAX = 64


def canonical_colormap(name: Optional[str]) -> Optional[str]:
    """输入色带名 → 规范名；未知返回 None（绝不静默回落）。"""
    if not isinstance(name, str) or not name.strip():
        return None
    raw = name.strip()
    if raw in KNOWN_COLORMAPS:
        return raw
    alias = PALETTE_ALIASES.get(raw)
    if alias is not None and (alias in COLOR_PALETTES or alias in MATPLOTLIB_COLORMAPS):
        return alias
    # 反向 cartography 色带（COLOR_PALETTES 里的名字 + "_r"）
    if raw.endswith("_r") and raw[:-2] in COLOR_PALETTES:
        return raw
    return None


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = value.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _stops_lut(hexes: List[str], size: int) -> np.ndarray:
    """hex stops → (size,3) uint8；与 raster_stretch.decode_payload_rgba 同舍入。"""
    stops = np.asarray([_hex_to_rgb(c) for c in hexes], dtype=np.float64)
    n = len(stops)
    pos = np.linspace(0.0, 1.0, size) * (n - 1)
    lo = np.floor(pos).astype(int)
    hi = np.minimum(lo + 1, n - 1)
    frac = (pos - lo)[:, None]
    rgb = stops[lo] * (1.0 - frac) + stops[hi] * frac
    return np.floor(rgb + 0.5).astype(np.uint8).clip(0, 255)


def colormap_lut(name: Optional[str], size: int = 256) -> Optional[np.ndarray]:
    """规范色带 → (size,3) uint8 LUT；未知名字返回 None。

    渲染、图例色样与导出色标全部调用本函数 —— 同一 colorbar 不会出现三份色值。
    """
    canonical = canonical_colormap(name)
    if canonical is None:
        return None
    key = (canonical, size)
    cached = _LUT_CACHE.get(key)
    if cached is not None:
        return cached
    if canonical.endswith("_r") and canonical[:-2] in COLOR_PALETTES:
        lut = _stops_lut(list(reversed(COLOR_PALETTES[canonical[:-2]])), size)
    elif canonical in COLOR_PALETTES:
        lut = _stops_lut(list(COLOR_PALETTES[canonical]), size)
    else:
        from app.schemas.raster_spec import colormap_rgb_lut

        lut = colormap_rgb_lut(canonical, size)
        if lut is None:
            return None
        lut = np.asarray(lut, dtype=np.uint8)
    if len(_LUT_CACHE) >= _LUT_CACHE_MAX:
        _LUT_CACHE.clear()
    _LUT_CACHE[key] = lut
    return lut


def legend_swatches(name: Optional[str], count: int = 6) -> List[str]:
    """图例色样（从渲染 LUT 直接采样，绝不另写一份颜色表）。"""
    lut = colormap_lut(name, 256)
    if lut is None:
        return []
    picks = np.linspace(0, 255, max(2, min(count, 12))).round().astype(int)
    return [f"#{int(r):02x}{int(g):02x}{int(b):02x}" for r, g, b in lut[picks]]
