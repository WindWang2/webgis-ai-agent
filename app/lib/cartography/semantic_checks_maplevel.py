"""地图场景级语义检查（组件布局/图语义、3D 拉伸、等值线）。

从 ``semantic_checks.py`` 拆出的场景级（非逐层 paint/legend）检查簇：

- ``_check_component_layout`` / ``_check_component_graph_semantics`` /
  ``_detect_floating_overlaps``（C4 组件布局 QA + 组件图级 QA）；
- ``_check_3d_extrusion_rules`` / ``_check_isoline_contour_rules``。

拆分动因：ISSUE-044 god_modules 收敛（``semantic_checks.py`` 行数棘轮）叠加
ISSUE-1544（``evaluate_cartography_semantics`` 按 check 簇拆分后需为文件腾出
余量）。契约与行为不变 —— ``semantic_checks`` 仍以
``evaluate_cartography_semantics`` 为唯一调用方（函数级延迟导入以规避模块级
循环依赖）；检查码清单冻结面见
``tests/cartography/test_semantic_checks_contract.py`` 的 SOURCE_FILES（已随
拆分扩面，防漏扫）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.lib.cartography.semantic_checks import CartographyFinding

if TYPE_CHECKING:
    from app.lib.cartography.semantic_checks import CartographyReport


def _check_component_layout(report: CartographyReport, mapspec: Dict[str, Any]) -> None:
    """C4（Cartographic QA）：组件布局检查——desired-state 证据即可评。

    维度（layout_constraints 的 zone 模型 + floating 矩形）：
    - zone 容量超限 / exclusive zone 重复占用（anchored 组件）；
    - singleton 组件重复（title/north_arrow/scale_bar/attribution）；
    - 组件 layerId 悬空（指向不存在的图层）；
    - floating 组件矩形重叠（拖拽后压住另一个 floating 面板）。

    floating 组件的位置由用户手势拥有（placement.mode=floating 豁免 zone
    容量——用户可以把它放任何地方），但 floating 之间的矩形重叠仍要报。
    无组件 / 全部 not_applicable 时 pass（有证据的通过，不是 not_evaluated）。
    """
    from types import SimpleNamespace

    from app.lib.cartography.layout_constraints import (
        detect_collisions,
        detect_orphan_components,
    )

    layout = mapspec.get("layout") if isinstance(mapspec.get("layout"), dict) else {}
    raw = layout.get("components")
    if not isinstance(raw, list) or not raw:
        return  # 无组件：布局维度不适用（不产检查项）
    components = [c for c in raw if isinstance(c, dict)]

    layer_ids = {
        str(layer.get("id"))
        for layer in (mapspec.get("layers") or [])
        if isinstance(layer, dict)
    }

    def _adapt(c: Dict[str, Any]) -> SimpleNamespace:
        placement = c.get("placement") if isinstance(c.get("placement"), dict) else {}
        return SimpleNamespace(
            id=str(c.get("id") or ""),
            type=str(c.get("type") or ""),
            enabled=bool(c.get("enabled", True)),
            # floating 组件豁免 zone 容量（位置归用户手势所有）
            position="none" if placement.get("mode") == "floating" else str(c.get("position") or "none"),
            priority=int(c.get("priority") or 0),
            options=c.get("options") if isinstance(c.get("options"), dict) else {},
        )

    adapted = [_adapt(c) for c in components]
    issues: List[str] = []
    issues.extend(detect_collisions(adapted))
    issues.extend(detect_orphan_components(adapted, sorted(layer_ids)))
    overlap_issues = _detect_floating_overlaps(components)
    issues.extend(overlap_issues)

    if not issues:
        report.add_check(
            "LAYOUT_COLLISION",
            "pass",
            f"{len(components)} components: no zone overflow, singleton duplication, "
            "orphan binding, or floating overlap",
            evidence_class="desired_state",
        )
        return
    # AC-07（ADR-0156）：warning → 可自动修复。status 保持 warning（quality_loop
    # 只自动修 fail —— user-wins 语义不回退），但附确定性修复建议：策略链
    # 改 anchor → 缩尺寸 → 折叠进溢出面板 → 隐藏最低优先组件；前端
    # composition-repair 执行同一词表的动作链。悬空 layerId 属绑定语义
    # （非摆位），不在摆位修复链域内 —— 不为其生成动作。
    from app.lib.cartography.component_composer import plan_layout_repairs

    actions = plan_layout_repairs(components)
    report.add_check(
        "LAYOUT_COLLISION",
        "warning",
        "; ".join(issues[:6]),
        severity="warning",
        evidence_class="desired_state",
        evidence={"issues": issues},
        repairability="auto_safe" if actions else "not_repairable",
        suggested_fix={
            "operation": "resolve_layout_collisions",
            "actions": actions,
        } if actions else None,
    )


def _check_component_graph_semantics(
    report: CartographyReport, mapspec: Dict[str, Any]
) -> None:
    """V7（Goal 08 Phase I）组件图级 QA——图语义与几何越界。

    规则（与 LAYOUT_COLLISION 的槽位计数正交）：
    - ``DUPLICATE_LEGEND_BINDING``：同型组件 binds_to 同一图层（图例族
      重复的根语义；per-layer 图例展开是合法构成，不在此列）。warning，
      修复需改绑定语义 → 非 auto。
    - ``COMPONENT_OUTSIDE_CANVAS``：浮动矩形完全在画布负象限（不可见、
      不可达 —— 不是用户摆放意图，是破损状态）。fail + auto_safe，
      suggested_fix 走 ``resolve_floating_layout``（确定性钳制进安全区）。
      可见的部分越界沿用 LAYOUT_COLLISION 的 user-wins 边界，不在此报。
    - ``COMPONENT_LINK_CYCLE``：显式 requires/under 边成环（z 序/依赖
      不可满足）。error，需模板/用户裁决 → 非 auto。

    无组件时本维度不适用（不产检查项，同 _check_component_layout）。
    """
    from app.lib.cartography.component_graph import (
        build_component_graph,
        validate_component_graph,
    )
    from app.lib.cartography.layout_geometry import (
        CanvasSpec,
        check_component_bounds,
        resolve_floating_rects,
        safe_area_for,
    )

    layout = mapspec.get("layout") if isinstance(mapspec.get("layout"), dict) else {}
    raw = layout.get("components")
    if not isinstance(raw, list) or not raw:
        return
    components = [c for c in raw if isinstance(c, dict)]

    graph = build_component_graph(mapspec)
    issues = validate_component_graph(graph)

    dup = [i for i in issues if i.code == "duplicate_binding"]
    if dup:
        report.add_check(
            "DUPLICATE_LEGEND_BINDING",
            "warning",
            "; ".join(i.message for i in dup[:3]),
            severity="warning",
            evidence_class="deterministic",
            evidence={"bindings": [i.model_dump() for i in dup[:4]]},
            repairability="not_repairable",
            suggested_fix=None,
        )
    else:
        report.add_check(
            "DUPLICATE_LEGEND_BINDING",
            "pass",
            "no duplicate same-type layer binding",
            evidence_class="deterministic",
        )

    cycles = [i for i in issues if i.code == "cycle"]
    if cycles:
        # AC-07（ADR-0156）：断环策略 —— 每环断开最低权重边（端点 priority
        # 和最小，平局字典序），建议载荷 remove_links 与前端/评审消费同构。
        # repairability=auto_with_semantic_risk：断边是语义手术（z 序/依赖
        # 声明被移除），不进 quality_loop 自动通道（其语义即 explicit
        # intent 才可执行），live 侧由 composition-repair 按同一策略执行。
        from app.lib.cartography.component_graph import break_component_cycles

        remove_links = break_component_cycles(graph)
        report.add_check(
            "COMPONENT_LINK_CYCLE",
            "fail",
            "; ".join(i.message for i in cycles[:3]),
            severity="error",
            evidence_class="deterministic",
            evidence={"cycles": [i.model_dump() for i in cycles[:4]]},
            repairability="auto_with_semantic_risk" if remove_links else "not_repairable",
            suggested_fix={
                "operation": "break_component_cycle",
                "remove_links": remove_links,
            } if remove_links else None,
        )
    else:
        report.add_check(
            "COMPONENT_LINK_CYCLE",
            "pass",
            "no explicit link cycle",
            evidence_class="deterministic",
        )

    # 浮动越界双层语义：
    # - 完全不可达（x+w<=0 或 y+h<=0，负象限）——任何视口都不可见，破损
    #   状态 → fail + auto_safe（resolve_floating_layout 确定性钳回）；
    # - 超出标称视口（1280×720 假设）但坐标为正 —— 大屏可能是合法摆放，
    #   不定罪 → warning + auto_safe 建议（quality_loop 只自动修 fail，
    #   假设性修复不进自动通道，供 harness 评审采纳）。
    # 可见的部分越界沿用 LAYOUT_COLLISION 的 user-wins 边界，不在此报。
    canvas = CanvasSpec(width=1280, height=720)
    safe = safe_area_for(canvas, layout.get("margins")
                         if isinstance(layout.get("margins"), dict) else None)
    bounds_issues = check_component_bounds(components, canvas, safe)
    unreachable = []
    assumed_out = []
    for c in components:
        placement = c.get("placement") if isinstance(c.get("placement"), dict) else {}
        if placement.get("mode") != "floating" or not c.get("enabled", True):
            continue
        try:
            x = float(placement.get("x", 0))
            y = float(placement.get("y", 0))
            w = float(placement.get("width", 0) or 0)
            h = float(placement.get("height", 0) or 0)
        except (TypeError, ValueError):
            continue
        # 与 layout_geometry.check_component_bounds 的 id 归一同口径
        # （id or ""）—— 空 id 时 bounds 侧不产条目，本侧也不得用
        # type/"?" 兜底造出匹配不上的悬空引用（auto_safe 建议会失配）。
        cid = str(c.get("id") or "")
        if not cid:
            continue
        if w <= 0 or h <= 0:
            continue
        if x + w <= 0 or y + h <= 0:
            unreachable.append(
                {"component_ids": [cid], "message": f"浮动组件 {cid} 完全在负象限（不可见）"})
        else:
            for i in bounds_issues:
                if i.code in ("outside_safe_area", "outside_canvas") \
                        and cid in i.component_ids:
                    assumed_out.append({"component_ids": [cid],
                                        "message": i.message})
    fix_placements: List[Dict[str, Any]] = []
    if unreachable or assumed_out:
        fix_report = resolve_floating_rects(components, canvas, safe)
        target_ids = {c for i in (*unreachable, *assumed_out)
                      for c in i["component_ids"]}
        fix_placements = [
            {"component_id": a.component_id,
             "x": round(a.to_rect.x, 2), "y": round(a.to_rect.y, 2)}
            for a in fix_report.adjustments if a.component_id in target_ids
        ]
    if unreachable:
        report.add_check(
            "COMPONENT_OUTSIDE_CANVAS",
            "fail",
            "; ".join(i["message"] for i in unreachable[:3]),
            severity="error",
            evidence_class="deterministic",
            evidence={"outside": unreachable[:4]},
            repairability="auto_safe",
            suggested_fix={
                "operation": "resolve_floating_layout",
                "placements": fix_placements,
            } if fix_placements else None,
        )
    elif assumed_out:
        report.add_check(
            "COMPONENT_OUTSIDE_CANVAS",
            "warning",
            "; ".join(i["message"] for i in assumed_out[:3]),
            severity="warning",
            evidence_class="deterministic",
            evidence={"outside": assumed_out[:4]},
            repairability="auto_safe",
            suggested_fix={
                "operation": "resolve_floating_layout",
                "placements": fix_placements,
            } if fix_placements else None,
        )
    else:
        report.add_check(
            "COMPONENT_OUTSIDE_CANVAS",
            "pass",
            "no floating component fully outside canvas",
            evidence_class="deterministic",
        )


def _detect_floating_overlaps(components: List[Dict[str, Any]]) -> List[str]:
    """floating 组件矩形重叠（归一化 x/y/width/height 相交检测）。

    仅报 warning：用户可能有意叠放（如临时收起的统计卡）；QA 曝光即可，
    不 auto_safe 修复（修复会挪动用户手动摆放的位置——user wins）。
    几何单一实现在 component_composer.floating_overlap_pairs（AC-07：
    披露与修复建议同源，不做二次实现）。
    """
    from app.lib.cartography.component_composer import floating_overlap_pairs

    return [
        f"floating components {a_id} and {b_id} overlap "
        f"({ox:.2f}x{oy:.2f} normalized units)"
        for a_id, b_id, ox, oy in floating_overlap_pairs(components)
    ]


def _check_3d_extrusion_rules(
    report: CartographyReport,
    mapspec: Dict[str, Any],
    layer: Dict[str, Any],
    lid: str,
    sid: str,
    profile: Optional[Dict[str, Any]],
) -> None:
    """ADR-0095: 3D 挤出图层语义检查（高度字段存在性、极值/全零检测、相机俯仰建议、高倾角遮挡预警）。"""
    ltype = layer.get("type")
    ext_spec = layer.get("extrusion") or {}
    if ltype != "fill-extrusion" and not ext_spec:
        return

    # 1. 高度字段检查
    h_field = ext_spec.get("height_field")
    if not h_field:
        paint = layer.get("paint") or {}
        raw_h = paint.get("fill-extrusion-height")
        if isinstance(raw_h, str):
            h_field = raw_h
        elif isinstance(raw_h, list) and len(raw_h) >= 2 and raw_h[0] == "get":
            h_field = raw_h[1]

    if isinstance(h_field, str) and profile and profile.get("fields"):
        fields = profile.get("fields")
        if h_field not in fields:
            report.add_check(
                "EXTRUSION_HEIGHT_FIELD_VALID",
                "fail",
                f"3D 挤出图层 '{lid}' 绑定的高度字段 '{h_field}' 在数据集中不存在",
                severity="error",
                layer_id=lid,
                source_id=sid,
                evidence={"height_field": h_field, "available_fields": list(fields.keys())},
            )
            report.findings.append(CartographyFinding(
                check="EXTRUSION_HEIGHT_FIELD_VALID",
                severity="error",
                message=f"3D 挤出图层 '{lid}' 绑定的高度字段 '{h_field}' 不存在",
                layer_id=lid,
                source_id=sid,
            ))
        else:
            report.add_check(
                "EXTRUSION_HEIGHT_FIELD_VALID",
                "pass",
                f"3D 挤出图层 '{lid}' 高度字段 '{h_field}' 验证通过",
                layer_id=lid,
                source_id=sid,
                evidence={"height_field": h_field},
            )

    # 2. 统计特征检查（全零 / 极值离群）
    stats = ext_spec.get("stats") or {}
    if stats.get("is_all_zero"):
        report.add_check(
            "EXTRUSION_HEIGHT_DISTRIBUTION",
            "warning",
            f"3D 挤出图层 '{lid}' 高度字段全部为 0，要素将呈平坦无高差显示",
            severity="warning",
            layer_id=lid,
            source_id=sid,
        )
    elif stats.get("has_extreme_outlier"):
        report.add_check(
            "EXTRUSION_HEIGHT_DISTRIBUTION",
            "warning",
            f"3D 挤出图层 '{lid}' 存在极大离群值，建议采用 log1p 或 sqrt 变换防止其他柱体被压平",
            severity="warning",
            layer_id=lid,
            source_id=sid,
        )

    # 3. 相机俯仰角与遮挡检查
    view = mapspec.get("view") if isinstance(mapspec.get("view"), dict) else {}
    pitch = float(view.get("pitch", 0.0) or 0.0)
    if pitch < 10.0:
        report.add_check(
            "EXTRUSION_PITCH_ADVISORY",
            "warning",
            f"3D 挤出图层 '{lid}' 已启用但相机处于平视模式 (pitch={pitch:.0f}°)，建议调整俯仰角为 35°-55° 以便立体观察",
            severity="warning",
            layer_id=lid,
            source_id=sid,
            evidence={"pitch": pitch},
        )
    else:
        report.add_check(
            "EXTRUSION_PITCH_ADVISORY",
            "pass",
            f"3D 挤出图层 '{lid}' 视角良好 (pitch={pitch:.0f}°)",
            layer_id=lid,
            source_id=sid,
            evidence={"pitch": pitch},
        )

    max_h = float(ext_spec.get("max_visual_height_m", 5000.0) or 5000.0)
    if pitch > 75.0 and max_h > 3000.0:
        report.add_check(
            "EXTRUSION_OCCLUSION_WARNING",
            "warning",
            f"大倾角 (pitch={pitch:.0f}°) 配合高柱体 ({max_h:.0f}m) 可能导致严重的前景遮挡",
            severity="warning",
            layer_id=lid,
            source_id=sid,
        )


def _check_isoline_contour_rules(
    report: CartographyReport,
    mapspec: Dict[str, Any],
    layer: Dict[str, Any],
    lid: str,
    sid: str,
) -> None:
    """ADR-0095: 等值线/等值面语义检查（单调递增性、级数充足性）。"""
    iso_meta = layer.get("isoline")
    if not isinstance(iso_meta, dict):
        return

    levels = iso_meta.get("levels")
    if isinstance(levels, list):
        if len(levels) < 2:
            report.add_check(
                "CONTOUR_LEVELS_VALID",
                "fail",
                f"等值线图层 '{lid}' 级数不足 2 级 (当前 {len(levels)} 级)，无法形成有效等值面",
                severity="error",
                layer_id=lid,
                source_id=sid,
            )
            report.findings.append(CartographyFinding(
                check="CONTOUR_LEVELS_VALID",
                severity="error",
                message=f"等值线图层 '{lid}' 级数不足 2 级",
                layer_id=lid,
                source_id=sid,
            ))
        else:
            is_monotonic = all(levels[i] < levels[i + 1] for i in range(len(levels) - 1))
            if not is_monotonic:
                report.add_check(
                    "CONTOUR_LEVELS_VALID",
                    "fail",
                    f"等值线图层 '{lid}' 等值级不是单调严格递增: {levels}",
                    severity="error",
                    layer_id=lid,
                    source_id=sid,
                )
                report.findings.append(CartographyFinding(
                    check="CONTOUR_LEVELS_VALID",
                    severity="error",
                    message=f"等值线图层 '{lid}' 等值级不是单调递增",
                    layer_id=lid,
                    source_id=sid,
                ))
            else:
                report.add_check(
                    "CONTOUR_LEVELS_VALID",
                    "pass",
                    f"等值线图层 '{lid}' {len(levels)} 级等值线单调有效",
                    layer_id=lid,
                    source_id=sid,
                    evidence={"levels_count": len(levels)},
                )



# ── /goal cartography-design M4：跨组件口径 / 视口覆盖 / 密度语义诚实 ──────
# 三个检查共用「单位语义族」词表（审定静态表；中英双语 token）。族间冲突
# 的判据是**词法证据**（图面文本/字段名/图层类型），不做语义推断——证据
# 缺席一律 not_evaluated，不伪造 pass（契约 not_evaluable_tail 纪律）。

#: 单位语义族 token（小写子串匹配；先具体后泛化——判定顺序即优先级）。
_MEASURE_FAMILY_TOKENS: List[tuple] = [
    ("density_per_pop", ("每万人", "每千人", "人均", "人口密度",
                          "population density", "per capita", "per 10k",
                          "万人口")),
    ("density_per_area", ("每平方公里", "每km²", "每平方千米", "/km²", "/km2",
                           "per km²", "per km2", "per square")),
    ("count", ("个", "所", "家", "条", "处", "数量", "计数", "总数",
                "count", "number of", "total")),
    ("density_generic", ("密度", "density")),
]
#: 披露 token（在场即视为口径已披露——核密度估计/估计值/模拟等）。
_DISCLOSURE_TOKENS = ("核密度", "kde", "kernel density", "估计", "估算",
                       "模拟", "simulated", "口径不同", "另注口径")


def _classify_measure_family(text: str) -> Optional[str]:
    """文本 → 单位语义族（词法证据；多族命中取最具体——表序即优先级）。"""
    lowered = (text or "").lower()
    if not lowered.strip():
        return None
    for family, tokens in _MEASURE_FAMILY_TOKENS:
        if any(tok in lowered for tok in tokens):
            return family
    return None


def _layer_measure_text(layer: Dict[str, Any]) -> str:
    """层的度量语义面：图层名 + legend_spec 标题/单位 + data_binding 字段。"""
    parts: List[str] = [str(layer.get("name") or "")]
    legend = layer.get("legend_spec")
    if isinstance(legend, dict):
        for key in ("title", "units", "unit", "label", "field"):
            value = legend.get(key)
            if isinstance(value, str):
                parts.append(value)
    binding = layer.get("data_binding")
    if isinstance(binding, dict):
        parts.append(str(binding.get("field") or ""))
    return " ".join(p for p in parts if p)


def _thematic_layers(layers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """主题层：带 legend_spec 或 data_binding 的可见层（口径对账的对象面）。"""
    return [
        ly for ly in layers
        if isinstance(ly, dict) and ly.get("visible", True)
        and (isinstance(ly.get("legend_spec"), dict)
             or isinstance(ly.get("data_binding"), dict))
    ]


def _chart_measure_components(
    mapspec: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """携带度量语义的图表/表格组件（chart_panel / table_panel）。"""
    layout = mapspec.get("layout") if isinstance(mapspec.get("layout"), dict) else {}
    raw = layout.get("components")
    if not isinstance(raw, list):
        return []
    out = []
    for comp in raw:
        if not isinstance(comp, dict) or comp.get("type") not in (
                "chart_panel", "table_panel"):
            continue
        options = comp.get("options") if isinstance(comp.get("options"), dict) else {}
        chart = options.get("chart") if isinstance(options.get("chart"), dict) else {}
        text = " ".join(str(t) for t in filter(None, (
            options.get("title"), comp.get("title"),
            chart.get("title"), chart.get("y_label"), chart.get("x_label"),
        )) if isinstance(t, str))
        if text.strip():
            out.append({"id": str(comp.get("id") or ""), "text": text})
    return out


def _check_chart_map_field_coherence(
    report: "CartographyReport", mapspec: Dict[str, Any],
) -> None:
    """统计图 ↔ 地图主题层度量口径对账（M4；图面共同口径收据）。

    - 双侧单位语义族在场且矛盾（率/密度 vs 计数）且无披露 → fail；
    - 同族 → pass；单侧证据 → not_evaluated；双侧无度量语义 → 不发检查
      （该图无统计图组件或图面无度量语义，维度不适用）。
    """
    charts = _chart_measure_components(mapspec)
    layers = [ly for ly in (mapspec.get("layers") or [])
              if isinstance(ly, dict)]
    thematic = _thematic_layers(layers)
    if not charts or not thematic:
        return
    worst: Optional[Dict[str, Any]] = None
    for chart in charts:
        chart_family = _classify_measure_family(chart["text"])
        for layer in thematic:
            map_family = _classify_measure_family(_layer_measure_text(layer))
            if chart_family is None or map_family is None:
                continue
            disclosed = any(tok in (chart["text"] +
                                    _layer_measure_text(layer)).lower()
                            for tok in _DISCLOSURE_TOKENS)
            rate_families = ("density_per_pop", "density_per_area")
            contradictory = (
                (chart_family in rate_families) != (map_family in rate_families))
            if contradictory and not disclosed:
                item = {"status": "fail", "chart": chart["id"],
                        "layer": str(layer.get("id") or ""),
                        "chartFamily": chart_family, "mapFamily": map_family}
            elif contradictory:
                item = {"status": "warning", "chart": chart["id"],
                        "layer": str(layer.get("id") or ""),
                        "chartFamily": chart_family, "mapFamily": map_family,
                        "disclosed": True}
            else:
                item = {"status": "pass", "chart": chart["id"],
                        "layer": str(layer.get("id") or ""),
                        "chartFamily": chart_family, "mapFamily": map_family}
            order = {"fail": 0, "warning": 1, "pass": 2}
            if worst is None or order[item["status"]] < order[worst["status"]]:
                worst = item
    if worst is None:
        report.add_check(
            "CHART_MAP_FIELD_COHERENCE", "not_evaluated",
            "chart/map measure semantics present on one side only — "
            "cannot reconcile units",
            evidence_class="desired_state",
        )
        return
    message = {
        "fail": (f"chart '{worst['chart']}' 与图层 '{worst['layer']}' 度量口径"
                 f"矛盾（{worst['chartFamily']} vs {worst['mapFamily']}）且无披露"),
        "warning": (f"chart '{worst['chart']}' 与图层 '{worst['layer']}' 度量口径"
                    f"矛盾（{worst['chartFamily']} vs {worst['mapFamily']}），"
                    "已有披露标注"),
        "pass": (f"chart '{worst['chart']}' 与图层 '{worst['layer']}' 度量口径"
                 f"同族（{worst['chartFamily']}）"),
    }[worst["status"]]
    report.add_check(
        "CHART_MAP_FIELD_COHERENCE", worst["status"], message,
        severity="error" if worst["status"] == "fail" else
        ("warning" if worst["status"] == "warning" else "info"),
        evidence_class="desired_state", evidence=worst,
    )


def _check_viewport_covers_results(
    report: "CartographyReport", mapspec: Dict[str, Any],
) -> None:
    """视口必须覆盖可见结果层（M4；补 carto.load.ratio coverage=0 反 pass 漏洞）。

    证据：view center/zoom（Web-Mercator 尺度推视口跨度，2× 屏宽余量）
    × 可见层 source profile bbox。任一可见层与视口相交 → pass；全部
    （有 bbox 证据者）零交集 → fail；view 或 bbox 证据缺席 → not_evaluated。
    """
    view = mapspec.get("view") if isinstance(mapspec.get("view"), dict) else {}
    center = view.get("center")
    zoom = view.get("zoom")
    sources = mapspec.get("sources") if isinstance(mapspec.get("sources"), dict) else {}
    layers = [ly for ly in (mapspec.get("layers") or []) if isinstance(ly, dict)]

    visible_with_bbox: List[Dict[str, Any]] = []
    for layer in layers:
        if not layer.get("visible", True):
            continue
        source = sources.get(layer.get("source") or "")
        profile = source.get("profile") if isinstance(source, dict) else None
        bbox = profile.get("bbox") if isinstance(profile, dict) else None
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            visible_with_bbox.append(
                {"id": str(layer.get("id") or ""), "bbox": [float(b) for b in bbox]})

    if not isinstance(center, (list, tuple)) or len(center) != 2 or zoom is None:
        report.add_check(
            "VIEWPORT_COVERS_RESULTS", "not_evaluated",
            "view center/zoom absent — viewport coverage not computable",
            evidence_class="desired_state",
            evidence={"visible_layers_checked": len(visible_with_bbox)},
        )
        return
    if not visible_with_bbox:
        report.add_check(
            "VIEWPORT_COVERS_RESULTS", "not_evaluated",
            "no visible layer carries bbox evidence — coverage not computable",
            evidence_class="desired_state", evidence={},
        )
        return
    # Web-Mercator 视口跨度估算（全屏世界宽 360°/2^z；2× 余量覆盖超宽屏与
    # pitch）。经度差归一到 [-180,180]（跨日界线诚实）。
    try:
        zoom_f = float(zoom)
        cx, cy = float(center[0]), float(center[1])
    except (TypeError, ValueError):
        report.add_check(
            "VIEWPORT_COVERS_RESULTS", "not_evaluated",
            "view center/zoom malformed", evidence_class="desired_state")
        return
    margin = 2.0
    half_w = margin * 360.0 / (2.0 ** (zoom_f + 1))
    half_h = margin * 170.0 / (2.0 ** (zoom_f + 1))
    for item in visible_with_bbox:
        w, s, e, n = item["bbox"]
        # 环绕归一：视口中心与 bbox 最近经度差（跨日界线诚实）
        nearest_lng = max(w, min(cx, e))
        dx = (cx - nearest_lng + 180.0) % 360.0 - 180.0
        dy = cy - max(s, min(cy, n))
        if abs(dx) <= half_w and abs(dy) <= half_h:
            report.add_check(
                "VIEWPORT_COVERS_RESULTS", "pass",
                f"viewport intersects visible layer '{item['id']}' bounds",
                evidence_class="desired_state",
                evidence={"layer": item["id"], "bbox": item["bbox"],
                          "center": [cx, cy], "zoom": zoom_f},
            )
            return
    report.add_check(
        "VIEWPORT_COVERS_RESULTS", "fail",
        "viewport does not intersect any visible layer with bounds evidence "
        "— necessary results are off-screen",
        severity="error",
        evidence_class="desired_state",
        evidence={"center": [cx, cy], "zoom": zoom_f,
                  "layers_outside": [i["id"] for i in visible_with_bbox[:6]],
                  "half_span_deg": [round(half_w, 3), round(half_h, 3)]},
    )


_COUNT_FIELD_TOKENS = ("count", "数量", "计数", "总数", "num", "_cnt",
                        "total", "sum", "个数")


def _check_density_semantics_honesty(
    report: "CartographyReport", mapspec: Dict[str, Any],
) -> None:
    """密度图面语义诚实（M4；分布图误标密度 / KDE 冒充人口密度）。

    - heatmap 层声称人口/单位面积密度且无核密度披露 → fail；
    - 原始计数字段标注率/密度口径且无披露 → warning；
    - 泛「密度」声称在 heatmap 上无核密度披露 → warning；
    - 数据口径证据不足（无字段证据可判）→ not_evaluated。
    无密度语义线索的层不发检查（维度不适用）。
    """
    layers = [ly for ly in (mapspec.get("layers") or []) if isinstance(ly, dict)]
    for layer in layers:
        lid = str(layer.get("id") or "")
        text = _layer_measure_text(layer)
        claim = _classify_measure_family(text)
        if claim not in ("density_per_pop", "density_per_area",
                         "density_generic"):
            continue
        disclosed = any(tok in text.lower() for tok in _DISCLOSURE_TOKENS)
        is_kde = str(layer.get("type") or "") == "heatmap"
        binding = layer.get("data_binding")
        field = str(binding.get("field") or "") if isinstance(binding, dict) else ""
        legend = layer.get("legend_spec")
        if not field and isinstance(legend, dict):
            field = str(legend.get("field") or "")
        field_countish = any(tok in field.lower() for tok in _COUNT_FIELD_TOKENS)
        if claim in ("density_per_pop", "density_per_area"):
            if is_kde and not disclosed:
                report.add_check(
                    "DENSITY_SEMANTICS_HONESTY", "fail",
                    f"layer '{lid}' 图面声称 {claim}，但热力层是核密度估计且"
                    "无披露——不得冒充人口/单位面积密度",
                    severity="error", layer_id=lid,
                    evidence_class="desired_state",
                    evidence={"claim": claim, "layer_type": "heatmap",
                              "disclosed": False},
                )
            elif is_kde:
                report.add_check(
                    "DENSITY_SEMANTICS_HONESTY", "pass",
                    f"layer '{lid}' 热力密度声称带核密度披露",
                    layer_id=lid, evidence_class="desired_state",
                    evidence={"claim": claim, "disclosed": True})
            elif field_countish and not disclosed:
                report.add_check(
                    "DENSITY_SEMANTICS_HONESTY", "warning",
                    f"layer '{lid}' 字段 '{field}' 为原始计数，图面标注"
                    f"{claim} 口径——需归一分母或改注计数口径",
                    severity="warning", layer_id=lid,
                    evidence_class="desired_state",
                    evidence={"claim": claim, "field": field},
                )
            else:
                report.add_check(
                    "DENSITY_SEMANTICS_HONESTY", "not_evaluated",
                    f"layer '{lid}' 密度口径声称在场，但数据字段口径证据不足",
                    layer_id=lid, evidence_class="desired_state",
                    evidence={"claim": claim, "field": field or None})
        elif claim == "density_generic" and is_kde:
            if disclosed:
                report.add_check(
                    "DENSITY_SEMANTICS_HONESTY", "pass",
                    f"layer '{lid}' 热力密度概览带核密度披露",
                    layer_id=lid, evidence_class="desired_state",
                    evidence={"claim": claim, "disclosed": True})
            else:
                report.add_check(
                    "DENSITY_SEMANTICS_HONESTY", "warning",
                    f"layer '{lid}' 热力层声称密度但未披露核密度估计性质"
                    "（KDE ≠ 人口密度）",
                    severity="warning", layer_id=lid,
                    evidence_class="desired_state",
                    evidence={"claim": claim, "disclosed": False})
