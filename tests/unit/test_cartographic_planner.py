"""Cartographic Planner 基础（C3）测试：分布驱动分类裁决 + cartographic_intent 投影。

CA-P1-1 回归：cartographic_intent 此前只读不写（全仓无生产者），QA 的
RESULT_VISIBILITY 恒 not_evaluated。现在 upsert 落意图、presentation patch
改写意图——"故意隐藏"与"结果层被误藏"可区分。
"""
import pytest

from app.lib.cartography.visualization_plan import (
    ClassificationChoice,
    build_visualization_plan,
    choose_classification,
    distribution_stats_from_values,
)
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    PatchLayerPresentationIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance


def _heavy_tail_values():
    # 城市计数形态：大量低值 + 少数极高值（mean >> median）
    values = [float(i % 10) for i in range(200)]
    values += [500.0, 2000.0, 9000.0]
    return values


def _uniform_values():
    return [float(i) for i in range(100)]


def _moderate_skew_values():
    # 温和右偏（skew ≈ 0.08，未到重尾阈值 0.12）
    return [float(i * i) for i in range(1, 30)]


def test_distribution_stats_basic():
    stats = distribution_stats_from_values([1.0, 2.0, 3.0, 4.0])
    assert stats is not None
    assert stats.n == 4
    assert stats.min == 1.0 and stats.max == 4.0
    assert stats.median == 2.5
    assert 2.49 < stats.mean < 2.51

    assert distribution_stats_from_values([1.0]) is None
    assert distribution_stats_from_values([]) is None


def test_choose_classification_heavy_tail_picks_head_tail():
    stats = distribution_stats_from_values(_heavy_tail_values())
    assert stats is not None
    choice = choose_classification(stats)
    assert choice.method == "head_tail"
    assert choice.source == "distribution"
    assert choice.authority  # Jiang (2013)
    assert any("重尾" in r or "median" in r for r in choice.reasons)
    # 等间距在重尾下的落选理由必须引用其 caveat
    rejected = {r["method"]: r["reason"] for r in choice.rejected}
    assert "equal_interval" in rejected


def test_choose_classification_uniform_picks_interval():
    stats = distribution_stats_from_values(_uniform_values())
    assert stats is not None
    # 推荐集含等间距 → 近均匀优先等间距（与直方图直觉对应）
    choice = choose_classification(stats, recommended=["equal_interval", "quantiles"])
    assert choice.method == "equal_interval"
    # 推荐集不含等间距 → 分位数是近均匀数据的合理次选（每类样本数均衡）
    choice2 = choose_classification(stats, recommended=["quantiles", "natural_breaks"])
    assert choice2.method == "quantiles"


def test_choose_classification_moderate_defaults_to_natural_breaks():
    stats = distribution_stats_from_values(_moderate_skew_values())
    assert stats is not None
    choice = choose_classification(stats, recommended=["quantiles", "natural_breaks"])
    assert choice.method == "natural_breaks"
    assert choice.k == 5


def test_choose_classification_respects_explicit_request():
    stats = distribution_stats_from_values(_heavy_tail_values())
    assert stats is not None
    choice = choose_classification(stats, requested_method="quantiles", requested_k=6)
    assert choice.method == "quantiles"
    assert choice.source == "explicit"
    assert choice.k == 6


def test_choose_classification_clamps_k():
    stats = distribution_stats_from_values(_uniform_values())
    assert stats is not None
    assert choose_classification(stats, requested_k=10).k == 7
    assert choose_classification(stats, requested_k=2).k == 3


def test_visualization_plan_serializes_steps():
    stats = distribution_stats_from_values(_heavy_tail_values())
    choice = choose_classification(stats)
    plan = build_visualization_plan(
        phenomenon="学校",
        geometry="Point",
        analysis_goal="distribution",
        map_model="poi_distribution",
        classification=choice,
        palette="YlOrRd",
        composition_template="composition.report_map",
        secondary_views=[{"kind": "district_aggregation_choropleth"}],
    )
    dumped = plan.to_dict()
    steps = {s["step"]: s for s in dumped["steps"]}
    assert set(steps) >= {"intent", "map_model", "classification", "palette", "composition"}
    assert "head_tail" in steps["classification"]["choice"]
    assert dumped["classification"]["authority"]
    assert dumped["secondary_views"][0]["kind"] == "district_aggregation_choropleth"


async def _seed(session_id: str) -> None:
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        session_id,
        UpsertLayerIntent(
            layer={
                "id": "result-layer",
                "type": "fill",
                "source": "src-1",
                "context_role": "result",
                "paint": {},
            },
            source_data={
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
                            "properties": {"v": 1.0},
                        }
                    ],
                },
            },
        ),
    )
    assert not res.is_error


@pytest.mark.asyncio
async def test_upsert_projects_cartographic_intent():
    sid = "plan-intent-1"
    await _seed(sid)
    spec = await mapspec_store_instance.get_mapspec(sid)
    layer = next(l for l in spec["layers"] if l["id"] == "result-layer")
    assert layer["cartographic_intent"] == {
        "expected_visible": True,
        "role": "result",
    }


@pytest.mark.asyncio
async def test_presentation_patch_rewrites_expected_visible():
    """用户/agent 隐藏是显式决策 → expected_visible=False → QA 不再误报。"""
    sid = "plan-intent-2"
    await _seed(sid)
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        sid, PatchLayerPresentationIntent(layer_id="result-layer", visible=False)
    )
    assert not res.is_error
    spec = await mapspec_store_instance.get_mapspec(sid)
    layer = next(l for l in spec["layers"] if l["id"] == "result-layer")
    assert layer["layout"]["visibility"] == "none"
    assert layer["cartographic_intent"]["expected_visible"] is False


@pytest.mark.asyncio
async def test_result_visibility_check_now_evaluated():
    """语义检查激活：隐藏 + expected_visible=True → fail（结果层被误藏）；
    隐藏 + expected_visible=False → pass（故意隐藏）。此前恒 not_evaluated。"""
    sid = "plan-intent-3"
    await _seed(sid)
    engine = MapSpecLifecycleEngine()
    # 故意隐藏（决策改写意图）
    await engine.apply_mutation(
        sid, PatchLayerPresentationIntent(layer_id="result-layer", visible=False)
    )
    spec = await mapspec_store_instance.get_mapspec(sid)
    report = evaluate_cartography_semantics(spec)
    checks = {c.rule: c for c in report.checks}
    vis = checks.get("RESULT_VISIBILITY")
    assert vis is not None
    assert vis.status == "pass"

    # 模拟误藏：直接篡改 spec 的 visibility 而不改意图（bug/竞态路径）
    import copy

    broken = copy.deepcopy(spec)
    layer = next(l for l in broken["layers"] if l["id"] == "result-layer")
    layer["layout"]["visibility"] = "none"
    layer["cartographic_intent"]["expected_visible"] = True
    report2 = evaluate_cartography_semantics(broken)
    checks2 = {c.rule: c for c in report2.checks}
    vis2 = checks2.get("RESULT_VISIBILITY")
    assert vis2 is not None
    assert vis2.status == "fail"
    assert vis2.repairability == "auto_safe"
