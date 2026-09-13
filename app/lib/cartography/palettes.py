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
    # PuOr：ColorBrewer diverging，色盲安全 —— 正式出版的红绿替代
    "PuOr": ["#e08214", "#fdb863", "#f7f7f7", "#b2abd2", "#5e3c99"],
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
    # ── V4：灰度（hillshade 预渲染 / 灰度打印诊断参考带）────────────────
    "Gray": ["#000000", "#404040", "#808080", "#bfbfbf", "#ffffff"],
}


def _hex_to_rgb_float(hex_color: str) -> tuple:
    """'#rrggbb' → (r,g,b) 线性化前 0-1 浮点。非法输入返回黑。"""
    try:
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        if len(h) != 6:
            return (0.0, 0.0, 0.0)
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except (ValueError, TypeError):
        return (0.0, 0.0, 0.0)


def _wcag_relative_luminance(hex_color: str) -> float:
    """WCAG 2.x 相对亮度（sRGB 线性化 + 加权）。"""
    r, g, b = _hex_to_rgb_float(hex_color)
    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG 2.x 对比度（1.0-21.0）。非法/透明色按黑处理（fail-closed）。"""
    la = _wcag_relative_luminance(hex_a)
    lb = _wcag_relative_luminance(hex_b)
    lighter = max(la, lb)
    darker = min(la, lb)
    return round((lighter + 0.05) / (darker + 0.05), 3)


def meets_wcag_contrast(hex_a: str, hex_b: str, *, level: str = "AA",
                        large_text: bool = False) -> bool:
    """WCAG 邻近判据：AA 正文 4.5 / 大字 3.0；AAA 正文 7.0 / 大字 4.5。"""
    threshold = {
        ("AA", False): 4.5, ("AA", True): 3.0,
        ("AAA", False): 7.0, ("AAA", True): 4.5,
    }.get((level, large_text), 4.5)
    return contrast_ratio(hex_a, hex_b) >= threshold


def palette_contrast_diagnostics(
    palette: str, *, canvas: str = "#ffffff"
) -> dict:
    """调色板 × 画布对比度诊断（图例文本/符号在画布上的可读性参考）。

    返回 {min_ratio, max_ratio, per_color: [{color, ratio, aa, aa_large}]}；
    未知调色板 → 空诊断（不编造）。
    """
    colors = COLOR_PALETTES.get(palette)
    if not colors:
        return {}
    per = []
    for c in colors:
        ratio = contrast_ratio(c, canvas)
        per.append({
            "color": c,
            "ratio": ratio,
            "aa": ratio >= 4.5,
            "aa_large": ratio >= 3.0,
        })
    ratios = [p["ratio"] for p in per]
    return {
        "canvas": canvas,
        "min_ratio": min(ratios),
        "max_ratio": max(ratios),
        "per_color": per,
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


# ─── CVD 模拟与上下文色带变换（AC-03 / ADR-0152）────────────────────────
# resolve_symbology 的 PaletteContext 校验依赖本节纯函数：
#   cvd_*  → simulate_cvd 后做 CIEDE2000 可分辨校验；
#   print  → print_desaturate（降饱和+明度单调趋势）后做灰度 ΔL 可分级校验。
# 全部为确定性纯函数（无随机、无 IO），golden 测试锁定数值。

# Machado et al. (2009) 色觉缺陷模拟矩阵，severity = 1.0，作用于**线性 RGB**。
# 这是目前引用最广的模拟标准（ColorBrewer/Chroma.js 同源），比 Brettel/Viénot
# 1999 的查表法更适合纯函数实现（任务书允许的简化变换）。
CVD_SIMULATION_MATRICES: Dict[str, tuple] = {
    "cvd_protanopia": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "cvd_deuteranopia": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "cvd_tritanopia": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}


def _gamma_encode_linear(c: float) -> float:
    """线性 RGB → sRGB 伽马编码（单通道，输入 clamp 到 [0,1]）。"""
    c = max(0.0, min(1.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1.0 / 2.4)) - 0.055


def simulate_cvd(hex_color: str, kind: str) -> _Optional[str]:
    """模拟色觉缺陷下的颜色感知（sRGB hex → sRGB hex）。

    线性 RGB 空间应用 Machado 矩阵后伽马编码回 sRGB。未知 kind 或不可解析
    颜色返回 None（fail-closed，调用方不猜）。输出确定性：同输入恒同输出。
    """
    matrix = CVD_SIMULATION_MATRICES.get(kind)
    rgb = parse_css_color(hex_color)
    if matrix is None or rgb is None:
        return None
    lin = tuple(_linearize_channel_f(c) for c in rgb)
    out = []
    for row in matrix:
        out.append(_gamma_encode_linear(row[0] * lin[0] + row[1] * lin[1] + row[2] * lin[2]))
    return "#{:02x}{:02x}{:02x}".format(*(int(round(c * 255.0)) for c in out))


def _linearize_channel_f(channel: int) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG 2.x 相对亮度（0-1）。非法色按黑处理。"""
    return _wcag_relative_luminance(hex_color)


def grayscale_ramp_separation(colors: List[str]) -> _Optional[float]:
    """色带相邻色的最小相对亮度差 ΔL（灰度可分级判据，print 上下文）。

    判据阈值 0.06 与 themes.cartographic.print_paper 的 print_safe 知识同源。
    任一色不可解析 → None（fail-closed）。
    """
    lums = []
    for c in colors:
        rgb = parse_css_color(c)
        if rgb is None:
            return None
        lums.append(_wcag_relative_luminance(c))
    if len(lums) < 2:
        return None
    return min(abs(lums[i] - lums[i + 1]) for i in range(len(lums) - 1))


def print_desaturate(colors: List[str], strength: float = 0.35) -> List[str]:
    """打印上下文的色带变换：向单调灰度目标 ramp 混合（降饱和 + 明度单调趋势）。

    target[i] 是首末色灰度之间的线性灰阶 —— 目标本身明度单调，混合后整体
    趋向降饱和且明度单调；最终可分辨性由 grayscale_ramp_separation 校验把关
    （不达标换带，而不是放宽阈值）。确定性纯函数。
    """
    parsed = [parse_css_color(c) for c in colors]
    if any(p is None for p in parsed) or len(parsed) < 2:
        return list(colors)
    lum_first = _wcag_relative_luminance(colors[0])
    lum_last = _wcag_relative_luminance(colors[-1])
    n = len(parsed)
    out: List[str] = []
    for i, rgb in enumerate(parsed):
        t = i / (n - 1)
        target_l = lum_first + (lum_last - lum_first) * t
        target = tuple(int(round(_gamma_encode_linear(target_l) * 255.0)) for _ in (0, 1, 2))
        blended = tuple(
            int(round(ch + (tg - ch) * strength)) for ch, tg in zip(rgb, target)
        )
        out.append("#{:02x}{:02x}{:02x}".format(*blended))
    return out


def sample_ramp_colors(palette: str, k: int) -> List[str]:
    """按 k 个归一化中点采样色带 ramp（与 resolve_thematic_colors 中点分支同口径）。

    这是裁决期「这条色带在 k 级下是否可分辨」的采样器：k 类的中点值
    (i+0.5)/k 经 get_color_from_palette 取色 —— 与成图时 graduated spec 的
    取色路径一致，裁决与成图不会各说各话。未知色带返回 []。
    """
    if k <= 0 or palette not in COLOR_PALETTES:
        return []
    return [get_color_from_palette(palette, (i + 0.5) / k) for i in range(k)]


def sample_heatmap_colors(family: str, k: int) -> List[str]:
    """原生热力色带族的 k 级采样（跳过透明首停靠点，端点对齐取色）。"""
    colors = heatmap_legend_colors(family)
    if k <= 0:
        return []
    if k == 1:
        return [colors[0]]
    return [colors[round(i * (len(colors) - 1) / (k - 1))] for i in range(k)]


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
    "CVD_SIMULATION_MATRICES", "simulate_cvd", "relative_luminance",
    "grayscale_ramp_separation", "print_desaturate",
    "sample_ramp_colors", "sample_heatmap_colors",
]
