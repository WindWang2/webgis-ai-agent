"""Skill Library 只读工具面测试（ADR-0182 S20：工具注册 + dispatch 端到端）。"""
import pytest

from app.tools.registry import ToolRegistry
from app.tools.skill_library_tools import (
    get_skill_evidence_recorder,
    register_skill_library_tools,
    reset_skill_evidence_recorder,
)

TOOL_NAMES = ("gis_skill_search", "gis_skill_detail", "gis_skill_replay_check")


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_skill_library_tools(reg)
    return reg


@pytest.fixture(autouse=True)
def _clean_recorder():
    reset_skill_evidence_recorder()
    yield
    reset_skill_evidence_recorder()


async def _call(registry, name, **kwargs):
    assert registry.has(name), f"tool {name} not registered"
    return await registry.dispatch(name, kwargs)


class TestRegistration:
    def test_three_tools_registered(self, registry):
        names = registry.tool_names()
        for name in TOOL_NAMES:
            assert name in names

    def test_schemas_bounded(self, registry):
        for schema in registry.get_schemas_subset(set(TOOL_NAMES)):
            params = schema["function"].get("parameters") or {}
            assert "properties" in params  # typed schema（防 prompt 化红线）


class TestSearchTool:
    async def test_end_to_end(self, registry):
        payload = await _call(registry, "gis_skill_search",
                              query="成都小学分布情况")
        selection = payload["selection"]
        assert selection["selected"] == "point_distribution_analysis"
        # 证据留痕（S21）
        assert get_skill_evidence_recorder().records[0].event == "skill_selected"

    async def test_no_selection_no_evidence(self, registry):
        await _call(registry, "gis_skill_search", query="")
        assert get_skill_evidence_recorder().records == []

    async def test_clarification_surface(self, registry):
        payload = await _call(registry, "gis_skill_search",
                              query="帮我处理一下量子纠缠")
        assert payload["clarification"] is not None
        assert payload["clarification"]["reason_code"] in (
            "AMBIGUOUS_TOP_CANDIDATES", "NO_MATCH")


class TestDetailTool:
    async def test_full_projection(self, registry):
        payload = await _call(registry, "gis_skill_detail",
                              skill_id="point_distribution_analysis")
        assert "procedure" in payload["skill"]
        assert payload["truncated"] is False

    async def test_unknown(self, registry):
        payload = await _call(registry, "gis_skill_detail", skill_id="nope")
        assert "error" in payload


class TestReplayTool:
    async def test_end_to_end_with_plan_facts(self, registry):
        payload = await _call(registry, "gis_skill_replay_check",
                              skill_id="administrative_aggregation",
                              plan_facts={
                                  "capabilities": ["spatial_join",
                                                   "admin_aggregation",
                                                   "admin_boundary_query",
                                                   "thematic_cartography"],
                                  "evidence_kinds": [
                                      "subject_qc_report",
                                      "boundary_level_declaration",
                                      "join_unmatched_report",
                                      "map_expression",
                                      "product_completeness"],
                              })
        assert "missing_steps" in payload
        assert payload["skill_id"] == "administrative_aggregation"

    async def test_unknown_skill(self, registry):
        payload = await _call(registry, "gis_skill_replay_check", skill_id="nope")
        assert "error" in payload
