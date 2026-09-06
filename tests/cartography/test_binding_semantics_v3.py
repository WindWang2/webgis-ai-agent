"""Component Binding & Multi-Layer Semantics V3 — 绑定契约测试（ADR-0101 D7）.

锁定：
- 实例 id 确定性：同输入重复 compose 的 id 集合/顺序完全稳定；
- 实例 id 唯一：MapSpec 组件 id 恢复可寻址（去重保先）；
- multi-layer：heatmap 主层 + choropleth 参考层 → colorbar + 图例并存
  （binding 级语义，各绑各层）；
- flow + admin context：流向主层 + 分类参考层；
- comparison 双主层：变化面 + 现状面各自获得独立图例；
- 主层 + 不确定性披露层绑定：uncertainty 面板随 context 而非 layer 竞争。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.composition_validation import validate_component_composition
from app.services.gis_harness.component_composer import ComponentComposer
from app.services.gis_harness.component_resolver import ComponentResolver

pytestmark = pytest.mark.cartography


def _resolve_compose(model: str, template: str, bindings: dict, layer_models: dict,
                     output: str = "interactive"):
    sel = ComponentResolver().resolve(
        composition_template_id=template, map_model_id=model,
        output_target=output,
    )
    components = ComponentComposer().compose(
        sel, title_text="绑定契约用例",
        layer_bindings=bindings, layer_model_ids=layer_models,
    )
    return sel, components


def test_instance_ids_deterministic_across_repeats() -> None:
    """同输入千次 compose：id 序列与载荷完全一致（确定性契约）。"""
    bindings = {"primary": "layer-heat", "secondary": "layer-choro"}
    layer_models = {"layer-heat": "visual_heatmap", "layer-choro": "administrative_choropleth"}
    baseline = None
    for _ in range(1000):
        _, comps = _resolve_compose("visual_heatmap", "composition.density_map", bindings, layer_models)
        snapshot = [(c.id, c.type, c.position, c.options.get("layerId")) for c in comps]
        if baseline is None:
            baseline = snapshot
        else:
            assert snapshot == baseline


def test_composed_ids_unique() -> None:
    """MapSpec 组件 id 唯一 —— lifecycle 恢复/寻址的前提。"""
    bindings = {"primary": "layer-heat", "secondary": "layer-choro", "reference": "layer-grid"}
    layer_models = {"layer-heat": "visual_heatmap", "layer-choro": "administrative_choropleth",
                    "layer-grid": "aggregate_grid"}
    _, comps = _resolve_compose("visual_heatmap", "composition.standard_analysis", bindings, layer_models)
    ids = [c.id for c in comps]
    assert len(ids) == len(set(ids)), f"实例 id 重复: {ids}"


def test_multi_layer_heatmap_plus_choropleth() -> None:
    bindings = {"primary": "layer-heat", "secondary": "layer-choro"}
    layer_models = {"layer-heat": "visual_heatmap", "layer-choro": "administrative_choropleth"}
    _, comps = _resolve_compose("visual_heatmap", "composition.standard_analysis", bindings, layer_models)
    by_layer = {}
    for c in comps:
        if c.type in ("legend", "continuous_colorbar", "categorical_legend"):
            by_layer[c.options.get("layerId")] = c.type
    assert by_layer.get("layer-heat") == "continuous_colorbar"
    assert by_layer.get("layer-choro") == "legend"
    # primary 保留旧固定 id（向后兼容既有会话）
    id_set = {c.id for c in comps}
    assert "colorbar-main" in id_set
    assert any(i.startswith("legend-") and i != "legend-main" for i in id_set)


def test_flow_with_admin_context() -> None:
    """流向主层 + 行政分类参考层：离散图例挂参考层，流向层无跨族冲突。"""
    bindings = {"primary": "layer-flow", "secondary": "layer-admin"}
    layer_models = {"layer-flow": "flow_od_arc", "layer-admin": "categorical_thematic"}
    sel, comps = _resolve_compose("flow_od_arc", "composition.flow_map_report", bindings, layer_models,
                                  output="pdf")
    legend_bindings = {
        c.options.get("layerId"): c.type
        for c in comps if c.type in ("legend", "categorical_legend", "continuous_colorbar")
    }
    assert legend_bindings.get("layer-admin") in ("categorical_legend", "legend")
    # flow 层本身不做连续/离散双挂（binding 级冲突不允许）
    flow_types = [t for lid, t in legend_bindings.items() if lid == "layer-flow"]
    assert not ({"legend", "continuous_colorbar"} <= set(flow_types))
    result = validate_component_composition(
        comps, composition_template_id=sel.composition_template_id,
        map_model_id="flow_od_arc",
        layer_ids=["layer-flow", "layer-admin"], output_target="pdf",
        layer_model_ids=layer_models,
    )
    assert result.ok, [(v.code, v.detail) for v in result.errors]


def test_comparison_two_thematic_primaries() -> None:
    """对比图（两期 choropleth）：每层独立图例，id 内嵌角色可寻址。"""
    bindings = {"primary": "layer-2020", "secondary": "layer-2025"}
    layer_models = {"layer-2020": "administrative_choropleth", "layer-2025": "administrative_choropleth"}
    _, comps = _resolve_compose("administrative_choropleth", "composition.statistical_map", bindings, layer_models)
    legends = [c for c in comps if c.type == "legend"]
    assert len(legends) == 2
    assert {c.options.get("layerId") for c in legends} == {"layer-2020", "layer-2025"}
    assert len({c.id for c in legends}) == 2


def test_primary_layer_with_uncertainty_context() -> None:
    """主层插值面 + uncertainty context：披露面板在场且不与图例族竞争。"""
    sel = ComponentResolver().resolve(
        composition_template_id="composition.risk_exposure_report",
        map_model_id="risk_exposure_classes", output_target="pdf",
        available_context=["uncertainty", "statistics"],
    )
    comps = ComponentComposer().compose(
        sel, title_text="风险披露",
        layer_bindings={"primary": "layer-risk"},
        layer_model_ids={"layer-risk": "risk_exposure_classes"},
    )
    types = [c.type for c in comps]
    assert "uncertainty_panel" in types
    assert "legend" in types
    result = validate_component_composition(
        comps, composition_template_id=sel.composition_template_id,
        map_model_id="risk_exposure_classes", layer_ids=["layer-risk"],
        output_target="pdf", layer_model_ids={"layer-risk": "risk_exposure_classes"},
    )
    assert result.ok, [(v.code, v.detail) for v in result.errors]


def test_legend_selection_per_layer_model() -> None:
    """图例选型按绑定层模型判定 —— 非主层模型决定该层图例族。"""
    reg = get_component_registry()
    compo_reg = __import__(
        "app.lib.cartography.composition_templates",
        fromlist=["get_composition_template_registry"],
    ).get_composition_template_registry()
    compo = compo_reg.get("composition.standard_analysis")
    legend_slot = next(s for s in compo.component_slots if s.id == "legend")
    assert "continuous_colorbar" in legend_slot.allowed_component_types
    assert "legend" in legend_slot.allowed_component_types
    # 两类图例的兼容域交集覆盖各自的模型（选型可判定）
    colorbar_desc = reg.get_by_type("continuous_colorbar")
    legend_desc = reg.get_by_type("legend")
    assert "visual_heatmap" in colorbar_desc.compatible_map_models
    assert "administrative_choropleth" in legend_desc.compatible_map_models
