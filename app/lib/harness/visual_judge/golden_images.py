"""10 黄金样本的确定性合成渲染器（ADR-0185 D8）。

同名样本字节级可复现（无随机源、纯算术布点）——支撑 sha256 幂等断言与
记忆化键稳定性。渲染器只求「缺陷视觉上可辨认」，不求制图美感；契约测试
对每个样本走全管线（extract → fake VLM → 消毒 → 评分 → 报告）。
"""
from __future__ import annotations

import io
import math
from typing import Callable, Dict, Tuple

from PIL import Image, ImageDraw, ImageFont

Size = Tuple[int, int]
_DEFAULT_SIZE: Size = (640, 480)
_FONT = None


def _font(size: int = 12):
    global _FONT
    try:
        if _FONT is None or _FONT[0] != size:
            _FONT = (size, ImageFont.load_default(size=size))
        return _FONT[1]
    except TypeError:  # Pillow < 10.1 不支持 size 参数
        return ImageFont.load_default()


def _label(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], text: str,
           fill=(20, 20, 20)):
    draw.text(xy, text, fill=fill, font=_font(12))


# ── 各样本渲染器 ──────────────────────────────────────────────────────────


def _render_overlapping_labels(draw: ImageDraw.ImageDraw, size: Size):
    draw.rectangle([0, 0, size[0], size[1]], fill=(235, 232, 225))
    # 中心区密集标签网格：间距小于行高 ⇒ 逐字形互相压盖。
    x0, y0 = int(size[0] * 0.22), int(size[1] * 0.28)
    for row in range(14):
        for col in range(10):
            _label(draw, (x0 + col * 18, y0 + row * 13),
                   f" Parcel {row}{chr(65 + col % 26)}-12", fill=(10, 10, 10))


def _render_low_contrast_dark_theme(draw: ImageDraw.ImageDraw, size: Size):
    draw.rectangle([0, 0, size[0], size[1]], fill=(28, 28, 38))
    # 深色图斑贴深色底图：填充/描边与背景亮度差 < 10。
    for i, box in enumerate([(60, 60, 260, 220), (300, 90, 560, 260),
                             (120, 280, 380, 430), (420, 300, 600, 440)]):
        draw.rectangle(box, fill=(42, 42, 54), outline=(52, 52, 64))
        _label(draw, (box[0] + 8, box[1] + 8), f"zone-{i}", fill=(58, 58, 70))


def _render_adjacent_palette_confusion(draw: ImageDraw.ImageDraw, size: Size):
    draw.rectangle([0, 0, size[0], size[1]], fill=(245, 245, 245))
    # 相邻两类的色差 ΔRGB ≈ 5 —— 视觉上无法分辨。
    draw.rectangle([40, 80, 300, 400], fill=(100, 120, 180), outline=(60, 60, 60))
    draw.rectangle([310, 80, 580, 400], fill=(105, 125, 185), outline=(60, 60, 60))
    _label(draw, (140, 40), "class A (42%)", fill=(20, 20, 20))
    _label(draw, (400, 40), "class B (41%)", fill=(20, 20, 20))


def _render_symbol_clutter(draw: ImageDraw.ImageDraw, size: Size):
    draw.rectangle([0, 0, size[0], size[1]], fill=(250, 250, 248))
    # 纯算术布点（无随机源）：415 个 3px 符号铺满画布。
    for i in range(415):
        x = (i * 97 + 13) % (size[0] - 12) + 6
        y = (i * 211 + 29) % (size[1] - 12) + 6
        r = 3
        draw.ellipse([x - r, y - r, x + r, y + r],
                     fill=(200, 40, 40) if i % 3 else (40, 60, 200))


def _render_sparse_canvas(draw: ImageDraw.ImageDraw, size: Size):
    draw.rectangle([0, 0, size[0], size[1]], fill=(255, 255, 255))
    cx, cy = int(size[0] * 0.62), int(size[1] * 0.5)
    draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(180, 30, 30))
    _label(draw, (cx + 8, cy - 6), "site", fill=(30, 30, 30))


def _render_bottom_heavy(draw: ImageDraw.ImageDraw, size: Size):
    draw.rectangle([0, 0, size[0], size[1]], fill=(240, 240, 238))
    y_top = int(size[1] * 0.75)  # 全部内容压进下 1/4。
    for i, x in enumerate(range(30, size[0] - 80, 90)):
        draw.polygon(
            [(x, size[1] - 20), (x + 40, y_top + 10), (x + 80, size[1] - 20)],
            fill=(70, 130, 90), outline=(30, 60, 40))
        _label(draw, (x + 10, y_top + 30), f"p{i}")
    _label(draw, (size[0] // 2 - 60, y_top + 8), "all features below this line")


def _render_overlay_offset(draw: ImageDraw.ImageDraw, size: Size):
    draw.rectangle([0, 0, size[0], size[1]], fill=(210, 214, 200))
    # 遥感底图语义：灰阶路网格。
    for x in range(0, size[0], 80):
        draw.line([(x, 0), (x, size[1])], fill=(150, 152, 145), width=6)
    for y in range(0, size[1], 80):
        draw.line([(0, y), (size[0], y)], fill=(150, 152, 145), width=6)
    # 矢量地块相对路网整体错位 34px —— 恒定偏移的错位缺陷。
    offset = 34
    for bx in range(20, size[0] - 60, 80):
        for by in range(20, size[1] - 60, 80):
            draw.rectangle([bx + offset, by + offset, bx + 56 + offset, by + 56 + offset],
                           outline=(220, 30, 30), width=2)


def _render_extreme_tilt(draw: ImageDraw.ImageDraw, size: Size):
    draw.rectangle([0, 0, size[0], size[1]], fill=(245, 244, 240))
    # 整幅网格旋转 32°：指北语义崩坏。
    angle = math.radians(32)
    cx, cy = size[0] / 2, size[1] / 2
    length = max(size) * 1.6
    dx, dy = math.cos(angle), math.sin(angle)
    for k in range(-14, 15):
        d = k * 46
        px, py = -dy * d, dx * d
        draw.line([(cx + px - dx * length / 2, cy + py - dy * length / 2),
                   (cx + px + dx * length / 2, cy + py + dy * length / 2)],
                  fill=(120, 120, 128), width=2)
    _label(draw, (30, 20), "N is not up", fill=(60, 20, 20))


def _render_tiny_text(draw: ImageDraw.ImageDraw, size: Size):
    draw.rectangle([0, 0, size[0], size[1]], fill=(250, 250, 250))
    draw.rectangle([80, 80, 280, 200], fill=(90, 140, 200), outline=(40, 40, 40))
    draw.rectangle([340, 220, 560, 380], fill=(200, 150, 90), outline=(40, 40, 40))
    small = _font(5)  # 5px：低于可读阈值。
    for x, y, text in [(90, 90, "riverside"), (360, 240, "hillside")]:
        draw.text((x, y), text, fill=(25, 25, 25), font=small)


def _render_clean_map(draw: ImageDraw.ImageDraw, size: Size):
    draw.rectangle([0, 0, size[0], size[1]], fill=(247, 247, 244))
    # 四象限均衡布斑 + 可读标注 + 标题 + 图例。
    quads = [
        (40, 70, (66, 122, 86), "forest"),
        (size[0] // 2 + 30, 70, (196, 148, 70), "farmland"),
        (40, size[1] // 2 + 30, (70, 100, 170), "water"),
        (size[0] // 2 + 30, size[1] // 2 + 30, (170, 80, 80), "urban"),
    ]
    for x, y, color, name in quads:
        draw.rectangle([x, y, x + 220, y + 130], fill=color, outline=(40, 40, 40))
        _label(draw, (x + 8, y + 8), name, fill=(255, 255, 255))
    _label(draw, (size[0] // 2 - 90, 16), "Land Use Overview", fill=(20, 20, 20))
    for i, (color, name) in enumerate([((66, 122, 86), "forest"),
                                       ((70, 100, 170), "water")]):
        ly = 40 + i * 22
        draw.rectangle([size[0] - 92, ly, size[0] - 76, ly + 12], fill=color,
                       outline=(40, 40, 40))
        _label(draw, (size[0] - 70, ly - 2), name)


_RENDERERS: Dict[str, Callable[[ImageDraw.ImageDraw, Size], None]] = {
    "overlapping_labels": _render_overlapping_labels,
    "low_contrast_dark_theme": _render_low_contrast_dark_theme,
    "adjacent_palette_confusion": _render_adjacent_palette_confusion,
    "symbol_clutter_overdensity": _render_symbol_clutter,
    "sparse_canvas_underdensity": _render_sparse_canvas,
    "bottom_heavy_layout": _render_bottom_heavy,
    "overlay_offset_misalignment": _render_overlay_offset,
    "extreme_tilt_rotation": _render_extreme_tilt,
    "tiny_unreadable_text": _render_tiny_text,
    "clean_balanced_map": _render_clean_map,
}


def render_golden_image(name: str, size: Size = _DEFAULT_SIZE) -> bytes:
    """确定性渲染黄金样本 PNG（同名同参 ⇒ 字节级一致）。"""
    renderer = _RENDERERS.get(name)
    if renderer is None:
        raise KeyError(f"unknown golden image: {name!r}")
    img = Image.new("RGB", size, (255, 255, 255))
    try:
        renderer(ImageDraw.Draw(img), size)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        img.close()


__all__ = ["render_golden_image"]
