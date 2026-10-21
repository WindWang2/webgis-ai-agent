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

