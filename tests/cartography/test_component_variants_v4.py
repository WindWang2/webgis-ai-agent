"""Component Modularization V4 — 组件变体/模板扩容契约测试.

锁定：
- 100 个 descriptor variants（19 类型）与 100 个 native 模板双向覆盖；
- chart_panel 的 kind 变体与 chart_kinds 词表一致（violin 不入 native）；
- 图表七态 + Agent 操作词表挂在 chart_panel descriptor 上；
- V4 字段（collision_class/states/interactions/accessibility）族内一致；
- renderer 真值矩阵仍与 descriptor 声明零漂移。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.chart_kinds import (
    AGENT_CHART_OPERATIONS,
    CHART_STATES,
    CHART_KINDS,
    chart_kind_ids,
)
from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.component_templates import (
    SEED_COMPONENT_TEMPLATES,
    get_component_template_registry,
)

pytestmark = pytest.mark.cartography

# native 可实现的图表 kind（violin planned 排除）
NATIVE_CHART_KINDS = [k for k in CHART_KINDS if k.live_engine != "planned"]


def test_v4_variant_count_target():
    reg = get_component_registry()
    total = sum(len(d.variants) for d in reg.native_descriptors())
    assert total >= 100, f"V4 变体目标 100+，实际 {total}"
    assert reg.count == 19


def test_v4_bidirectional_variant_template_coverage():
    """native 模板 variant ⊆ descriptor 词表（validate 已锁），此处锁反向：
    descriptor 词表的每个变体都有模板条目。"""
    reg = get_component_registry()
    tmpl_reg = get_component_template_registry()
    for desc in reg.native_descriptors():
        for v in desc.variants:
            hit = any(
                t.component_type == desc.type and t.variant == v
                for t in SEED_COMPONENT_TEMPLATES if t.runtime_status == "native"
            )
            assert hit, f"{desc.type}/{v} 无 native 模板条目"


def test_chart_kind_variants_align_with_registry():
    reg = get_component_registry()
    desc = reg.get_by_type("chart_panel")
    expected = sorted(
        ["default", "compact", "transparent", "report"]
        + [k.id for k in NATIVE_CHART_KINDS]
    )
    assert sorted(desc.variants) == expected
    assert "violin" not in desc.variants


def test_chart_panel_states_and_agent_operations():
    reg = get_component_registry()
    desc = reg.get_by_type("chart_panel")
    assert set(desc.states) == set(CHART_STATES)
    assert set(desc.interactions) == set(AGENT_CHART_OPERATIONS.keys())


def test_v4_collision_classes_partition():
    """碰撞类词表封闭：每类型属于五类之一；图例族/面板族/chrome 族语义合理。"""
    reg = get_component_registry()
    valid = {"chrome", "legend", "panel", "canvas", "none"}
    for d in reg.native_descriptors():
        assert d.collision_class in valid, f"{d.type}: 非法 collision_class"
    legend_types = {"legend", "categorical_legend", "continuous_colorbar"}
    for t in legend_types:
        assert reg.get_by_type(t).collision_class == "legend"
    assert reg.get_by_type("graticule").collision_class == "canvas"
    assert reg.get_by_type("export_layout").collision_class == "none"


def test_v4_accessibility_metadata_present():
    reg = get_component_registry()
    for d in reg.native_descriptors():
        assert d.accessibility.role, f"{d.type}: 缺 accessibility.role"
        assert d.accessibility.label_zh, f"{d.type}: 缺 accessibility.label_zh"


def test_no_planned_templates_remain():
    """V4 转正后种子目录不再有 planned 模板（诚实原则：planned 的能力
    在 chart_kinds / model library 层诚实登记，不留在模板目录）。"""
    planned = [t.id for t in SEED_COMPONENT_TEMPLATES if t.runtime_status == "planned"]
    assert planned == []


def test_template_count_growth():
    tmpl_reg = get_component_template_registry()
    assert tmpl_reg.count >= 100
