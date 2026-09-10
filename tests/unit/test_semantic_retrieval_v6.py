"""Semantic Retrieval V6 单元测试（ADR-0119 D1/D2）。

覆盖：双语扩展词表（确定性/有界/不回环）、capability 别名（全部指向
真实能力）、方法论证据（方法优先级加权）、否定反证、置信度校准与
弃权、kill switch 行为。
"""
import pytest

from app.services.chat.semantic_retrieval import (
    ABSTAIN_THRESHOLD,
    _CAPABILITY_ALIASES,
    compute_confidence,
    expand_query_terms,
    hybrid_signals,
    methodology_signal_tools,
    negation_anti_terms,
    v6_retrieval_enabled,
)


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    init_tools(reg)
    return reg


def test_expand_query_terms_deterministic_bounded():
    a = expand_query_terms("用克里金把土壤数据铺成面")
    b = expand_query_terms("用克里金把土壤数据铺成面")
    assert a == b and len(a) <= 24
    assert "kriging" in a
    assert expand_query_terms("") == ()
    assert expand_query_terms("   ") == ()


def test_expand_query_no_recursion_loop():
    # 扩展词不回环再扩展（一层桥接）
    terms = expand_query_terms("缓冲区")
    assert "buffer" in terms
    # 二次调用扩展词不再引出新词表爆炸
    again = expand_query_terms(" ".join(terms))
    assert len(again) <= 24


def test_capability_aliases_all_exist():
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    caps = set(get_algorithm_registry().capability_tool_map().keys())
    missing = [c for _, c in _CAPABILITY_ALIASES if c not in caps]
    assert missing == [], f"悬空 capability 别名: {missing}"


def test_alias_hits_boost_correct_tools():
    sig = hybrid_signals(None, "给门店图层生成5公里直线服务范围圈")
    assert sig.boosts, "直线服务范围别名必须命中 geometry_buffer 通道"
    sig2 = hybrid_signals(None, "按行政区统计每个区的平均海拔")
    assert "zonal_statistics" in sig2.capability_alias_hits
    assert "zonal_stats" in sig2.boosts


def test_methodology_signal_priority_weighting():
    fam, boosts = methodology_signal_tools("做空间插值铺连续表面")
    assert fam
    assert boosts
    # 加成分量有界且可排序
    assert all(0 < w <= 3.0 for w in boosts.values())
    assert len(boosts) <= 12


def test_methodology_zero_hit_is_empty():
    fam, boosts = methodology_signal_tools("xyzzy nothing matches here 12345")
    assert fam == "" and boosts == {}


def test_negation_anti_terms():
    terms = negation_anti_terms("只要空间分布的密度聚类，没有时间信息")
    assert any("时间" in t or t in ("time", "temporal", "时空",
                                    "spatiotemporal") for t in terms)
    # 线索后无短语 → 空（「没有」句尾）
    assert negation_anti_terms("普通查询没有") == ()
    assert negation_anti_terms("") == ()


def test_confidence_calibration_and_abstention():
    # 空候选 → 必弃权
    conf, abstain, reason = compute_confidence([], channels_for_top=0)
    assert conf == 0.0 and abstain and reason == "no_candidates"
    # 强证据 → 高置信度不弃权
    conf, abstain, _ = compute_confidence(
        [("a", 15.0), ("b", 2.0)], channels_for_top=3)
    assert conf > ABSTAIN_THRESHOLD and not abstain
    # 弱证据 → 弃权且理由披露
    conf, abstain, reason = compute_confidence(
        [("a", 1.0), ("b", 0.9)], channels_for_top=0)
    assert conf < ABSTAIN_THRESHOLD and abstain and "low_confidence" in reason


def test_selection_confidence_and_abstain_flow(registry):
    from app.services.chat.tool_surface_v3 import (
        DynamicToolSurface,
        ToolSelectionContext,
    )

    sel = DynamicToolSurface(registry).select(
        ToolSelectionContext(user_message="给学校加500米缓冲区"))
    assert 0.0 <= sel.confidence <= 1.0
    assert sel.selection_trace.get("v6", {}).get("expanded_terms") >= 0
    d = sel.as_dict()
    assert {"confidence", "abstained", "abstain_reason"} <= set(d)


def test_kill_switch_off_disables_v6(registry, monkeypatch):
    from app.services.chat.tool_surface_v3 import (
        DynamicToolSurface,
        ToolSelectionContext,
    )

    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V6", "0")
    assert v6_retrieval_enabled() is False
    sel = DynamicToolSurface(registry).select(
        ToolSelectionContext(user_message="缓冲区分析"))
    # V6 关闭 → 置信度保持中性默认（与 V5 逐位兼容）
    assert sel.confidence == 1.0
    assert sel.abstained is False
    assert "v6" not in sel.selection_trace


def test_v6_switch_gates_scoring_fix(registry, monkeypatch):
    """M1 回归：GIS_TOOL_RETRIEVAL_V6=0 → V5 打分数学逐位一致。

    停用单字（的）在 V5 数学中贡献 desc 权重；V6 开启时零权重。
    """
    from app.services.chat import tool_retrieval as tr

    q = "的"  # 纯停用字查询：V5 数学给含「的」描述的工具加分；V6 = 零
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V6", "0")
    hits_v5 = tr.rank_tools(registry, q, min_score=1.0, enriched=False)
    monkeypatch.setenv("GIS_TOOL_RETRIEVAL_V6", "1")
    hits_v6 = tr.rank_tools(registry, q, min_score=1.0, enriched=False)
    assert hits_v5, "V5 数学下停用字命中描述应产生命中"
    assert hits_v6 == [], "V6 判别力修复下停用字不得产生命中"


def test_embedding_retriever_bounded_failure(registry, monkeypatch):
    """C1 回归：embedding 检索器真实调用路径有界失败（不毒化、不悬挂）。"""
    import pytest as _pytest

    from app.services.chat import semantic_retrieval as sr

    # 无 faiss/模型环境 → RuntimeError（有界）；指纹面故障不得毒化模型态
    monkeypatch.setitem(sr._embed_state, "model_failed", False)
    try:
        hits = sr.embedding_retriever(registry, "缓冲区分析", top_k=5)
    except RuntimeError:
        # 有界失败（模型缺席部署）→ 记忆化后再次调用立即失败且形态一致
        with _pytest.raises(RuntimeError):
            sr.embedding_retriever(registry, "缓冲区分析", top_k=5)
        return
    # 模型可得环境：返回归一化 RetrievalHit
    assert len(hits) <= 5
    for h in hits:
        assert h.score > 0.0
