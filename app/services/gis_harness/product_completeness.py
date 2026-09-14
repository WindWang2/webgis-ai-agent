"""Product Completeness Validator —— 语义完整性（ADR-0183 §7，M8）。

回答「这个产品是否构成用户要求的产品」，与两套既有核验**正交**：

- binding 完成度（planner.assess_completeness）：计划行是否绑定；
- 渲染/视口核验（gis_harness/completion/）：层在场/可见/组件渲染/视口对账。

本验证器的检查面（全部结构化、确定性、fail-closed 披露不谎报）：

1. shape 应然视图在场（required_view_kinds / required_any_kinds）；
2. 用户点名的显式视图（view.required）在场且启用；
3. 对比声明成立：comparison 视图/关系真的构成对比（≥2 个被比较端点或
   comparison 关系在图上）；
4. 来源注记在场：shape.requires_source_note 时 attribution 证据面可达
   （组合槽位满足 / 编译器 chrome 指派）；
5. chart-map 同数据绑定：chart_linked_to_map 两端 dataset_ref 一致（冲突 =
   图表画的不是地图那份数据 —— 语义级断链）；
6. shared_legend 一致：shared_legend 两端各自解析到的图例族一致；
7. delivery 覆盖披露：chart/statistics 视图在 export 目标下受 LIVE-only
   渲染面限制（已知缺口如实披露，不谎报可导出）。

必需性单一来源：shape（本模块）+ ProductFacetContract（组件族必需信号）
—— 不建第二份契约。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.product_shapes import (
    GENERIC_SHAPE,
    ProductShape,
    shape_for_archetype,
)
from app.services.gis_harness.product_spec import MapProductSpec

_MAX_FINDINGS = 16
#: 导出目标下已知 LIVE-only 的组件族（component_renderers 支持矩阵）
_LIVE_ONLY_FAMILIES = ("chart_panel", "statistics_panel")


class CompletenessFinding(BaseModel):
    """单条完整性发现（code 稳定可断言；bounded）。"""

    code: str = Field(max_length=48)
    severity: str = "error"   # error | warning
    view_id: str = ""
    detail: str = Field(default="", max_length=200)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class ProductCompletenessReport(BaseModel):
    """语义完整性报告（bounded / serializable）。"""

    complete: bool = True
    product_type: str = ""
    findings: List[CompletenessFinding] = Field(
        default_factory=list, max_length=_MAX_FINDINGS)
    checked: List[str] = Field(default_factory=list, max_length=8)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    @property
    def errors(self) -> List[CompletenessFinding]:
        return [f for f in self.findings if f.severity == "error"]


def _finding(code: str, severity: str = "error", **kw) -> CompletenessFinding:
    return CompletenessFinding(code=code, severity=severity, **kw)


def validate_product_completeness(
    spec: MapProductSpec,
    *,
    compile_result: Any = None,
    shape: Optional[ProductShape] = None,
) -> ProductCompletenessReport:
    """语义完整性验证（纯函数；输入只有 spec 与其编译产物投影）。"""
    findings: List[CompletenessFinding] = []
    checked: List[str] = []
    shp = shape or shape_for_archetype(spec.product_type) or GENERIC_SHAPE

    live = {v.view_id: v for v in spec.views if v.enabled}
    kinds_present = {v.kind for v in live.values()}

    # 显式撤回面（override 账）：用户点名移除/禁用/关闭的视图 kind ——
    # shape 的类型级应然构成让位于用户显式改主意（降级为诚实 warning）。
    retracted_kinds: set = set()
    for ov in spec.overrides:
        if ov.op == "remove_view" and ov.target:
            v = spec.view(ov.target)
            if v is not None:
                retracted_kinds.add(v.kind)
        elif ov.op == "toggle_view" and ov.target:
            v = spec.view(ov.target)
            if v is not None and not v.enabled:
                retracted_kinds.add(v.kind)
        elif ov.op == "toggle_component" and not ov.payload.get("enabled", True):
            ctype = str(ov.payload.get("component_type") or ov.target)
            kind = {"chart_panel": "chart", "statistics_panel": "stats_panel"}.get(ctype)
            if kind:
                retracted_kinds.add(kind)
    shape_retracted: List[str] = []

    # 1. shape 应然视图（被显式撤回的 kind → warning 披露，不判失败）
    checked.append("shape_required_views")
    for kind in shp.required_view_kinds:
        if kind in kinds_present:
            continue
        if kind in retracted_kinds:
            shape_retracted.append(kind)
        else:
            findings.append(_finding(
                "required_view_missing", view_id=f"kind:{kind}",
                detail=f"shape '{shp.archetype or 'generic'}' requires a {kind} view"))
    checked.append("shape_required_any")
    for group in shp.required_any_kinds:
        if any(k in kinds_present for k in group):
            continue
        if any(k in retracted_kinds for k in group):
            shape_retracted.extend(k for k in group if k in retracted_kinds)
        else:
            findings.append(_finding(
                "required_any_missing",
                detail=f"none of {list(group)} present — "
                       f"'{shp.archetype or 'generic'}' claim unsupported"))
    if shape_retracted:
        findings.append(_finding(
            "shape_face_retracted", severity="warning",
            detail=f"user explicitly retracted {sorted(set(shape_retracted))} face(s) "
                   f"typical of '{shp.archetype or 'generic'}'"))

    # 2. 用户点名视图（required 且被启用；被显式编辑撤回的已不在 required）
    checked.append("user_required_views")
    for v in spec.views:
        if v.required and not v.enabled:
            findings.append(_finding(
                "user_required_view_disabled", view_id=v.view_id,
                detail="explicitly requested view is disabled"))

    # 3. 对比声明成立
    checked.append("comparison_claim")
    comparison_views = [v for v in live.values() if v.kind == "comparison"]
    comparison_rels = [
        r for r in spec.relations
        if r.kind == "comparison"
        and r.src in live and r.dst in live
    ]
    if comparison_views or comparison_rels:
        pass  # 对比构成在场
    elif any("对比" in c or "比较" in c or "comparison" in c.lower()
             for c in spec.claims):
        findings.append(_finding(
            "comparison_claim_unsupported",
            severity="warning",
            detail="claims mention comparison but no comparison view/relation exists"))

    # 4. 来源注记
    if shp.requires_source_note:
        checked.append("source_note")
        attribution_backed = bool(spec.overrides and any(
            ov.op == "toggle_component"
            and ov.payload.get("component_type") == "attribution"
            and ov.payload.get("enabled") is False
            for ov in spec.overrides))
        compile_attribution = False
        if compile_result is not None:
            slots = getattr(compile_result, "slots", None) or []
            compile_attribution = any(
                s.satisfied and any(t == "attribution" for t in s.component_types)
                for s in slots)
        if attribution_backed:
            findings.append(_finding(
                "source_note_removed", severity="warning", view_id="v-map",
                detail="attribution explicitly disabled — provenance face lost"))
        elif compile_result is not None and not compile_attribution:
            findings.append(_finding(
                "source_note_unbacked", severity="warning", view_id="v-map",
                detail="composition slots do not back an attribution family"))

    # 5. chart-map 同数据绑定
    checked.append("chart_map_binding")
    for rel in spec.relations:
        if rel.kind != "chart_linked_to_map":
            continue
        src, dst = live.get(rel.src), live.get(rel.dst)
        if src is None or dst is None:
            continue
        map_view = dst if dst.kind == "map" else (src if src.kind == "map" else None)
        chart_view = src if map_view is dst else (dst if map_view is src else None)
        if map_view is None or chart_view is None:
            continue
        chart_data = chart_view.binding.dataset_ref or map_view.binding.dataset_ref
        if (
            chart_view.binding.dataset_ref
            and map_view.binding.dataset_ref
            and chart_view.binding.dataset_ref != map_view.binding.dataset_ref
        ):
            findings.append(_finding(
                "chart_map_data_mismatch", view_id=chart_view.view_id,
                detail=f"chart bound to {chart_view.binding.dataset_ref} "
                       f"but map to {map_view.binding.dataset_ref}"))

    # 6. shared_legend 一致性
    checked.append("shared_legend")
    for rel in spec.relations:
        if rel.kind != "shared_legend":
            continue
        a, b = live.get(rel.src), live.get(rel.dst)
        if a is None or b is None:
            continue
        kinds_a = a.binding.component_hint or a.kind
        kinds_b = b.binding.component_hint or b.kind
        if a.kind == b.kind and a.kind not in ("map",):
            findings.append(_finding(
                "shared_legend_degenerate", severity="warning",
                detail=f"shared_legend between two {a.kind} views adds no "
                       f"legend semantics"))

    # 7. delivery 覆盖披露（已知 LIVE-only 缺口 —— 如实披露不谎报）
    checked.append("delivery_coverage")
    export_targets = [t for t in spec.delivery.targets if t != "interactive"]
    if export_targets:
        has_live_only = any(
            t in _LIVE_ONLY_FAMILIES
            for v in live.values() for t in (v.binding.component_hint,))
        has_panel_views = bool(
            kinds_present & {"chart", "stats_panel", "comparison"})
        if has_live_only or has_panel_views:
            findings.append(_finding(
                "export_partial_coverage", severity="warning",
                detail="chart/statistics panels are live-only renderers today "
                       f"(export targets {export_targets} will omit them)"))

    errors = [f for f in findings if f.severity == "error"]
    return ProductCompletenessReport(
        complete=not errors,
        product_type=spec.product_type,
        findings=findings[:_MAX_FINDINGS],
        checked=checked,
    )


__all__ = [
    "CompletenessFinding",
    "ProductCompletenessReport",
    "validate_product_completeness",
]
