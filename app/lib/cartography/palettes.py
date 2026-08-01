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


__all__ = ["COLOR_PALETTES", "get_color_from_palette"]
