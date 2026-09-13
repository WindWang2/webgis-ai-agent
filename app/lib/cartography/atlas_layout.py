"""Atlas Layout — 多图版面编排（V11 W5.3，ADR-0165）。

专题集 / 图册的多页自动编排：每页从 **C2 IR**（``build_layout_ir``）渲染
版面描述 —— 主图 + 共享 chrome（标题族/比例尺/署名）+ 可选插图槽。
确定性：页序、组件 id、几何全部由输入决定；页数有界（渲染面不接收无界
载荷）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.lib.cartography.layout_description import build_layout_ir

#: 页数上界（有界载荷；超出截断并记 degradation）。
MAX_ATLAS_PAGES = 20

#: 共享 chrome 的锚点约定（每页一致 —— 图册视觉连续性）。
_SHARED_CHROME = (
    {"id": "attribution", "kind": "attribution", "role": "decorative",
     "frame": {"x": 0.86, "y": 0.965, "width": 0.13, "height": 0.025,
               "anchor": "bottom_right", "z": 30}},
    {"id": "scale_bar", "kind": "scale_bar", "role": "decorative",
     "frame": {"x": 0.04, "y": 0.94, "width": 0.16, "height": 0.03,
               "anchor": "bottom_left", "z": 30}},
    {"id": "title", "kind": "title", "role": "secondary",
     "frame": {"x": 0.04, "y": 0.03, "width": 0.5, "height": 0.06,
               "anchor": "top_left", "z": 10},
     "typography": {"fontFamily": "system-ui", "fontSizePx": 20,
                    "fontWeight": 600, "lineHeightPx": 26,
                    "align": "left", "wrapMode": "cjk_char"}},
)


def plan_atlas_pages(
    scenarios: List[Dict[str, Any]],
    *,
    canvas: Dict[str, int],
    atlas_title: str = "",
) -> Dict[str, Any]:
    """场景列表 → 逐页 C2 IR（确定性；封面可选）。

    ``scenarios`` 每项：``{id, title, map_kind}``（map_kind = 主图表达种类，
    进 IR 的组件 kind=inset_map 的 style token 引用）。页数超 ``MAX_ATLAS_PAGES``
    截断并如实记 degradation。
    """
    if not isinstance(canvas, dict) or canvas.get("widthPx", 0) <= 0 \
            or canvas.get("heightPx", 0) <= 0:
        raise ValueError("atlas 需要 canvas {widthPx, heightPx}")
    truncated = len(scenarios) > MAX_ATLAS_PAGES
    pages: List[Dict[str, Any]] = []
    degradations: List[Dict[str, str]] = []
    if truncated:
        degradations.append({
            "code": "atlas_truncated",
            "detail": f"场景数 {len(scenarios)} 超上界 {MAX_ATLAS_PAGES}，已截断",
        })
    for idx, scenario in enumerate(scenarios[:MAX_ATLAS_PAGES]):
        sid = str(scenario.get("id") or f"page{idx + 1}")
        title = str(scenario.get("title") or sid)
        components: List[Dict[str, Any]] = [
            {"id": f"{sid}__map", "kind": "inset_map", "role": "primary",
             "frame": {"x": 0.03, "y": 0.11, "width": 0.94, "height": 0.8,
                       "anchor": "top_left", "z": 0},
             "style": {"token": f"map.{scenario.get('map_kind', 'default')}"}},
        ]
        for chrome in _SHARED_CHROME:
            entry = dict(chrome)
            entry["id"] = f"{sid}__{chrome['id']}"
            if chrome["id"] == "title":
                entry = dict(entry)
                entry["typography"] = dict(entry["typography"])
            components.append(entry)
        page_title = f"{atlas_title} · {title}" if atlas_title else title
        ir = build_layout_ir(
            canvas=canvas, components=components, degradations=list(degradations),
        )
        pages.append({"page_id": sid, "title": page_title, "ir": ir})
    return {
        "version": 1,
        "canvas": dict(canvas),
        "pageCount": len(pages),
        "pages": pages,
        "degradations": degradations,
    }


__all__ = ["MAX_ATLAS_PAGES", "plan_atlas_pages"]
