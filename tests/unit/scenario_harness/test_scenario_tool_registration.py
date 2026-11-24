"""G05 工具注册契约：8 个 webgis_scenario_* 工具在**完整装配**中注册成功。

价值：真实注册器的词表门禁（side_effect/data_mutations 等）只有全量装配才触发
—— 单模块注册通过 ≠ 全装配通过（曾抓出 read_only/mapspec 两个非法词）。
"""
from __future__ import annotations

import importlib

from app.tools import _TOOL_MODULES
from app.tools.registry import ToolRegistry

SCENARIO_TOOLS = {
    "webgis_scenario_inspect",
    "webgis_scenario_propose",
    "webgis_scenario_execute",
    "webgis_scenario_compare",
    "webgis_scenario_adopt",
    "webgis_scenario_abandon",
    "webgis_scenario_lock",
    "webgis_scenario_unlock",
}


def test_scenario_tools_register_in_full_assembly():
    registry = ToolRegistry()
    for mod_name, fn_name in _TOOL_MODULES:
        mod = importlib.import_module(mod_name)
        getattr(mod, fn_name)(registry)
    registered = set(registry.tool_names()) & SCENARIO_TOOLS
    assert registered == SCENARIO_TOOLS, sorted(SCENARIO_TOOLS - registered)


def test_scenario_tool_module_in_wiring_table():
    modules = [m for m, _ in _TOOL_MODULES]
    assert "app.tools.scenario_harness_tools" in modules
