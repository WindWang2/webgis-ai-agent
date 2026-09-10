"""Workflow Runtime V6 —— Cartography 适配器（真实渲染 → PDF → blob）。

Phase H「真实执行」的制图端：``cartography`` 节点调用**真实渲染栈** ——

- matplotlib（Agg 后端）绘制主题图：features 输入画要素散点（按
  value field 着色 + 图例），rows 输入画分类构成条形图；
- ``app.lib.cartography.pdf_renderer.generate_map_pdf`` 合成 A4 横版
  专业图幅（标题/副题/作者/边框 —— 真实 PDF 字节）；
- ``get_filesystem_blob_store().put_blob`` 内容寻址落存（put-if-absent +
  sha256），输出 ref = ``blob:<key>``，evidence 携带 size/sha。

重依赖（matplotlib/PIL）缺席 → typed ``CARTOGRAPHY_UNAVAILABLE``
（诚实失败，绝不输出占位图）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class CartographyOutcome:
    """cartography 节点执行结果（有界）。"""

    __slots__ = ("ok", "output_ref", "error_code", "error_message",
                 "duration_ms")

    def __init__(self, *, ok: bool, output_ref: str = "",
                 error_code: str = "", error_message: str = "",
                 duration_ms: int = 0):
        self.ok = ok
        self.output_ref = output_ref
        self.error_code = error_code[:64]
        self.error_message = error_message[:200]
        self.duration_ms = duration_ms


def _palette() -> List[str]:
    """分类配色（图省界对比友好；与 Visual System 中性底协调）。"""
    return ["#4c78a8", "#f58518", "#54a24b", "#e45756", "#72b7b2",
            "#b279a2", "#ff9da6", "#9d755d", "#edc948", "#bab0ac"]


def _feature_xy(feature: Dict[str, Any]) -> tuple:
    """要素 → (xs, ys)（Point 中心 / Polygon 外环 / LineString 折线）。"""
    geo = feature.get("geometry") or {}
    gtype = str(geo.get("type") or "")
    coords = geo.get("coordinates")

    def _flat(ring):
        return ([p[0] for p in ring], [p[1] for p in ring])

    if gtype == "Point" and coords:
        return [coords[0]], [coords[1]]
    if gtype == "Polygon" and coords:
        return _flat(coords[0] or [])
    if gtype == "MultiPolygon" and coords:
        xs: List[float] = []
        ys: List[float] = []
        for poly in coords:
            for p in (poly[0] or []):
                xs.append(p[0])
                ys.append(p[1])
        return xs, ys
    if gtype == "LineString" and coords:
        return _flat(coords)
    return [], []


def _render_features_png(features: List[Dict[str, Any]], *,
                         title: str, field: str) -> bytes:
    """要素 → 主题图 PNG（Point 散点 / Polygon 填色，按 field 着色 + 图例）。"""
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Polygon as MplPolygon

    feats = features[:20000]
    cats: List[str] = []
    for f in feats:
        val = (f.get("properties") or {}).get(field, "n/a")
        cats.append(str(val))
    uniq = sorted(set(cats))
    colors = {c: _palette()[i % len(_palette())] for i, c in enumerate(uniq)}

    fig, ax = plt.subplots(figsize=(8, 6), dpi=120)
    point_handles: List[Line2D] = []
    for cat in uniq:
        polys: List[List[float]] = []
        pts_x: List[float] = []
        pts_y: List[float] = []
        for f, c in zip(feats, cats):
            if c != cat:
                continue
            gtype = str((f.get("geometry") or {}).get("type") or "")
            xs, ys = _feature_xy(f)
            if not xs:
                continue
            if gtype == "Polygon":
                polys.append(list(zip(xs, ys)))
            elif gtype == "MultiPolygon":
                polys.append(list(zip(xs, ys)))
            else:
                pts_x.extend(xs)
                pts_y.extend(ys)
        if polys:
            for ring in polys[:5000]:
                ax.add_patch(MplPolygon(ring, closed=True, facecolor=colors[cat],
                                        edgecolor=colors[cat], alpha=0.45,
                                        linewidth=0.5))
        if pts_x:
            ax.scatter(pts_x, pts_y, s=18, c=colors[cat], alpha=0.85,
                       edgecolors="none")
        point_handles.append(Line2D(
            [0], [0], marker="o" if pts_x else "s", linestyle="",
            markersize=6, markerfacecolor=colors[cat],
            markeredgecolor="none", label=cat[:24]))
    if point_handles:
        ax.legend(handles=point_handles[:10], loc="best", fontsize=8,
                  title=field[:24], title_fontsize=8)
    ax.set_title(title[:80], fontsize=13)
    ax.set_xlabel("lon")
    ax.set_ylabel("lat")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(True, linewidth=0.3, alpha=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _render_rows_png(rows: List[Dict[str, Any]], *,
                     title: str, field: str) -> bytes:
    """聚合表 → 分类构成水平条形图 PNG（真实 matplotlib）。"""
    import io

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = rows[:20]
    rows_sorted = sorted(rows, key=lambda r: -int(r.get("count", 0) or 0))
    labels = [str(r.get("__group__", r.get("category", r.get("group", i)))[:24])
              for i, r in enumerate(rows_sorted)]
    values = [int(r.get("count", 0) or 0) for r in rows_sorted]
    fig, ax = plt.subplots(figsize=(8, 6), dpi=120)
    ypos = range(len(rows_sorted))
    ax.barh(ypos, values, color=_palette()[:len(rows_sorted)])
    ax.set_yticks(list(ypos))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("count")
    ax.set_title(title[:80], fontsize=13)
    ax.grid(True, axis="x", linewidth=0.3, alpha=0.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


async def execute_cartography_node(
    node: Dict[str, Any], *, input_refs: List[str], params: Dict[str, Any],
    session_id: str,
) -> CartographyOutcome:
    """真实渲染 cartography 节点（matplotlib → generate_map_pdf → blob）。"""
    import time as _time

    from app.services.session_data import session_data_manager

    start = _time.monotonic()
    try:
        payload: Any = None
        for ref in input_refs[:4]:
            payload = await session_data_manager.get(session_id, ref)
            if isinstance(payload, list) and payload:
                payload = {"features": payload}
            if isinstance(payload, dict) and (
                    payload.get("features") or payload.get("rows")):
                break
            payload = None
        if not isinstance(payload, dict):
            return CartographyOutcome(
                ok=False, error_code="CARTOGRAPHY_INPUT_EMPTY",
                error_message="no features/rows resolvable from inputs",
                duration_ms=_elapsed(start))

        title = str(params.get("title") or node.get("title")
                    or "Workflow 专题图")
        field = str(params.get("field") or params.get("value_field")
                    or "category")
        png_bytes = _render(payload, title=title, field=field)

        from app.lib.cartography.pdf_renderer import generate_map_pdf

        pdf_bytes = generate_map_pdf(
            png_bytes, title=title,
            subtitle=str(params.get("subtitle") or ""),
            scale_text=str(params.get("scale_text") or ""))

        from app.services.durable_blob_store import (
            get_filesystem_blob_store,
            safe_blob_key,
            sha256_of_bytes,
        )

        key = safe_blob_key(
            f"wf-cartography-{sha256_of_bytes(pdf_bytes)[:32]}.pdf")
        get_filesystem_blob_store().put_blob(
            key, pdf_bytes, content_type="application/pdf")
        return CartographyOutcome(
            ok=True, output_ref=f"blob:{key}",
            duration_ms=_elapsed(start))
    except ImportError as exc:
        return CartographyOutcome(
            ok=False, error_code="CARTOGRAPHY_UNAVAILABLE",
            error_message=f"render deps missing: {exc}",
            duration_ms=_elapsed(start))
    except Exception as exc:  # noqa: BLE001 — 分类落证据
        code = getattr(exc, "code", None) or "CARTOGRAPHY_RENDER_ERROR"
        return CartographyOutcome(
            ok=False, error_code=str(code),
            error_message=str(exc), duration_ms=_elapsed(start))


def _render(payload: Dict[str, Any], *, title: str, field: str) -> bytes:
    if payload.get("features"):
        return _render_features_png(payload["features"], title=title,
                                    field=field)
    return _render_rows_png(payload["rows"], title=title, field=field)


def _elapsed(start: float) -> int:
    import time as _time

    return int((_time.monotonic() - start) * 1000)
