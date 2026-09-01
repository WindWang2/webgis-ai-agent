"""制图组件校验 validator（components facet）— ADR-0081 / ADR-0091。"""
from __future__ import annotations

from typing import Any, Dict, List

from ..contracts import (
    F_COMPONENT_DISABLED,
    F_COMPONENT_MISSING,
    F_LAYOUT_CONFLICT,
    F_ORPHAN_BINDING,
    R_ADD_COMPONENT,
    R_ENABLE_COMPONENT,
    _SINGLETON_TYPES,
    MapCompletionFinding,
)


def _family_renderable(family: List[str]) -> bool:
    """slot 族内是否有任一类型存在 live 渲染器或导出消费方（支持矩阵）。"""
    try:
        from app.lib.cartography.component_renderers import (
            get_component_renderer_registry,
        )

        registry = get_component_renderer_registry()
        for t in family:
            support = registry.support_for(t)
            if support and (support.renderers or support.exporters):
                return True
        return False
    except Exception:  # noqa: BLE001 — 矩阵缺失按可修复处理（保守）
        return True


def validate_components(
    mapspec: Dict[str, Any],
    required_slots: List[List[str]],
    layer_ids: List[str],
) -> List[MapCompletionFinding]:
    """制图组件校验：模板 required 槽在场且启用（slot 族语义）。

    required 槽由 allowed_component_types 表达（如 "legend" 槽可由
    legend/categorical_legend/continuous_colorbar 任一满足）；缺失/禁用均
    可修复（修复取族的第一个类型）。
    """
    findings: List[MapCompletionFinding] = []
    components = [
        c
        for c in ((mapspec.get("layout") or {}).get("components") or [])
        if isinstance(c, dict)
    ]
    enabled_types = {
        str(c.get("type") or "") for c in components if c.get("enabled") is not False
    }
    present_types = {str(c.get("type") or "") for c in components}
    for family in required_slots:
        family = [t for t in family if t] or ["title"]
        primary = family[0]
        # review P2：两侧都无渲染/导出消费方的类型（map_border /
        # export_layout / graticule / inset_map）不修也不判 error ——
        # "修复"一个永远不可见的组件是完成度表演；降级为 warning 披露。
        repairable = _family_renderable(family)
        if not any(t in present_types for t in family):
            findings.append(
                MapCompletionFinding(
                    code=F_COMPONENT_MISSING,
                    severity="error" if repairable else "warning",
                    target=primary,
                    detail=(
                        f"required component slot '{primary}' absent "
                        f"(any of {', '.join(family[:3])})"
                    ),
                    repair=R_ADD_COMPONENT if repairable else None,
                    family=family,
                )
            )
        elif not any(t in enabled_types for t in family):
            findings.append(
                MapCompletionFinding(
                    code=F_COMPONENT_DISABLED,
                    severity="error" if repairable else "warning",
                    target=primary,
                    detail=f"required component slot '{primary}' is disabled",
                    repair=R_ENABLE_COMPONENT if repairable else None,
                    family=family,
                )
            )
    # 单例重复（desired-state 即可评）
    for t in _SINGLETON_TYPES:
        n = sum(1 for c in components if c.get("type") == t and c.get("enabled") is not False)
        if n > 1:
            findings.append(
                MapCompletionFinding(
                    code=F_LAYOUT_CONFLICT,
                    severity="warning",
                    target=t,
                    detail=f"{n} enabled singleton components of type '{t}'",
                )
            )
    # 孤儿绑定（layerId 指向已删层）
    known = set(layer_ids)
    for c in components:
        if c.get("enabled") is False:
            continue
        lid = str((c.get("options") or {}).get("layerId") or "")
        if lid and known and lid not in known:
            findings.append(
                MapCompletionFinding(
                    code=F_ORPHAN_BINDING,
                    severity="warning",
                    target=str(c.get("id") or lid),
                    detail=f"component layerId '{lid[:48]}' not in spec layers",
                )
            )
    return findings
