"""Atlas page compiler —— 批量路径的单页编译 + 页头/页码注入。

编译本体 100% 复用 ``publication_export.compile_publication_page`` 接缝
（与同步矢量链同一事实源）。批量路径**独有**的增量是页头注入：内容页
SVG 右上角追加确定性文本（页题 + ``第 n / N 页``）—— 用户验收"每页能
对应行政区 + 有页号"的最小诚实实现。

红线：注入只发生在批量图册路径（同步 vector-pdf 路由输出**字节不变**，
由既有 golden/atlas 测试锁定）；注入元素只用页纸张像素（4px/mm 同源），
不改 viewBox/既有元素；文本 XML 转义。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.services.publication_export import (
    PageCompileFailure,
    PageCompileOutcome,
    compile_publication_page,
)

#: 注入字号（px；≈4.5mm @4px/mm——页眉级小字，避开共享 chrome 的标题带）。
_HEADER_FONT_PX_TITLE = 22
_HEADER_FONT_PX_NUMBER = 18


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def inject_page_header(
    svg: str,
    *,
    page_number: int,
    page_count: int,
    page_title: str,
    width_px: int,
    height_px: int,
    atlas_title: str = "",
) -> str:
    """内容页 SVG 右上角注入页题/页码/图册题（确定性；批量路径专属）。

    插入点 = 最后一个 ``</svg>`` 之前；不解析/不改写既有内容。布局：
    右上角右对齐两行 —— 第一行页题（+ 可选图册题前缀），第二行 ``第 n / N 页``。
    """
    if "</svg>" not in svg:
        return svg  # 结构异常（非编译面产物）→ 不注入不破坏
    x = int(width_px * 0.975)
    y1 = int(height_px * 0.045)
    y2 = int(height_px * 0.045) + _HEADER_FONT_PX_TITLE + 6
    title_text = f"{atlas_title} · {page_title}" if atlas_title else page_title
    lines = [
        f'<text x="{x}" y="{y1}" text-anchor="end" font-family="sans-serif" '
        f'font-size="{_HEADER_FONT_PX_TITLE}" font-weight="600" fill="#0f172a">'
        f'{_esc(title_text[:80])}</text>',
        f'<text x="{x}" y="{y2}" text-anchor="end" font-family="sans-serif" '
        f'font-size="{_HEADER_FONT_PX_NUMBER}" fill="#475569">'
        f'{_esc(f"第 {page_number} / {page_count} 页")}</text>',
    ]
    idx = svg.rfind("</svg>")
    return svg[:idx] + "".join(lines) + svg[idx:]


def compile_atlas_page(
    document: Dict[str, Any],
    page: Any,
    pub_ir: Any,
    *,
    frames_lookup: List[Any],
    target_dpi: int,
    max_labels: int,
    svg_budget_remaining: int,
    atlas_title: str = "",
) -> PageCompileOutcome:
    """单页编译（接缝）+ 批量页头注入（内容页）。

    ``page`` = PublicationIR 的 :class:`~app.lib.cartography.publication_ir.
    PageLayoutIR`（编译接缝的既有输入；封面不注入——TOC 自带页码表）。失败沿
    :class:`PageCompileFailure` 上抛（调用方证据化）；返回的 ``svg`` 即最终
    页面体（含注入）。
    """
    outcome = compile_publication_page(
        document, page, pub_ir,
        frames_lookup=frames_lookup,
        target_dpi=target_dpi,
        max_labels=max_labels,
        svg_budget_remaining=svg_budget_remaining,
    )
    if page.cover:
        return outcome
    injected = inject_page_header(
        outcome.svg,
        page_number=page.page_number,
        page_count=page.page_count,
        page_title=str(page.title or page.page_id),
        width_px=page.paper.width_px,
        height_px=page.paper.height_px,
        atlas_title=atlas_title,
    )
    # svg_bytes 反映最终页面体（缓存/预算记账与实际载荷一致）
    return PageCompileOutcome(
        svg=injected,
        width_mm=outcome.width_mm,
        height_mm=outcome.height_mm,
        page=outcome.page,
        svg_bytes=len(injected),
        frame_diagnostics=outcome.frame_diagnostics,
        rendered_component_types=outcome.rendered_component_types,
        omitted_components=outcome.omitted_components,
    )


def diagnostic_dicts(items: List[Any], *, cap: int = 32) -> List[Dict[str, Any]]:
    """RenderDiagnostic 列表 → 有界 dict 投影（manifest/status 用）。"""
    out: List[Dict[str, Any]] = []
    for d in items[:cap]:
        code = getattr(d, "code", "")
        if not code:
            continue
        out.append({
            "code": str(code)[:64],
            "severity": str(getattr(d, "severity", "info"))[:16],
            "detail": str(getattr(d, "detail", ""))[:160],
        })
    return out


__all__ = [
    "inject_page_header",
    "compile_atlas_page",
    "diagnostic_dicts",
    "PageCompileFailure",
]
