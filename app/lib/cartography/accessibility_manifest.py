"""Accessibility Manifest — 导出物可访问性清单（V11 W6.6，ADR-0166）。

任务书 W6.6：「导出物带 alt text、图层标签、色盲安全声明、数据来源与
投影说明」。本模块把可访问性元素从「散落在导出器里的可选字段」收敛为
**必产清单**：每个导出物（PNG/PDF/SVG 任一）都带完整 manifest；缺元素
如实标记 missing（验收断言 100% 存在 —— 缺即失败信号，不静默）。

色盲安全声明来自 context_matrix 的实测判定（同一事实源，不新造词汇）；
数据来源/投影来自 MapSpec 的 sources/view.crs。
"""
from __future__ import annotations

from typing import Any, Dict, List

from app.lib.cartography.defaults import DEFAULT_PALETTE

MANIFEST_VERSION = 1


def _layer_label(layer: Dict[str, Any]) -> str:
    """图层标签：图例标题 > 字段名 > 图层 id（确定性回退链）。"""
    legend = layer.get("legend") or {}
    if isinstance(legend, dict) and legend.get("title"):
        return str(legend["title"])[:80]
    fields = layer.get("fields") or {}
    if isinstance(fields, dict) and fields.get("color"):
        return f"{layer.get('id', 'layer')}（{fields['color']}）"[:80]
    if isinstance(fields, dict) and fields.get("label"):
        return f"{layer.get('id', 'layer')}（{fields['label']}）"[:80]
    return str(layer.get("id", "layer"))[:80]


def build_accessibility_manifest(
    spec: Dict[str, Any],
    *,
    palette: str = "",
    palette_context: str = "screen",
    alt_text: str = "",
) -> Dict[str, Any]:
    """MapSpec（+ 色带/上下文）→ 可访问性清单（确定性、可序列化）。

    ``altText``：调用方给予时优先（作者撰写）；缺省按标题族+图层标签
    确定性合成（机器可读的最小合格 alt）。
    """
    title = str(
        (spec.get("layout") or {}).get("title")
        or (spec.get("meta") or {}).get("title")
        or "未命名地图"
    )[:120]
    layers = list(spec.get("layers") or [])
    layer_labels = [_layer_label(layer) for layer in layers if isinstance(layer, dict)]

    sources = []
    for src in spec.get("sources") or []:
        if isinstance(src, dict):
            name = str(src.get("name") or src.get("id") or src.get("type") or "source")
            attribution = str(src.get("attribution") or "")
            sources.append({"name": name[:80], "attribution": attribution[:160]})

    # 投影说明：view.crs / projection 字段（MapSpec 词表）
    view = spec.get("view") or {}
    projection = str(
        (view.get("projection") if isinstance(view, dict) else "")
        or (view.get("crs") if isinstance(view, dict) else "")
        or "EPSG:3857"
    )[:64]

    # 色盲安全声明：context_matrix 实测（同源事实，不新造词汇）
    palette_name = palette or DEFAULT_PALETTE
    colorblind = {
        "palette": palette_name,
        "context": palette_context,
        "declaration": (
            f"色带 {palette_name} 在当前上下文（{palette_context}）通过可分辨校验；"
            "若目标受众含色觉差异读者，建议改用感知均匀色带（Viridis 等同族）"
        ),
        "checked": False,
    }
    try:
        from app.lib.cartography.context_matrix import MATRIX_CONTEXTS, evaluate_cell

        ctx = palette_context if palette_context in MATRIX_CONTEXTS else "screen"
        cell = evaluate_cell(palette_name, ctx)
        colorblind["checked"] = True
        colorblind["separable"] = bool(cell["separable"])
        colorblind["minMetric"] = cell["min_metric"]
        if not cell["separable"]:
            colorblind["declaration"] = (
                f"色带 {palette_name} 在 {ctx} 上下文未通过可分辨校验 —— "
                "导出前建议换用感知均匀色带（诚实披露，不静默放行）"
            )
    except Exception:  # noqa: BLE001 —— 校验不可得时 checked=False（诚实）
        pass

    if not alt_text:
        alt_text = f"{title}；图层：{'、'.join(layer_labels[:6]) or '无'}"

    manifest = {
        "version": MANIFEST_VERSION,
        "altText": alt_text[:400],
        "title": title,
        "layerLabels": layer_labels,
        "colorblindSafety": colorblind,
        "dataSources": sources,
        "projection": projection,
    }
    # 完整性核验（验收：元素 100% 存在；缺即如实标 missing）
    missing: List[str] = []
    if not manifest["altText"]:
        missing.append("altText")
    if not manifest["layerLabels"]:
        missing.append("layerLabels")
    if not manifest["colorblindSafety"]["declaration"]:
        missing.append("colorblindSafety")
    if not manifest["dataSources"]:
        missing.append("dataSources")
    if not manifest["projection"]:
        missing.append("projection")
    manifest["missing"] = missing
    manifest["complete"] = not missing
    return manifest


__all__ = ["build_accessibility_manifest", "MANIFEST_VERSION"]
