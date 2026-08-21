"""
Cartographic color palette constants and interpolation utilities.
"""
from typing import Dict, List

COLOR_PALETTES: Dict[str, List[str]] = {
    "YlOrRd": ["#ffffb2", "#fed976", "#feb24c", "#fd8d3c", "#f03b20", "#bd0026"],
    "Blues": ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
    "Greens": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
    "Reds": ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
    "Viridis": ["#440154", "#3b528b", "#21908c", "#5dc963", "#fde725"],
    "Magma": ["#000004", "#3b0f70", "#8c2981", "#de4968", "#feb078", "#fcfdbf"],
}


def get_color_from_palette(palette_name: str, value: float) -> str:
    """
    Get color from palette corresponding to normalized value (0.0 ~ 1.0).
    """
    palette = COLOR_PALETTES.get(palette_name, COLOR_PALETTES["YlOrRd"])
    n = len(palette)
    idx = min(int(value * n), n - 1)
    return palette[idx]


def resolve_palette_colors(palette: str, fallback: str = "YlOrRd") -> List[str]:
    """Resolve a palette name to its COLOR_PALETTES color list, with fallback.

    Returns a fresh list. If ``palette`` is unknown, falls back to ``fallback``
    (default YlOrRd); if that too is unknown, falls back to a hardcoded
    YlOrRd triple. Consolidates the palette-resolution logic that was
    duplicated inline in heatmap_data's _build_legend_spec and h3_binning's
    legend block (ADR-0037 Win 4).
    """
    colors = COLOR_PALETTES.get(palette) or COLOR_PALETTES.get(fallback)
    if colors:
        return list(colors)
    return list(COLOR_PALETTES.get("YlOrRd", ["#ffffb2", "#feb24c", "#bd0026"]))


# ─── 原生热力图（MapLibre heatmap 层）────────────────────────────────────
# 色带与前端 frontend/lib/map-kit/renderer.ts 的 HEATMAP_PALETTES +
# HEATMAP_STOP_POSITIONS 同源：首色透明（官方示例的 blur 效果），其余 6 色
# 与前端逐色一致——图例（_build_legend_spec）、MapSpec 授权
# （analysis_cartography_converter）与前端渲染三者不会出现色带漂移。
# 停靠点位置按 MapLibre 累积 shader 的密度域标定：单点高斯峰 ≈0.4·weight·
# intensity，中间色压在 0.12-0.45 段保证单点/小簇/密集核分级可辨。
HEATMAP_STOP_POSITIONS = (0, 0.12, 0.25, 0.45, 0.65, 0.85, 1.0)

NATIVE_HEATMAP_COLORS: Dict[str, List[str]] = {
    "classic": ["rgba(38,110,182,0)", "#428cd2", "#3dbce8", "#60d678",
                "#fae032", "#fa8c28", "#eb2828"],
    "magma": ["rgba(0,0,4,0)", "#341058", "#70207a", "#b63679",
              "#f46d43", "#fcc178", "#ffffd9"],
    "viridis": ["rgba(68,1,84,0)", "#482878", "#3b5c9d", "#23948b",
                "#7acb62", "#fdd53c", "#ffffdc"],
    "thermal": ["rgba(0,40,255,0)", "#0066ff", "#00d6ff", "#50f078",
                "#ffe600", "#ff7800", "#eb1414"],
}


def heatmap_legend_colors(palette: str) -> List[str]:
    """图例渐变色 = 色带去掉透明的首色（6 段不透明色）。"""
    colors = NATIVE_HEATMAP_COLORS.get(palette, NATIVE_HEATMAP_COLORS["classic"])
    return list(colors[1:])


def heatmap_paint(palette: str = "classic", radius_px: int = 30) -> Dict[str, object]:
    """原生热力图层的 MapLibre paint 表达式（官方 create-a-heatmap-layer 范式）。

    - heatmap-radius/intensity 随 zoom 插值：远视图半径小、放大后补偿强度，
      避免「缩小全是红核 / 放大整片冷色」；
    - heatmap-color 多停靠点密度色带（首段透明）；
    - ``radius_px`` 语义是**屏幕像素**。单位归一化（legacy 米制 radius 的
      消化）只在 ``app.lib.cartography.heatmap_contract`` 的 compatibility
      adapter 中发生——本函数不做单位猜测，显式值仅做契约区间 clamp
      （[4, 80] px），非法类型回落默认 30px。
    """
    from app.lib.cartography.heatmap_contract import (
        DEFAULT_RADIUS_PX,
        clamp_radius_px,
    )

    colors = NATIVE_HEATMAP_COLORS.get(palette, NATIVE_HEATMAP_COLORS["classic"])
    stops: List[object] = []
    for pos, color in zip(HEATMAP_STOP_POSITIONS, colors):
        stops.extend([pos, color])
    try:
        radius = clamp_radius_px(radius_px)
    except (TypeError, ValueError):
        radius = DEFAULT_RADIUS_PX
    return {
        "heatmap-weight": 1,
        "heatmap-intensity": ["interpolate", ["linear"], ["zoom"],
                              0, 0.6, 9, 1.4, 13, 2.2],
        "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"], *stops],
        "heatmap-radius": ["interpolate", ["linear"], ["zoom"],
                           0, 2, 9, radius, 13, min(80, int(radius * 1.7))],
        "heatmap-opacity": 0.9,
    }


__all__ = [
    "COLOR_PALETTES", "get_color_from_palette", "resolve_palette_colors",
    "HEATMAP_STOP_POSITIONS", "NATIVE_HEATMAP_COLORS",
    "heatmap_legend_colors", "heatmap_paint",
]
