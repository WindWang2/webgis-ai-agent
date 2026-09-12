"""出版版面描述中间层（Publication Layout IR）—— Python 忠实镜像（ADR-0157 P6）。

前端单源：``frontend/lib/export/layout-description.ts``（含 extent 数学
``frontend/lib/export/extent.ts``）。本模块逐字段镜像其纯函数装配语义：

- 版面档（paper/orientation/dpi/colorMode/bleed/cropMarks）；
- 标题回退链（请求参数 > spec 组件 > 空串）；
- 比例尺 nice-number（1/2/5×10^k 就近，与 ``map-kit/scale-math.ts`` 同算法）；
- WYSIWYG 范围契约（遮罩 ⊆ 导出、Mercator 归一量纲纵横比、数据超界提示）。

双端 parity 由 golden corpus 锁定（``tests/cartography/golden_corpus/
layout_description/``：pytest 与 vitest 消费同一批 fixture，逐字段对拍）。
前端 ``composeLayout``（canvas 绘制面）不在镜像范围 —— 本模块只承载
**决策**（什么元素、什么文本、什么数字），不承载像素绘制。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

PUBLICATION_LAYOUT_VERSION = 1

#: 出版档（cmyk）出血宽度（毫米）；与前端 PUBLICATION_BLEED_MM 同值。
PUBLICATION_BLEED_MM = 3

#: A4/A3 纵横比（width/height）；与前端 frameAspectWH / prepareExportCanvas 同口径。
_PAPER_RATIO = 1.414

BoundsWSEN = Tuple[float, float, float, float]


# ── Mercator 范围数学（extent.ts 镜像）─────────────────────────────────


def merc_norm_y(lat: float) -> float:
    """Web Mercator 归一 y（0=赤道，1=纬 85.05°）；无效纬度钳到投影范围。"""
    clamped = max(-85.05112878, min(85.05112878, lat))
    return 0.5 - math.log(math.tan(math.pi / 4 + math.radians(clamped) / 2)) / (2 * math.pi)


def _lat_of_norm(y: float) -> float:
    clamped = max(0.0, min(1.0, y))
    merc_n = 2 * math.pi * (0.5 - clamped)
    return math.degrees(2 * math.atan(math.exp(merc_n)) - math.pi / 2)


def _is_finite_bounds(b: Optional[Sequence[float]]) -> bool:
    return (
        b is not None
        and len(b) == 4
        and all(isinstance(v, (int, float)) and math.isfinite(v) for v in b)
        and b[2] > b[0]
        and b[3] > b[1]
    )


def export_bounds_for_frame(mask: BoundsWSEN, aspect_wh: float) -> Optional[BoundsWSEN]:
    """遮罩范围 → 图框导出范围（⊇ 遮罩，Mercator 中心不动点扩张）。

    量纲契约：经度差归一化（/360）后再与归一纬差比 —— 与前端实现一致
    （此前 TS 侧度/归一混用的 bug 即由该 parity 测试面捕获）。
    """
    if not _is_finite_bounds(mask) or not (aspect_wh > 0):
        return None
    w, s, e, n = mask
    y0 = merc_norm_y(s)
    y1 = merc_norm_y(n)
    merc_w = (e - w) / 360.0
    merc_h = y0 - y1
    if not (merc_w > 0) or not (merc_h > 0):
        return None

    cx = (w + e) / 2.0
    cy = (y0 + y1) / 2.0
    if merc_w / merc_h > aspect_wh:
        out_w, out_h = merc_w, merc_w / aspect_wh
    else:
        out_h, out_w = merc_h, merc_h * aspect_wh

    west = cx - out_w / 2 * 360
    east = cx + out_w / 2 * 360
    south = _lat_of_norm(cy + out_h / 2)
    north = _lat_of_norm(cy - out_h / 2)
    return (west, south, east, north)


def bounds_contained(
    data: Optional[Sequence[float]], frame: Optional[Sequence[float]]
) -> bool:
    """数据范围是否完全落入导出范围（非法输入 → true，不误报）。"""
    if not _is_finite_bounds(data) or not _is_finite_bounds(frame):
        return True
    eps = 1e-9
    return (
        data[0] >= frame[0] - eps
        and data[1] >= frame[1] - eps
        and data[2] <= frame[2] + eps
        and data[3] <= frame[3] + eps
    )


# ── 比例尺 nice-number（map-kit/scale-math.ts 镜像）────────────────────


def compute_nice_scale(meters_per_px: float, target_px: float) -> Dict[str, float]:
    """就近 nice 距离（1/2/5×10^k），与 TS 实现同序同判（严格 <，先到先得）。"""
    mpp = meters_per_px if meters_per_px > 0 else 1
    target = target_px if target_px > 0 else 100
    raw = mpp * target
    magnitude = 10 ** math.floor(math.log10(raw))
    best = magnitude
    for n in (1, 2, 5, 10):
        candidate = n * magnitude
        if abs(candidate - raw) < abs(best - raw):
            best = candidate
    return {"meters": float(best), "px": best / mpp}


def format_scale_label(meters: float) -> str:
    """距离标签（1000+ 米转 km，至多 1 位小数）。"""
    if meters >= 1000:
        return f"{round(meters / 1000, 1):g} km"
    return f"{meters:g} m"


def frame_aspect_wh(orientation: str) -> float:
    return 1.0 / _PAPER_RATIO if orientation == "portrait" else _PAPER_RATIO


# ── 版面描述装配（layout-description.ts 镜像）──────────────────────────


@dataclass
class LayoutInput:
    """装配输入（与前端 BuildPublicationLayoutInput 同形）。"""

    paper_size: str  # 'screen' | 'A4' | 'A3'
    orientation: str  # 'landscape' | 'portrait'
    dpi: int
    frame_width: float
    frame_height: float
    request_title: str = ""
    spec_title: str = ""
    request_subtitle: str = ""
    spec_subtitle: str = ""
    author: str = ""
    data_source: str = ""
    attribution_text: str = ""
    meters_per_pixel: Optional[float] = None
    mask_extent: Optional[BoundsWSEN] = None
    export_extent: Optional[BoundsWSEN] = None
    data_overflow: bool = False
    color_mode: str = "srgb"  # 'srgb' | 'cmyk'
    scale_bar_target_px: float = 120.0


def build_publication_layout(inp: LayoutInput) -> Dict[str, Any]:
    """装配出版版面描述（确定性、JSON 可序列化 —— golden 对拍面）。"""
    color_mode = inp.color_mode if inp.color_mode in ("srgb", "cmyk") else "srgb"
    bleed_mm = PUBLICATION_BLEED_MM if color_mode == "cmyk" else 0
    page = {
        "paperSize": inp.paper_size,
        "orientation": inp.orientation,
        "dpi": inp.dpi,
        "widthPx": round(inp.frame_width),
        "heightPx": round(inp.frame_height),
        "colorMode": color_mode,
        "bleedMm": bleed_mm,
        "cropMarks": color_mode == "cmyk",
    }

    title = inp.request_title or inp.spec_title or ""
    subtitle = inp.request_subtitle or inp.spec_subtitle or ""
    texts: List[Dict[str, str]] = []
    if title:
        texts.append({"kind": "title", "text": title})
    if subtitle:
        texts.append({"kind": "subtitle", "text": subtitle})
    if inp.author:
        texts.append({"kind": "author", "text": inp.author})
    if inp.data_source:
        texts.append({"kind": "dataSource", "text": inp.data_source})
    if inp.attribution_text:
        texts.append({"kind": "attribution", "text": inp.attribution_text})

    scale_bar: Optional[Dict[str, Any]] = None
    mpp = inp.meters_per_pixel
    if mpp is not None and mpp > 0:
        nice = compute_nice_scale(mpp, inp.scale_bar_target_px)
        scale_bar = {
            "metersPerPixel": mpp,
            "nice": nice,
            "label": format_scale_label(nice["meters"]),
        }

    degradations: List[Dict[str, str]] = []
    if inp.data_overflow:
        degradations.append(
            {
                "code": "extent_overflow_data",
                "detail": "数据范围超出出图范围，超界部分以背景呈现（未静默裁切）",
            }
        )

    return {
        "version": PUBLICATION_LAYOUT_VERSION,
        "page": page,
        "mapFrame": {"x": 0, "y": 0, "width": page["widthPx"], "height": page["heightPx"]},
        "texts": texts,
        "scaleBar": scale_bar,
        "extent": {
            "mask": list(inp.mask_extent) if inp.mask_extent else None,
            "export": list(inp.export_extent) if inp.export_extent else None,
            "dataOverflow": inp.data_overflow,
        },
        "degradations": degradations,
    }
