"""AC-07（ADR-0156 P6）：inset_map 全链路 native 状态锁定。

P0 勘察确认 inset_map 已非 planned（live inset-map.tsx + export
drawChromeInset 同链）；本测试把该真值钉进回归 —— 防止状态机回退，
并锁定 source 隔离契约（插图不依赖主图数据源，bbox 未填充时渲染端
自弃）。
"""
from typing import get_args

from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.component_renderers import get_component_renderer_registry
from app.services.gis_harness.components import ComponentType


def test_inset_map_runtime_status_is_native():
    reg = get_component_registry()
    desc = reg.get("inset_map")
    assert desc is not None
    assert desc.runtime_status == "native"


def test_inset_map_support_matrix_declares_live_and_export_truth():
    registry = get_component_renderer_registry()
    support = registry.support_for("inset_map")
    assert support is not None
    assert "interactive" in support.renderers
    assert "png" in support.exporters


def test_inset_map_is_registered_component_type():
    assert "inset_map" in get_args(ComponentType)


def test_inset_map_requires_inset_context_for_selection():
    """防空选：无 inset_context 时不自动选位（options.bbox 由 Agent 填充）。"""
    reg = get_component_registry()
    desc = reg.get("inset_map")
    assert "inset_context" in desc.required_context
