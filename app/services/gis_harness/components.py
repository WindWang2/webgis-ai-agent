"""CartographyComponent 组装/突变权威（ADR-0216 后为 services 侧半模块）。

组件契约核（词表/模型/放置/纯校验器）已下沉
``app/contracts/cartography_components``；本模块保留依赖 lib 组件目录与
chart 协议的行为面：variant 目录权威（coerce_variant）、组件工厂、
默认组件集派生与突变编排（mutate/remove/duplicate/rebind）。
契约核符号经 import + re-export 保持全部既有 import path（36 引用方零改动）。
"""
from __future__ import annotations

import math

from typing import Any, Dict, List, Optional

from app.contracts.cartography_components import (  # noqa: F401
    MAX_ANNOTATION_ITEMS,
    MAX_ANNOTATION_TEXT,
    MAX_CHART_DATA_POINTS,
    MAX_DECISION_ROWS,
    MAX_INSET_BOUNDARY_POINTS,
    MAX_METHODOLOGY_NOTES,
    MAX_METHODOLOGY_TEXT,
    MAX_STAT_ITEMS,
    MAX_TABLE_COLUMN_NAME,
    MAX_TABLE_COLUMNS,
    MAX_UNCERTAINTY_ITEMS,
    MULTI_INSTANCE_TYPES,
    CartographyComponent,
    ComponentPlacement,
    ComponentType,
    Position,
    normalize_placement,
    validate_annotation_payload,
    validate_decision_payload,
    validate_inset_payload,
    validate_methodology_payload,
    validate_stats_payload,
    validate_table_binding,
    validate_uncertainty_payload,
    _valid_bbox4,
    _valid_lnglat,
)

def valid_variants_for_type(component_type: str) -> tuple:
    """从 descriptor registry 读取类型合法 variant 集（目录缺失 → 空集=不限）。"""
    try:
        from app.lib.cartography.component_registry import get_component_registry
        desc = get_component_registry().get_by_type(component_type)
        if desc is not None and desc.variants:
            return tuple(desc.variants)
    except Exception:  # noqa: BLE001 - 目录不可用不阻塞组件构造（宽松回退）
        pass
    return ()

def coerce_variant(component_type: str, variant: str) -> str:
    """非法 variant 确定性回退到类型默认 variant（不抛错——组件突变不因
    variant 拼写失败而整单失败）。"""
    candidates = valid_variants_for_type(component_type)
    if not candidates or variant in candidates:
        return variant
    try:
        from app.lib.cartography.component_registry import get_component_registry
        desc = get_component_registry().get_by_type(component_type)
        if desc is not None and desc.default_variant:
            return desc.default_variant
    except Exception:  # noqa: BLE001
        pass
    return candidates[0]

def north_arrow_component(
    variant: str = "compass_minimal_black",
    position: str = "top-right",
    component_id: str = "north-arrow",
) -> CartographyComponent:
    variant = coerce_variant("north_arrow", variant)
    return CartographyComponent(
        id=component_id, type="north_arrow", position=position, priority=30,
        variant=variant,
        options={"variant": variant},
    )

def scale_bar_component(
    position: str = "bottom-right",
    orientation: str = "horizontal",
    component_id: str = "scale-bar",
) -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="scale_bar", position=position, priority=20,
        options={"orientation": orientation, "unit": "metric"},
    )

def title_component(text: str, position: str = "top-center", component_id: str = "title") -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="title", position=position, priority=10,
        options={"text": text},
    )

def subtitle_component(text: str, position: str = "top-center", component_id: str = "subtitle") -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="subtitle", position=position, priority=11,
        options={"text": text},
    )

def attribution_component(text: str, component_id: str = "attribution") -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="attribution", position="bottom-left", priority=50,
        options={"text": text},
    )

def colorbar_component(
    orientation: str = "horizontal",
    position: str = "bottom-right",
    layer_id: str = "",
    title: str = "",
    component_id: str = "colorbar-main",
) -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="continuous_colorbar", position=position, priority=15,
        options={"orientation": orientation, "layerId": layer_id, "title": title},
    )

def legend_component(
    position: str = "bottom-left",
    layer_id: str = "",
    title: str = "",
    component_id: str = "legend-main",
) -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="legend", position=position, priority=16,
        options={"layerId": layer_id, "title": title},
    )

def categorical_legend_component(
    position: str = "bottom-left",
    layer_id: str = "",
    title: str = "",
    component_id: str = "legend-categorical",
) -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="categorical_legend", position=position, priority=17,
        options={"layerId": layer_id, "title": title},
    )

def validate_chart_payload(chart: Any) -> "str | None":
    """校验 inline ChartData。返回 None=合法，否则错误信息。"""
    from app.tools.chart import validate_chart_payload as _validate
    return _validate(chart)

def statistics_panel_component(
    position: str = "top-left",
    component_id: str = "statistics",
    stats: Optional[Dict[str, Any]] = None,
    variant: str = "default",
    placement: Optional[ComponentPlacement] = None,
) -> CartographyComponent:
    """统计摘要面板。stats 契约见 validate_stats_payload（缺省=空面板占位）。"""
    options: Dict[str, Any] = {}
    if stats is not None:
        options["stats"] = stats
    position, placement = normalize_placement(position, placement)  # type: ignore[arg-type]
    return CartographyComponent(
        id=component_id, type="statistics_panel", position=position,
        placement=placement, priority=40,
        variant=coerce_variant("statistics_panel", variant),
        options=options,
    )

def chart_panel_component(
    position: str = "top-left",
    component_id: str = "chart-panel",
    chart: Optional[Dict[str, Any]] = None,
    chart_ref: str = "",
    variant: str = "default",
    placement: Optional[ComponentPlacement] = None,
    title: str = "",
) -> CartographyComponent:
    """图表面板：inline ChartData（options.chart）或 artifact ref（options.chartRef）。

    与 chat 图表共用 ChartData 协议（validate_chart_payload）；ref 路径用于
    大数据/派生数据 —— ref 本体存 session_data_manager，MapSpec 只持引用。
    """
    options: Dict[str, Any] = {}
    if chart is not None:
        options["chart"] = chart
    if chart_ref:
        options["chartRef"] = chart_ref
    if title:
        options["title"] = title
    position, placement = normalize_placement(position, placement)  # type: ignore[arg-type]
    return CartographyComponent(
        id=component_id, type="chart_panel", position=position,
        placement=placement, priority=41,
        variant=coerce_variant("chart_panel", variant),
        options=options,
    )

def export_layout_component(
    paper_size: str = "A4",
    orientation: str = "landscape",
    dpi: int = 300,
    component_id: str = "export-layout",
) -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="export_layout", position="none", priority=90,
        options={"paperSize": paper_size, "orientation": orientation, "dpi": dpi},
    )

def table_panel_component(
    position: str = "bottom-right",
    component_id: str = "table-panel",
    table_ref: str = "",
    layer_id: str = "",
    columns: Optional[List[str]] = None,
    title: str = "",
    variant: str = "default",
    placement: Optional[ComponentPlacement] = None,
) -> CartographyComponent:
    """交互表格面板（Runtime V4 §10）：ref/图层绑定，数据本体不入 MapSpec。

    与 chart_panel 同纪律：MapSpec 只持绑定引用（tableRef/layerId），表数据
    由前端按 ref 拉取或从 HUD 图层读取（MVT 层经 attribute-table 水合）。
    """
    options: Dict[str, Any] = {}
    if table_ref:
        options["tableRef"] = table_ref
    if layer_id:
        options["layerId"] = layer_id
    if columns:
        options["columns"] = list(columns)
    if title:
        options["title"] = title
    position, placement = normalize_placement(position, placement)  # type: ignore[arg-type]
    return CartographyComponent(
        id=component_id, type="table_panel", position=position,
        placement=placement, priority=42,
        variant=coerce_variant("table_panel", variant),
        options=options,
    )

def graticule_component(enabled: bool = False, component_id: str = "graticule") -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="graticule", position="none", priority=60,
        enabled=enabled,
    )

def map_border_component(component_id: str = "map-border") -> CartographyComponent:
    return CartographyComponent(
        id=component_id, type="map_border", position="none", priority=70,
        options={"style": "neutral"},
    )

def label_layer_component(
    field: Optional[str] = None,
    layer_id: Optional[str] = None,
    *,
    profile: Optional[Dict[str, Any]] = None,
    component_id: str = "label-layer",
    variant: str = "auto_field",
) -> CartographyComponent:
    """标注图层组件（ac-05，ADR-0154）：可寻址的标注绑定/决策面。

    - ``field`` 显式指定 → ``explicit_field`` 变体，options.field 直通；
    - 缺省 + ``profile``（spatial_meta_profiler 产物或同形 dict）→
      ``auto_field``：label_plan.choose_label_field 自动挑选并把策略
      （mode/topN/priorityField/zoomBands）一并冻结进 ``options.label``；
    - 无候选字段（如 sensors 型数据）→ auto 模式产出 ``auto: False`` 的
      空绑定（**不用 ID 凑数**），仅留 advisory。

    注意：``options.label``（build_label_spec 产物）的写入桥**延期** ——
    目前无消费方把它落到绑定图层的 ``layer.label``（接线随 layout/compose
    线，ADR-0154 §3 诚实披露）；本工厂只负责组件面（绑定/决策）。

    换字段走 ``rebind_component({field: ...})`` —— 只改 options，不重建
    图层（前端 label-only 快路径消费同一契约）。
    """
    from app.lib.cartography.label_plan import build_label_spec

    options: Dict[str, Any] = {"layerId": layer_id}
    resolved_variant = variant
    if field:
        resolved_variant = "explicit_field" if variant in ("auto_field", "explicit_field") else variant
        options["field"] = field
        options["auto"] = False
    elif profile is not None:
        spec = build_label_spec(profile)
        if spec is not None:
            options["field"] = spec["field"]
            options["label"] = spec
            options["auto"] = True
        else:
            options["auto"] = False
            options["advisory"] = "no_name_like_field"
    else:
        options["auto"] = True
    return CartographyComponent(
        id=component_id, type="label_layer", position="none", priority=20,
        variant=coerce_variant("label_layer", resolved_variant),
        options=options,
    )

def annotation_component(
    text: str = "",
    component_id: str = "annotation",
    variant: str = "text",
    anchor: Optional[List[float]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    position: str = "top-left",
) -> CartographyComponent:
    """注记组件（v2 框架）：text 静态卡 / callout（anchor 坐标 + 引线）/
    group（options.items 多条相关注记）。

    三种形态共享同一组件类型与渲染/导出语义；payload 由
    validate_annotation_payload 把关（有界）。
    """
    options: Dict[str, Any] = {"variant": variant}
    if text:
        options["text"] = text
    if anchor is not None:
        options["anchor"] = list(anchor)
    if items is not None:
        options["items"] = items
    # 空载荷 = 工厂起步形态（upsert 后经突变填充），不做内容校验
    if text or items or anchor is not None:
        err = validate_annotation_payload(options)
        if err:
            raise ValueError(err)
    return CartographyComponent(
        id=component_id, type="annotation", position=position, priority=55,
        variant=coerce_variant("annotation", variant),
        options=options,
    )

def inset_map_component(
    bbox: List[float],
    component_id: str = "inset-map",
    variant: str = "overview",
    main_bbox: Optional[List[float]] = None,
    boundary: Optional[List[List[float]]] = None,
    label: str = "",
    position: str = "top-right",
) -> CartographyComponent:
    """区位插图（v2）：轻量静态小地图（bbox 范围 + 可选边界折线 + 主图范围
    指示框）。不 mount 第二个业务地图 runtime —— live 与 export 共享同一
    纯几何投影语义（前端 geo-anchor 模块）。payload 由
    validate_inset_payload 把关（有界）。"""
    options: Dict[str, Any] = {"bbox": list(bbox)}
    if main_bbox is not None:
        options["mainBbox"] = list(main_bbox)
    if boundary is not None:
        options["boundary"] = [list(pt) for pt in boundary]
    if label:
        options["label"] = label
    err = validate_inset_payload(options)
    if err:
        raise ValueError(err)
    return CartographyComponent(
        id=component_id, type="inset_map", position=position, priority=65,
        variant=coerce_variant("inset_map", variant),
        options=options,
    )

def methodology_note_component(
    warnings: Optional[List[Dict[str, Any]]] = None,
    component_id: str = "methodology-note",
    variant: str = "default",
    position: str = "bottom-left",
) -> CartographyComponent:
    """方法论披露组件（VNext §5）：稳定警告码 + 文案随产品渲染。

    「缺分母不能谈公平性」必须长在地图产品上，不是藏在日志里。
    """
    options: Dict[str, Any] = {}
    if warnings is not None:
        options["warnings"] = [
            {
                "code": str(w.get("code") or "")[:64],
                "pattern": str(w.get("pattern") or "")[:64],
                "text": str(w.get("text") or w.get("disclosures") or "")[:MAX_METHODOLOGY_TEXT],
            }
            for w in warnings[:MAX_METHODOLOGY_NOTES]
        ]
    err = validate_methodology_payload(options)
    if err:
        raise ValueError(err)
    return CartographyComponent(
        id=component_id, type="methodology_note", position=position,
        priority=46, variant=coerce_variant("methodology_note", variant),
        options=options,
    )

def uncertainty_panel_component(
    items: Optional[List[Dict[str, Any]]] = None,
    sample_note: str = "",
    component_id: str = "uncertainty-panel",
    variant: str = "default",
    position: str = "bottom-right",
) -> CartographyComponent:
    """不确定性面板（VNext §5）：插值不确定性/样本限制/区间披露。"""
    options: Dict[str, Any] = {}
    if items is not None or sample_note:
        unc: Dict[str, Any] = {}
        if items is not None:
            unc["items"] = [
                {
                    "label": str(it.get("label") or "")[:80],
                    "kind": str(it.get("kind") or "interval")[:24],
                    "detail": str(it.get("detail") or "")[:200],
                }
                for it in items[:MAX_UNCERTAINTY_ITEMS]
            ]
        if sample_note:
            unc["sampleNote"] = sample_note[:200]
        options["uncertainty"] = unc
    err = validate_uncertainty_payload(options)
    if err:
        raise ValueError(err)
    return CartographyComponent(
        id=component_id, type="uncertainty_panel", position=position,
        priority=47, variant=coerce_variant("uncertainty_panel", variant),
        options=options,
    )

def decision_panel_component(
    rows: Optional[List[Dict[str, Any]]] = None,
    method: str = "",
    weight_source: str = "",
    vetoes: Optional[List[str]] = None,
    component_id: str = "decision-panel",
    variant: str = "default",
    position: str = "top-left",
) -> CartographyComponent:
    """决策面板（VNext §12）：候选排名 + 方法 + 权重来源 + 硬约束否决披露。

    观测证据与用户假设必须可区分（weightSource 显式声明）—— 绝不合成。
    """
    options: Dict[str, Any] = {}
    if rows is not None or method or weight_source or vetoes:
        dec: Dict[str, Any] = {}
        if method:
            dec["method"] = method[:32]
        if weight_source:
            dec["weightSource"] = weight_source[:120]
        if rows is not None:
            def _finite(v: Any) -> Any:
                if isinstance(v, float) and not math.isfinite(v):
                    return None
                return v

            dec["rows"] = [
                {
                    "rank": _finite(it.get("rank", i + 1)),
                    "name": str(it.get("name") or "")[:80],
                    "score": _finite(it.get("score")),
                    "basis": str(it.get("basis") or "observed")[:24],
                }
                for i, it in enumerate(rows[:MAX_DECISION_ROWS])
            ]
        if vetoes:
            dec["vetoes"] = [str(v)[:120] for v in vetoes[:8]]
        options["decision"] = dec
    err = validate_decision_payload(options)
    if err:
        raise ValueError(err)
    return CartographyComponent(
        id=component_id, type="decision_panel", position=position,
        priority=48, variant=coerce_variant("decision_panel", variant),
        options=options,
    )

def build_default_components(
    *,
    primary_cartography: str,
    title: str = "",
    subtitle: str = "",
    attribution: str = "© OpenStreetMap contributors",
    report_product: bool = False,
    scope_name: str = "",
    subject_category: str = "",
    extra_types: Optional[List[str]] = None,
) -> List[CartographyComponent]:
    """按主专题表达派生默认组件集（确定性）。

    组件规则的唯一权威是模型库（MapModel.recommended_components，
    app/lib/cartography/model_library.py）—— 旧词汇兼容分支已删除
    （ADR-0151 / P6：词表收编进模型库别名，第二事实源不再存在）。
    模型库未收录的表达 → 无图例组件（诚实缺省，不猜图例类型）。

    - 视觉热力/连续面 → continuous_colorbar；
    - 分级填色（choropleth/graduated/hotspot/proximity 覆盖面）→ legend（离散）；
    - 分类专题 → categorical_legend；
    - 报告成果 → 额外附 title/subtitle/export_layout/map_border；
    - ``extra_types``：recipe 声明的附加组件（如 statistics_panel）按需并入。
    """
    components: List[CartographyComponent] = []

    if not title:
        title = f"{scope_name}{subject_category}分布" if (scope_name or subject_category) else "专题地图"
    components.append(title_component(title))
    if subtitle:
        components.append(subtitle_component(subtitle))

    legend_types: List[str] = []
    try:
        from app.lib.cartography.model_library import get_map_model_registry
        model = get_map_model_registry().resolve(primary_cartography)
    except Exception:  # noqa: BLE001 - 模型库不可用不阻塞组件推导
        model = None
    if model is not None:
        legend_types = [
            t for t in model.recommended_components
            if t in ("continuous_colorbar", "legend", "categorical_legend")
        ]

    for t in legend_types:
        if t == "continuous_colorbar":
            components.append(colorbar_component())
        elif t == "categorical_legend":
            components.append(categorical_legend_component())
        elif t == "legend":
            components.append(legend_component())

    components.append(north_arrow_component())
    components.append(scale_bar_component())
    components.append(attribution_component(attribution))

    for extra in extra_types or []:
        if extra == "statistics_panel" and not any(
            c.type == "statistics_panel" for c in components
        ):
            components.append(statistics_panel_component())
        elif extra == "chart_panel" and not any(
            c.type == "chart_panel" for c in components
        ):
            components.append(chart_panel_component())

    if report_product:
        components.append(map_border_component())
        components.append(export_layout_component())

    # 稳定排序：priority 升序 + id 字典序（确定性 diff）
    components.sort(key=lambda c: (c.priority, c.id))
    return components

def mutate_component(
    components: List[CartographyComponent],
    *,
    component_id: Optional[str] = None,
    component_type: Optional[str] = None,
    enabled: Optional[bool] = None,
    position: Optional[str] = None,
    placement: Optional[Dict[str, Any]] = None,
    variant: Optional[str] = None,
    style: Optional[Dict[str, Any]] = None,
    options: Optional[Dict[str, Any]] = None,
    upsert: bool = False,
) -> tuple:
    """局部组件突变。只改命中的单个组件，其余不动。

    ``upsert=True`` 且按 id/type 未命中时创建新组件（需要 component_type；
    工厂默认值起步，再应用给定突变字段）——Agent 加 chart_panel 等新面板
    用同一入口，不开 per-type 工具。

    Returns (mutated_list, change_record)。change_record 记录 from→to，
    供 Harness evidence（ComponentMutation 只动组件、不动数据层）；
    新建组件 change_record 带 ``created: True``。
    """
    target_idx = -1
    for idx, comp in enumerate(components):
        if component_id and comp.id == component_id:
            target_idx = idx
            break
        if component_id is None and component_type and comp.type == component_type:
            target_idx = idx
            break

    created = False
    if target_idx < 0:
        if not upsert or not component_type:
            return list(components), None
        factory = _FACTORY_BY_TYPE.get(component_type)
        if factory is None:
            return list(components), None
        new_id = component_id or f"{component_type.replace('_', '-')}"
        base = factory(component_id=new_id)
        target_idx = len(components)
        components = list(components) + [base]
        created = True

    original = components[target_idx]
    mutated = original.model_copy(deep=True)
    changes: Dict[str, Any] = {"id": mutated.id, "type": mutated.type}
    if created:
        changes["created"] = True
        changes["id"] = mutated.id

    if enabled is not None:
        changes["enabled"] = {"from": mutated.enabled, "to": enabled}
        mutated.enabled = enabled
    if position is not None:
        changes["position"] = {"from": mutated.position, "to": position}
        mutated.position = position  # type: ignore[assignment]
        # position 槽位突变 → anchor placement 双写一致化（单一真相）。对已
        # floating 的面板是重新锚定（否则 position 突变是静默 no-op——change
        # 记录说改了而地图不动）。
        mutated.placement = ComponentPlacement(mode="anchor", anchor=position)  # type: ignore[arg-type]
    if placement is not None:
        parsed = ComponentPlacement.model_validate(placement)
        # anchor 模式同步 position；floating 模式 position 保留原值（旧消费
        # 者兜底显示不漂移）
        if parsed.mode == "anchor":
            mutated.position = parsed.anchor  # type: ignore[assignment]
        # #1065: change 记录跨工具结果的 JSON 边界 —— pydantic 对象会在
        # dispatch 的 json.dumps(default=numpy_json_default) 处抛 TypeError，
        # 而此时变更已提交（重试 = 二次应用 + dedup 键滞留）。序列化为 dict。
        changes["placement"] = {
            "from": original.placement.model_dump() if original.placement else None,
            "to": parsed.model_dump(),
        }
        mutated.placement = parsed
    if variant is not None:
        coerced = coerce_variant(mutated.type, variant)
        changes["variant"] = {"from": mutated.variant, "to": coerced}
        mutated.variant = coerced
        # options.variant 是历史载体（north_arrow 等），保持同步
        mutated.options = {**mutated.options, "variant": coerced}
    if style is not None:
        changes["style"] = {"from": mutated.style, "to": style}
        mutated.style = {**mutated.style, **style}
    if options is not None:
        # options 合并语义：嵌套 dict 深合并，标量整体替换。
        # chart ↔ chartRef 互斥绑定：新值携带其一时移除另一个键——深合并会
        # 保留旧 inline chart，导致 renderer（inline 优先）与 catalog 一直
        # 显示旧数据（review P1-3）。
        merged_opts = {**mutated.options}
        if "chart" in options:
            merged_opts.pop("chartRef", None)
        if "chartRef" in options:
            merged_opts.pop("chart", None)
        for k, v in options.items():
            if isinstance(v, dict) and isinstance(merged_opts.get(k), dict):
                merged_opts[k] = {**merged_opts[k], **v}
            else:
                merged_opts[k] = v
        changes["options"] = {"from": mutated.options, "to": merged_opts}
        mutated.options = merged_opts

    out = list(components)
    out[target_idx] = mutated
    return out, changes

_REBIND_FIELDS = {
    "chart_panel": ("chartRef", "layerId"),
    "table_panel": ("tableRef", "layerId"),
    "legend": ("layerId",),
    "categorical_legend": ("layerId",),
    "continuous_colorbar": ("layerId",),
    "statistics_panel": ("layerId",),
    "inset_map": (),
    "annotation": (),
    # ac-05（ADR-0154）：换标注字段 / 换目标图层 = 局部突变（不重建图层）。
    "label_layer": ("field", "layerId"),
}

def _find_component(
    components: List[CartographyComponent],
    component_id: str,
) -> int:
    for idx, comp in enumerate(components):
        if comp.id == component_id:
            return idx
    return -1

def remove_component(
    components: List[CartographyComponent],
    *,
    component_id: str,
) -> "tuple[List[CartographyComponent], Dict[str, Any] | None]":
    """真删除：从 layout.components 移除命中组件（enabled=False 是隐藏不是删除）。

    Returns (remaining, change_record)；未命中 → (原列表, None)。
    """
    idx = _find_component(components, component_id)
    if idx < 0:
        return list(components), None
    removed = components[idx]
    out = list(components)
    out.pop(idx)
    return out, {
        "id": removed.id,
        "type": removed.type,
        "removed": True,
        "had_binding": {
            k: removed.options[k]
            for k in ("chartRef", "tableRef", "layerId")
            if k in removed.options
        },
    }

def duplicate_component(
    components: List[CartographyComponent],
    *,
    component_id: str,
    new_id: str = "",
) -> "tuple[List[CartographyComponent], CartographyComponent | None, str | None]":
    """复制多实例组件：新 id + floating 偏移（+16px，避免完全重叠）。

    Returns (list_with_copy, copy, error)。单例类型 / 未命中 / id 冲突 →
    (原列表, None, error)。共享 artifact ref 是合法复制（引用不是所有权）。
    """
    idx = _find_component(components, component_id)
    if idx < 0:
        return list(components), None, f"component {component_id} not found"
    source = components[idx]
    if source.type not in MULTI_INSTANCE_TYPES:
        return (
            list(components),
            None,
            f"组件类型 {source.type} 是单例（cardinality=single），不支持复制",
        )
    base_id = new_id.strip() or f"{source.id}-copy"
    dup_id = base_id
    suffix = 2
    existing = {c.id for c in components}
    while dup_id in existing:
        dup_id = f"{base_id}{suffix}"
        suffix += 1
        if suffix > 99:
            return list(components), None, "无法生成唯一副本 id（上限 99）"
    copy = source.model_copy(deep=True)
    copy.id = dup_id
    # 副本浮动偏移：锚点组件复制后转为 floating（同槽双锚是布局冲突源），
    # 已 floating 的就地偏移。
    if copy.placement and copy.placement.mode == "floating":
        # review M：model_copy(update=…) 不重校验 —— 上界必须显式 clamp
        # （越界条目会让后续所有组件事务的 model_validate 失败）。
        copy.placement = copy.placement.model_copy(update={
            "x": min(8192, max(-4096, (copy.placement.x or 0) + 16)),
            "y": min(8192, max(-4096, (copy.placement.y or 0) + 16)),
        })
    else:
        copy.placement = ComponentPlacement(
            mode="floating", x=32, y=96, width=360, height=280, zIndex=42,
        )
        copy.position = "none"
    out = list(components)
    out.append(copy)
    return out, copy, None

def rebind_component(
    components: List[CartographyComponent],
    *,
    component_id: str,
    bindings: Dict[str, str],
) -> "tuple[List[CartographyComponent], Dict[str, Any] | None, str | None]":
    """重绑定：改写组件 options 中的引用字段（chartRef/tableRef/layerId）。

    绑定字段按类型白名单校验（chart_panel 不接受 tableRef 等）；互斥纪律
    由调用方（工具层）对绑定目标存在性做权威校验 —— 纯函数只管 schema。
    Returns (list, change_record, error)。
    """
    idx = _find_component(components, component_id)
    if idx < 0:
        return list(components), None, f"component {component_id} not found"
    target = components[idx]
    allowed = _REBIND_FIELDS.get(target.type, ())
    if not bindings:
        return list(components), None, "bindings 不能为空"
    unknown = [k for k in bindings if k not in allowed]
    if unknown:
        return (
            list(components),
            None,
            f"组件类型 {target.type} 不接受绑定字段 {unknown}（允许: {list(allowed) or '无'}）",
        )
    ref_keys = {"chartRef", "tableRef"}
    if len(ref_keys & set(bindings)) + ("layerId" in bindings) > 1:
        return (
            list(components),
            None,
            "一次重绑定只能换一个通道（chartRef / tableRef / layerId 互斥）",
        )
    for k, v in bindings.items():
        if not isinstance(v, str) or not v.strip():
            return list(components), None, f"绑定 {k} 必须是非空字符串"
    mutated = target.model_copy(deep=True)
    changes: Dict[str, Any] = {"id": mutated.id, "type": mutated.type, "rebound": {}}
    for k, v in bindings.items():
        changes["rebound"][k] = {"from": mutated.options.get(k), "to": v}
        mutated.options[k] = v
    # 互斥纪律：换绑一个通道时清掉另一通道的残留（chartRef↔layerId /
    # tableRef↔layerId），否则渲染端双通道歧义。chart_panel 另须清掉
    # 旧 inline chart —— 渲染端 inline 优先，残留会一直压过新绑定
    # （与 mutate_component 的 chart↔chartRef 互斥清理同理由）。
    if "chartRef" in bindings:
        mutated.options.pop("layerId", None)
        mutated.options.pop("chart", None)
    elif "tableRef" in bindings:
        mutated.options.pop("layerId", None)
    elif "layerId" in bindings:
        mutated.options.pop("chartRef", None)
        mutated.options.pop("tableRef", None)
        mutated.options.pop("chart", None)
    out = list(components)
    out[idx] = mutated
    return out, changes, None

_FACTORY_BY_TYPE = {
    "title": lambda component_id: title_component(text="", component_id=component_id),
    "subtitle": lambda component_id: subtitle_component(text="", component_id=component_id),
    "continuous_colorbar": lambda component_id: colorbar_component(component_id=component_id),
    "categorical_legend": lambda component_id: categorical_legend_component(component_id=component_id),
    "legend": lambda component_id: legend_component(component_id=component_id),
    "attribution": lambda component_id: attribution_component(text="© OpenStreetMap contributors", component_id=component_id),
    "chart_panel": lambda component_id: chart_panel_component(component_id=component_id),
    "table_panel": lambda component_id: table_panel_component(component_id=component_id),
    "statistics_panel": lambda component_id: statistics_panel_component(component_id=component_id),
    "north_arrow": lambda component_id: north_arrow_component(component_id=component_id),
    "scale_bar": lambda component_id: scale_bar_component(component_id=component_id),
    "map_border": lambda component_id: map_border_component(component_id=component_id),
    "export_layout": lambda component_id: export_layout_component(component_id=component_id),
    "annotation": lambda component_id: CartographyComponent(
        id=component_id, type="annotation", position="top-left", priority=55,
        variant="text", options={"variant": "text", "text": ""},
    ),
    "inset_map": lambda component_id: CartographyComponent(
        id=component_id, type="inset_map", position="top-right", priority=65,
        variant="overview", options={"variant": "overview", "bbox": []},
    ),
    # #1220（audit3 C-8）：ComponentType 词表内的 4 类此前缺工厂登记 ——
    # webgis_component_update create=true 对 graticule / methodology_note /
    # uncertainty_panel / decision_panel 恒失败（工厂已存在，只是没进表）。
    "graticule": lambda component_id: graticule_component(component_id=component_id),
    "methodology_note": lambda component_id: methodology_note_component(component_id=component_id),
    "uncertainty_panel": lambda component_id: uncertainty_panel_component(component_id=component_id),
    "decision_panel": lambda component_id: decision_panel_component(component_id=component_id),
    # ac-05（ADR-0154）：标注图层（空绑定起步形态，upsert 后突变/重绑填充）。
    "label_layer": lambda component_id: label_layer_component(component_id=component_id),
}

__all__ = [
    "CartographyComponent",
    "ComponentPlacement",
    "build_default_components",
    "mutate_component",
    "remove_component",
    "duplicate_component",
    "rebind_component",
    "MULTI_INSTANCE_TYPES",
    "normalize_placement",
    "valid_variants_for_type",
    "coerce_variant",
    "validate_chart_payload",
    "validate_stats_payload",
    "validate_table_binding",
    "validate_annotation_payload",
    "validate_inset_payload",
    "north_arrow_component",
    "scale_bar_component",
    "title_component",
    "subtitle_component",
    "attribution_component",
    "colorbar_component",
    "legend_component",
    "categorical_legend_component",
    "statistics_panel_component",
    "chart_panel_component",
    "table_panel_component",
    "export_layout_component",
    "graticule_component",
    "map_border_component",
    "annotation_component",
    "inset_map_component",
    "MAX_ANNOTATION_ITEMS",
    "MAX_INSET_BOUNDARY_POINTS",
]

__all__ = [
    "CartographyComponent",
    "ComponentPlacement",
    "build_default_components",
    "mutate_component",
    "remove_component",
    "duplicate_component",
    "rebind_component",
    "MULTI_INSTANCE_TYPES",
    "normalize_placement",
    "valid_variants_for_type",
    "coerce_variant",
    "validate_chart_payload",
    "validate_stats_payload",
    "validate_table_binding",
    "validate_annotation_payload",
    "validate_inset_payload",
    "north_arrow_component",
    "scale_bar_component",
    "title_component",
    "subtitle_component",
    "attribution_component",
    "colorbar_component",
    "legend_component",
    "categorical_legend_component",
    "statistics_panel_component",
    "chart_panel_component",
    "table_panel_component",
    "export_layout_component",
    "graticule_component",
    "map_border_component",
    "annotation_component",
    "inset_map_component",
    "MAX_ANNOTATION_ITEMS",
    "MAX_INSET_BOUNDARY_POINTS",
]
