"""Product Shapes —— 产品原型 → 视图构成（ADR-0183 §4，M3）。

PRODUCT_ARCHETYPES（product_templates.py，P7 防模板膨胀词表）已经是产品
类型目录；本模块给每个原型定义**视图构成缺省**：

    archetype → ProductShape{declared_claims, required/conditional view kinds,
                             required_any_kinds, base relations, delivery}

并给 `build_product_spec_from_plan()` 把既有确定性产物（MapProductPlan +
MapRequestIntent + MapProductTemplate）装配成 MapProductSpec v1。

单一真相纪律：

- shape 只**引用**既有词表（archetype / intent.output_intents / template 字段），
  不重建 recipe/template/组件目录（CA-P1-3 不加重）；
- builder 是纯函数：同输入同 spec（digest 稳定），不调 LLM、不做 IO；
- 视图的 `required` 标志只来自用户显式信号（output_intents）与 shape 应然
  构成 —— 不虚构（与 ProductFacetContract 的保守诚实同语义）。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from app.services.gis_harness.product_spec import (
    DELIVERY_TARGETS,
    PRODUCT_SPEC_VERSION,
    ProductDeliveryIntent,
    ProductRelation,
    ProductView,
    ProductViewBinding,
    MapProductSpec,
)

if TYPE_CHECKING:  # 契约类型仅注解（运行时零 import 成本）
    from app.services.gis_harness.intent import MapRequestIntent
    from app.services.gis_harness.planner import MapProductPlan
    from app.services.gis_harness.product_templates import MapProductTemplate


@dataclass(frozen=True)
class ProductShape:
    """原型 → 应然视图构成（bounded / serializable / deterministic）。"""

    archetype: str
    #: 该产品类型可支撑的分析声明（M8 语义完整性检查视图是否支撑声明；
    #: {subject} 占位由 builder 以 intent 主体替换）
    declared_claims: Tuple[str, ...] = ()
    #: 应然视图 kind（缺席 = 产品不完整）
    required_view_kinds: Tuple[str, ...] = ("map",)
    #: 至少其一在场的 kind 组（如对比型产品：comparison/chart/stats_panel 任一）
    required_any_kinds: Tuple[Tuple[str, ...], ...] = ()
    #: 条件视图 kind（用户点名/编辑加入；缺席不欠）
    conditional_view_kinds: Tuple[str, ...] = ()
    #: (src_kind, dst_kind, relation_kind) 缺省关系（两端视图在场才实例化）
    base_relations: Tuple[Tuple[str, str, str], ...] = ()
    delivery_targets: Tuple[str, ...] = ("interactive",)
    #: 产品是否应有来源归属面（M8：attribution/来源注记在场性）
    requires_source_note: bool = True


_SHAPES: Dict[str, ProductShape] = {
    "distribution_overview": ProductShape(
        archetype="distribution_overview",
        declared_claims=("{subject}的空间分布格局",),
        required_view_kinds=("map",),
        required_any_kinds=(("chart", "stats_panel"),),
        conditional_view_kinds=("chart", "stats_panel", "inset", "overview", "narrative"),
        base_relations=(
            ("chart", "map", "chart_linked_to_map"),
            ("stats_panel", "map", "derived_statistic"),
            ("inset", "map", "shared_extent"),
            ("overview", "map", "overview_detail"),
        ),
        delivery_targets=("interactive", "png", "pdf"),
    ),
    "regional_comparison": ProductShape(
        archetype="regional_comparison",
        declared_claims=("各行政区{subject}数量的可对比差异",),
        required_view_kinds=("map",),
        required_any_kinds=(("comparison", "chart", "stats_panel"),),
        conditional_view_kinds=("chart", "stats_panel", "comparison", "narrative"),
        base_relations=(
            ("comparison", "map", "comparison"),
            ("chart", "map", "chart_linked_to_map"),
            ("stats_panel", "map", "derived_statistic"),
        ),
        delivery_targets=("interactive", "png", "pdf", "csv"),
    ),
    "density_analysis": ProductShape(
        archetype="density_analysis",
        declared_claims=("{subject}的密度空间格局与可量化差异",),
        required_view_kinds=("map",),
        required_any_kinds=(("chart", "stats_panel"),),
        conditional_view_kinds=("chart", "stats_panel", "inset"),
        base_relations=(
            ("chart", "map", "chart_linked_to_map"),
            ("stats_panel", "map", "derived_statistic"),
        ),
        delivery_targets=("interactive", "png", "pdf"),
    ),
    "remote_sensing": ProductShape(
        archetype="remote_sensing",
        declared_claims=("栅格表面/指数的空间分布",),
        required_view_kinds=("map",),
        required_any_kinds=(),
        conditional_view_kinds=("chart", "narrative", "time_panel"),
        base_relations=(
            ("chart", "map", "chart_linked_to_map"),
            ("time_panel", "map", "comparison"),
        ),
        delivery_targets=("interactive", "png", "pdf"),
    ),
    "simple_view": ProductShape(
        archetype="simple_view",
        declared_claims=("{subject}的位置",),
        required_view_kinds=("map",),
        required_any_kinds=(),
        conditional_view_kinds=(),
        base_relations=(),
        delivery_targets=("interactive",),
        requires_source_note=False,
    ),
    "proportional_symbol": ProductShape(
        archetype="proportional_symbol",
        declared_claims=("{subject}数值规模的空间差异",),
        required_view_kinds=("map",),
        required_any_kinds=(),
        conditional_view_kinds=("chart", "stats_panel"),
        base_relations=(
            ("chart", "map", "chart_linked_to_map"),
            ("stats_panel", "map", "derived_statistic"),
        ),
        delivery_targets=("interactive", "png"),
    ),
}

#: 兜底 shape（archetype 缺失/未注册 → 最小诚实产品：一张图 + 交付 interactive）
GENERIC_SHAPE = ProductShape(archetype="")

#: 已注册 shape 的原型键集（测试锁定与 PRODUCT_ARCHETYPES 词表同源）
PRODUCT_ARCHETYPES_SHAPE_KEYS = frozenset(_SHAPES.keys())


def shape_for_archetype(archetype: str) -> ProductShape:
    """原型 → shape（未注册原型 → GENERIC_SHAPE，不虚构应然构成）。"""
    return _SHAPES.get(archetype, GENERIC_SHAPE)


def shape_claims(shape: ProductShape, subject: str) -> List[str]:
    """shape 声明 → 具体产品 claims（{subject} 占位替换；有界 ≤3）。"""
    return [
        c.replace("{subject}", subject or "主体")[:120]
        for c in shape.declared_claims[:3]
    ]


def _wants(intent: "MapRequestIntent", flag: str) -> bool:
    intents = getattr(intent, "output_intents", None) or []
    return flag in intents


def _chart_kind_for_plan(plan: "MapProductPlan") -> str:
    """plan.charts（recipe 级图表标识）→ chart_kinds 词表首个命中。

    词表单源 chart_kinds；无命中 → ""（builder 不虚构 kind，编译器以
    fallback 披露补缺省 —— recipe 级别名（如 admin_bar）的物理映射是
    编译器/图表通道的职责，不是 shape 的）。
    """
    try:
        from app.lib.cartography.chart_kinds import CHART_KINDS

        known = {k.id for k in CHART_KINDS}
    except Exception:  # noqa: BLE001 — 词表不可用 → 不猜
        return ""
    for c in plan.charts or []:
        if str(c) in known:
            return str(c)
    return ""


def build_product_spec_from_plan(
    plan: "MapProductPlan",
    intent: "MapRequestIntent",
    template: Optional["MapProductTemplate"] = None,
) -> MapProductSpec:
    """确定性装配：plan/intent/template → MapProductSpec v1（纯函数）。

    视图构成来源（优先级从高到低，全部是既有事实）：
    1. intent.output_intents 的用户显式信号（chart/statistics/table/summary）；
    2. plan 的裁决产物（charts/statistics 列表、template.exports）；
    3. template.archetype → shape 缺省。
    """
    archetype = getattr(template, "archetype", "") or ""
    shape = shape_for_archetype(archetype)
    subject = str(getattr(intent.subject, "category", "") or "")
    scope = str(getattr(intent.scope, "name", "") or "")
    plan_id = str(getattr(plan, "plan_id", "") or "")

    views: List[ProductView] = []
    rel_specs: List[Tuple[str, str, str]] = []

    # 主地图视图（一切产品型 shape 的应然构成）
    views.append(ProductView(
        view_id="v-map", kind="map", role="primary", required=True,
        title=(f"{scope}{subject}分布" if (scope or subject) else "")[:160],
        subject=subject,
        binding=ProductViewBinding(
            layer_hint=next(
                (ly.layer_id for ly in plan.map_layers if ly.role == "primary"), ""),
        ),
    ))

    # 图表视图：plan.charts 有裁决（用户点名 chart 时 planner 必填）
    chart_kind = _chart_kind_for_plan(plan)
    if plan.charts:
        views.append(ProductView(
            view_id="v-chart", kind="chart", role="secondary",
            required=_wants(intent, "chart"),
            chart_kind=chart_kind,
            title="统计图",
        ))
        rel_specs.append(("chart", "map", "chart_linked_to_map"))

    # 统计面板：plan.statistics 有裁决
    if plan.statistics:
        views.append(ProductView(
            view_id="v-stats", kind="stats_panel", role="secondary",
            required=_wants(intent, "statistics"),
            title="统计面板",
        ))
        rel_specs.append(("stats_panel", "map", "derived_statistic"))

    # 对比面板：intent.comparison 显式给出对比对象时
    comparison = str(getattr(intent, "comparison", "") or "")
    if comparison:
        views.append(ProductView(
            view_id="v-compare", kind="comparison", role="secondary",
            required=True, title=f"对比：{comparison}"[:160],
            description=comparison,
        ))
        rel_specs.append(("comparison", "map", "comparison"))

    # 时间面板：intent.time 显式给出时期时（变化/时序类请求的诚实构成）
    time_scope = str(getattr(intent, "time", "") or "")
    if time_scope:
        views.append(ProductView(
            view_id="v-time", kind="time_panel", role="secondary",
            title=f"时间：{time_scope}"[:160],
        ))
        rel_specs.append(("time_panel", "map", "comparison"))

    # 叙述/方法注记：报告类请求
    if bool(getattr(intent, "report_product", False)):
        views.append(ProductView(
            view_id="v-note", kind="narrative", role="context",
            title="方法说明",
        ))

    # 表格输出（output_intents.table）：以 stats 视图的 table 呈现位表达，
    # 不新建 kind —— 词表里没有 table 视图（统计面板承载）。

    # shape 缺省关系实例化（两端视图在场才落边）
    view_by_kind: Dict[str, str] = {}
    for v in views:
        view_by_kind.setdefault(v.kind, v.view_id)
    relations: List[ProductRelation] = []
    seen: set = set()
    for src_kind, dst_kind, rel in rel_specs:
        src_id, dst_id = view_by_kind.get(src_kind), view_by_kind.get(dst_kind)
        if not src_id or not dst_id or (src_id, dst_id, rel) in seen:
            continue
        seen.add((src_id, dst_id, rel))
        relations.append(ProductRelation(src=src_id, dst=dst_id, kind=rel))

    # 交付意图：template.exports ∩ 词表 ∪ shape 缺省 ∪ intent.export_intents
    raw_targets: List[str] = []
    for src in (
        list(getattr(intent, "export_intents", None) or []),
        list(getattr(template, "exports", None) or []),
        list(shape.delivery_targets),
    ):
        for t in src:
            t = str(t)
            if t in DELIVERY_TARGETS and t not in raw_targets:
                raw_targets.append(t)
    if not raw_targets:
        raw_targets = ["interactive"]
    audience = "report" if bool(getattr(intent, "report_product", False)) else ""

    spec = MapProductSpec(
        spec_id="prod-" + hashlib.sha1(  # noqa: S324 — 身份摘要非安全用途
            f"{plan_id}".encode("utf-8"), usedforsecurity=False).hexdigest()[:12],
        spec_version=PRODUCT_SPEC_VERSION,
        query=str(getattr(intent, "query", "") or getattr(plan, "query", "") or "")[:300],
        goal=shape_claims(shape, subject)[0] if shape.declared_claims else "",
        product_type=archetype,
        task=str(getattr(intent, "task", "") or ""),
        scope=scope, temporal=time_scope, subject=subject,
        claims=shape_claims(shape, subject),
        views=views, relations=relations,
        delivery=ProductDeliveryIntent(targets=raw_targets, audience=audience),
        recipe_id=str(getattr(plan, "recipe_id", "") or ""),
        template_id=str(getattr(plan, "template_id", "") or ""),
        composition_template_id=str(
            getattr(template, "composition_template_id", "") or ""),
        plan_id=plan_id,
        provenance_note=f"shape={shape.archetype or 'generic'}; source=plan_replay",
    )
    return spec


def shape_summary(shape: ProductShape) -> Dict[str, Any]:
    """有界 dict 投影（披露/测试用）。"""
    return {
        "archetype": shape.archetype,
        "declared_claims": list(shape.declared_claims),
        "required_view_kinds": list(shape.required_view_kinds),
        "required_any_kinds": [list(g) for g in shape.required_any_kinds],
        "conditional_view_kinds": list(shape.conditional_view_kinds),
        "delivery_targets": list(shape.delivery_targets),
        "requires_source_note": shape.requires_source_note,
    }
