"""场景矩阵索引（task §18 Scenario A–G）—— 每个场景锚定到具体测试。

这不是新增行为测试，而是**场景覆盖契约**：后续重构若移动/删除某场景的
承载测试，本索引先失败，强制更新映射（防止场景覆盖无声流失）。
"""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _has_test(module: str, name: str) -> bool:
    import importlib

    try:
        mod = importlib.import_module(module)
    except Exception:  # noqa: BLE001 — 导入失败即索引失败
        return False
    return hasattr(mod, name)


def _frontend_test_exists(rel: str) -> bool:
    return (_REPO / "frontend" / rel).exists()


def test_scenario_matrix_anchored():
    """A–G 每个场景至少锚定一个真实存在的具名测试/测试文件。"""
    backend_anchors = {
        # A 成都小学分布全产品（POI→聚合→密度→热力→统计→图表→终验）
        "A_chengdu_schools_full_product": [
            ("tests.unit.test_map_product_finalization_scenarios",
             "test_scenario_a_chengdu_schools_full_product_completion"),
            ("tests.unit.gis_harness.test_product_graph", "test_full_product_facets"),
        ],
        # B 简单点图不强制 colorbar/统计/图表
        "B_simple_point_map_no_forced_components": [
            ("tests.unit.test_map_product_finalization_scenarios",
             "test_scenario_b_simple_point_map_no_colorbar_forced"),
        ],
        # C 大规模点数据 → resolver 自动切换聚合通道（P2 成本感知）
        "C_large_dataset_resolver_switch": [
            ("tests.unit.gis.test_cost_model",
             "test_large_point_dataset_switches_off_native_rendering"),
            ("tests.unit.gis.test_cost_model",
             "test_renderable_scale_keeps_native_heatmap"),
        ],
        # D 用户手动隐藏图层 → finalizer 不重开（user-wins）
        "D_user_hidden_layer_respected": [
            ("tests.unit.gis_harness.test_map_completion",
             "test_user_hidden_layer_is_respected_not_overridden"),
            ("tests.unit.test_map_product_finalization_scenarios",
             "test_scenario_c_user_wins_disclosed_not_overridden"),
        ],
        # E 算法失败 → failed 披露 → 重试 → complete（下游解锁由 plan_graph
        # 套件锁定）
        "E_failure_retry_complete": [
            ("tests.unit.gis_harness.test_map_completion",
             "test_failed_capability_does_not_finalize_then_retry_completes"),
            ("tests.unit.gis_harness.test_map_completion",
             "test_failed_regression_overwrites_stored_complete_block"),
        ],
        # H 渲染观察闭环：observation 匹配 revision → facet 全完成 → final
        "H_render_observed_success": [
            ("tests.unit.test_map_product_finalization_scenarios",
             "test_scenario_h_render_observed_success"),
        ],
        # I MapSpec 正确而 runtime 缺层 → 不得静默宣称 verified
        "I_runtime_layer_missing_detected": [
            ("tests.unit.test_map_product_finalization_scenarios",
             "test_scenario_i_mapspec_correct_runtime_layer_missing"),
            ("tests.unit.gis_harness.test_render_observation",
             "test_matched_revision_missing_layer_is_error"),
        ],
        # J 旧 revision 的观察不得验证新 spec
        "J_stale_observation_guarded": [
            ("tests.unit.test_map_product_finalization_scenarios",
             "test_scenario_j_stale_observation_cannot_validate"),
            ("tests.unit.gis_harness.test_render_observation",
             "test_validate_stale_observation_cannot_validate_newer_spec"),
        ],
        # K style reload → 重放 → observation 再生成 → 门打破 → 重验
        "K_style_reload_reobservation": [
            ("tests.unit.test_map_product_finalization_scenarios",
             "test_scenario_k_style_reload_replay_regenerates_observation"),
            ("tests.unit.gis_harness.test_render_observation",
             "test_finalizer_complete_with_stale_observation_discloses"),
        ],
        # L 用户隐藏层 → user-wins：观察如实披露、无修复对抗
        "L_user_wins_render_observation": [
            ("tests.unit.test_map_product_finalization_scenarios",
             "test_scenario_l_user_hidden_layer_no_repair_fight"),
        ],
        # M chart facet 欠账 → 产品图点名 + 确定性 next action
        "M_missing_chart_facet_next_action": [
            ("tests.unit.test_map_product_finalization_scenarios",
             "test_scenario_m_missing_chart_facet_next_action"),
            ("tests.unit.gis_harness.test_product_action",
             "test_chart_owed_maps_to_channel_not_tool_shortcut"),
        ],
    }
    frontend_anchors = {
        # F style reload → 运行时图层/source/可见性恢复正确
        "F_style_reload_restoration": "lib/map-kit/runtime-layer-registry.test.ts",
        # G live vs PNG vs PDF vs SVG 关键组件语义一致
        "G_live_export_parity": "lib/map-exporter/map-exporter.test.ts",
        # H 前端采集面：render observation（组件观察/错误环/settle/revision）
        "H_render_observed_success": "lib/mapspec-runtime/render-observation.test.ts",
    }

    for scenario, anchors in backend_anchors.items():
        alive = [f"{m}::{n}" for m, n in anchors if _has_test(m, n)]
        assert alive, (
            f"scenario {scenario} lost all backend anchors "
            f"({[f'{m}::{n}' for m, n in anchors]}) — move the mapping, don't drop it"
        )
    for scenario, rel in frontend_anchors.items():
        assert _frontend_test_exists(rel), (
            f"scenario {scenario} lost its vitest anchor ({rel})"
        )
