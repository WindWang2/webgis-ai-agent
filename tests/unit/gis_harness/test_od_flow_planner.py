from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.planner import MapProductPlanner


def test_real_od_table_plan_binds_flow_mapping_without_cost_matrix_or_provider():
    intent = resolve_map_request_intent("把这份成都区县通勤表画成流图，两向分别显示")
    plan = MapProductPlanner().plan_from_intent(
        intent,
        available_tools={"od_flow_edges"},
        use_memo=False,
    )
    assert plan.recipe_id == "od_flow_overview"
    primary = next(layer for layer in plan.map_layers if layer.role == "primary")
    assert primary.source_capability == "od_flow_mapping"
    assert primary.cartography == "flow_od_arc"
    requirement_ids = {row.capability for row in plan.data_requirements}
    step_ids = {row.capability for row in plan.analysis_steps}
    assert "od_flow_mapping" in requirement_ids | step_ids
    assert "od_matrix" not in requirement_ids | step_ids
    assert "network_od_matrix" not in {row.resolved_tool for row in plan.analysis_steps}
