"""Tool Retrieval V4 测试（ADR-0104 决策 5）。

覆盖面（Wave 4 验收）：
- 富化词料：已声明字段进语料；未声明字段贡献为零（不虚构）；
- rerank：phase / artifact-type / CRS / scale / 失败反馈 / 续作 / 确定性 /
  预算各信号在单场景中确实移动期望工具的相对序；分量与理由留痕；
- 选择上下文缺席 → V3 行为逐位一致（306 语料 golden 全量对比，含
  reasons/trace 的 as_dict 深比较）；
- kill switch（GIS_TOOL_RETRIEVAL_V4=0）→ 会话信号不可达，投影字节一致；
- tier-3 永不入面（含 continuation/fallback 注入路径）；破坏性确认仍只在
  dispatch 期；
- 索引失效：description-only 编辑必然重建词料（audit gap #9 修复）。
"""
import asyncio

import pytest

from app.services.chat.tool_retrieval import (
    ToolLexicon,
    ToolRetrievalIndex,
    rank_tools,
    v4_retrieval_enabled,
)
from app.services.chat.tool_surface_v3 import (
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


@pytest.fixture()
def _v4_on(monkeypatch):
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V4", "1")


@pytest.fixture()
def _v4_off(monkeypatch):
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V4", "0")


# ---------------------------------------------------------------------------
# 合成注册表（隔离场景；真实 registry 只用于 golden / 安全不变式）
# ---------------------------------------------------------------------------

def _mini_registry(**tools) -> "ToolRegistry":
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    for name, kw in tools.items():
        kw = dict(kw)
        func = kw.pop("func", None) or (lambda: {"ok": True})
        reg.register(name=name, description=kw.pop("description", name), func=func, tier=1, **kw)
    return reg


# ---------------------------------------------------------------------------
# (a) 富化词料
# ---------------------------------------------------------------------------

def test_enriched_lexicon_includes_declared_fields(registry):
    lex = ToolLexicon.build(registry.descriptor("query_local_poi"))
    assert lex.example_tokens, "declared examples must enter the lexicon"
    assert "锦江" in lex.example_tokens  # CJK bigram from examples
    assert lex.anti_tokens, "declared anti_examples must enter the lexicon"
    assert lex.semantic_tags or lex.failure_tokens, "declared semantic fields expected"


def test_enriched_lexicon_semantic_tags_from_declarations():
    reg = _mini_registry(
        big_tool=dict(
            description="chart tool", tags=("bigly",),
            scale_class="large", crs_semantics="wgs84",
            output_semantic_type="chart", side_effect="pure",
        ),
    )
    lex = ToolLexicon.build(reg.descriptor("big_tool"))
    for tag in ("large", "wgs84", "chart", "pure"):
        assert tag in lex.semantic_tags, f"{tag} should be a semantic tag"
    assert lex.example_tokens == () and lex.anti_tokens == () and lex.failure_tokens == ()


def test_absent_fields_contribute_nothing():
    reg = _mini_registry(bare_tool=dict(description="plain tool", tags=("plainly",)))
    lex = ToolLexicon.build(reg.descriptor("bare_tool"))
    assert lex.semantic_tags == ()
    assert lex.example_tokens == ()
    assert lex.anti_tokens == ()
    assert lex.failure_tokens == ()
    # 未声明 scale/crs → 查询 "large"/"wgs84" 不得命中（无虚构）
    assert rank_tools(reg, "large wgs84 chart", min_score=1.0, enriched=True) == []


def test_semantic_tag_match_only_under_v4_enrichment():
    reg = _mini_registry(
        big_tool=dict(description="chart tool", scale_class="large", side_effect="pure"),
    )
    hits_v4 = rank_tools(reg, "large", min_score=1.0, enriched=True)
    hits_v3 = rank_tools(reg, "large", min_score=1.0, enriched=False)
    assert [h.name for h in hits_v4] == ["big_tool"]
    assert hits_v3 == [], "V3 math must ignore enriched corpora"


def test_anti_examples_downrank_is_bounded_and_explained():
    reg = _mini_registry(
        anti_pro=dict(description="probe", tags=("antiquery",),
                      anti_examples=("不要用 antiquery 关键词",)),
        clean_pro=dict(description="probe", tags=("antiquery",)),
    )
    hits = rank_tools(reg, "antiquery", min_score=1.0, enriched=True)
    assert hits[0].name == "clean_pro", "anti-evidence must downrank anti_pro"
    anti_hit = next(h for h in hits if h.name == "anti_pro")
    assert anti_hit.anti_matched == ("antiquery",)
    # V3 打分下无负证据，双方同分 → 名序 anti_pro 在前
    hits_v3 = rank_tools(reg, "antiquery", min_score=1.0, enriched=False)
    assert [h.name for h in hits_v3] == ["anti_pro", "clean_pro"]


# ---------------------------------------------------------------------------
# (d) 索引失效：description-only 编辑
# ---------------------------------------------------------------------------

def test_index_invalidation_on_description_only_edit():
    reg = _mini_registry(desc_tool=dict(description="alpha beta"))

    def _rebind():  # 同名重注册需新函数对象
        return {"ok": True}

    idx = ToolRetrievalIndex()
    assert idx.rank(reg, "gamma", min_score=1.0) == []
    key_before = idx._fingerprint
    rf_before = reg.registry_fingerprint()
    # description-only 编辑：同名同 schema 重注册（schema 指纹不变）
    reg.register(name="desc_tool", description="alpha beta gamma",
                 func=_rebind, tier=1)
    assert reg.registry_fingerprint() == rf_before, (
        "sanity: schema-only registry fingerprint must be blind to description edits"
    )
    idx.build_if_stale(reg)
    assert idx._fingerprint != key_before, "index key must include descriptor fingerprints"
    hits = idx.rank(reg, "gamma", min_score=1.0)
    assert [h.name for h in hits] == ["desc_tool"]
    # 未变化的 registry 不重建
    key_stable = idx._fingerprint
    idx.build_if_stale(reg)
    assert idx._fingerprint == key_stable


def test_rank_tools_default_follows_kill_switch(monkeypatch):
    reg = _mini_registry(big_tool=dict(description="chart tool", scale_class="large"))
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V4", "0")
    assert rank_tools(reg, "large", min_score=1.0) == []
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V4", "1")
    assert [h.name for h in rank_tools(reg, "large", min_score=1.0)] == ["big_tool"]
    assert v4_retrieval_enabled() is True


# ---------------------------------------------------------------------------
# (b) rerank 信号逐项移动相对序（全部在合成 registry 上隔离验证）
# ---------------------------------------------------------------------------

def _select(reg, **kw):
    return DynamicToolSurface(reg).select(ToolSelectionContext(**kw))


def test_rerank_phase_fit_moves_preferred_tool_up(registry, _v4_on):
    ctx_kw = dict(user_message="导出地图产品 输出图件", k_max=20)
    sel_assembly = _select(registry, **ctx_kw, workflow_stage="assembly")
    sel_planning = _select(registry, **ctx_kw, workflow_stage="planning")
    comp = sel_assembly.score_components.get("webgis_map_product", {})
    assert comp.get("phase_preferred") == pytest.approx(3.0)
    assert "phase_preferred" not in sel_planning.score_components.get(
        "webgis_map_product", {})
    assert sel_assembly.names.index("webgis_map_product") < sel_planning.names.index(
        "webgis_map_product")
    reason = next(r for r in sel_assembly.reasons["webgis_map_product"]
                  if r.startswith("rerank("))
    assert "phase_preferred" in reason


def test_rerank_artifact_type_match():
    reg = _mini_registry(
        art_consumer=dict(description="probe", tags=("artfit",),
                          input_artifacts=("geojson_fc",)),
        art_other=dict(description="probe", tags=("artfit",)),
    )
    sel = _select(reg, user_message="artfit", k_max=10,
                  session_artifact_types=("geojson_fc",))
    assert sel.names[0] == "art_consumer"
    assert sel.score_components["art_consumer"]["artifact_type"] == pytest.approx(2.0)
    sel_none = _select(reg, user_message="artfit", k_max=10)
    assert sel_none.names.index("art_other") < sel_none.names.index("art_consumer")


def test_rerank_crs_semantics_compatibility_moves_both_ways():
    reg = _mini_registry(
        crs_tool_w=dict(description="probe", tags=("crsfit",), crs_semantics="wgs84"),
        crs_tool_g=dict(description="probe", tags=("crsfit",), crs_semantics="gcj02"),
    )
    sel_w = _select(reg, user_message="crsfit", k_max=10,
                    session_artifact_types=("crs:wgs84",))
    assert sel_w.names[0] == "crs_tool_w"
    assert sel_w.score_components["crs_tool_w"]["crs_match"] == pytest.approx(0.5)
    assert sel_w.score_components["crs_tool_g"]["crs_mismatch"] == pytest.approx(-1.0)
    sel_g = _select(reg, user_message="crsfit", k_max=10,
                    session_artifact_types=("crs:gcj02",))
    assert sel_g.names[0] == "crs_tool_g"


def test_rerank_scale_class_fit():
    reg = _mini_registry(
        scale_tool_s=dict(description="probe", tags=("scfit",), scale_class="small"),
        scale_tool_l=dict(description="probe", tags=("scfit",), scale_class="large"),
    )
    sel_l = _select(reg, user_message="scfit", k_max=10,
                    session_artifact_types=("scale:large",))
    assert sel_l.names[0] == "scale_tool_l"
    assert sel_l.score_components["scale_tool_s"]["scale_mismatch"] == pytest.approx(-1.0)
    sel_s = _select(reg, user_message="scfit", k_max=10,
                    session_artifact_types=("scale:small",))
    assert sel_s.names[0] == "scale_tool_s"


def test_rerank_prior_failure_downrank_and_fallback_boost(_v4_on):
    reg = _mini_registry(
        flaky_analyzer=dict(description="probe", tags=("failfit",),
                            fallback_tool="robust_analyzer"),
        robust_analyzer=dict(description="probe", tags=("failfit",)),
    )
    outcomes = ({"tool": "flaky_analyzer", "ok": False, "failure_class": "timeout"},
                {"tool": "flaky_analyzer", "ok": False, "failure_class": "timeout"})
    sel = _select(reg, user_message="failfit", k_max=10, recent_tool_outcomes=outcomes)
    assert sel.names[0] == "robust_analyzer"
    assert sel.score_components["flaky_analyzer"]["prior_failure"] == pytest.approx(-4.0)
    assert sel.score_components["robust_analyzer"]["fallback_boost"] == pytest.approx(1.5)
    # 失败工具只降不剔
    assert "flaky_analyzer" in sel.names
    # 单次失败 → -2
    sel1 = _select(reg, user_message="failfit", k_max=10,
                   recent_tool_outcomes=({"tool": "flaky_analyzer",
                                          "ok": False, "failure_class": "timeout"},))
    assert sel1.score_components["flaky_analyzer"]["prior_failure"] == pytest.approx(-2.0)
    assert any("fallback_of:flaky_analyzer" in r
               for r in sel1.reasons["robust_analyzer"])


def test_rerank_continuation_boost_and_injection(_v4_on):
    reg = _mini_registry(
        cont_tool=dict(description="continuation probe tool"),
        other_tool=dict(description="unrelated matter"),
    )
    sel = _select(reg, user_message="unrelated matter", k_max=10,
                  continuation_tools=("cont_tool",))
    assert "cont_tool" in sel.names, "continuation candidates must be injected"
    assert sel.score_components["cont_tool"]["continuation"] == pytest.approx(2.5)
    assert any(r.startswith("rerank_candidate:continuation")
               for r in sel.reasons["cont_tool"])
    sel_none = _select(reg, user_message="unrelated matter", k_max=10)
    assert "cont_tool" not in sel_none.names


def test_rerank_deterministic_preference(_v4_on):
    reg = _mini_registry(
        aaa_tool=dict(description="probe", tags=("detfit",), deterministic=False),
        zzz_tool=dict(description="probe", tags=("detfit",), deterministic=True),
    )
    ctx_kw = dict(user_message="detfit", k_max=10, workflow_stage="analysis")
    sel = _select(reg, **ctx_kw)
    assert sel.names[0] == "zzz_tool", "declared deterministic=True gets the mild boost"
    assert sel.score_components["zzz_tool"]["deterministic"] == pytest.approx(0.25)
    assert "deterministic" not in sel.score_components.get("aaa_tool", {})


def test_rerank_budget_pressure_downranks_heavy():
    reg = _mini_registry(
        slow_tool=dict(description="probe", tags=("budfit",),
                       latency_class="slow", memory_class="heavy"),
        fast_tool=dict(description="probe", tags=("budfit",), latency_class="fast"),
    )
    tight = _select(reg, user_message="budfit", k_max=10, byte_budget=1000)
    assert tight.names[0] == "fast_tool"
    assert tight.score_components["slow_tool"]["budget_heavy"] == pytest.approx(-2.0)
    assert tight.score_components["fast_tool"]["budget_light"] == pytest.approx(0.25)
    loose = _select(reg, user_message="budfit", k_max=10, byte_budget=100_000)
    assert "budget_heavy" not in loose.score_components.get("slow_tool", {})


def test_score_components_bounded_and_explained(registry, _v4_on):
    sel = _select(registry, user_message="统计成都市各区县的小学密度并做热力图",
                  workflow_stage="analysis", k_max=30)
    assert len(sel.score_components) <= 64
    for tool, comp in sel.score_components.items():
        assert len(comp) <= 8
        assert tool in sel.reasons
        assert any(r.startswith("rerank(") for r in sel.reasons[tool])
    d = sel.as_dict()
    assert d["score_components"].keys() == sel.score_components.keys()


# ---------------------------------------------------------------------------
# (c) 上下文缺席 → V3 行为逐位一致（golden 全量对比，306 语料）
# ---------------------------------------------------------------------------

def test_selection_context_absent_matches_v3_golden(registry, monkeypatch):
    from app.evaluation.case_matrix import build_matrix_cases
    from app.evaluation.golden_cases import GOLDEN_CASES

    surface = DynamicToolSurface(registry)
    cases = [*GOLDEN_CASES, *build_matrix_cases()]
    assert cases, "golden corpus must be present"
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V4", "1")
    v4_results = [
        surface.select(ToolSelectionContext(
            user_message=c.query,
            active_capabilities=tuple(c.expected_capabilities),
            k_max=30,
        )).as_dict()
        for c in cases
    ]
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V4", "0")
    v3_results = [
        surface.select(ToolSelectionContext(
            user_message=c.query,
            active_capabilities=tuple(c.expected_capabilities),
            k_max=30,
        )).as_dict()
        for c in cases
    ]
    assert v4_results == v3_results, (
        "absent V4 context must reproduce V3 behavior bit-for-bit "
        "(names, reasons, dropped, trace)"
    )


# ---------------------------------------------------------------------------
# Kill switch：会话信号不可达 + 投影字节一致
# ---------------------------------------------------------------------------

def test_kill_switch_session_signals_unreachable(registry, monkeypatch):
    surface = DynamicToolSurface(registry)
    signals = dict(
        workflow_stage="analysis",
        session_artifact_types=("geojson_fc", "crs:wgs84", "scale:large"),
        recent_tool_outcomes=({"tool": "heatmap_data", "ok": False,
                               "failure_class": "timeout"},),
        continuation_tools=("buffer_analysis",),
    )
    plain = dict(user_message="统计成都市各区县的小学密度并做热力图", k_max=20)

    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V4", "0")
    off_plain = surface.select(ToolSelectionContext(**plain)).as_dict()
    off_signals = surface.select(ToolSelectionContext(**plain, **signals)).as_dict()
    assert off_plain == off_signals, "kill switch must make session signals unreachable"
    assert "rerank" not in off_signals["selection_trace"]
    assert off_signals["score_components"] == {}

    proj_plain = surface.project(ToolSelectionContext(**plain, byte_budget=24576))
    proj_signals = surface.project(
        ToolSelectionContext(**plain, byte_budget=24576, **signals))
    assert proj_plain["schemas"] == proj_signals["schemas"]
    assert proj_plain["fingerprint"] == proj_signals["fingerprint"]
    assert proj_plain["bytes_used"] == proj_signals["bytes_used"]


def test_kill_switch_overrides_session_signals(registry, monkeypatch):
    """信号在场但 kill switch 关 → 仍精确 V3（开关优先于证据门）。"""
    surface = DynamicToolSurface(registry)
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V4", "0")
    ctx = ToolSelectionContext(
        user_message="给地图加图层并出图", workflow_stage="assembly", k_max=15,
        continuation_tools=("buffer_analysis",),
    )
    sel = surface.select(ctx)
    assert "rerank" not in sel.selection_trace
    assert sel.score_components == {}
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V4", "1")
    sel_on = surface.select(ctx)
    assert "rerank" in sel_on.selection_trace


# ---------------------------------------------------------------------------
# (f) 安全不变式：tier-3 永不入面；破坏性确认只在 dispatch 期
# ---------------------------------------------------------------------------

def _tier3_tool(registry) -> str:
    for name in registry.list_tools():
        if int(registry.descriptor(name).tier) >= 3:
            return name
    pytest.skip("no tier-3 tool in registry")


def test_tier3_never_leaks_via_v4_signals(registry, _v4_on):
    t3 = _tier3_tool(registry)
    surface = DynamicToolSurface(registry)
    hostile = [
        dict(user_message="委派子代理 批量任务", workflow_stage="analysis",
             continuation_tools=(t3,)),
        dict(user_message="危险操作 清空", session_artifact_types=("crs:wgs84",),
             continuation_tools=(t3, t3)),
        dict(user_message="分析数据",
             recent_tool_outcomes=({"tool": t3, "ok": False,
                                    "failure_class": "timeout"},)),
    ]
    for kw in hostile:
        sel = surface.select(ToolSelectionContext(k_max=30, **kw))
        assert t3 not in sel.names, f"tier-3 {t3} leaked via {kw}"
        for name in sel.names:
            desc = registry.descriptor(name)
            assert int(desc.tier) < 3 and desc.effective_security_tier < 3


def test_destructive_tool_still_requires_dispatch_confirmation(registry, _v4_on):
    from app.tools.descriptor import ToolStatus

    t3 = _tier3_tool(registry)
    desc = registry.descriptor(t3)
    assert desc.requires_confirmation, "descriptor honesty must be untouched"
    assert desc.status is not ToolStatus.PLANNED
    # ranking 阶段从不授予确认：dispatch 无 confirm_tier3 必拒
    async def _attempt():
        return await registry.dispatch(t3, {})

    try:
        asyncio.run(_attempt())
        raised = None
    except Exception as e:  # noqa: BLE001
        raised = e
    assert raised is not None, "tier-3 dispatch without confirmation must refuse"


# ---------------------------------------------------------------------------
# 确定性
# ---------------------------------------------------------------------------

def test_v4_selection_deterministic(registry, _v4_on):
    surface = DynamicToolSurface(registry)
    kw = dict(user_message="统计成都市各区县的小学密度并做热力图",
              workflow_stage="analysis",
              session_artifact_types=("geojson_fc", "scale:medium"),
              recent_tool_outcomes=({"tool": "voronoi_polygons", "ok": False,
                                    "failure_class": "invalid_args"},),
              continuation_tools=("heatmap_data",),
              k_max=20)
    a = surface.select(ToolSelectionContext(**kw)).as_dict()
    b = surface.select(ToolSelectionContext(**kw)).as_dict()
    assert a == b
