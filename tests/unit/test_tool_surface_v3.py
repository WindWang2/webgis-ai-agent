"""Dynamic Tool Surface V3 测试（ADR-0103）。

- 确定性：同上下文两次选择必同结果（names 顺序、指纹一致）；
- 规模控制：k_max 硬上限、k_min 不强行凑数；
- contract 过滤：tier-3 / 隐藏 / 角色副作用策略永不入面；
- capability 精确命中：检索 miss 时 algorithm registry 反查兜底；
- 可插拔语义检索：注入成功加分留痕、注入失败降级词法不阻断；
- 核心前门常在；schema 组装走 registry 真相。
"""
import pytest

from app.services.chat.tool_surface_v3 import (
    CORE_TOOL_NAMES,
    ROLE_SIDE_EFFECT_POLICY,
    DynamicToolSurface,
    ToolSelectionContext,
)


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def _ctx(**kw) -> ToolSelectionContext:
    kw.setdefault("user_message", "统计成都市各区县的小学密度并做热力图")
    return ToolSelectionContext(**kw)


def test_selection_deterministic(registry):
    surface = DynamicToolSurface(registry)
    a = surface.select(_ctx()).as_dict()
    b = surface.select(_ctx()).as_dict()
    assert a["names"] == b["names"]
    assert a["reasons"] == b["reasons"]
    assert a["dropped"] == b["dropped"]


def test_k_max_respected_and_core_present(registry):
    surface = DynamicToolSurface(registry)
    sel = surface.select(_ctx(k_max=12))
    assert len(sel.names) <= 12
    for core in CORE_TOOL_NAMES:
        assert core in sel.names, f"core tool {core} missing"


def test_tier3_never_selected(registry):
    surface = DynamicToolSurface(registry)
    sel = surface.select(_ctx(user_message="删除图层 清空数据 危险操作 optimize_route location_allocation"))
    names = set(sel.names)
    for name in names:
        desc = registry.descriptor(name)
        assert int(desc.tier) < 3
        assert desc.effective_security_tier < 3


def test_role_policy_filters_state_mutation(registry):
    surface = DynamicToolSurface(registry)
    ctx = _ctx(
        user_message="给地图加标注 加图层 改样式 display_layer add_marker",
        role="corpus_worker",
    )
    sel = surface.select(ctx)
    for name in sel.names:
        desc = registry.descriptor(name)
        assert desc.side_effect.value in ROLE_SIDE_EFFECT_POLICY["corpus_worker"], (
            f"{name} ({desc.side_effect.value}) leaked into corpus_worker surface"
        )


def test_capability_hit_backfill(registry):
    """active_capabilities 精确命中：即使词法 query 不含相关词也能入候选。"""
    surface = DynamicToolSurface(registry)
    ctx = _ctx(user_message="", active_capabilities=("spatial_interpolation",))
    sel = surface.select(ctx)
    trace = sel.selection_trace
    assert trace["capability_candidates"] >= 1
    cap_reasons = [n for n, rs in sel.reasons.items() if any(r.startswith("capability:") for r in rs)]
    assert cap_reasons, "capability-hit tools should be in surface"


def test_project_shape_and_determinism(registry):
    surface = DynamicToolSurface(registry)
    out = surface.project(_ctx(), compress="compact")
    assert out["schemas"], "projection should contain schemas"
    assert out["bytes_used"] > 0
    assert len(out["fingerprint"]) == 16
    for s in out["schemas"]:
        assert s["function"]["name"] in sel_names(out)
    # 确定性
    out2 = surface.project(_ctx(), compress="compact")
    assert out["fingerprint"] == out2["fingerprint"]


def sel_names(out):
    return {s["function"]["name"] for s in out["schemas"]}


def test_byte_budget_respected(registry):
    surface = DynamicToolSurface(registry)
    out = surface.project(_ctx(k_max=30), compress="compact")
    big = surface.project(_ctx(k_max=30, byte_budget=1500), compress="compact")
    assert big["bytes_used"] <= 1500
    assert len(big["schemas"]) < len(out["schemas"]) or out["bytes_used"] <= 1500
    dropped = [n for n, why in big["dropped"].items() if why == "byte_budget"]
    assert dropped, "budget drop should be recorded"


def test_semantic_retriever_injection(monkeypatch, registry):
    from app.services.chat import tool_surface_v3 as v3

    def fake_semantic(reg, query, top_k):
        from app.services.chat.tool_retrieval import RetrievalHit
        return [RetrievalHit(name="webgis_map_intent", score=99.0, matched=("fake",))]

    monkeypatch.setattr(v3, "_SEMANTIC_RETRIEVER_SPEC", "tests.fake:retriever")
    monkeypatch.setattr(v3, "_load_semantic_retriever", lambda: fake_semantic)
    surface = DynamicToolSurface(registry)
    sel = surface.select(_ctx())
    assert sel.retriever.startswith("semantic:")
    assert "webgis_map_intent" in sel.names


def test_semantic_retriever_failure_degrades(monkeypatch, registry):
    from app.services.chat import tool_surface_v3 as v3

    def broken(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(v3, "_SEMANTIC_RETRIEVER_SPEC", "tests.fake:retriever")
    monkeypatch.setattr(v3, "_load_semantic_retriever", lambda: broken)
    surface = DynamicToolSurface(registry)
    sel = surface.select(_ctx())
    assert sel.retriever == "lexical"
    assert sel.names, "lexical baseline must still serve"


def test_empty_context_gives_core_surface(registry):
    surface = DynamicToolSurface(registry)
    sel = surface.select(ToolSelectionContext())
    assert set(CORE_TOOL_NAMES) <= set(sel.names)
    assert len(sel.names) <= ToolSelectionContext().k_max
