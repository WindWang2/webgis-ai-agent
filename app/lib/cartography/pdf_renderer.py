"""PDF Cartography Rendering Engine (app/lib/cartography/pdf_renderer.py).

Pure domain module for rendering Canvas map images into standard A4 landscape thematic PDF maps.
Encapsulates Matplotlib layout geometry, thread-safe CJK font resolution, and PDF metadata embedding.

ac-08（ADR-0157 P6）：后端 PDF 整饰补齐 —— 指北针/比例尺/图例此前缺失；
现消费出版版面描述 IR（``app/lib/cartography/layout_description.py``，与
前端 ``frontend/lib/export/layout-description.ts`` 同源对拍）绘制：

- 指北针（右上，静态图示 —— 无方位角输入时不假装旋转）；
- 比例尺（右下，``layout.scaleBar`` 的 scale-math 单源数字；缺席不画不虚构）；
- 图例框（左下，``legend_items`` 来自 legend 单源派生 ``derive_legend_items``）。

CJK 字体：优先仓内 vendored Noto Sans SC 子集（``fonts/``，font_manager
addfont 注册，确定性覆盖 GB2312 全表），系统字体关键字扫描降为后备。
"""
import io
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# CJK font fallback keywords
_CJK_KEYWORDS = (
    "cjk", "noto", "source han", "wqy", "simhei",
    "simsun", "microsoft yahei", "pingfang", "heiti"
)

#: 仓内 vendored 字体（与 frontend/public/fonts 同一份构建产物）。
_VENDORED_FONT = Path(__file__).parent / "fonts" / "NotoSansSC-Regular-subset.ttf"


def _register_vendored_cjk_font():
    """注册仓内 Noto Sans SC 子集并返回 FontProperties；缺席 → None。"""
    import matplotlib.font_manager as fm

    if _VENDORED_FONT.is_file():
        try:
            fm.fontManager.addfont(str(_VENDORED_FONT))
            return fm.FontProperties(fname=str(_VENDORED_FONT))
        except Exception as e:  # noqa: BLE001 —— 字体损坏时走系统扫描
            logger.warning("[pdf_renderer] vendored CJK font load failed: %s", e)
    return None


def _resolve_cjk_font_properties():
    """Find a CJK font and return a Matplotlib FontProperties object for thread-safe isolation."""
    import matplotlib.font_manager as fm

    vendored = _register_vendored_cjk_font()
    if vendored is not None:
        return vendored
    for f in fm.fontManager.ttflist:
        if any(kw in f.name.lower() for kw in _CJK_KEYWORDS):
            return fm.FontProperties(family=f.name)
    logger.warning(
        "[pdf_renderer] CJK font not found in system. Chinese titles may render as tofu boxes. "
        "Recommend installing Noto CJK, Source Han Sans, or Microsoft YaHei."
    )
    return None


def _draw_north_arrow(fig, ax_map) -> None:
    """指北针（右上，fig 坐标）：实心北三角 + N 标 —— 静态图示。"""
    from matplotlib.patches import Polygon

    x0 = 0.945
    y0 = 0.885
    w = 0.012
    h = 0.032
    north = Polygon(
        [(x0, y0 + h), (x0 - w, y0), (x0, y0 + h * 0.28)],
        closed=True, facecolor="#1e293b", edgecolor="#1e293b", zorder=30,
    )
    south = Polygon(
        [(x0, y0 + h), (x0 + w, y0), (x0, y0 + h * 0.28)],
        closed=True, facecolor="white", edgecolor="#1e293b", zorder=30,
    )
    fig.add_artist(north)
    fig.add_artist(south)
    fig.text(x0, y0 + h + 0.008, "N", ha="center", va="bottom",
             fontsize=9, fontweight="bold", color="#1e293b", zorder=30)


def _draw_scale_bar(fig, ax_map, scale_bar: Dict[str, Any], map_frame: Dict[str, Any]) -> None:
    """比例尺（ax 右下）：nice 单源数字 → 图面长度按地图画幅宽换算。"""
    import matplotlib.lines as mlines

    nice = scale_bar.get("nice") or {}
    frame_w = float(map_frame.get("width") or 0)
    bar_px = float(nice.get("px") or 0)
    label = str(scale_bar.get("label") or "")
    if bar_px <= 0 or frame_w <= 0 or not label:
        return
    # bar 图面长度 = bar_px / 画幅总宽 → ax 宽度分数（上限 0.4 防溢出）
    frac = min(bar_px / frame_w, 0.4)
    x1 = 0.955
    x0 = x1 - frac
    y = 0.115
    for (xa, ya), (xb, yb) in (
        ((x0, y), (x1, y)),  # 主线
        ((x0, y - 0.006), (x0, y + 0.006)),  # 端刻
        ((x1, y - 0.006), (x1, y + 0.006)),
    ):
        fig.add_artist(mlines.Line2D(
            [xa, xb], [ya, yb],
            transform=fig.transFigure, color="#1e293b", linewidth=1.4, zorder=30,
        ))
    fig.text(
        x1, y + 0.010, label, ha="right", va="bottom",
        fontsize=7.5, color="#1e293b", zorder=30,
    )


def _draw_legend_box(fig, legend_items: List[Dict[str, str]]) -> None:
    """图例框（左下）：单源派生条目（label + color 矩形卡）。"""
    from matplotlib.patches import Rectangle

    if not legend_items:
        return
    items = legend_items[:8]  # 与前端 legend_entries_truncated 上限同精神
    x0 = 0.045
    y0 = 0.135
    row_h = 0.026
    box_h = 0.022 + row_h * len(items)
    fig.add_artist(Rectangle(
        (x0, y0 - 0.010), 0.235, box_h,
        facecolor="white", edgecolor="#cbd5e1", linewidth=0.7, alpha=0.92, zorder=28,
    ))
    for i, item in enumerate(items):
        ry = y0 + box_h - 0.026 - i * row_h
        fig.add_artist(Rectangle(
            (x0 + 0.010, ry), 0.016, 0.014,
            facecolor=str(item.get("color") or "#94a3b8"),
            edgecolor="#64748b", linewidth=0.4, zorder=30,
        ))
        fig.text(
            x0 + 0.034, ry + 0.006, str(item.get("label") or ""),
            ha="left", va="center", fontsize=7.5, color="#1e293b", zorder=30,
        )


def generate_map_pdf(
    img_bytes: bytes,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    author: Optional[str] = "WebGIS AI Agent",
    scale_text: Optional[str] = None,
    dpi: int = 150,
    layout: Optional[Dict[str, Any]] = None,
    legend_items: Optional[List[Dict[str, str]]] = None,
) -> bytes:
    """Render raw map image bytes into a compiled A4 landscape PDF bytes.

    Args:
        img_bytes: Raw image bytes (PNG, JPEG) of the map canvas
        title: Main map title (defaults to "专题地图")
        subtitle: Optional map subtitle
        author: Cartographer / author string
        scale_text: Scale text description (e.g. "1:10,000")
        dpi: Output PDF DPI resolution (default: 150)
        layout: ADR-0157 P6 出版版面描述 IR（layout_description.build_publication_layout
            产物）—— 在场时绘制指北针/比例尺整饰（scaleBar/mapFrame 单源数字）
        legend_items: 图例条目（单源派生 label/color）—— 在场时绘制图例框

    Returns:
        Compiled PDF file bytes

    Raises:
        ValueError: If img_bytes cannot be parsed as a valid image
    """
    if not img_bytes:
        raise ValueError("img_bytes cannot be empty")

    try:
        from PIL import Image
        import numpy as np
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img_arr = np.array(img)
    except Exception as e:
        raise ValueError(f"Invalid or unparseable image bytes for map rendering: {e}") from e

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cjk_font_prop = _resolve_cjk_font_properties()

    # ── A4 Landscape (297×210 mm ≈ 11.69×8.27 in) ──
    fig = plt.figure(figsize=(11.69, 8.27), facecolor="white")

    try:
        map_top = 0.88
        map_bottom = 0.10
        ax_map = fig.add_axes([0.04, map_bottom, 0.92, map_top - map_bottom])
        ax_map.imshow(img_arr, aspect="auto")
        ax_map.axis("off")

        # Map Frame Border
        for spine in ax_map.spines.values():
            spine.set_visible(True)
            spine.set_linewidth(0.8)
            spine.set_edgecolor("#cccccc")

        # ── Title & Subtitle ──
        map_title = title or "专题地图"
        title_kwargs = {"fontsize": 15, "fontweight": "bold", "color": "#1e293b"}
        if cjk_font_prop:
            title_kwargs["fontproperties"] = cjk_font_prop

        fig.text(0.5, 0.955, map_title, ha="center", va="top", **title_kwargs)

        if subtitle:
            sub_kwargs = {"fontsize": 10, "color": "#64748b"}
            if cjk_font_prop:
                sub_kwargs["fontproperties"] = cjk_font_prop
            fig.text(0.5, 0.925, subtitle, ha="center", va="top", **sub_kwargs)

        # ── 出版整饰（ADR-0157 P6：与前端同源版面描述驱动）──
        _draw_north_arrow(fig, ax_map)
        if layout and isinstance(layout.get("scaleBar"), dict):
            _draw_scale_bar(
                fig, ax_map, layout["scaleBar"],
                layout.get("mapFrame") if isinstance(layout.get("mapFrame"), dict) else {},
            )
        if legend_items:
            _draw_legend_box(fig, legend_items)

        # ── Footer ──
        date_str = time.strftime("%Y-%m-%d")
        footer_parts = [f"制图日期: {date_str}"]
        if author:
            footer_parts.append(f"制图者: {author}")
        if scale_text:
            footer_parts.append(f"比例尺: {scale_text}")
        footer_parts.append("Generated by WebGIS AI Agent")

        footer_kwargs = {"fontsize": 7, "color": "#94a3b8", "style": "italic"}
        if cjk_font_prop:
            footer_kwargs["fontproperties"] = cjk_font_prop

        fig.text(0.5, 0.025, "  |  ".join(footer_parts), ha="center", va="bottom", **footer_kwargs)

        # ── In-Memory PDF Compilation ──
        pdf_buf = io.BytesIO()
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Glyph .* missing from font", category=UserWarning)
            fig.savefig(
                pdf_buf,
                format="pdf",
                dpi=dpi,
                bbox_inches="tight",
                metadata={
                    "Title": map_title,
                    "Author": author or "WebGIS AI Agent",
                    "Subject": subtitle or "",
                    "Creator": "WebGIS AI Agent",
                },
            )
        return pdf_buf.getvalue()

    finally:
        plt.close(fig)
