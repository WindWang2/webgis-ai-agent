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


__all__ = ["COLOR_PALETTES", "get_color_from_palette", "resolve_palette_colors"]
