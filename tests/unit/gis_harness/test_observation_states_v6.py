"""Observation State Ladder 测试（ADR-0119 决策 D9）。

验收：状态阶梯派生纯函数（诚实弱态优先）、聚合最弱链法、workflow
health 消费词汇、map_product_block 的 observation_health additive 键。
"""

from app.services.gis_harness.observation_states import (
    LADDER,
    aggregate_observation_state,
    build_observation_summary,
    chart_observation_state,
    layer_observation_state,
    to_workflow_health,
)


def test_ladder_closed_vocabulary():
    assert LADDER == (
        "unknown", "pending", "mounted", "loaded", "rendered",
        "data_present", "semantically_correct",
    )


def test_layer_state_honest_weakness():
    # 无遥测 = unknown（诚实缺席，不假通过）
    assert layer_observation_state(None) == "unknown"
    assert layer_observation_state({}) == "unknown"
    assert layer_observation_state({"mounted": False}) == "pending"
    # 源错误停留在 pending（阻塞面）
    assert layer_observation_state(
        {"mounted": True, "source_status": "error"}) == "pending"
    assert layer_observation_state({"mounted": True}) == "mounted"
    assert layer_observation_state(
        {"mounted": True, "source_converged": True}) == "loaded"
    assert layer_observation_state(
        {"mounted": True, "source_converged": True,
         "render_complete": True}) == "rendered"
    # feature_count=0（viewport-scoped）不谎报 data_present
    assert layer_observation_state(
        {"mounted": True, "render_complete": True, "feature_count": 0}
    ) == "rendered"
    assert layer_observation_state(
        {"mounted": True, "render_complete": True, "feature_count": 42}
    ) == "data_present"
    # intent 未核对 → 不得 semantically_correct
    assert layer_observation_state(
        {"mounted": True, "render_complete": True, "feature_count": 3},
        intent_verified=False) == "data_present"
    assert layer_observation_state(
        {"mounted": True, "render_complete": True, "feature_count": 3},
        intent_verified=True) == "semantically_correct"


def test_chart_state():
    assert chart_observation_state(None) == "unknown"
    assert chart_observation_state({"rendered": False}) == "pending"
    assert chart_observation_state({"rendered": True}) == "rendered"
    assert chart_observation_state(
        {"rendered": True, "data_points": 7}) == "data_present"


def test_aggregate_weakest_link():
    assert aggregate_observation_state([]) == "unknown"
    assert aggregate_observation_state(
        ["data_present", "rendered", "semantically_correct"]) == "rendered"
    assert aggregate_observation_state(["ok", "unknown"]) == "unknown"


def test_workflow_health_mapping():
    assert to_workflow_health("unknown") == "blocked"
    assert to_workflow_health("pending") == "degraded"
    assert to_workflow_health("rendered") == "partial"
    assert to_workflow_health("data_present") == "ok"
    assert to_workflow_health("semantically_correct") == "ok"
    assert to_workflow_health("garbage") == "blocked"


def test_build_observation_summary_bounded():
    obs = {
        "revision": 3,
        "layers": {f"l{i}": {"mounted": True, "render_complete": True,
                             "feature_count": i}
                   for i in range(40)},  # 超 32 层 → 截断
        "charts": [{"rendered": True, "data_points": 5}],
    }
    summary = build_observation_summary(obs, intent_verified=True)
    assert len(summary["layers"]) <= 32
    assert summary["charts"] == ["data_present"]
    assert summary["aggregate"] == "rendered"  # l0 feature_count=0
    assert summary["health"] == "partial"
    assert summary["observed_revision"] == 3
    # 缺 observation → blocked 诚实缺席
    empty = build_observation_summary(None)
    assert empty["aggregate"] == "unknown" and empty["health"] == "blocked"


def test_map_product_block_carries_observation_health():
    from app.services.gis_harness.completion.contracts import (
        MapCompletionResult,
    )
    from app.services.gis_harness.completion.pipeline import map_product_block

    result = MapCompletionResult()
    block = map_product_block(
        result, 7, observation={"revision": 7, "layers": {}},
        intent_verified=True)
    assert block["observation_health"]["aggregate"] == "unknown"
    assert block["observation_health"]["health"] == "blocked"
    # 不传 observation → 恒发射诚实 blocked（workflow 消费方按三态裁决）
    block2 = map_product_block(result, 7)
    assert block2["observation_health"]["aggregate"] == "unknown"
    assert block2["observation_health"]["health"] == "blocked"
