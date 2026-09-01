"""视口 bbox 派生 + 导出一致性（viewport/export facet）— ADR-0081 / ADR-0091。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def derive_result_bbox(
    chapter: Dict[str, Any],
    descriptors: Dict[str, Optional[dict]],
) -> Optional[List[float]]:
    """主结果 bbox：全部 bound ref descriptor 的 bbox 并集（元数据 O(N)）。

    不逐 feature 扫描、不复制 GeoJSON —— descriptor 缺 bbox 时该 ref 跳过；
    全部缺失 → None（viewport finding 由调用方判定）。
    """
    best: Optional[List[float]] = None
    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        ref = str(row.get("bound_ref") or "")
        if not ref:
            continue
        desc = descriptors.get(ref)
        if not desc:
            if ref not in descriptors:
                continue  # unknown（探测失败）：跳过不判（不虚构 bbox）
            continue  # 确认缺失：无 bbox 可贡献（过期 finding 由 artifacts 侧报）
        bbox = desc.get("bbox")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            continue
        try:
            w, s, e, n = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
        except (TypeError, ValueError):
            continue
        if not (w <= e and s <= n):
            continue
        if best is None:
            best = [w, s, e, n]
        else:
            best = [
                min(best[0], w),
                min(best[1], s),
                max(best[2], e),
                max(best[3], n),
            ]
    return best


def assess_export_parity(mapspec: Dict[str, Any]) -> str:
    """导出一致性：enabled 组件是否全部被导出管线支持（support matrix 派生）。

    这是 desired-state 的静态判定（哪些组件类型有导出消费方），不是渲染
    证据；渲染级 parity 由 exporter 的共享 resolver + 测试锁定。
    """
    try:
        from app.lib.cartography.component_renderers import (
            get_component_renderer_registry,
        )

        registry = get_component_renderer_registry()
        components = [
            c
            for c in ((mapspec.get("layout") or {}).get("components") or [])
            if isinstance(c, dict) and c.get("enabled") is not False
        ]
        unsupported = []
        for c in components:
            t = str(c.get("type") or "")
            support = registry.support_for(t)
            if support is not None and t not in (
                "export_layout",
                "basemap",
                "inset_map",
                "annotation",
            ) and not support.exporters:
                unsupported.append(t)
        if not components or not unsupported:
            return "parity"
        return "divergent"
    except Exception:  # noqa: BLE001 — 矩阵缺失不阻断完成度判定
        return "unknown"
