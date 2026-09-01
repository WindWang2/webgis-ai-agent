"""Runtime V4 Tool Surface compiler 契约测试（§28-30）。

不变量：
- 纯派生：同输入必同输出，无 IO；
- 不是 planner：不产生步骤、不持久化，只派生偏好；
- preferred 是**预算豁免**不是隐藏面：关键词/tier-1 行为不变；
- 有界：preferred/hidden/allowed_domains 全部有限集；
- 预算合同：preferred 工具在预算截断时幸存，schema 字节有界。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pytest

from app.services.gis_harness.tool_surface import (
    PHASE_ASSEMBLY,
    PHASE_FINAL,
    PHASE_PLANNING,
    ToolSurface,
    compile_tool_surface,
)


@dataclass
class FakeStep:
    n: int
    goal: str = ""
    tool_family: Optional[str] = None
    done: bool = False
    tool_binding: Optional[List[str]] = None


@dataclass
class FakePlan:
    intent: str = "map"
    domains: List[str] = field(default_factory=list)
    steps: List[FakeStep] = field(default_factory=list)
    gis_intent: Optional[dict] = None
    recipe_id: str = ""


class TestPhaseDerivation:
    def test_no_plan_is_planning(self):
        surface = compile_tool_surface(plan=None)
        assert surface.phase == PHASE_PLANNING
        assert isinstance(surface, ToolSurface)

    def test_no_steps_is_planning(self):
        surface = compile_tool_surface(plan=FakePlan(steps=[]))
        assert surface.phase == PHASE_PLANNING

    def test_current_step_family_drives_phase(self):
        plan = FakePlan(steps=[
            FakeStep(n=1, tool_family="osm", done=True),
            FakeStep(n=2, tool_family="statistics"),  # 当前步骤
            FakeStep(n=3, tool_family="mapspec"),
        ])
        surface = compile_tool_surface(plan=plan)
        assert surface.phase == "analysis"
        assert any("family=statistics" in e for e in surface.evidence)

    def test_all_done_is_final(self):
        plan = FakePlan(steps=[FakeStep(n=1, tool_family="osm", done=True)])
        surface = compile_tool_surface(plan=plan)
        assert surface.phase == PHASE_FINAL
        assert "webgis_map_product" in surface.preferred_tools

    def test_unknown_family_is_planning(self):
        plan = FakePlan(steps=[FakeStep(n=1, tool_family="quantum_gis")])
        surface = compile_tool_surface(plan=plan)
        assert surface.phase == PHASE_PLANNING
        assert "unmapped" in surface.evidence[0]

    def test_assembly_prefers_product_front_doors(self):
        plan = FakePlan(steps=[FakeStep(n=2, tool_family="mapspec")])
        surface = compile_tool_surface(plan=plan)
        assert surface.phase == PHASE_ASSEMBLY
        for name in ("webgis_map_product", "webgis_component_catalog", "webgis_component_update"):
            assert name in surface.preferred_tools
        assert {"mapspec", "report"} <= set(surface.allowed_domains)

    def test_product_status_overrides_steps(self):
        plan = FakePlan(steps=[FakeStep(n=1, tool_family="osm")])
        assert compile_tool_surface(plan=plan, product_status="needs_repair").phase == PHASE_ASSEMBLY
        done = FakePlan(steps=[FakeStep(n=1, tool_family="osm", done=True)])
        assert compile_tool_surface(plan=done, product_status="complete").phase == PHASE_FINAL


class TestPurityAndBounds:
    def test_same_inputs_same_output(self):
        plan = FakePlan(steps=[FakeStep(n=1, tool_family="raster")])
        a = compile_tool_surface(plan=plan)
        b = compile_tool_surface(plan=plan)
        assert a == b

    def test_registry_meta_prunes_fictional_tools(self):
        plan = FakePlan(steps=[FakeStep(n=1, tool_family="mapspec")])
        surface = compile_tool_surface(
            plan=plan,
            registry_meta={"webgis_map_product": {"tier": 2}},  # 只有这一个
        )
        assert surface.preferred_tools == frozenset({"webgis_map_product"})
        assert any("pruned" in e for e in surface.evidence)

    def test_hidden_empty_by_safety_doctrine(self):
        surface = compile_tool_surface(plan=FakePlan(steps=[FakeStep(n=1, tool_family="osm")]))
        assert surface.hidden_tools == frozenset()

    def test_projection_line_is_bounded(self):
        surface = compile_tool_surface(plan=None)
        line = surface.projection_line()
        assert line.startswith("[ToolSurface]")
        assert len(line) < 200

    def test_fallback_is_self_rescue_channel(self):
        assert "list_available_tools" in compile_tool_surface(plan=None).fallback_tools


class TestCatalogIntegration:
    """surface 与 ToolCatalog 的集成合同（预算豁免 + 域并集）。"""

    def _catalog(self):
        from app.tools.registry import ToolRegistry
        from app.tools.registry import tool as tool_dec
        from app.services.tool_catalog import ToolCatalog

        registry = ToolRegistry()
        for i in range(40):
            tool_dec(
                registry, tier=2, domains=["statistics"], name=f"stat_tool_{i:02d}",
                description=f"statistics filler {i}",
            )(lambda **kw: {})
        tool_dec(
            registry, tier=2, domains=["report"], name="webgis_map_product",
            description="product front door",
        )(lambda **kw: {})
        return ToolCatalog(registry, sticky_ttl=0), registry

    def test_preferred_survives_budget_cut(self, monkeypatch):
        catalog, registry = self._catalog()
        # 压缩预算迫使截断：40 个 statistics 工具远超 1KB。
        monkeypatch.setattr("app.services.tool_catalog._TIER2_SCHEMA_BUDGET_BYTES", 1024)
        from app.services.gis_harness.tool_surface import compile_tool_surface

        plan = FakePlan(steps=[FakeStep(n=1, tool_family="mapspec")])
        surface = compile_tool_surface(plan=plan, registry_meta=registry.all_metadata())

        without = catalog.select_schemas("分布", session_id=None)
        with_surface = catalog.select_schemas(
            "分布", session_id=None, surface=surface,
        )
        names_without = {s["function"]["name"] for s in without}
        names_with = {s["function"]["name"] for s in with_surface}
        # 无 surface：前门被预算挤出（report 域未命中关键词）。
        assert "webgis_map_product" not in names_without
        # 有 surface：前门幸存（预算豁免）。
        assert "webgis_map_product" in names_with

    def test_surface_additive_not_replace(self, monkeypatch):
        """关键词命中的域不被 surface 移除（安全网语义）。"""
        catalog, registry = self._catalog()
        surface = compile_tool_surface(
            plan=FakePlan(steps=[FakeStep(n=1, tool_family="osm")]),
        )
        schemas = catalog.select_schemas("热点 聚类 统计", session_id=None, surface=surface)
        names = {s["function"]["name"] for s in schemas}
        assert any(n.startswith("stat_tool_") for n in names)


@pytest.mark.unit
class TestSchemaBudgetBounded:
    def test_surface_never_unbounds_schema_bytes(self, monkeypatch):
        """预算合同：preferred 只豁免清单内的前门工具，不放开总预算。"""
        from app.tools.registry import ToolRegistry
        from app.tools.registry import tool as tool_dec
        from app.services.tool_catalog import ToolCatalog

        registry = ToolRegistry()
        for i in range(30):
            tool_dec(
                registry, tier=2, domains=["report"], name=f"report_tool_{i:02d}",
                description=f"report filler {i}",
            )(lambda **kw: {})
        tool_dec(
            registry, tier=2, domains=["report"], name="webgis_map_product",
            description="product front door",
        )(lambda **kw: {})
        catalog = ToolCatalog(registry, sticky_ttl=0)
        monkeypatch.setattr(
            "app.services.tool_catalog._TIER2_SCHEMA_BUDGET_BYTES", 2048,
        )
        plan = FakePlan(steps=[FakeStep(n=1, tool_family="mapspec")])
        surface = compile_tool_surface(plan=plan)
        schemas = catalog.select_schemas("制图 报告", session_id=None, surface=surface)
        names = [s["function"]["name"] for s in schemas]
        assert "webgis_map_product" in names
        tier2_non_preferred = [n for n in names if n.startswith("report_tool_")]
        # 非前门的 report 工具仍受预算约束（不会被全量纳入）。
        assert len(tier2_non_preferred) < 30
