"""V5 layout intent correctness（ADR-0118 D2）回归测试。

锁定四个事实：
1. P0：SetLayoutIntent 显式 legend visible=False 必须真实提交 —— commit 前
   的 cartographic AUTO_SAFE 修复回路不得把显式关闭翻回 True（历史缺陷：
   工具报 success 但 committed spec 仍 visible）。
2. 部分图例意图做字段级 merge —— 不丢既有 position 等键。
3. presentation batch 的 prior-blocking 缓存驱逐不再触发
   dict.popitem TypeError（>256 指纹即崩，批量事务整体回滚）。
4. webgis_layout_set 的 controls 形参与引擎/前端词表一致（List，非 Dict）。
"""


import pytest

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    SetLayoutIntent,
    UpsertLayerIntent,
)

pytestmark = pytest.mark.unit

_THEMATIC_LAYER = {
    "id": "choropleth-districts",
    "type": "fill",
    "source": "src-districts",
    "paint": {"fill-color": ["#eff3ff", "#6baed6", "#08519c"], "fill-opacity": 0.8},
    "legend_spec": {
        "type": "graduated",
        "field": "population",
        "palette_colors": ["#eff3ff", "#6baed6", "#08519c"],
        "min": 0,
        "max": 100,
    },
}

_SOURCE_DATA = {"type": "FeatureCollection", "features": []}


async def _seed_thematic_session(session_id: str) -> MapSpecLifecycleEngine:
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        session_id,
        UpsertLayerIntent(layer=_THEMATIC_LAYER, source_data=_SOURCE_DATA),
    )
    assert res.is_error is False, res.error_msg
    return engine


@pytest.mark.asyncio
async def test_legend_visible_false_commits_without_repair_flip():
    """P0 回归：显式关闭图例必须提交生效，且 finding 诚实披露（不静默改写）。"""
    engine = await _seed_thematic_session("v5_legend_off_1")
    res = await engine.apply_mutation(
        "v5_legend_off_1", SetLayoutIntent(legend={"visible": False})
    )
    assert res.is_error is False, res.error_msg
    legend = res.mapspec["layout"]["legend"]
    assert legend["visible"] is False, (
        "explicit legend visible=False was silently flipped by AUTO_SAFE repair"
    )
    # 诚实披露：review 证据必须携带 legend completeness 的 fail finding
    review = res.cartographic_review or {}
    checks = (review.get("review") or review).get("checks", [])
    completeness = [
        c for c in checks if c.get("rule") == "carto.legend.completeness"
    ]
    assert completeness, "legend completeness finding missing from evidence"
    assert completeness[0]["status"] == "fail"


@pytest.mark.asyncio
async def test_legend_visible_false_via_tool_store_path():
    """经 mapspec_store.layout_set（webgis_layout_set 真实调用链）同样生效。"""
    from app.services import mapspec_store

    store = mapspec_store.MapSpecStore()
    engine = await _seed_thematic_session("v5_legend_off_2")
    store.engine = engine
    res = await store.layout_set("v5_legend_off_2", legend={"visible": False})
    assert res.get("success") is True
    assert res["layout"]["legend"]["visible"] is False


@pytest.mark.asyncio
async def test_partial_legend_intent_preserves_existing_keys():
    """字段级 merge：只发 visible 不丢 position；只发 position 不翻 visible。"""
    engine = MapSpecLifecycleEngine()
    sid = "v5_legend_merge_1"
    res1 = await engine.apply_mutation(
        sid, SetLayoutIntent(legend={"visible": True, "position": "bottom-left"})
    )
    assert res1.is_error is False
    res2 = await engine.apply_mutation(
        sid, SetLayoutIntent(legend={"visible": False})
    )
    assert res2.is_error is False
    legend = res2.mapspec["layout"]["legend"]
    assert legend["visible"] is False
    assert legend["position"] == "bottom-left", (
        "partial legend intent must not drop pre-existing keys"
    )


@pytest.mark.asyncio
async def test_legend_hidden_state_survives_unrelated_mutations():
    """跨变异 user-wins（review-r1 MAJOR-4）：先显式关图例，随后仅改
    margins 的变异不得让 AUTO_SAFE 把显式 False 翻回 True。"""
    engine = await _seed_thematic_session("v5_legend_off_3")
    res1 = await engine.apply_mutation(
        "v5_legend_off_3", SetLayoutIntent(legend={"visible": False})
    )
    assert res1.is_error is False
    res2 = await engine.apply_mutation(
        "v5_legend_off_3", SetLayoutIntent(margins={"top": 48})
    )
    assert res2.is_error is False, res2.error_msg
    legend = res2.mapspec["layout"]["legend"]
    assert legend["visible"] is False, (
        "explicit legend hidden state must survive unrelated mutations"
    )


@pytest.mark.asyncio
async def test_layout_margins_partial_merge():
    engine = MapSpecLifecycleEngine()
    sid = "v5_margins_merge_1"
    await engine.apply_mutation(
        sid, SetLayoutIntent(margins={"top": 40, "right": 24})
    )
    res = await engine.apply_mutation(sid, SetLayoutIntent(margins={"top": 60}))
    assert res.is_error is False
    margins = res.mapspec["layout"]["margins"]
    assert margins == {"top": 60, "right": 24}


@pytest.mark.asyncio
async def test_presentation_batch_cache_eviction_no_type_error():
    """prior-blocking 缓存 >256 条时驱逐不得抛 dict.popitem TypeError。"""
    engine = MapSpecLifecycleEngine()
    sid = "v5_batch_evict_1"
    for i in range(300):
        engine._prior_blocking_cache[f"fp-{i}"] = set()
    res = await engine.apply_mutation(sid, SetLayoutIntent(legend={"title": "t"}))
    assert res.is_error is False, res.error_msg


def test_webgis_layout_set_controls_param_is_list():
    """controls 形参必须与 SetLayoutIntent/前端 MapSpecControlConfig[] 对齐。"""
    import inspect

    from app.tools.cartography_tools import register_mapspec_cartography_tools
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register_mapspec_cartography_tools(registry)
    func = registry._tools["webgis_layout_set"]
    annotation = str(inspect.signature(func).parameters["controls"].annotation)
    assert "List" in annotation or "list" in annotation, (
        f"webgis_layout_set.controls must be a list type, got: {annotation}"
    )
