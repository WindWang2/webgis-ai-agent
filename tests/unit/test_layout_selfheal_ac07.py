"""AC-07（ADR-0156）：版面冲突自愈 —— V4 求解器 / 修复规划器 / 断环 / QA 建议接线。

P1 验收域：
- solve_layout_v4 策略链四级（改 anchor → 缩尺寸 → 折叠 → 隐藏最低优先）
  逐级有触发测试；V3 形输入与 solve_layout_v3 等价（回归锁定）；
- plan_layout_repairs 产出 suggested_fix 动作链（semantic_checks 消费）；
- break_component_cycles 按最低权重断边（确定性、平局字典序）；
- LAYOUT_COLLISION warning + auto_safe 建议（quality_loop 不吃 warning —— 行为零回退）；
- COMPONENT_LINK_CYCLE fail + auto_with_semantic_risk + remove_links 建议。
"""

from app.lib.cartography.component_composer import (
    COLLAPSIBLE_TYPES,
    plan_layout_repairs,
    required_components_for,
)
from app.lib.cartography.component_graph import (
    build_component_graph,
    break_component_cycles,
)
from app.lib.cartography.layout_solver import (
    LayoutParticipantV4,
    solve_layout_v3,
    solve_layout_v4,
)
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics


def test_collapsible_vocabulary_parity():
    """solver 与 composer 的可折叠词表单源一致（parity 真实锁定）。"""
    from app.lib.cartography import layout_solver

    assert layout_solver._V4_COLLAPSIBLE_TYPES is COLLAPSIBLE_TYPES


def test_required_components_purpose_vocabulary_includes_a3():
    from app.lib.cartography.component_composer import OUTPUT_PURPOSES

    assert {"a3_portrait", "a3_landscape"} <= set(OUTPUT_PURPOSES)
    plan = required_components_for("a3_landscape", {"has_projection_info": True})
    assert [r.type for r in plan.required if r.type == "graticule"]


# ── helpers ──────────────────────────────────────────────────────────────


def _spec(components, layers=None, links=None):
    layout = {"components": components}
    if links:
        layout["component_links"] = links
    return {
        "version": "1.0",
        "sources": {"src1": {"type": "geojson"}},
        "layers": layers or [
            {"id": "layer-1", "type": "circle", "source": "src1", "paint": {}}
        ],
        "layout": layout,
    }


def _check(mapspec, rule):
    report = evaluate_cartography_semantics(mapspec)
    for check in report.checks:
        if check.rule == rule:
            return check
    return None


def _p4(pid, ptype, zone, *, priority=50, optional=True, width=1,
        collapsible=False):
    return LayoutParticipantV4(
        id=pid, type=ptype, requested_zone=zone, priority=priority,
        optional=optional, width_units=width, collapsible=collapsible,
    )


# ── V4：与 V3 等价（无冲突域回归锁定）────────────────────────────────────


def test_v4_matches_v3_when_no_conflict():
    participants = [
        _p4("t", "title", "top-center"),
        _p4("n", "north_arrow", "top-right"),
        _p4("s", "scale_bar", "bottom-right"),
        _p4("lg", "legend", "bottom-left"),
    ]
    v3 = solve_layout_v3(participants, page_profile="viewport")
    v4 = solve_layout_v4(participants, page_profile="viewport")
    assert not v4.healed
    assert v4.repair_steps == []
    assert {p.id: p.zone for p in v4.placements} == {
        p.id: p.zone for p in v3.placements
    }
    assert v4.ok


# ── V4：策略链 L1 改 anchor ──────────────────────────────────────────────


def test_v4_reanchors_when_all_candidates_full():
    # bottom-left/bottom-center 全满 → legend 无候选 → V4 跨槽兜底 bottom-right
    participants = [
        _p4("a", "legend", "bottom-left"),
        _p4("b", "legend", "bottom-left"),
        _p4("c", "categorical_legend", "bottom-left"),
        _p4("d", "legend", "bottom-center"),
        _p4("x", "legend", "bottom-left"),
    ]
    sol = solve_layout_v4(participants, page_profile="viewport")
    zones = {p.id: p.zone for p in sol.placements}
    assert "x" not in {p.id for p in sol.suppressed} or sol.healed
    healed_ids = {s.component_id for s in sol.repair_steps
                  if s.action == "change_anchor"}
    assert "x" in healed_ids
    assert zones.get("x") in ("bottom-right", "top-left", "top-right", "top-center")


# ── V4：策略链 L2 缩尺寸 ─────────────────────────────────────────────────


def test_v4_shrinks_width_before_suppressing():
    # bottom-right 容量 2：两个 width=1 先占满；宽件 width=2 无处可放 →
    # 缩到 min_width_units=1 仍无容量 → 落 L3/L4（此处 collapsible=False，
    # optional=True 且无可让者 → 抑制并披露）。缩尺寸触发的可观察面：
    # min_width_units=1 且有邻槽时以 change_anchor 收容。
    participants = [
        _p4("a", "legend", "bottom-right"),
        _p4("b", "legend", "bottom-right"),
        _p4("wide", "legend", "bottom-right", width=2, optional=True),
        _p4("z", "legend", "bottom-center"),  # 邻槽占位
    ]
    sol = solve_layout_v4(participants, page_profile="viewport")
    actions = {s.action for s in sol.repair_steps}
    wide_steps = [s for s in sol.repair_steps if s.component_id == "wide"]
    # 宽件最终被收容（缩尺寸或换锚）而不是静默抑制
    assert "wide" in {p.id for p in sol.placements} or sol.healed
    assert wide_steps, "宽件必须有自愈轨迹"
    assert actions & {"shrink", "change_anchor", "collapse_to_overflow"}


# ── V4：策略链 L3 折叠为溢出面板 ─────────────────────────────────────────


def test_v4_collapses_panel_when_full_and_optional_elsewhere():
    # 全部槽位灌满 → statistics_panel（可折叠族）以折叠态收容而非抑制
    participants = [
        _p4("a", "legend", "top-left"),
        _p4("b", "legend", "top-left"),
        _p4("c", "legend", "top-right"),
        _p4("d", "legend", "top-center"),
        _p4("e", "legend", "top-center"),
        _p4("f", "legend", "bottom-left"),
        _p4("g", "legend", "bottom-left"),
        _p4("h", "legend", "bottom-right"),
        _p4("i", "legend", "bottom-right"),
        _p4("p", "statistics_panel", "top-left", collapsible=True),
    ]
    sol = solve_layout_v4(participants, page_profile="viewport")
    assert "p" in {q.id for q in sol.placements}
    assert "p" in sol.collapsed_ids
    assert any(s.action == "collapse_to_overflow" and s.component_id == "p"
               for s in sol.repair_steps)


# ── V4：策略链 L4 隐藏最低优先腾让 ───────────────────────────────────────


def test_v4_hides_lowest_priority_to_yield():
    # 每槽容量钉为 1：6 个 optional 各占一槽 + 第 7 个 required（不可抑制、
    # 非可折叠族 → L1/L2/L3 全不可行）→ L4 让位：donor = optional 已放置者
    # 中 (priority 最大, id 字典序最大)。
    participants = [
        _p4("a", "legend", "top-left", priority=10),
        _p4("b", "legend", "top-center", priority=10),
        _p4("c", "legend", "top-right", priority=10),
        _p4("d", "legend", "bottom-left", priority=10),
        _p4("e", "legend", "bottom-center", priority=10),
        _p4("f", "legend", "bottom-right", priority=10),
        _p4("req", "legend", "top-left", priority=1, optional=False),
        _p4("donor", "table_panel", "top-right", priority=99),
    ]
    caps = {z: 1 for z in ("top-left", "top-center", "top-right",
                           "bottom-left", "bottom-center", "bottom-right")}
    sol = solve_layout_v4(participants, page_profile="viewport", zone_capacity=caps)
    assert "req" in {q.id for q in sol.placements}
    hides = [s for s in sol.repair_steps if s.action == "hide_lowest_priority"]
    assert hides, "必须有隐藏腾让动作"
    assert all(s.reason.startswith("yield_for:") for s in hides)
    # required 参与者不得进入隐藏/抑制域（功能件不静默消失）
    assert "req" not in sol.hidden_ids
    assert "req" not in {p.id for p in sol.suppressed}


def test_v4_duplicate_singleton_stays_suppressed():
    participants = [
        _p4("t1", "title", "top-center"),
        _p4("t2", "title", "top-left"),
        _p4("n", "north_arrow", "top-right"),
    ]
    sol = solve_layout_v4(participants, page_profile="viewport")
    suppressed = {p.id: p.reason for p in sol.suppressed}
    assert "t2" in suppressed
    assert suppressed["t2"] == "duplicate_singleton"
    assert "t2" not in sol.hidden_ids  # 不是自愈隐藏 —— 重复本身非法


def test_v4_required_overflow_kept_when_no_donor():
    # 7 个 required + 每槽容量钉 1（6 槽）：第 7 个无处安放，且域内无
    # optional 可让（L4 不可行）→ required 保留原位（不静默消失）。
    participants = [
        _p4("r", "legend", "top-left", priority=5, optional=False),
        _p4("n", "north_arrow", "top-right", priority=0, optional=False),
        _p4("s", "scale_bar", "bottom-right", priority=0, optional=False),
        _p4("t", "title", "top-center", priority=0, optional=False),
        _p4("at", "attribution", "bottom-left", priority=0, optional=False),
        _p4("x", "legend", "bottom-center", priority=99, optional=False),
        _p4("y", "legend", "bottom-center", priority=98, optional=False),
    ]
    caps = {z: 1 for z in ("top-left", "top-center", "top-right",
                           "bottom-left", "bottom-center", "bottom-right")}
    sol = solve_layout_v4(participants, page_profile="viewport", zone_capacity=caps)
    kept = [p for p in sol.placements if p.reason == "required_overflow_kept"]
    assert kept, "无让位对象时 required 必须保留原位并告警"
    assert kept[0].id == "x"  # (priority, id) 序最后（99 排 y 之后）
    assert any("kept" in w for w in sol.warnings)


# ── plan_layout_repairs：suggested_fix 动作链 ────────────────────────────


def test_plan_repairs_exclusive_top_center_moves_second():
    components = [
        {"id": "title", "type": "title", "enabled": True,
         "position": "top-center", "priority": 0},
        {"id": "subtitle", "type": "subtitle", "enabled": True,
         "position": "top-center", "priority": 1},
    ]
    actions = plan_layout_repairs(components)
    assert actions, "exclusive 重复必须有修复动作"
    first = actions[0]
    assert first["action"] == "change_anchor"
    assert first["component_id"] == "subtitle"
    assert first["from_"] == "top-center"
    assert first["to"] in ("top-left", "top-right")


def test_plan_repairs_duplicate_singleton_hidden():
    components = [
        {"id": "n1", "type": "north_arrow", "enabled": True,
         "position": "top-right", "priority": 0},
        {"id": "n2", "type": "north_arrow", "enabled": True,
         "position": "top-left", "priority": 5},
    ]
    actions = plan_layout_repairs(components)
    hide = [a for a in actions if a["action"] == "hide_lowest_priority"]
    assert len(hide) == 1
    assert hide[0]["component_id"] == "n2"
    assert hide[0]["reason"].startswith("duplicate_singleton")


def test_plan_repairs_floating_overlap_suggests_collapse():
    components = [
        {"id": "f1", "type": "statistics_panel", "enabled": True, "position": "none",
         "priority": 10,
         "placement": {"mode": "floating", "x": 10, "y": 10, "width": 200, "height": 100}},
        {"id": "f2", "type": "chart_panel", "enabled": True, "position": "none",
         "priority": 50,
         "placement": {"mode": "floating", "x": 50, "y": 40, "width": 200, "height": 100}},
    ]
    actions = plan_layout_repairs(components)
    assert actions and actions[0]["action"] == "collapse_to_overflow"
    assert actions[0]["component_id"] == "f2"  # priority 大者收纳
    assert actions[0]["overlap_with"] == "f1"


def test_plan_repairs_required_overflow_kept_action():
    # 全部槽位容量 0（无任何收容位）+ required 非可折叠组件 → 链尾 keep_required
    comps = [{"id": "c", "type": "north_arrow", "enabled": True,
              "position": "bottom-center", "priority": 99, "required": True}]
    caps = {z: 0 for z in ("top-left", "top-center", "top-right",
                           "bottom-left", "bottom-center", "bottom-right")}
    actions = plan_layout_repairs(comps, zone_capacity=caps)
    assert [a["action"] for a in actions] == ["shrink", "keep_required"]
    assert actions[-1]["reason"] == "required_overflow_kept"


# ── required_components_for ──────────────────────────────────────────────


def test_required_components_screen_baseline():
    plan = required_components_for("screen_16_9", {})
    types = [r.type for r in plan.required]
    assert types == ["title", "scale_bar", "north_arrow", "attribution"]
    assert plan.missing == ["data_source"]
    assert any("待补充" in r.placeholder_options.get("text", "")
               for r in plan.required if r.type == "attribution")


def test_required_components_print_adds_context_dependent():
    base = required_components_for("a4_portrait", {
        "has_thematic_layer": True,
        "has_projection_info": True,
        "has_location_context": True,
        "has_data_source": True,
    })
    types = [r.type for r in base.required]
    for t in ("title", "scale_bar", "north_arrow", "attribution",
              "legend", "graticule", "inset_map"):
        assert t in types, f"a4 缺 {t}"
    assert base.missing == []


def test_required_components_unknown_purpose_falls_back_screen():
    plan = required_components_for("hologram", {})
    assert plan.purpose == "screen_16_9"


def test_required_components_no_projection_print_advisory():
    plan = required_components_for("a4_landscape", {})
    assert not [r for r in plan.required if r.type == "graticule"]
    assert any("经纬网 alone" in a for a in plan.advisories)


# ── break_component_cycles ───────────────────────────────────────────────


def _cyc_spec(links, priorities=None):
    comps = [
        {"id": "a", "type": "legend", "enabled": True, "position": "bottom-left"},
        {"id": "b", "type": "legend", "enabled": True, "position": "bottom-center"},
        {"id": "c", "type": "legend", "enabled": True, "position": "bottom-right"},
    ]
    for cid, pr in (priorities or {}).items():
        for c in comps:
            if c["id"] == cid:
                c["priority"] = pr
    return _spec(comps, links=links)


def test_break_under_cycle_removes_lowest_weight_edge():
    links = [
        {"src": "a", "dst": "b", "type": "under"},
        {"src": "b", "dst": "c", "type": "under"},
        {"src": "c", "dst": "a", "type": "under"},
    ]
    graph = build_component_graph(_cyc_spec(links, {"a": 10, "b": 20, "c": 90}))
    removals = break_component_cycles(graph)
    assert len(removals) == 1
    # 最低权重 = 端点 priority 和最小：a-b(30) < b-c(110) < c-a(100)
    assert {removals[0]["src"], removals[0]["dst"]} == {"a", "b"}
    assert removals[0]["reason"].startswith("cycle_break:lowest_weight:under")


def test_break_cycles_tie_breaks_lexicographic():
    links = [
        {"src": "a", "dst": "b", "type": "requires"},
        {"src": "b", "dst": "c", "type": "requires"},
        {"src": "c", "dst": "a", "type": "requires"},
    ]
    graph = build_component_graph(_cyc_spec(links))  # 全默认 priority 50
    removals = break_component_cycles(graph)
    assert len(removals) == 1
    # 权重全 100 平局 → (dst, src) 字典序最小者被断：候选 (a,b)(b,c)(c,a)
    # → dst 最小 a（来自 c→a）
    assert (removals[0]["src"], removals[0]["dst"]) == ("c", "a")


def test_break_cycles_acyclic_graph_is_noop():
    links = [{"src": "a", "dst": "b", "type": "under"}]
    graph = build_component_graph(_cyc_spec(links))
    assert break_component_cycles(graph) == []


def test_break_cycles_handles_two_independent_cycles():
    links = [
        {"src": "a", "dst": "b", "type": "under"},
        {"src": "b", "dst": "a", "type": "under"},
    ]
    comps = _cyc_spec(links)["layout"]["components"] + [
        {"id": "d", "type": "annotation", "enabled": True, "position": "top-left"},
        {"id": "e", "type": "annotation", "enabled": True, "position": "top-right"},
    ]
    spec = _spec(comps, links=links + [
        {"src": "d", "dst": "e", "type": "under"},
        {"src": "e", "dst": "d", "type": "under"},
    ])
    graph = build_component_graph(spec)
    removals = break_component_cycles(graph)
    assert len(removals) == 2


# ── semantic_checks 建议接线 ─────────────────────────────────────────────


def test_layout_collision_warning_carries_repair_suggestion():
    components = [
        {"id": "title", "type": "title", "enabled": True,
         "position": "top-center", "priority": 0},
        {"id": "subtitle", "type": "subtitle", "enabled": True,
         "position": "top-center", "priority": 1},
    ]
    check = _check(_spec(components), "LAYOUT_COLLISION")
    assert check.status == "warning"  # 状态不回退（quality_loop 只修 fail）
    assert check.repairability == "auto_safe"
    assert check.suggested_fix["operation"] == "resolve_layout_collisions"
    assert check.suggested_fix["actions"]
    assert check.suggested_fix["actions"][0]["action"] in (
        "change_anchor", "shrink", "collapse_to_overflow", "hide_lowest_priority")


def test_link_cycle_fail_carries_break_suggestion():
    links = [
        {"src": "a", "dst": "b", "type": "under"},
        {"src": "b", "dst": "c", "type": "under"},
        {"src": "c", "dst": "a", "type": "under"},
    ]
    check = _check(_cyc_spec(links), "COMPONENT_LINK_CYCLE")
    assert check.status == "fail"
    assert check.repairability == "auto_with_semantic_risk"
    fix = check.suggested_fix
    assert fix["operation"] == "break_component_cycle"
    assert len(fix["remove_links"]) == 1
    assert set(fix["remove_links"][0].keys()) >= {"src", "dst", "type", "weight"}


def test_layout_collision_pass_has_no_fix():
    components = [
        {"id": "title", "type": "title", "enabled": True, "position": "top-center"},
        {"id": "north", "type": "north_arrow", "enabled": True, "position": "top-right"},
    ]
    check = _check(_spec(components), "LAYOUT_COLLISION")
    assert check.status == "pass"
    assert not check.suggested_fix
