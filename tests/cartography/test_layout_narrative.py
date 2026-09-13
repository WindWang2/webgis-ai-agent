"""W5 版面与叙事测试（V11，ADR-0165）。

覆盖：G1 备选版面生产接线（planner 缝）、G2 自愈 executed（前端 compose
侧见 compose.test.ts 的 executed 断言与 __fallback_* 归零 grep）、版面
五维评分。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

REPO_ROOT = Path(__file__).resolve().parents[2]

# ── W5.1 G1 备选版面生产接线 ────────────────────────────────────────────

def test_composition_alternatives_production_wiring() -> None:
    """grep 断言：composition_alternatives_payload 在 planner 生产路径被
    调用（W5 验收「有生产调用」的机器可查形态）。"""
    src = (REPO_ROOT / "app/services/gis_harness/planner.py").read_text(encoding="utf-8")
    assert "composition_alternatives_payload(" in src
    assert "_composition_alternatives_evidence(plan" in src
    # 证据入账（template_selection 可消费）
    assert '"composition_alternatives"' in src


def test_alternatives_evidence_helper() -> None:
    """投影缝：intent 事实 → ≥3 候选 + 评分（有界、确定性）。"""
    from app.services.gis_harness.planner import _composition_alternatives_evidence

    class _Scope:
        name = "成都市"
        level = "city"

    class _Intent:
        task = "distribution_overview"
        scope = _Scope()
        data_kind = "sequential"

    class _Plan:
        intent = _Intent()

    payload = _composition_alternatives_evidence(_Plan(), {"geometryTypes": ["Point"]})
    assert payload.get("version") == 1
    assert 1 <= payload.get("count", 0) <= 3
    candidates = payload.get("candidates") or []
    assert candidates and {"mapModel", "score", "reasons"} <= set(candidates[0])
    # 确定性
    assert payload == _composition_alternatives_evidence(_Plan(), {"geometryTypes": ["Point"]})


def test_alternatives_evidence_fail_safe() -> None:
    """坏输入 → 空 dict（证据缺席 ≠ 规划失败）。"""
    from app.services.gis_harness.planner import _composition_alternatives_evidence

    class _Broken:
        intent = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    assert _composition_alternatives_evidence(_Broken()) == {}


# ── W5.5 版面五维评分 ───────────────────────────────────────────────────

def test_layout_score_balanced_vs_clustered() -> None:
    from app.lib.cartography.layout_score import score_layout

    balanced = score_layout([
        {"type": "legend", "placement": {"anchor": "top-left"}},
        {"type": "scale_bar", "position": "bottom-right"},
        {"type": "north_arrow", "position": "top-right"},
        {"type": "attribution", "position": "bottom-center"},
    ])
    clustered = score_layout([
        {"type": "legend", "placement": {"anchor": "top-left"}},
        {"type": "title", "position": "top-left"},
        {"type": "chart_panel", "position": "top-left"},
    ])
    assert balanced["overall"] > clustered["overall"]
    assert balanced["scores"]["contrast"] == 1.0  # 对角分布
    assert clustered["scores"]["balance"] < balanced["scores"]["balance"]


def test_layout_score_deterministic_and_empty() -> None:
    from app.lib.cartography.layout_score import score_layout

    components = [{"type": "legend", "position": "top-right"}]
    assert score_layout(components) == score_layout(components)
    empty = score_layout([])
    assert empty["overall"] == 0.0 and empty["reasons"]


# ── W5.3 多图版面（C2 IR 编排）──────────────────────────────────────────

def test_atlas_pages_deterministic_and_bounded() -> None:
    from app.lib.cartography.atlas_layout import MAX_ATLAS_PAGES, plan_atlas_pages

    scenarios = [{"id": f"s{i}", "title": f"场景{i}", "map_kind": "choropleth"}
                 for i in range(5)]
    out = plan_atlas_pages(scenarios, canvas={"widthPx": 1000, "heightPx": 700})
    assert out["pageCount"] == 5
    assert out == plan_atlas_pages(scenarios, canvas={"widthPx": 1000, "heightPx": 700})
    page = out["pages"][0]
    assert page["ir"]["version"] == 2
    ids = [c["id"] for c in page["ir"]["components"]]
    assert "s0__map" in ids and "s0__title" in ids
    # 有界：超上界截断并如实记 degradation
    many = [{"id": f"x{i}", "title": "t"} for i in range(MAX_ATLAS_PAGES + 3)]
    big = plan_atlas_pages(many, canvas={"widthPx": 1000, "heightPx": 700})
    assert big["pageCount"] == MAX_ATLAS_PAGES
    assert any(d["code"] == "atlas_truncated" for d in big["degradations"])
    # 非法画布 fail-closed
    with pytest.raises(ValueError):
        plan_atlas_pages(scenarios, canvas={"widthPx": 0, "heightPx": 0})


# ── W5.4 StoryMap 大纲 ──────────────────────────────────────────────────

def test_story_outline_from_session() -> None:
    from app.lib.cartography.story_outline import story_outline_from_session

    out = story_outline_from_session({
        "question": "成都学校分布如何？",
        "dataSources": ["POI 要素集", "行政区聚合表"],
        "analysisSteps": ["点密度统计", "分级渲染"],
        "mapTitle": "成都学校分布图",
        "mapSpecId": "spec-1",
        "takeaways": ["中心城区密度最高"],
    }, title="成都学校分布")
    assert out["complete"] is True
    assert [c["key"] for c in out["chapters"]] == ["question", "data", "analysis", "map"]
    assert out["chapters"][0]["points"] == ["成都学校分布如何？"]
    # 缺事实 → missing 诚实标记（不虚构）
    sparse = story_outline_from_session({"question": "为什么？"})
    assert sparse["complete"] is False
    assert sparse["chapters"][1]["missing"] is True
