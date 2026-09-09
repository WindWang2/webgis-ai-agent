"""Epic 11 —— 方法知识工具面契约（注册 + 只读 + bounded 输出）。"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    reg = ToolRegistry()
    init_tools(reg)
    return reg


KNOWLEDGE_TOOLS = (
    "gis_task_classify",
    "gis_method_qualify",
    "gis_method_rank",
    "gis_method_explain",
    "gis_template_plan",
    "gis_component_query",
)


def test_all_knowledge_tools_registered(registry) -> None:
    for name in KNOWLEDGE_TOOLS:
        assert registry.has(name), name


def test_tools_are_tier1_read_only(registry) -> None:
    for name in KNOWLEDGE_TOOLS:
        meta = registry.metadata(name)
        assert meta is not None, name
        assert meta.get("side_effect") in ("pure", None, ""), name


def test_classify_tool_contract(registry) -> None:
    fn = registry._tools["gis_task_classify"]
    out = fn(query="分析成都便利店的空间密度")
    assert out["primary_category"] == "density"
    garbage = fn(query="dfkahsdflkh")
    assert garbage["abstain"] is True


def test_rank_tool_abstains_on_garbage(registry) -> None:
    fn = registry._tools["gis_method_rank"]
    out = fn(query="dfkahsdflkh")
    assert out["abstained"] is True
    assert out["selected"] is None


def test_explain_tool_does_not_leak_graph(registry) -> None:
    """解释面不得暴露图结构（只有语义字段与出处投影）。"""
    fn = registry._tools["gis_method_explain"]
    out = fn(method_id="interp.ordinary_kriging")
    assert out["known"] is True
    forbidden = {"edges", "nodes", "adjacency", "graph"}
    assert not (set(out.keys()) & forbidden)
    if out.get("provenance"):
        assert set(out["provenance"].keys()) <= {
            "provenance_id", "source_kind", "source_ref", "confidence"}


def test_qualify_tool_bounded(registry) -> None:
    fn = registry._tools["gis_method_qualify"]
    out = fn(method_id="density.kernel_surface",
             profile={"geometryTypes": ["Polygon"], "featureCount": 100})
    assert out["status"] == "rejected"
    assert len(out["dimensions"]) <= 8


def test_template_plan_tool(registry) -> None:
    fn = registry._tools["gis_template_plan"]
    out = fn(method_id="interp.ordinary_kriging",
             category_id="interpolation")
    assert out["base"]
    slots = [s["component"] for s in out["slots"]]
    assert "uncertainty_panel" in slots
