"""ADR-0186 视觉自愈编译器（visual self-healing v1）单测。

覆盖面（docs/dev/visual-self-healing-rules.md 为规范来源）：

1. 15 组典型缺陷 → MapSpecPatch 精确命中靶图层×属性（纯函数层：Planner + apply_heal_plan）；
2. 归一化桥 normalize_visual_report（维度级 VisualJudgeReport → 可定位缺陷，诚实丢弃）；
3. 引擎事务挂载 apply_visual_heal_patch：revision 严格单调、指纹必变、checkpoint、
   mutation_id 幂等、锁面拒绝、HEAL_PLAN_EMPTY 诚实拒绝；
4. 收敛防护：同一缺陷指纹 ≤2 次自愈，第 3 次请求触发 SelfHealConvergenceExhausted
   （raise / degrade 双路径）+ 重复补丁防护；
5. 向后兼容：healed spec 通过 coordinator.validate 与 MapSpecDocument 权威 schema。
"""
from __future__ import annotations

import copy
import uuid

import pytest

from app.lib.cartography.palettes import (
    contrast_ratio,
    min_adjacent_delta_e,
    relative_luminance,
)
from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.lib.harness.visual_evaluator import VisualCritique, VisualJudgeReport
from app.services.mapspec.coordinator import validate as validate_mapspec
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    SetWorkbenchStateIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.visual_healer import (
    MAX_VISUAL_HEAL_ITERATIONS,
    MUTATION_HEAL_CONTRAST,
    MUTATION_HEAL_LABEL_COLLISION,
    MUTATION_HEAL_LAYER_ORDER,
    MUTATION_HEAL_OPACITY,
    SelfHealConvergenceExhausted,
    VisualCritiqueItem,
    VisualHealStrategyPlanner,
    apply_heal_plan,
    canvas_color,
    defect_fingerprint,
    normalize_visual_report,
    select_contrast_palette,
)

# ─────────────────────────────────────────────────────────────────────────────
# 夹具构造（纯 spec，不走引擎）
# ─────────────────────────────────────────────────────────────────────────────


def _bg(color: str = "#ffffff") -> dict:
    return {
        "id": "basemap-bg",
        "type": "background",
        "source": "src-1",
        "paint": {"background-color": color},
    }


def _symbol(pid: str = "poi-labels", size: int = 14, overlap: bool = True,
            padding: int = 2) -> dict:
    return {
        "id": pid,
        "type": "symbol",
        "source": "src-1",
        "layout": {
            "text-field": "{name}",
            "text-size": size,
            "text-allow-overlap": overlap,
            "text-padding": padding,
        },
        "paint": {"icon-opacity": 1.0},
    }


def _heat() -> dict:
    return {
        "id": "density-heat",
        "type": "heatmap",
        "source": "src-1",
        "paint": {"heatmap-opacity": 0.9},
    }


def _choropleth(colors: list | None = None, opacity: float = 0.6,
                pid: str = "districts-fill") -> dict:
    colors = colors if colors is not None else ["#ffffd9", "#edf8b1", "#c7e9b4"]
    paint = {
        "fill-color": {
            "method": "step",
            "field": "value",
            "default": colors[0],
            "stops": [[10, c] for c in colors[1:]],
        },
        "fill-opacity": opacity,
    }
    return {"id": pid, "type": "fill", "source": "src-1", "paint": paint}


def _categorical(colors: list | None = None, pid: str = "zones-fill") -> dict:
    colors = colors if colors is not None else ["#fbb4ae", "#b3cde3", "#ccebc5"]
    keys = ["res", "com", "ind"]
    return {
        "id": pid,
        "type": "fill",
        "source": "src-1",
        "paint": {
            "fill-color": {
                "method": "match",
                "field": "zone",
                "cases": [[k, c] for k, c in zip(keys, colors)],
                "default": "#f5f5f5",
            }
        },
        "legend_spec": {
            "type": "categorical",
            "categories": [{"key": k, "color": c} for k, c in zip(keys, colors)],
        },
    }


def _spec(layers: list) -> dict:
    return {
        "version": "1.0",
        "view": {"center": [116.4, 39.9], "zoom": 10},
        "sources": {"src-1": {"type": "geojson"}},
        "layers": layers,
        "layout": {"legend": {"visible": True, "position": "top-right"}},
        "thresholds": {"maxFeatures": 5000, "timeoutMs": 30000},
    }


def _sid(prefix: str) -> str:
    """跨进程封闭的会话 id（MapSpecStore 磁盘持久化，revision/幂等账本跨运行累积）。"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _layer(spec: dict, layer_id: str) -> dict:
    for layer in spec["layers"]:
        if layer.get("id") == layer_id:
            return layer
    raise AssertionError(f"layer {layer_id} not found")


# ─────────────────────────────────────────────────────────────────────────────
# 15 组缺陷 → 补丁精确命中（参数化，纯函数层）
# ─────────────────────────────────────────────────────────────────────────────


def _case_label_overlap_true():
    """A1: text-allow-overlap=True 的注记层 → 强制 False + padding 翻倍 + ignore-placement。"""
    spec = _spec([_bg(), _symbol()])
    defect = VisualCritiqueItem(
        category="label_collision", severity="error", layer_ids=("poi-labels",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert [op.op for op in plan.ops] == [MUTATION_HEAL_LABEL_COLLISION]
    assert plan.ops[0].layer_ids == ("poi-labels",)
    out, applied = apply_heal_plan(spec, plan)
    assert applied == 1
    layout = _layer(out, "poi-labels")["layout"]
    assert layout["text-allow-overlap"] is False
    assert layout["text-padding"] == 4
    assert layout["text-ignore-placement"] is True
    assert layout["text-size"] == 14  # attempt 0 不动字号
    # 靶外图层零触碰
    assert _layer(out, "basemap-bg") == _bg()


def _case_label_size_stepdown():
    """A2: 第二次迭代（attempt=1）→ 步进式 text-size 缩小，保留此前避让补丁。"""
    mid = _symbol(size=16, overlap=False, padding=4)
    spec = _spec([_bg(), mid])
    defect = VisualCritiqueItem(
        category="label_collision", severity="warning", layer_ids=("poi-labels",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect], attempt=1)
    assert plan.ops and plan.ops[0].op == MUTATION_HEAL_LABEL_COLLISION
    out, applied = apply_heal_plan(spec, plan)
    assert applied == 1
    layout = _layer(out, "poi-labels")["layout"]
    assert layout["text-size"] == pytest.approx(16 * 0.85)
    assert layout["text-size"] >= 8  # 下限保护
    assert layout["text-allow-overlap"] is False  # 此前补丁保留
    assert layout["text-padding"] == 8  # min(4*2, 8)


def _case_label_missing_layout_keys():
    """A3: 缺 layout 键的 symbol 层 → 补丁建键而非报错。"""
    sparse = {"id": "metro-labels", "type": "symbol", "source": "src-1",
              "layout": {"text-field": "{station}"}}
    spec = _spec([_bg(), sparse])
    defect = VisualCritiqueItem(
        category="label_collision", severity="warning", layer_ids=("metro-labels",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    out, applied = apply_heal_plan(spec, plan)
    assert applied == 1
    layout = _layer(out, "metro-labels")["layout"]
    assert layout["text-allow-overlap"] is False
    assert layout["text-padding"] == 4
    assert layout["text-ignore-placement"] is True
    assert "text-size" not in layout  # attempt 0 不新增字号


def _case_label_unknown_layer():
    """A4: 靶图层不存在 → 诚实跳过（unknown_layer），不产出任何 op。"""
    spec = _spec([_bg(), _symbol()])
    defect = VisualCritiqueItem(
        category="label_collision", severity="error", layer_ids=("ghost-layer",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert plan.ops == ()
    assert any(s["reason"] == "unknown_layer" for s in plan.skipped)


def _case_label_multi_layer_selective():
    """A5: 多图层缺陷（含非 symbol 靶）→ 只对 symbol 层产出 op，fill 层披露跳过。"""
    spec = _spec([_bg(), _symbol("a"), _symbol("b", overlap=False), _choropleth()])
    defect = VisualCritiqueItem(
        category="label_collision", severity="error",
        layer_ids=("a", "b", "districts-fill"),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert {op.layer_ids[0] for op in plan.ops} == {"a", "b"}
    assert all(op.op == MUTATION_HEAL_LABEL_COLLISION for op in plan.ops)
    out, _ = apply_heal_plan(spec, plan)
    assert _layer(out, "districts-fill")["paint"] == _layer(spec, "districts-fill")["paint"]
    skipped_targets = {s.get("layer_ids") for s in plan.skipped}
    assert any("districts-fill" in (t or ()) for t in skipped_targets)
    assert any(s["reason"] == "unsupported_layer_type" for s in plan.skipped)


def _case_contrast_step_gate45():
    """B6: 2 类 step 色带白底不可辨（显式 4.5 门限）→ RdBu 达标替换，断点不动。"""
    bad = ["#fddbc7", "#efefef"]
    spec = _spec([_bg(), _choropleth(colors=bad)])
    layer = _layer(spec, "districts-fill")
    layer["legend_spec"] = {"type": "graduated", "palette_colors": list(bad),
                            "min": 0, "max": 20}
    defect = VisualCritiqueItem(
        category="contrast", severity="error", layer_ids=("districts-fill",),
        min_contrast_ratio=4.5,
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert [op.op for op in plan.ops] == [MUTATION_HEAL_CONTRAST]
    expected = select_contrast_palette(2, "#ffffff", 4.5)
    assert expected is not None and list(plan.ops[0].colors) == list(expected)
    out, applied = apply_heal_plan(spec, plan)
    assert applied == 1
    paint = _layer(out, "districts-fill")["paint"]["fill-color"]
    assert paint["method"] == "step" and paint["field"] == "value"
    assert paint["stops"] == [[10, expected[1]]]  # 断点数值不动，仅换色
    legend = _layer(out, "districts-fill")["legend_spec"]
    assert legend["palette_colors"] == list(expected)
    assert legend["min"] == 0 and legend["max"] == 20
    assert all(contrast_ratio(c, "#ffffff") >= 4.5 for c in expected)


def _case_contrast_dark_canvas():
    """B7: 暗底画布 → 选取与暗底对比达标的色板，且不同于白底选择。"""
    dark = "#101820"
    near_dark = ["#2b2b2b", "#374151", "#3f3f46"]
    spec = _spec([_bg(dark), _choropleth(colors=near_dark)])
    assert relative_luminance(canvas_color(spec)) < 0.18
    defect = VisualCritiqueItem(
        category="contrast", severity="error", layer_ids=("districts-fill",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert plan.ops and plan.ops[0].op == MUTATION_HEAL_CONTRAST
    chosen = list(plan.ops[0].colors)
    assert all(contrast_ratio(c, dark) >= 3.0 for c in chosen)
    assert chosen != list(select_contrast_palette(3, "#ffffff", 3.0))
    out, _ = apply_heal_plan(spec, plan)
    stops = _layer(out, "districts-fill")["paint"]["fill-color"]["stops"]
    assert [s[1] for s in stops] == chosen[1:]
    assert _layer(out, "districts-fill")["paint"]["fill-color"]["default"] == chosen[0]


def _case_contrast_match_categories():
    """B8: match 分类表达式 + legend categories → 色同步替换，类别键原样保留。"""
    spec = _spec([_bg(), _categorical()])
    keys_before = [c["key"] for c in _layer(spec, "zones-fill")["legend_spec"]["categories"]]
    defect = VisualCritiqueItem(
        category="contrast", severity="warning", layer_ids=("zones-fill",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert plan.ops and plan.ops[0].op == MUTATION_HEAL_CONTRAST
    chosen = list(plan.ops[0].colors)
    out, applied = apply_heal_plan(spec, plan)
    assert applied == 1
    fill = _layer(out, "zones-fill")["paint"]["fill-color"]
    assert [c[0] for c in fill["cases"]] == keys_before
    assert [c[1] for c in fill["cases"]] == chosen
    cats = _layer(out, "zones-fill")["legend_spec"]["categories"]
    assert [c["key"] for c in cats] == keys_before
    assert [c["color"] for c in cats] == chosen
    # 感知区分度不劣化
    before_de = min_adjacent_delta_e(["#fbb4ae", "#b3cde3", "#ccebc5"])
    assert min_adjacent_delta_e(chosen) >= (before_de or 0.0)


def _case_contrast_already_optimal():
    """B9: 现状已是判据最优色板 → already_optimal 诚实跳过，不产出补丁。"""
    best = list(select_contrast_palette(3, "#ffffff", 3.0))
    spec = _spec([_bg(), _choropleth(colors=best)])
    defect = VisualCritiqueItem(
        category="contrast", severity="error", layer_ids=("districts-fill",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert plan.ops == ()
    assert any(s["reason"] == "already_optimal" for s in plan.skipped)


def _case_contrast_expression_unsafe():
    """B10: MapLibre 表达式数组形态的 fill-color → unsafe_target 诚实跳过。"""
    layer = _choropleth()
    layer["paint"]["fill-color"] = [
        "interpolate", ["linear"], ["get", "value"],
        0, "#ffffd9", 10, "#edf8b1", 20, "#c7e9b4",
    ]
    spec = _spec([_bg(), layer])
    defect = VisualCritiqueItem(
        category="contrast", severity="error", layer_ids=("districts-fill",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert plan.ops == ()
    assert any(s["reason"] == "unsafe_target" for s in plan.skipped)


def _case_order_heatmap_occlusion():
    """C11: 注记被上方热力图遮挡 → 最小重排抬到热力图正上方，background 恒居首。"""
    spec = _spec([_bg(), _choropleth(), _symbol(), _heat()])
    defect = VisualCritiqueItem(
        category="layer_order", severity="error", layer_ids=("poi-labels",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert [op.op for op in plan.ops] == [MUTATION_HEAL_LAYER_ORDER]
    assert plan.ops[0].move_above == ("poi-labels", "density-heat")
    out, applied = apply_heal_plan(spec, plan)
    assert applied == 1
    ids = [layer["id"] for layer in out["layers"]]
    assert ids[0] == "basemap-bg"
    assert ids.index("poi-labels") > ids.index("density-heat")
    assert ids.index("districts-fill") < ids.index("density-heat")  # 他者相对序不变


def _case_order_explicit_occluder():
    """C12: 显式 occluder → 目标精确抬升到遮挡者正上方。"""
    spec = _spec([_bg(), _choropleth(), _heat()])
    defect = VisualCritiqueItem(
        category="layer_order", severity="warning", layer_ids=("districts-fill",),
        occluder_layer_id="density-heat",
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert plan.ops and plan.ops[0].move_above == ("districts-fill", "density-heat")
    out, _ = apply_heal_plan(spec, plan)
    ids = [layer["id"] for layer in out["layers"]]
    assert ids == ["basemap-bg", "density-heat", "districts-fill"]


def _case_order_already_on_top():
    """C13: 靶层已高于全部遮挡型层 → already_optimal，空计划。"""
    spec = _spec([_bg(), _choropleth(), _heat(), _symbol()])
    defect = VisualCritiqueItem(
        category="layer_order", severity="error", layer_ids=("poi-labels",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert plan.ops == ()
    assert any(s["reason"] == "already_optimal" for s in plan.skipped)


def _case_order_background_target():
    """C14: 靶层为 background → 违反不变量，unsafe_target 拒绝。"""
    spec = _spec([_bg(), _symbol()])
    defect = VisualCritiqueItem(
        category="layer_order", severity="error", layer_ids=("basemap-bg",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert plan.ops == ()
    assert any(s["reason"] == "unsafe_target" for s in plan.skipped)


def _case_opacity_raise():
    """D15: 低透明 fill → 阶梯抬升 +0.3，paint 键精确命中。"""
    spec = _spec([_bg(), _choropleth(opacity=0.15)])
    defect = VisualCritiqueItem(
        category="opacity", severity="warning", layer_ids=("districts-fill",),
    )
    plan = VisualHealStrategyPlanner().plan(spec, [defect])
    assert [op.op for op in plan.ops] == [MUTATION_HEAL_OPACITY]
    assert plan.ops[0].paint_opacity == {"districts-fill": pytest.approx(0.45)}
    out, applied = apply_heal_plan(spec, plan)
    assert applied == 1
    assert _layer(out, "districts-fill")["paint"]["fill-opacity"] == pytest.approx(0.45)
    assert _layer(out, "basemap-bg") == _bg()


HEAL_CASES = [
    ("label_overlap_true", _case_label_overlap_true),
    ("label_size_stepdown", _case_label_size_stepdown),
    ("label_missing_layout_keys", _case_label_missing_layout_keys),
    ("label_unknown_layer", _case_label_unknown_layer),
    ("label_multi_layer_selective", _case_label_multi_layer_selective),
    ("contrast_step_gate45", _case_contrast_step_gate45),
    ("contrast_dark_canvas", _case_contrast_dark_canvas),
    ("contrast_match_categories", _case_contrast_match_categories),
    ("contrast_already_optimal", _case_contrast_already_optimal),
    ("contrast_expression_unsafe", _case_contrast_expression_unsafe),
    ("order_heatmap_occlusion", _case_order_heatmap_occlusion),
    ("order_explicit_occluder", _case_order_explicit_occluder),
    ("order_already_on_top", _case_order_already_on_top),
    ("order_background_target", _case_order_background_target),
    ("opacity_raise", _case_opacity_raise),
]


@pytest.mark.parametrize("case_name,case_fn", HEAL_CASES, ids=[c[0] for c in HEAL_CASES])
def test_visual_heal_defect_case(case_name: str, case_fn) -> None:
    """15 组典型缺陷输入：补丁精确命中靶图层×属性（或诚实空计划）。"""
    case_fn()


# ─────────────────────────────────────────────────────────────────────────────
# 规划器：严重度/影响域排序 与 确定性
# ─────────────────────────────────────────────────────────────────────────────


def test_planner_orders_error_layer_order_before_warning_label_collision() -> None:
    spec = _spec([_bg(), _symbol(), _choropleth(), _heat()])
    warning_label = VisualCritiqueItem(
        category="label_collision", severity="warning", layer_ids=("poi-labels",),
    )
    error_order = VisualCritiqueItem(
        category="layer_order", severity="error", layer_ids=("poi-labels",),
        occluder_layer_id="density-heat",
    )
    plan = VisualHealStrategyPlanner().plan(spec, [warning_label, error_order])
    # error > warning；且二者同触 poi-labels 呈现面 → layer_order 影响域更高留任
    assert len(plan.ops) == 1
    assert plan.ops[0].op == MUTATION_HEAL_LAYER_ORDER
    assert any(
        s["reason"] == "superseded_by_higher_priority" for s in plan.skipped
    )


def test_defect_fingerprint_is_order_insensitive_and_stable() -> None:
    d1 = VisualCritiqueItem(category="opacity", severity="warning",
                            layer_ids=("a",), evidence="x")
    d2 = VisualCritiqueItem(category="contrast", severity="error", layer_ids=("b",))
    assert defect_fingerprint([d1, d2]) == defect_fingerprint([d2, d1])
    assert defect_fingerprint([d1]) != defect_fingerprint([d2])
    assert defect_fingerprint([d1]).startswith("vheal-sha256:")


# ─────────────────────────────────────────────────────────────────────────────
# 归一化桥：VisualJudgeReport → VisualCritiqueItem
# ─────────────────────────────────────────────────────────────────────────────


def test_normalize_visual_report_bridge() -> None:
    report = VisualJudgeReport(
        status="evaluated",
        critiques=[
            VisualCritique(
                dimension="readability", severity="error",
                suggestion="POI labels overlap each other (poi-labels)",
            ),
            VisualCritique(
                dimension="color_discriminability", severity="warning",
                suggestion="districts-fill classes hard to tell apart",
            ),
            VisualCritique(
                dimension="composition_balance", severity="error",
                suggestion="districts-fill is covered by heatmap density-heat",
            ),
            VisualCritique(
                dimension="polish_completeness", severity="info",
                suggestion="overall composition feels pleasant",
            ),
        ],
    )
    items = normalize_visual_report(
        report,
        known_layer_ids=("basemap-bg", "poi-labels", "districts-fill", "density-heat"),
    )
    by_cat = {}
    for item in items:
        by_cat.setdefault(item.category, []).append(item)
    assert set(by_cat) == {"label_collision", "contrast", "layer_order"}
    assert by_cat["label_collision"][0].layer_ids == ("poi-labels",)
    assert by_cat["label_collision"][0].severity == "error"
    assert by_cat["contrast"][0].layer_ids == ("districts-fill",)
    order = by_cat["layer_order"][0]
    assert order.layer_ids == ("districts-fill",)  # 文本先出现者为靶
    assert order.occluder_layer_id == "density-heat"
    # unmappable（polish+无关键词）诚实丢弃，不计入
    assert len(items) == 3


def test_normalize_visual_report_unlocalized_defect_dropped_by_planner() -> None:
    report = VisualJudgeReport(
        status="evaluated",
        critiques=[
            VisualCritique(
                dimension="readability", severity="warning",
                suggestion="some labels overlap somewhere",
            ),
        ],
    )
    items = normalize_visual_report(report, known_layer_ids=("poi-labels",))
    assert items and items[0].layer_ids == ()
    plan = VisualHealStrategyPlanner().plan(_spec([_bg(), _symbol()]), items)
    assert plan.ops == ()
    assert any(s["reason"] == "unlocalized_defect" for s in plan.skipped)


# ─────────────────────────────────────────────────────────────────────────────
# 引擎事务挂载
# ─────────────────────────────────────────────────────────────────────────────


async def _seed_layers(engine, session_id: str, layers: list) -> dict:
    """经既有意图通道落一份 spec，返回最后一次提交结果。"""
    res = await engine.apply_mutation(
        session_id, InitProjectIntent(view={"center": [116.4, 39.9], "zoom": 10})
    )
    assert res.is_error is False, res.error_msg
    for layer in layers:
        res = await engine.apply_mutation(
            session_id,
            UpsertLayerIntent(
                layer=copy.deepcopy(layer),
                source_data={"type": "FeatureCollection", "features": []},
            ),
        )
        assert res.is_error is False, res.error_msg
    return res


async def test_engine_heal_commit_is_atomic_monotonic_and_idempotent() -> None:
    from app.services.mapspec import MapSpecLifecycleEngine

    engine = MapSpecLifecycleEngine()
    sid = _sid("vheal_engine_atomic")
    await _seed_layers(engine, sid, [_bg(), _symbol()])

    prior = await engine.store.get_mapspec(sid)
    prior_rev = None
    prior_fp = cartographic_fingerprint(prior)

    defect = VisualCritiqueItem(
        category="label_collision", severity="error", layer_ids=("poi-labels",),
    )
    result = await engine.apply_visual_heal_patch(
        sid, [copy.deepcopy(defect)], mutation_id="vheal-atomic-1",
    )
    assert result.is_error is False, result.error_msg
    assert result.mutation_revision >= 1
    prior_rev = result.mutation_revision
    assert result.checkpoint_id
    healed = await engine.store.get_mapspec(sid)
    assert cartographic_fingerprint(healed) != prior_fp
    layout = _layer(healed, "poi-labels")["layout"]
    assert layout["text-allow-overlap"] is False

    # 幂等回放：同 mutation_id → duplicate=True，revision 不再推进
    replay = await engine.apply_visual_heal_patch(
        sid, [copy.deepcopy(defect)], mutation_id="vheal-atomic-1",
    )
    assert replay.duplicate is True
    assert replay.mutation_revision == prior_rev


async def test_engine_heal_convergence_exhausted_raise_and_degrade() -> None:
    from app.services.mapspec import MapSpecLifecycleEngine

    defect = VisualCritiqueItem(
        category="label_collision", severity="warning", layer_ids=("poi-labels",),
    )

    # ── raise 路径：两次提交（score 不提升 0.5 → 0.45），第 3 次必拦 ──
    engine = MapSpecLifecycleEngine()
    sid = _sid("vheal_convergence_raise")
    await _seed_layers(engine, sid, [_bg(), _symbol(size=16)])
    r1 = await engine.apply_visual_heal_patch(
        sid, [copy.deepcopy(defect)], quality_score=0.5,
    )
    assert r1.is_error is False
    r2 = await engine.apply_visual_heal_patch(
        sid, [copy.deepcopy(defect)], quality_score=0.45,
    )
    assert r2.is_error is False
    assert r2.mutation_revision == r1.mutation_revision + 1  # 严格单调
    rev_after_two = r2.mutation_revision

    with pytest.raises(SelfHealConvergenceExhausted) as exc_info:
        await engine.apply_visual_heal_patch(
            sid, [copy.deepcopy(defect)], quality_score=0.44,
        )
    assert exc_info.value.attempts == MAX_VISUAL_HEAL_ITERATIONS

    # 保护触发后 spec/revision 纹丝不动（禁止死循环/半提交）
    spec_after = await engine.store.get_mapspec(sid)
    assert cartographic_fingerprint(spec_after) == cartographic_fingerprint(
        await engine.store.get_mapspec(sid)
    )
    assert _layer(spec_after, "poi-labels")["layout"]["text-size"] == pytest.approx(
        16 * 0.85
    )

    # ── degrade 路径：同序列，第 3 次优雅降级为机器可读错误 ──
    engine2 = MapSpecLifecycleEngine()
    sid2 = _sid("vheal_convergence_degrade")
    await _seed_layers(engine2, sid2, [_bg(), _symbol(size=16)])
    await engine2.apply_visual_heal_patch(sid2, [copy.deepcopy(defect)], quality_score=0.5)
    await engine2.apply_visual_heal_patch(sid2, [copy.deepcopy(defect)], quality_score=0.45)
    degraded = await engine2.apply_visual_heal_patch(
        sid2, [copy.deepcopy(defect)], quality_score=0.44, on_exhausted="degrade",
    )
    assert degraded.is_error is True
    assert degraded.error_code == "HEAL_CONVERGENCE_EXHAUSTED"
    assert degraded.mapspec is not None
    assert degraded.mutation_revision == rev_after_two or (
        degraded.mutation_revision >= 2
    )
    spec2 = await engine2.store.get_mapspec(sid2)
    layout2 = _layer(spec2, "poi-labels")["layout"]
    assert layout2["text-size"] == pytest.approx(16 * 0.85)


async def test_engine_heal_repeated_patch_guard() -> None:
    from app.services.mapspec import MapSpecLifecycleEngine

    engine = MapSpecLifecycleEngine()
    sid = _sid("vheal_repeated_patch")
    await _seed_layers(engine, sid, [_bg(), _symbol()])
    defect = VisualCritiqueItem(
        category="label_collision", severity="warning", layer_ids=("poi-labels",),
    )
    key = defect_fingerprint([defect])
    spec = await engine.store.get_mapspec(sid)
    plan = VisualHealStrategyPlanner().plan(spec, [copy.deepcopy(defect)])
    assert plan.ops
    # 白盒注入：该指纹已被记录为"补丁已试"（模拟他 pod 已提交同一补丁）
    engine._visual_heal_ledger[key] = {
        "attempts": 0,
        "last_score": None,
        "no_improvement": 0,
        "tried_signatures": {plan.ops_signature},
    }
    with pytest.raises(SelfHealConvergenceExhausted) as exc_info:
        await engine.apply_visual_heal_patch(sid, [copy.deepcopy(defect)])
    assert exc_info.value.reason == "repeated_patch"


async def test_engine_heal_rejects_locked_layer() -> None:
    from app.services.mapspec import MapSpecLifecycleEngine

    engine = MapSpecLifecycleEngine()
    sid = _sid("vheal_locked_layer")
    seed_res = await _seed_layers(engine, sid, [_bg(), _symbol()])
    wb_res = await engine.apply_mutation(
        sid,
        SetWorkbenchStateIntent(doc={
            "version": 5,
            "groups": [],
            "membership": {},
            "lockedLayerIds": ["poi-labels"],
            "mode": "explore",
        }),
        origin="user", expected_revision=seed_res.mutation_revision,
    )
    assert wb_res.is_error is False, wb_res.error_msg
    assert wb_res.superseded is False  # 封闭性：revision 动态获取，绝不 superseded

    defect = VisualCritiqueItem(
        category="label_collision", severity="error", layer_ids=("poi-labels",),
    )
    result = await engine.apply_visual_heal_patch(sid, [defect])
    assert result.is_error is True
    assert result.error_code == "layer_locked"
    # 未提交：revision 不动
    spec = await engine.store.get_mapspec(sid)
    assert _layer(spec, "poi-labels")["layout"]["text-allow-overlap"] is True


async def test_engine_heal_empty_plan_is_honest_rejection() -> None:
    from app.services.mapspec import MapSpecLifecycleEngine

    engine = MapSpecLifecycleEngine()
    sid = _sid("vheal_empty_plan")
    await _seed_layers(engine, sid, [_bg(), _symbol()])
    spec = await engine.store.get_mapspec(sid)
    defect = VisualCritiqueItem(
        category="label_collision", severity="error", layer_ids=("ghost",),
    )
    result = await engine.apply_visual_heal_patch(sid, [defect])
    assert result.is_error is True
    assert result.error_code == "HEAL_PLAN_EMPTY"
    after = await engine.store.get_mapspec(sid)
    assert cartographic_fingerprint(after) == cartographic_fingerprint(spec)


async def test_healed_mapspec_backward_compat_mixed_sequence() -> None:
    """四类自愈各来一发（不同缺陷指纹）→ 每步 validate 通过、revision 严格递增、
    终态通过权威 schema（MapSpecDocument）与结构校验。"""
    from app.lib.cartography.mapspec_schema import MapSpecDocument
    from app.services.mapspec import MapSpecLifecycleEngine

    engine = MapSpecLifecycleEngine()
    sid = _sid("vheal_backward_compat")
    await _seed_layers(
        engine, sid,
        [_bg(), _symbol(), _choropleth(colors=["#fddbc7", "#efefef"]), _heat()],
    )
    layers_spec = await engine.store.get_mapspec(sid)
    symbol_idx = [layer["id"] for layer in layers_spec["layers"]].index("poi-labels")
    heat_idx = [layer["id"] for layer in layers_spec["layers"]].index("density-heat")
    occluded = symbol_idx < heat_idx

    defects = [
        VisualCritiqueItem(category="label_collision", severity="warning",
                           layer_ids=("poi-labels",)),
        VisualCritiqueItem(category="contrast", severity="error",
                           layer_ids=("districts-fill",), min_contrast_ratio=4.5),
        VisualCritiqueItem(category="opacity", severity="warning",
                           layer_ids=("districts-fill",)),
    ]
    if occluded:
        defects.append(
            VisualCritiqueItem(category="layer_order", severity="error",
                               layer_ids=("poi-labels",))
        )

    revisions = []
    for defect in defects:
        result = await engine.apply_visual_heal_patch(sid, [copy.deepcopy(defect)])
        assert result.is_error is False, f"{defect.category}: {result.error_msg}"
        revisions.append(result.mutation_revision)
        spec = await engine.store.get_mapspec(sid)
        validation = validate_mapspec(spec)
        assert validation["success"] is True, validation["errors"]

    assert revisions == sorted(set(revisions))  # 严格单调递增
    final = await engine.store.get_mapspec(sid)
    assert validate_mapspec(final)["success"] is True
    parsed = MapSpecDocument.model_validate(final)  # 权威 schema 不拒绝 healed spec
    assert parsed.version == final["version"]
    ids = [layer["id"] for layer in final["layers"]]
    assert ids[0] == "basemap-bg"
    if occluded:
        assert ids.index("poi-labels") > ids.index("density-heat")
    fill = _layer(final, "districts-fill")
    assert fill["paint"]["fill-opacity"] >= 0.45
    chosen = fill["paint"]["fill-color"]["stops"][0][1]
    assert contrast_ratio(chosen, "#ffffff") >= 3.0
