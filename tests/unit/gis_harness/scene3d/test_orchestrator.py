"""G3D04 M5 · 编排 DAG 测试。

覆盖：结构推断（artifact 依赖）、ready 推进、取消语义、恢复载荷
ref-first、typed 占位步骤（G3D02/G3D03 边界）、环丢弃披露。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.scene3d.orchestrator import (
    build_scene3d_plan,
    cancel_semantics,
    resume_payload,
)

pytestmark = pytest.mark.unit


def test_full_analysis_chain_dag_structure():
    plan = build_scene3d_plan("立体地形+视域", analysis_goal="viewshed",
                              include_camera=True, elevation_ref="ref:dem-1")
    ids = {s.step_id for s in plan.steps}
    assert {"s3d-elev", "s3d-qualify", "s3d-scene", "s3d-analysis",
            "s3d-attach", "s3d-camera", "s3d-verdict"} <= ids
    assert not plan.dropped_cycle_edges
    # artifact 推断：qualify 依赖 elev；scene 依赖 qualify；
    # analysis 依赖 elev + scene；verdict 收口
    assert plan.step("s3d-qualify").depends_on == ["s3d-elev"]
    assert plan.step("s3d-scene").depends_on == ["s3d-qualify"]
    assert set(plan.step("s3d-analysis").depends_on) == {"s3d-elev", "s3d-scene"}
    assert plan.step("s3d-verdict").depends_on


def test_analysis_chain_verdict_waits_for_run_analysis():
    """artifact 首产者获胜（setdefault）曾使 final_verdict/attach_result
    只依赖 s3d-elev —— s3d-elev/scene 完成后 ready_steps 即含 s3d-verdict，
    判定先于分析完成（违背 run_analysis → attach_result → final_verdict
    文档链）。"""
    plan = build_scene3d_plan("立体地形+视域", analysis_goal="viewshed",
                              elevation_ref="ref:dem-1")
    assert "s3d-analysis" in plan.step("s3d-verdict").depends_on
    assert "s3d-analysis" in plan.step("s3d-attach").depends_on
    # elev/scene 完成但 analysis 未完成 → verdict 不得 ready
    for sid in ("s3d-elev", "s3d-qualify", "s3d-scene"):
        plan.step(sid).status = "complete"
    assert "s3d-verdict" not in plan.ready_steps()
    assert "s3d-analysis" in plan.ready_steps()
    plan.step("s3d-analysis").status = "complete"
    assert "s3d-verdict" in plan.ready_steps()
    # 表达链（无分析）verdict 仍只依赖 elev/scene（零行为变化）
    plain = build_scene3d_plan("表达链", analysis_goal="")
    assert set(plain.step("s3d-verdict").depends_on) == {"s3d-elev", "s3d-scene"}


def test_ready_progression_and_skip_unlocks():
    plan = build_scene3d_plan("表达链", analysis_goal="")
    assert plan.ready_steps() == ["s3d-elev"]
    plan.step("s3d-elev").status = "complete"
    assert plan.ready_steps() == ["s3d-qualify"]
    # skipped 也解锁下游（fallback-unlock 语义）
    plan.step("s3d-qualify").status = "skipped"
    assert "s3d-scene" in plan.ready_steps()


def test_pending_boundaries_are_typed_unavailable():
    plan = build_scene3d_plan("带资产", analysis_goal="viewshed",
                              include_scene_asset=True)
    asset = plan.step("s3d-asset")
    assert asset.status == "unavailable"
    assert asset.reason_code == "SCENE_CAP_PENDING_G3D02"
    attach = plan.step("s3d-attach")
    assert attach.status == "unavailable"
    assert attach.reason_code == "SCENE_RESULT_ATTACH_PENDING_G3D03"
    # unavailable 占位不阻塞 ready 推进（optional 语义）
    assert "s3d-verdict" not in plan.ready_steps()


def test_cancel_semantics_step_boundary_safety():
    plan = build_scene3d_plan("x", analysis_goal="viewshed")
    semantics = cancel_semantics(plan)
    assert semantics["safety_point"] == "step_boundary"
    assert "s3d-analysis" in semantics["cancellable_steps"]
    assert "s3d-verdict" in semantics["non_cancellable"]
    assert semantics["idempotency"]["s3d-analysis"] == "analysis:viewshed"


def test_resume_payload_ref_first_and_bounded():
    plan = build_scene3d_plan("x", analysis_goal="viewshed",
                              elevation_ref="ref:dem-1")
    payload = resume_payload(plan)
    assert payload["refs"] == ["ref:dem-1"]
    assert len(payload["refs"]) <= 128
    assert payload["step_status"]["s3d-elev"] == "pending"
    assert payload["next_ready"] == ["s3d-elev"]
    # 载荷不含大对象（DEM/mesh 数据本体）——只有 ref
    assert "ref:" in payload["refs"][0]


def test_plan_deterministic_for_same_inputs():
    a = build_scene3d_plan("任务", analysis_goal="viewshed", include_camera=True)
    b = build_scene3d_plan("任务", analysis_goal="viewshed", include_camera=True)
    assert a.model_dump() == b.model_dump()


def test_every_step_maps_to_known_action_vocab():
    from app.services.gis_harness.scene3d.orchestrator import STEP_ACTIONS

    plan = build_scene3d_plan("x", analysis_goal="viewshed", include_camera=True,
                              include_scene_asset=True)
    for step in plan.steps:
        assert step.action in STEP_ACTIONS
