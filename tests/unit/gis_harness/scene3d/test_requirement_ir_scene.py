"""G3D04 M2 · Requirement IR SceneSpec 契约测试。

覆盖：genesis cue 投影、patch 白名单与值校验、user-wins（owner 冲突 +
scene_mode/scene_camera 锁）、digest 条件包含、义务面 scene 条目、
edit cue patches（幂等/锁）、锁携带重建、回放不变式。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.intent import MapRequestIntent
from app.services.gis_harness.requirement_ir.build import (
    build_document,
    diff_to_patches,
)
from app.services.gis_harness.requirement_ir.contracts import PatchRecord
from app.services.gis_harness.requirement_ir.digest import canonical_core
from app.services.gis_harness.requirement_ir.patch import (
    PatchConflict,
    PatchUnknownPath,
    PatchValueInvalid,
    apply_patch,
    replay,
)

pytestmark = pytest.mark.unit


def _core() -> MapRequestIntent:
    return MapRequestIntent(task="distribution_overview")


def _patch(doc, *, op="set", path="", value=None, actor="user", op_id=None, turn=1):
    return apply_patch(doc, PatchRecord(
        op_id=op_id or f"t-{op}-{path}-{turn}-{actor}",
        turn=turn, actor=actor, op=op,  # type: ignore[arg-type]
        path=path, value=value,
    ))


# ── genesis 投影 ─────────────────────────────────────────────────────


def test_genesis_projects_scene_cues_with_user_provenance():
    doc = build_document("展示成都市的立体地形", _core())
    scene = doc.intent.scene
    assert scene.dimension == "3d"
    assert scene.terrain_requested is True
    # 用户话语直接表达 → 字段级 origin=user（第一轮即 user-wins）
    assert doc.intent.field_provenance["scene.dimension"].is_user()
    assert doc.intent.field_provenance["scene.terrain_requested"].is_user()


def test_genesis_without_cues_stays_default():
    doc = build_document("显示成都小学分布情况", _core())
    assert doc.intent.scene.is_default()
    assert doc.intent.output.medium == ""


def test_genesis_medium_and_analysis_goals():
    doc = build_document("计算塔台的通视范围", _core())
    assert "viewshed" in doc.intent.scene.analysis_goals
    assert "line_of_sight" in doc.intent.scene.analysis_goals
    doc2 = build_document("出一张 PDF 静态图", _core())
    assert doc2.intent.output.medium == "export_pdf"


# ── patch 面 ─────────────────────────────────────────────────────────


def test_patch_set_scene_fields_whitelist_and_validation():
    doc = build_document("显示成都小学分布", _core())
    doc, applied = _patch(doc, path="scene.dimension", value="3d")
    assert applied and doc.intent.scene.dimension == "3d"
    doc, _ = _patch(doc, path="scene.exaggeration", value=2.5, turn=2)
    assert doc.intent.scene.exaggeration == 2.5
    doc, _ = _patch(doc, path="scene.camera_pitch", value=45.0, turn=3)
    assert doc.intent.scene.camera_pitch == 45.0
    doc, _ = _patch(doc, path="scene.analysis_goals", value=["viewshed"], turn=4)
    assert doc.intent.scene.analysis_goals == ("viewshed",)
    doc, _ = _patch(doc, path="output.medium", value="print", turn=5)
    assert doc.intent.output.medium == "print"


def test_patch_rejects_unknown_and_invalid_values():
    doc = build_document("x", _core())
    with pytest.raises(PatchUnknownPath):
        _patch(doc, path="scene.mode", value="3d")
    with pytest.raises(PatchValueInvalid):
        _patch(doc, path="scene.dimension", value="4d")
    with pytest.raises(PatchValueInvalid):
        _patch(doc, path="scene.exaggeration", value=99.0)
    with pytest.raises(PatchValueInvalid):
        _patch(doc, path="scene.camera_bearing", value=270.0)
    with pytest.raises(PatchValueInvalid):
        _patch(doc, path="scene.analysis_goals", value=["warp"])


def test_patch_user_wins_agent_cannot_overwrite_user_dimension():
    doc = build_document("展示成都市的立体地形", _core())  # user: dimension=3d
    with pytest.raises(PatchConflict):
        _patch(doc, path="scene.dimension", value="2d", actor="agent")
    # user 自己可以改
    doc, applied = _patch(doc, path="scene.dimension", value="2.5d", actor="user")
    assert applied and doc.intent.scene.dimension == "2.5d"


def test_scene_mode_lock_blocks_agent_and_survives_rebuild():
    doc = build_document("显示成都小学分布", _core())
    doc, _ = _patch(doc, op="add_lock",
                    value={"scope": "scene_mode", "value": "3d"})
    with pytest.raises(PatchConflict):
        _patch(doc, path="scene.dimension", value="2d", actor="agent")
    # 用户 remove_lock 后 agent 才能动
    doc, _ = _patch(doc, op="remove_lock",
                    value={"scope": "scene_mode", "value": "3d"}, turn=2)
    doc, applied = _patch(doc, path="scene.dimension", value="2d",
                          actor="agent", turn=3)
    assert applied


def test_scene_camera_lock_protects_pitch_and_bearing():
    doc = build_document("x", _core())
    doc, _ = _patch(doc, path="scene.camera_pitch", value=30.0, actor="user")
    doc, _ = _patch(doc, op="add_lock",
                    value={"scope": "scene_camera", "value": "pitch"}, turn=2)
    with pytest.raises(PatchConflict):
        _patch(doc, path="scene.camera_pitch", value=60.0, actor="agent", turn=3)


def test_vertical_evidence_records_via_patch():
    doc = build_document("x", _core())
    evidence = [{"kind": "elevation_dem", "ref": "dem1", "verified": True,
                 "detail": "registered"}]
    doc, applied = _patch(doc, path="scene.vertical_evidence", value=evidence,
                          actor="agent")
    assert applied and doc.intent.scene.vertical_evidence[0].ref == "dem1"
    with pytest.raises(PatchValueInvalid):
        _patch(doc, path="scene.vertical_evidence",
               value=[{"kind": "magic"}], actor="agent", turn=2)


# ── digest / 义务面 ──────────────────────────────────────────────────


def test_digest_conditional_scene_block_zero_drift_for_legacy_docs():
    legacy = build_document("显示成都小学分布情况", _core())
    assert "scene" not in canonical_core(legacy)
    assert "medium" not in canonical_core(legacy)["output"]
    scene_doc = build_document("用三维地图显示", _core())
    assert canonical_core(scene_doc)["scene"]["dimension"] == "3d"


def test_obligations_include_scene_items():
    doc = build_document("展示成都市的立体地形", _core())
    paths = [i.source_path for i in doc.requirements.items]
    assert "scene.dimension" in paths
    doc2 = build_document("计算塔台的通视范围", _core())
    assert "scene.analysis_goals" in [i.source_path for i in doc2.requirements.items]


# ── edit cue patches ─────────────────────────────────────────────────


def test_edit_cue_dimension_change_adds_lock_and_is_idempotent():
    doc = build_document("显示成都小学分布", _core())
    patches = diff_to_patches(doc, _core(), turn=1, query="切成 3D 视图")
    set_patches = [p for p in patches if p.op == "set" and p.path == "scene.dimension"]
    lock_patches = [p for p in patches if p.op == "add_lock"
                    and isinstance(p.value, dict)
                    and p.value["scope"] == "scene_mode"]
    assert set_patches and set_patches[0].value == "3d"
    assert lock_patches
    # 应用后重放：重复话语零 patch（幂等）
    doc2 = doc
    for p in patches:
        doc2, _ = apply_patch(doc2, p)
    again = diff_to_patches(doc2, _core(), turn=2, query="切成 3D 视图")
    assert not [p for p in again if p.op == "set" and p.path == "scene.dimension"]


def test_edit_cue_camera_bearing_locks_scene_camera():
    doc = build_document("显示成都小学分布", _core())
    patches = diff_to_patches(doc, _core(), turn=1, query="从西南方向看")
    assert any(p.path == "scene.camera_bearing" and p.value == 45.0
               for p in patches if p.op == "set")
    assert any(p.op == "add_lock" and isinstance(p.value, dict)
               and p.value["scope"] == "scene_camera" for p in patches)


def test_edit_cue_user_can_replace_own_scene_lock():
    """用户改主意换锁：同一用户的新维度表达解除旧锁并换新锁（锁不挡
    owner）；agent 仍被锁挡（patch 层防线）。"""
    doc = build_document("x", _core())
    doc, _ = _patch(doc, op="add_lock",
                    value={"scope": "scene_mode", "value": "3d"})
    patches = diff_to_patches(doc, _core(), turn=1, query="改成 2D 平面地图")
    ops = [(p.op, p.path, p.value) for p in patches]
    assert ("remove_lock", "", {"scope": "scene_mode", "value": "3d"}) in ops
    assert ("set", "scene.dimension", "2d") in ops
    assert ("add_lock", "", {"scope": "scene_mode", "value": "2d"}) in ops


def test_replay_invariant_with_scene_patches():
    doc = build_document("显示成都小学分布", _core())
    patches = diff_to_patches(doc, _core(), turn=1,
                              query="切成 3D 视图，地形夸张 2 倍")
    doc2 = doc
    for p in patches:
        doc2, applied = apply_patch(doc2, p)
        assert applied
    rebuilt = replay(doc, doc2.patches)
    assert rebuilt.intent.scene == doc2.intent.scene
    assert rebuilt.model_dump() == doc2.model_dump()


def test_lock_carry_to_rebuilt_spec():
    """scene_mode 锁在超替重建（carried_locks）时落回 scene 面。"""
    from app.services.gis_harness.requirement_ir.contracts import Provenance, UserLock

    lock = UserLock(scope="scene_mode", value="3d",
                    provenance=Provenance(origin="user"))
    doc = build_document("新任务", _core(), carried_locks=[lock])
    assert doc.intent.scene.dimension == "3d"
    assert doc.intent.field_provenance["scene.dimension"].is_user()
