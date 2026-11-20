"""G20 单幅同语义输出：冻结修订 → 真实 PNG / 离线可读 SVG + 表达说明。

- 位图嵌入（data URI）而非 session URL：离线打开不依赖过期 token。
- 图例端点、色样、波段顺序、拉伸口径全部来自同一个已冻结的
  ``RasterExpressionSpec``，不重新估计自动范围。
- 其它矢量/底图图层不在本服务渲染能力内 → 显式 ``omitted_layers`` 披露，
  绝不静默丢层。PDF 明确拒绝（现有 publication PDF 路径另有降级披露）。
"""
from __future__ import annotations

import base64
import io
import json
from html import escape as html_escape
from typing import Any, Dict, List, Sequence

from PIL import Image, ImageDraw, ImageFont

from app.schemas.raster_spec import RasterExpressionError
from app.services.raster_authoring.legend import build_legend, expression_statement
from app.services.raster_authoring.palettes import colormap_lut
from app.services.raster_authoring.render import render_snapshot
from app.services.raster_authoring.source import RasterSource

_CANVAS_BG = (247, 249, 252)
_PANEL_BG = (255, 255, 255)
_TEXT = (30, 41, 59)
_MUTED = (100, 116, 139)


def _font(size: int = 13):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:  # noqa: BLE001 - 无字体文件时用 PIL 内置
        return ImageFont.load_default()


def _text_lines(legend: Dict[str, Any], statement: Dict[str, Any]) -> List[str]:
    prov = statement["stretch_provenance"]
    lines = [
        "Raster expression snapshot",
        f"revision: {statement['data_revision']}",
        f"style: {statement['style_id']}",
        f"mode: {statement['mode']}  bands: "
        + ",".join(str(b["index"]) for b in statement["bands"]),
        f"resampling: {statement['resampling']}  opacity: {statement['opacity']}",
        f"stretch: {prov['method']} ({'approximate' if prov['approximate'] else 'manual'})",
    ]
    if prov.get("sample_count"):
        lines.append(f"sample count: {prov['sample_count']}")
    if legend.get("type") == "continuous":
        lines += [
            f"band {legend['band']} ({legend['unit_status']} unit"
            + (f": {legend['unit']}" if legend.get("unit") else "") + ")",
            f"min: {legend['min']:g}   max: {legend['max']:g}",
            f"palette: {legend['palette']}",
            "out of range: clipped; invalid: transparent",
        ]
        if legend.get("constant"):
            lines.append("constant / all-nodata span: no pseudo-extremes")
    else:
        for ch in legend.get("channels", []):
            lines.append(
                f"{ch['channel']} <- band {ch['band']}  [{ch['min']:g}, {ch['max']:g}]"
                f"  unit={ch['unit_status']}"
            )
        lines.append("RGB composite: no continuous colorbar")
    lines += [
        "display stretch does not change source values",
        "higher DPI does not add remote-sensing detail",
    ]
    return lines


def _compose_png_image(entries: Sequence[Dict[str, Any]]) -> Image.Image:
    """多数值栅格层按序叠放 + 右侧图例/表达说明面板。"""
    map_w, map_h = 900, 620
    panel_w = 330
    canvas = Image.new("RGB", (map_w + panel_w, map_h), _CANVAS_BG)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([map_w, 0, map_w + panel_w, map_h], fill=_PANEL_BG)
    draw.line([(map_w, 0), (map_w, map_h)], fill=(226, 232, 240), width=1)

    for entry in entries:
        img: Image.Image = entry["render"]["image"].convert("RGBA")
        img.thumbnail((map_w - 24, map_h - 24), Image.Resampling.LANCZOS)
        x = (map_w - img.width) // 2
        y = (map_h - img.height) // 2
        canvas.paste(img, (x, y), img)

    font = _font(12)
    title_font = _font(15)
    y = 16
    for entry in entries:
        legend = entry["legend"]
        draw.text((map_w + 16, y), "Raster expression snapshot", fill=_TEXT, font=title_font)
        y += 26
        # 色带条：直接采样渲染 LUT，与像素同源
        if legend.get("type") == "continuous":
            lut = colormap_lut(legend["palette"], 256)
            if lut is None:
                lut = colormap_lut("gray", 256)
            if lut is not None:
                # LUT 是 (256,3)；显式铺成 (1,256,3) 的一行色带，
                # 避免 PIL 把 3 当宽度并按 RGB 行跨读取越界。
                bar = Image.fromarray(lut.reshape(1, 256, 3).copy()).resize(
                    (panel_w - 32, 18), Image.Resampling.NEAREST
                )
                canvas.paste(bar, (map_w + 16, y))
                y += 24
        for line in _text_lines(legend, entry["statement"]):
            draw.text((map_w + 16, y), line[:46], fill=_MUTED if line.startswith(("display", "higher")) else _TEXT, font=font)
            y += 19
        y += 14
        if y > map_h - 40:
            break
    return canvas


def compose(entries: Sequence[Dict[str, Any]], *, fmt: str = "png",
            frozen: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """返回 ``{"bytes","media_type","extension","sidecar","embedded"}``。"""
    if not entries:
        raise RasterExpressionError(
            "EXPORT_UNSUPPORTED",
            "the frozen MapSpec has no numeric raster layer to export",
        )
    if fmt not in ("png", "svg"):
        raise RasterExpressionError(
            "EXPORT_UNSUPPORTED",
            f"format {fmt!r} is not supported for numeric raster snapshots; use png or svg",
            field="format",
            correction_hint=(
                "png = real composited bitmap; svg = self-contained offline embedding. "
                "The publication PDF path does not embed numeric raster snapshots and is refused."
            ),
        )
    sidecar = {
        "schema_version": "raster_expression.v1",
        "frozen": frozen or {},
        "layers": [
            {
                "layer_id": e["layer_id"],
                "style_id": e["spec"].style_id,
                "data_revision": e["spec"].data_revision,
                "legend": e["legend"],
                "statement": e["statement"],
                "render": {
                    k: v for k, v in e["render"].items() if k != "image"
                },
            }
            for e in entries
        ],
        "embedding": "base64 PNG data URI (offline, no session URL)",
        "limits": [
            "bitmap is bounded to 2048 source pixels on the longest side",
            "increasing DPI does not add remote-sensing detail",
            "vector / basemap layers are not rendered by this service (see frozen.omitted_layers)",
        ],
    }
    image = _compose_png_image(entries)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    png_bytes = buf.getvalue()
    if fmt == "png":
        return {
            "bytes": png_bytes, "media_type": "image/png", "extension": ".png",
            "sidecar": sidecar, "embedded": False,
        }
    encoded = base64.b64encode(png_bytes).decode("ascii")
    w, h = image.size
    # <desc> 必须同时转义 & 和 <：band description 来自栅格 tag，可能含 "&"。
    legend_json = html_escape(json.dumps(
        [e["legend"] for e in entries], ensure_ascii=False, separators=(",", ":"),
    ).replace("<", "\\u003c"))
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
        f'<title>Raster expression snapshot</title>'
        f'<desc>{legend_json}</desc>'
        f'<image x="0" y="0" width="{w}" height="{h}" '
        f'xlink:href="data:image/png;base64,{encoded}" href="data:image/png;base64,{encoded}"/>'
        f'</svg>'
    )
    return {
        "bytes": svg.encode("utf-8"), "media_type": "image/svg+xml",
        "extension": ".svg", "sidecar": sidecar, "embedded": True,
    }


def build_entry(layer_id: str, spec: Any, source: RasterSource) -> Dict[str, Any]:
    return {
        "layer_id": layer_id,
        "spec": spec,
        "legend": build_legend(spec, source),
        "statement": expression_statement(spec, source),
        "render": render_snapshot(source, spec),
    }
