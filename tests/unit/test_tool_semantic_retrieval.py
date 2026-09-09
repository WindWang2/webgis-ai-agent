"""Tool Semantic Retrieval 测试（W12a：Tool Retrieval V6 语义路径接线）。

- ToolSemanticIndex（假 embed_fn，零模型依赖）：排序确定性 / 0.15 相似度
  下限 / top_k 截断 / 同 registry 缓存不重建 / 同输入同输出；
- kill-switch：spec 取 off/0/disabled/none（大小写/空白不敏感）或
  GIS_TOOL_SEMANTIC=0 → 静默返回 None（不打 warning），surface 逐位回退词法；
- surface 融合：注入语义命中 → retriever 留痕 + 加分理由 + 命中工具入面；
- 默认 lexical：无注入时 loader 为 None（test_tool_surface_v3.py:139 语义）。

红线：registry 仍是唯一工具事实源 —— 索引语料与断言对象全部来自真实
registry + init_tools，假的只是编码器（embed_fn 注入点本就是为此设计）。
"""
import logging

import pytest

from app.services.chat import tool_surface_v3 as v3
from app.services.chat.tool_semantic_retrieval import (
    _MIN_SIMILARITY,
    _SEMANTIC_SCORE_SCALE,
    _tool_text,
    ToolSemanticIndex,
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


class _FakeEmbed:
    """确定性假编码器：按语料关键词映射到三档二维向量（调用可计数）。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            tl = t.lower()
            if "heat" in tl or "热力" in t:
                out.append([1.0, 0.0])
            elif "map" in tl or "地图" in t:
                out.append([0.8, 0.6])
            else:
                out.append([0.0, 1.0])
        return out


def _query(registry, index, text="heat map", top_k=200):
    return index.query(registry, text, top_k)


def test_index_sorting_and_score_scale(registry):
    # 查询 "heat map" → [1,0]：heat 档 sim=1.0（6.0 分），map 档 sim=0.8
    # （4.8 分），其余 sim=0.0 被下限滤掉。
    index = ToolSemanticIndex(embed_fn=_FakeEmbed())
    hits = _query(registry, index)
    assert hits, "假编码器下应有 heat/map 两档命中"
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True), "分数必须降序"
    assert hits[0].score == pytest.approx(1.0 * _SEMANTIC_SCORE_SCALE)
    # 同分 tie 按工具名升序（确定性，与 surface 排序纪律一致）
    for a, b in zip(hits, hits[1:]):
        if a.score == b.score:
            assert a.name <= b.name
    # 两档分界：6.0 段全在 4.8 段之前
    assert scores.index(pytest.approx(0.8 * _SEMANTIC_SCORE_SCALE)) > \
        scores.index(pytest.approx(1.0 * _SEMANTIC_SCORE_SCALE))


def test_index_similarity_floor(registry):
    # 0.15 下限：sim=0.0 的工具（语料既无 heat/热力 又无 map/地图）不得出现；
    # 存量命中分全部 ≥ 0.15 * scale。
    index = ToolSemanticIndex(embed_fn=_FakeEmbed())
    hits = _query(registry, index)
    floor = round(_MIN_SIMILARITY * _SEMANTIC_SCORE_SCALE, 4)
    assert all(h.score >= floor for h in hits)
    names = {h.name for h in hits}
    for name in registry.list_tools():
        text = _tool_text(registry.descriptor(name))
        if "heat" not in text.lower() and "热力" not in text \
                and "map" not in text.lower() and "地图" not in text:
            assert name not in names, f"{name} sim=0 应被下限滤掉"
            break
    else:
        pytest.fail("registry 应存在至少一个双关键词都不含的工具")


def test_index_top_k(registry):
    index = ToolSemanticIndex(embed_fn=_FakeEmbed())
    full = _query(registry, index)
    assert len(full) > 5, "候选应远多于截断值，截断断言才有意义"
    top3 = _query(registry, index, top_k=3)
    assert [h.name for h in top3] == [h.name for h in full[:3]]
    assert len(_query(registry, index, top_k=5)) == 5


def test_index_cache_no_rebuild(registry):
    # 同 registry 二次查询只多一次 embed 调用（查 query 向量），不重建索引。
    embed = _FakeEmbed()
    index = ToolSemanticIndex(embed_fn=embed)
    first = _query(registry, index)
    assert embed.calls == 2, "首次查询 = 1 次建索引批量 + 1 次 query 编码"
    second = _query(registry, index)
    assert embed.calls == 3, "二次查询不得重建索引"
    assert [h.name for h in second] == [h.name for h in first]


def test_index_deterministic(registry):
    index = ToolSemanticIndex(embed_fn=_FakeEmbed())
    a = [(h.name, h.score) for h in _query(registry, index)]
    b = [(h.name, h.score) for h in _query(registry, index)]
    assert a == b


@pytest.mark.parametrize("spec", [
    "off", "0", "disabled", "none",
    "OFF", "Disabled", "NONE", "  off  ",
])
def test_kill_switch_spec_silent(monkeypatch, caplog, spec):
    # kill-switch 走静默路径：返回 None 且本模块不打 warning。
    monkeypatch.setenv("TOOL_RETRIEVAL_SEMANTIC", spec)
    monkeypatch.delenv("GIS_TOOL_SEMANTIC", raising=False)
    with caplog.at_level(logging.WARNING):
        assert v3._load_semantic_retriever() is None
    assert [r for r in caplog.records
            if r.name == "app.services.chat.tool_surface_v3"] == []


def test_kill_switch_env_silent(monkeypatch, caplog):
    # GIS_TOOL_SEMANTIC=0：即使 spec 看似有效也不尝试 import，静默缺席。
    monkeypatch.setenv("TOOL_RETRIEVAL_SEMANTIC", "no.such.module:fn")
    monkeypatch.setenv("GIS_TOOL_SEMANTIC", "0")
    with caplog.at_level(logging.WARNING):
        assert v3._load_semantic_retriever() is None
    assert [r for r in caplog.records
            if r.name == "app.services.chat.tool_surface_v3"] == []


def test_kill_switch_surface_falls_back_lexical(monkeypatch, registry):
    # kill-switch 下 surface 逐位回退词法：retriever=lexical 且面非空。
    monkeypatch.setenv("TOOL_RETRIEVAL_SEMANTIC", "off")
    monkeypatch.delenv("GIS_TOOL_SEMANTIC", raising=False)
    surface = DynamicToolSurface(registry)
    assert surface._semantic is None
    sel = surface.select(ToolSelectionContext(user_message="热力图 heat map"))
    assert sel.retriever == "lexical"
    assert sel.names, "词法 baseline 必须照常服务"


def test_loader_failure_still_warns(monkeypatch, caplog):
    # 非 kill-switch 的真实加载失败仍走 warning 降级（kill-switch 不吞故障痕）。
    monkeypatch.setenv("TOOL_RETRIEVAL_SEMANTIC", "no.such.module:fn")
    monkeypatch.delenv("GIS_TOOL_SEMANTIC", raising=False)
    with caplog.at_level(logging.WARNING):
        assert v3._load_semantic_retriever() is None
    assert [r for r in caplog.records
            if r.name == "app.services.chat.tool_surface_v3"] != []


def test_default_is_lexical(monkeypatch, registry):
    # 默认（无注入）即 lexical —— test_tool_surface_v3.py:139 的前置语义。
    monkeypatch.setenv("TOOL_RETRIEVAL_SEMANTIC", "")
    monkeypatch.delenv("GIS_TOOL_SEMANTIC", raising=False)
    assert v3._load_semantic_retriever() is None
    sel = DynamicToolSurface(registry).select(
        ToolSelectionContext(user_message="统计成都市各区县的小学密度并做热力图"))
    assert sel.retriever == "lexical"


def test_surface_semantic_fusion(monkeypatch, registry):
    # 融合：语义命中经既有 hook 加分留痕，retriever 标记语义源。
    index = ToolSemanticIndex(embed_fn=_FakeEmbed())

    def fake_semantic(reg, query, top_k):
        return index.query(reg, "heat map", top_k)

    monkeypatch.setenv("TOOL_RETRIEVAL_SEMANTIC", "fake:index")
    monkeypatch.setattr(v3, "_load_semantic_retriever", lambda: fake_semantic)
    monkeypatch.delenv("GIS_TOOL_SEMANTIC", raising=False)
    surface = DynamicToolSurface(registry)
    sel = surface.select(ToolSelectionContext(user_message="heat map 热力图"))
    assert sel.retriever.startswith("semantic:")
    semantic_reasons = [r for rs in sel.reasons.values() for r in rs
                        if r.startswith("semantic(")]
    assert semantic_reasons, "语义加分必须留痕"
    semantic_names = {h.name for h in index.query(registry, "heat map", 60)}
    assert semantic_names & set(sel.names), "语义命中应有代表入面"
