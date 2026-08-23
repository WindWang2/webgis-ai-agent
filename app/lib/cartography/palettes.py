"""
Cartographic color palette constants and interpolation utilities.
"""
from typing import Dict, List

COLOR_PALETTES: Dict[str, List[str]] = {
    # ── Sequential（ColorBrewer 2.0，5-class 官方 hex）──────────────────
    "YlOrRd": ["#ffffb2", "#fed976", "#feb24c", "#fd8d3c", "#f03b20", "#bd0026"],
    "Blues": ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
    "Greens": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
    "Reds": ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
    "Oranges": ["#feedde", "#fdbe85", "#fd8d3c", "#e6550d", "#a63603"],
    "Purples": ["#f2f0f7", "#cbc9e2", "#9e9ac8", "#756bb1", "#54278f"],
    # ── Diverging（以有意义中点为中心：偏差/阈值/相关性）────────────────
    "RdYlGn": ["#d73027", "#fc8d59", "#fee08b", "#d9ef8b", "#91cf60", "#1a9850"],
    "RdBu": ["#ca0020", "#f4a582", "#f7f7f7", "#92c5de", "#0571b0"],
    # ── Qualitative（类别/唯一值；上限即 ColorBrewer 定义的最大类数）─────
    "Set1": ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
             "#ff7f00", "#ffff33", "#a65628", "#f781bf", "#999999"],
    "Set2": ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3",
             "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3"],
    "Dark2": ["#1b9e77", "#d95f02", "#7570b3", "#e7298a",
              "#66a61e", "#e6ab02", "#a6761d", "#666666"],
    "Pastel1": ["#fbb4ae", "#b3cde3", "#ccebc5", "#decbe4", "#fed9a6",
                "#ffffcc", "#e5d8bd", "#fddaec", "#f2f2f2"],
    # ── Perceptual uniform（感知均匀、色盲安全、灰度打印保真）────────────
    "Viridis": ["#440154", "#3b528b", "#21908c", "#5dc963", "#fde725"],
    "Magma": ["#000004", "#3b0f70", "#8c2981", "#de4968", "#feb078", "#fcfdbf"],
    "Inferno": ["#000004", "#420a68", "#932667", "#dd513a", "#fca50a", "#fcffa4"],
    "Plasma": ["#0d0887", "#6a00a8", "#b12a90", "#e16462", "#fca636", "#f0f921"],
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


# native 热力色带名 → 通用 cartography 调色板名（图例渲染的 ramp key）
HEATMAP_LEGEND_PALETTE_KEY: Dict[str, str] = {
    "classic": "YlOrRd",
    "magma": "Magma",
    "viridis": "Viridis",
    "thermal": "Reds",
}


def build_heatmap_legend_spec(
    palette: str, min_val: float = 0.0, max_val: float = 1.0
) -> Dict[str, object]:
    """#718: 产品路径热力图层的 legend_spec 单一构建口——与 heatmap_data
    工具同源（NATIVE_HEATMAP_COLORS 停靠点色），消除『同一系统两处挂载
    热力图、只有一处带图例证据』的漂移。"""
    key = palette if palette in NATIVE_HEATMAP_COLORS else "classic"
    return {
        "type": "continuous",
        "min": min_val,
        "max": max_val,
        "palette": HEATMAP_LEGEND_PALETTE_KEY.get(key, "YlOrRd"),
        "palette_colors": heatmap_legend_colors(key),
    }


def heatmap_paint(palette: str = "classic", radius_px: int = 30) -> Dict[str, object]:
    """原生热力图层的 MapLibre paint 表达式（官方 create-a-heatmap-layer 范式）。

    - heatmap-radius/intensity 随 zoom 插值：远视图半径小、放大后补偿强度，
      避免「缩小全是红核 / 放大整片冷色」；
    - heatmap-color 多停靠点密度色带（首段透明）；
    - ``radius_px`` 语义是**屏幕像素**。单位归一化（legacy 米制 radius 的
      消化）只在 ``app.lib.cartography.heatmap_contract`` 的 compatibility
      adapter 中发生——本函数不做单位猜测，显式值仅做区间 clamp
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


# ─── 感知色差（CIEDE2000）与可分性色带 ─────────────────────────────────
# specs/cartographic-quality-rules-and-memory-spec P1: carto.color.separability
# 依赖的纯函数。无第三方依赖：sRGB→Lab(D65) 转换 + CIEDE2000（Sharma et al.
# 2005 公式,含色相旋转项）。

import math as _math
from typing import Optional as _Optional, Tuple as _Tuple


def parse_css_color(value: object) -> _Optional[_Tuple[int, int, int]]:
    """解析 CSS 颜色为不透明 sRGB 三元组。

    半透明色（rgba alpha<1）先合成到白底——图例/地图上的实际观感
    取决于底色，比较时以白底合成色为准。Pillow 拒绝 CSS4 浮点 alpha 的
    ``rgba()``（见 semantic_checks._is_supported_color 同源注释），因此
    functional rgb/rgba 自行解析，Pillow 仅兜底 hex/命名色/hsl。
    非法输入返回 None（调用方按 fail-closed 处理，绝不猜）。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    import re as _re

    color = value.strip()
    functional = _re.fullmatch(r"rgba?\(([^)]*)\)", color, flags=_re.IGNORECASE)
    if functional:
        parts = [p.strip() for p in functional.group(1).split(",")]
        expected = 4 if color.lower().startswith("rgba") else 3
        if len(parts) != expected:
            return None
        channels: List[float] = []
        for part in parts[:3]:
            try:
                if part.endswith("%"):
                    channels.append(float(part[:-1]) / 100.0 * 255.0)
                else:
                    channels.append(float(part))
            except ValueError:
                return None
        if any(c < 0.0 or c > 255.0 for c in channels):
            return None
        if expected == 4:
            alpha_part = parts[3]
            try:
                alpha = (
                    float(alpha_part[:-1]) / 100.0
                    if alpha_part.endswith("%")
                    else float(alpha_part)
                )
            except ValueError:
                return None
            if not 0.0 <= alpha <= 1.0:
                return None
        else:
            alpha = 1.0
        if alpha <= 0.0:
            return None
        if alpha >= 1.0:
            return (int(channels[0]), int(channels[1]), int(channels[2]))
        return (
            int(round(channels[0] * alpha + 255 * (1 - alpha))),
            int(round(channels[1] * alpha + 255 * (1 - alpha))),
            int(round(channels[2] * alpha + 255 * (1 - alpha))),
        )
    try:
        from PIL import ImageColor

        r, g, b, a = ImageColor.getcolor(color, "RGBA")
    except (ImportError, TypeError, ValueError):
        return None
    alpha = float(a) / 255.0
    if alpha <= 0.0:
        return None
    if alpha >= 1.0:
        return (int(r), int(g), int(b))
    return (
        int(round(int(r) * alpha + 255 * (1 - alpha))),
        int(round(int(g) * alpha + 255 * (1 - alpha))),
        int(round(int(b) * alpha + 255 * (1 - alpha))),
    )


def _srgb_to_lab(rgb: _Tuple[int, int, int]) -> _Tuple[float, float, float]:
    def _linearize(channel: int) -> float:
        c = channel / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (_linearize(c) for c in rgb)
    # sRGB → XYZ (D65)
    x = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
    y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
    z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041
    # D65 白点
    xn, yn, zn = 0.95047, 1.00000, 1.08883

    def _f(t: float) -> float:
        return t ** (1.0 / 3.0) if t > 216.0 / 24389.0 else (24389.0 / 27.0 * t + 16.0) / 116.0

    fx, fy, fz = _f(x / xn), _f(y / yn), _f(z / zn)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def ciede2000(
    rgb1: _Tuple[int, int, int], rgb2: _Tuple[int, int, int]
) -> float:
    """两 sRGB 颜色的 CIEDE2000 色差 ΔE00（Sharma et al. 2005 实现）。"""
    l1, a1, b1 = _srgb_to_lab(rgb1)
    l2, a2, b2 = _srgb_to_lab(rgb2)

    c1 = _math.hypot(a1, b1)
    c2 = _math.hypot(a2, b2)
    c_bar = (c1 + c2) / 2.0
    c_bar7 = c_bar ** 7
    g = 0.5 * (1.0 - _math.sqrt(c_bar7 / (c_bar7 + 25.0 ** 7)))
    a1p = (1.0 + g) * a1
    a2p = (1.0 + g) * a2
    c1p = _math.hypot(a1p, b1)
    c2p = _math.hypot(a2p, b2)

    def _hp(ap: float, b: float) -> float:
        if ap == 0.0 and b == 0.0:
            return 0.0
        h = _math.degrees(_math.atan2(b, ap))
        return h + 360.0 if h < 0.0 else h

    h1p = _hp(a1p, b1)
    h2p = _hp(a2p, b2)

    d_l = l2 - l1
    d_c = c2p - c1p
    if c1p * c2p == 0.0:
        d_h = 0.0
    else:
        d_h = h2p - h1p
        if d_h > 180.0:
            d_h -= 360.0
        elif d_h < -180.0:
            d_h += 360.0
    d_h_big = 2.0 * _math.sqrt(c1p * c2p) * _math.sin(_math.radians(d_h) / 2.0)

    l_bar = (l1 + l2) / 2.0
    c_barp = (c1p + c2p) / 2.0
    if c1p * c2p == 0.0:
        h_bar = h1p + h2p
    else:
        diff = abs(h1p - h2p)
        sum_h = h1p + h2p
        h_bar = (
            sum_h / 2.0 if diff <= 180.0
            else (sum_h + 360.0 if sum_h < 360.0 else sum_h - 360.0) / 2.0
        )

    t = (
        1.0
        - 0.17 * _math.cos(_math.radians(h_bar - 30.0))
        + 0.24 * _math.cos(_math.radians(2.0 * h_bar))
        + 0.32 * _math.cos(_math.radians(3.0 * h_bar + 6.0))
        - 0.20 * _math.cos(_math.radians(4.0 * h_bar - 63.0))
    )
    d_theta = 30.0 * _math.exp(-(((h_bar - 275.0) / 25.0) ** 2))
    c_barp7 = c_barp ** 7
    r_c = 2.0 * _math.sqrt(c_barp7 / (c_barp7 + 25.0 ** 7))
    s_l = 1.0 + (0.015 * (l_bar - 50.0) ** 2) / _math.sqrt(20.0 + (l_bar - 50.0) ** 2)
    s_c = 1.0 + 0.045 * c_barp
    s_h = 1.0 + 0.015 * c_barp * t
    r_t = -_math.sin(_math.radians(2.0 * d_theta)) * r_c

    return _math.sqrt(
        (d_l / s_l) ** 2
        + (d_c / s_c) ** 2
        + (d_h_big / s_h) ** 2
        + r_t * (d_c / s_c) * (d_h_big / s_h)
    )


def min_adjacent_delta_e(colors: List[str]) -> _Optional[float]:
    """色带相邻类的最小 ΔE00。任一色不可解析 → None（fail-closed）。"""
    parsed = [parse_css_color(c) for c in colors]
    if any(p is None for p in parsed) or len(parsed) < 2:
        return None
    return min(
        ciede2000(parsed[i], parsed[i + 1]) for i in range(len(parsed) - 1)
    )


_PERCEPTUAL_RAMP_ANCHORS = COLOR_PALETTES["Viridis"]


def perceptual_ramp(n: int) -> List[str]:
    """从感知均匀锚点色带（Viridis）均匀采样 n 色的替代色带。

    用于 carto.color.separability 失败时的 AUTO_SAFE 换带建议：类数不变、
    仅换呈现色，不触碰分类语义。n<2 或超过锚点可分能力时返回空列表
    （调用方不会拿到一条本身就不达标的“修复”）。
    """
    if n < 2 or n > 10:
        return []
    anchors = _PERCEPTUAL_RAMP_ANCHORS
    out: List[str] = []
    for i in range(n):
        pos = i / (n - 1) * (len(anchors) - 1)
        lo = int(_math.floor(pos))
        hi = min(lo + 1, len(anchors) - 1)
        frac = pos - lo
        c1 = parse_css_color(anchors[lo])
        c2 = parse_css_color(anchors[hi])
        if c1 is None or c2 is None:
            return []
        rgb = tuple(int(round(a + (b - a) * frac)) for a, b in zip(c1, c2))
        out.append("#{:02x}{:02x}{:02x}".format(*rgb))
    return out


__all__ = [
    "COLOR_PALETTES", "get_color_from_palette", "resolve_palette_colors",
    "HEATMAP_STOP_POSITIONS", "NATIVE_HEATMAP_COLORS",
    "heatmap_legend_colors", "heatmap_paint",
    "parse_css_color", "ciede2000", "min_adjacent_delta_e", "perceptual_ramp",
]
