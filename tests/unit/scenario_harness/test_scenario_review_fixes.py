"""G05 review 修复回归（C 审查盲区清单）：

- P0-1: Agent 工具面无 unlock（释放仅 HTTP 路由）
- P2-3: user-wins 守卫命中 → 逐项 user_wins_skip（非整笔失败）
- P2-6: unresolvable 候选拒绝采纳（adopt_unresolvable）
- P2-2: 采纳后漂移 → revert 默认拒绝 + allow_drift 披露丢弃区间
- P2-10: 锁字段路径形态校验（词表外变体假安全）
"""
from __future__ import annotations

import importlib
import uuid

import pytest

from app.services.gis_world_state import apply_gis_mutation
from app.services.mapspec.intents import (
    PatchLayerStyleIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.scenario_harness.adopt import adopt_candidate, revert_adoption
from app.services.scenario_harness.baseline import observe_baseline_move
from app.services.scenario_harness.candidates import (
    apply_candidate_interventions,
    propose_candidate,
)
from app.services.scenario_harness.contracts import AdoptSelection
from app.services.scenario_harness.locks import set_lock
from app.services.scenario_harness.store import RegistryError
from app.services.session_data import session_data_manager


def _sid() -> str:
    return f"g05-fix-{uuid.uuid4().hex[:8]}"


async def _seed(parent: str) -> None:
    res = await apply_gis_mutation(
        parent,
        UpsertLayerIntent(
            layer={"id": "schools", "type": "circle", "source": "schools"},
            source_data={"type": "geojson", "inlineData": {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "id": "s1",
                     "geometry": {"type": "Point", "coordinates": [104.03, 30.61]},
                     "properties": {"id": "s1"}},
                ],
            }},
        ),
        origin="agent", actor="seed",
    )
    assert not res.is_error, res.error_msg


async def _current_rev(parent: str) -> int:
    state = await session_data_manager.get_map_state(parent)
    return int(state.get("_cartographic_mutation_revision", 0) or 0)


def test_p0_1_unlock_tool_not_registered():
    """Agent 面不提供锁释放工具（释放仅走 require_owned_session 路由）。"""
    from app.tools import _TOOL_MODULES
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    for mod_name, fn_name in _TOOL_MODULES:
        mod = importlib.import_module(mod_name)
        getattr(mod, fn_name)(registry)
    assert "webgis_scenario_unlock" not in registry.tool_names()
    assert "webgis_scenario_revert" in registry.tool_names()


@pytest.mark.asyncio
async def test_p2_3_user_wins_guard_downgrades_item_not_whole_batch():
    """origin=agent 采纳 presentation 候选命中 user 所有权 → 该条
    user_wins_skip，其余条照常 applied —— 不是整笔失败。"""
    parent = _sid()
    await _seed(parent)
    from app.contracts.mapspec_intents import PatchLayerPresentationIntent

    # 1) 用户手动隐藏 schools（origin=user → durable 所有权 → 守卫环）
    rev = await _current_rev(parent)
    hide = await apply_gis_mutation(
        parent,
        PatchLayerPresentationIntent(layer_id="schools", visible=False),
        origin="user", actor="user_panel", expected_revision=rev,
    )
    assert not hide.is_error, hide.error_msg

    # 2) Agent 候选要重新显示 + 无关样式 → 采纳时 presentation 条被降级
    cand = await propose_candidate(
        parent,
        title="混合候选",
        intents=[
            PatchLayerPresentationIntent(layer_id="schools", visible=True),
            PatchLayerStyleIntent(layer_id="schools", paint={"opacity": 0.85}),
        ],
        created_by="agent",
    )
    await apply_candidate_interventions(parent, cand.candidate_id)
    receipt = await adopt_candidate(
        parent, cand.candidate_id, AdoptSelection(mode="all"),
        origin="agent", actor="agent_scenario_adopt",
    )
    verdicts = {i.seq: i.verdict for i in receipt.items}
    assert verdicts[2] == "applied", receipt.model_dump(mode="json")
    assert verdicts[1] == "user_wins_skip", verdicts
    spec = await mapspec_store_instance.get_mapspec(parent)
    school = next(
        lay for lay in spec.get("layers", []) if lay.get("id") == "schools"
    )
    assert (school.get("layout") or {}).get("visibility") == "none", "用户隐藏不被覆盖"


@pytest.mark.asyncio
async def test_p2_6_unresolvable_candidate_rejected_at_adopt():
    """数据版本推进的候选：adopt 拒绝（adopt_unresolvable），旧结果不混入权威态。
    （ref 基数据源才有版本锚 —— inline 载荷无版本概念，见 baseline 锚清单。）"""
    parent = _sid()
    await _seed(parent)
    ref_id = await session_data_manager.store(parent, {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "s1",
             "geometry": {"type": "Point", "coordinates": [104.03, 30.61]},
             "properties": {"id": "s1"}},
        ],
    })
    res = await apply_gis_mutation(
        parent,
        UpsertLayerIntent(
            layer={"id": "zones", "type": "fill", "source": "zones-src"},
            source_data={"type": "geojson", "ref_id": ref_id},
        ),
        origin="agent", actor="seed",
    )
    assert not res.is_error, res.error_msg
    cand = await propose_candidate(
        parent, title="分析候选",
        intents=[UpsertLayerIntent(
            layer={"id": "buffer-500", "type": "circle", "source": "buffer-500"},
            source_data={"type": "geojson", "inlineData": {
                "type": "FeatureCollection", "features": []}},
        )],
        created_by="agent",
    )
    await apply_candidate_interventions(parent, cand.candidate_id)
    # 数据版本推进（ref overwrite）→ 基准数据锚漂移
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "s2",
         "geometry": {"type": "Point", "coordinates": [104.04, 30.62]},
         "properties": {"id": "s2"}}]}
    ok = await session_data_manager.overwrite(parent, ref_id, fc)
    assert ok, "ref overwrite 必须成功"
    move = await observe_baseline_move(parent, cand.baseline_id)
    assert move.dataset_changes, "数据锚漂移必须在场"

    with pytest.raises(RegistryError) as ei:
        await adopt_candidate(
            parent, cand.candidate_id, AdoptSelection(mode="all"),
            origin="user", actor="user_workbench",
        )
    assert ei.value.code == "adopt_unresolvable"


@pytest.mark.asyncio
async def test_p2_2_revert_drift_gate_and_disclosure():
    """采纳后再有推进：revert 默认拒绝并披露区间；allow_drift 放行并记录。"""
    parent = _sid()
    await _seed(parent)
    cand = await propose_candidate(
        parent, title="样式候选",
        intents=[PatchLayerStyleIntent(layer_id="schools", paint={"opacity": 0.8})],
        created_by="agent",
    )
    await apply_candidate_interventions(parent, cand.candidate_id)
    receipt = await adopt_candidate(
        parent, cand.candidate_id, AdoptSelection(mode="all"),
        origin="user", actor="user_workbench",
    )
    assert receipt.applied_count == 1

    # 采纳后推进一个 revision
    rev = await _current_rev(parent)
    extra = await apply_gis_mutation(
        parent, PatchLayerStyleIntent(layer_id="schools", paint={"opacity": 0.9}),
        origin="agent", actor="agent_later", expected_revision=rev,
    )
    assert not extra.is_error

    with pytest.raises(RegistryError) as ei:
        await revert_adoption(parent, receipt.adoption_id, origin="user", actor="user")
    assert ei.value.code == "revert_drift"

    reverted = await revert_adoption(
        parent, receipt.adoption_id, origin="user", actor="user", allow_drift=True,
    )
    assert reverted.discarded_revisions >= 1
    spec = await mapspec_store_instance.get_mapspec(parent)
    school = next(
        lay for lay in spec.get("layers", []) if lay.get("id") == "schools"
    )
    assert school.get("paint", {}).get("opacity") != 0.8, "回退生效"


@pytest.mark.asyncio
async def test_p2_10_lock_field_path_validation():
    """词表外路径变体登记被拒（假安全防线）。"""
    parent = _sid()
    with pytest.raises(RegistryError) as ei:
        await set_lock(parent, kind="layer_field", target_id="L1",
                       field_paths=[" Paint.fill-color"], reason="x")
    assert ei.value.code == "lock_field_invalid"
    with pytest.raises(RegistryError) as ei2:
        await set_lock(parent, kind="layer_field", field_paths=["   "], reason="x")
    assert ei2.value.code == "lock_field_invalid"
    lock = await set_lock(parent, kind="layer_field", target_id="L1",
                          field_paths=[" paint.opacity ", "paint.fill-color"],
                          reason="ok")
    assert lock.field_paths == ["paint.opacity", "paint.fill-color"]


def test_n1_revert_tool_has_no_allow_drift():
    """工具面 revert 不暴露 allow_drift（丢弃同意来自真实用户身份路由）。"""
    from app.tools.scenario_harness_tools import ScenarioRevertArgs

    assert "allow_drift" not in ScenarioRevertArgs.model_fields
    # 路由面保留（真实用户身份）
    from app.api.routes.scenario_harness import post_scenario_revert
    import inspect

    sig = inspect.signature(post_scenario_revert)
    assert "allow_drift" in sig.parameters


def test_n9_registration_expects_full_tool_set():
    from app.tools import _TOOL_MODULES
    from app.tools.registry import ToolRegistry

    registry = ToolRegistry()
    import importlib

    for mod_name, fn_name in _TOOL_MODULES:
        getattr(importlib.import_module(mod_name), fn_name)(registry)
    scenario_tools = {t for t in registry.tool_names() if t.startswith("webgis_scenario_")}
    assert scenario_tools == {
        "webgis_scenario_inspect", "webgis_scenario_propose",
        "webgis_scenario_execute", "webgis_scenario_compare",
        "webgis_scenario_adopt", "webgis_scenario_revert",
        "webgis_scenario_abandon", "webgis_scenario_lock",
    }


@pytest.mark.asyncio
async def test_n3_incremental_interventions_via_tool_shape():
    """propose(1 条) → execute(追加 1 条) —— seq 自动接续不再撞车。"""
    from app.services.scenario_harness.candidates import (
        apply_candidate_interventions,
        get_candidate,
        propose_candidate,
    )
    from app.services.scenario_harness.intents_codec import serialize_intents

    parent = _sid()
    await _seed(parent)
    cand = await propose_candidate(
        parent,
        title="基础候选",
        intents=[PatchLayerStyleIntent(layer_id="schools", paint={"opacity": 0.8})],
        created_by="agent",
    )
    assert [d.seq for d in cand.delta] == [1]
    extra = serialize_intents([
        PatchLayerStyleIntent(layer_id="schools", paint={"opacity": 0.6}),
    ])
    assert [d.seq for d in extra] == [1], "工具路径固定从 1 编号（触发过撞车的形状）"
    # 先应用已有 delta，再增量追加（复现工具面真实两步流）
    await apply_candidate_interventions(parent, cand.candidate_id, deltas=None)
    results = await apply_candidate_interventions(parent, cand.candidate_id, deltas=extra)
    assert results and results[0]["status"] == "applied"
    final = await get_candidate(parent, cand.candidate_id)
    assert [d.seq for d in final.delta] == [1, 2], "追加 delta 接续为 seq 2"
    assert final.state == "comparable"


@pytest.mark.asyncio
async def test_n2_checkpoint_cas_superseded_path():
    """锚点 CAS：expected 落后于真实 revision → adopt_superseded（零副作用）。"""
    from app.services.scenario_harness.adopt import adopt_candidate
    from app.services.scenario_harness.store import RegistryError

    parent = _sid()
    await _seed(parent)
    cand = await propose_candidate(
        parent,
        title="CAS 候选",
        intents=[PatchLayerStyleIntent(layer_id="schools", paint={"opacity": 0.5})],
        created_by="agent",
    )
    await apply_candidate_interventions(parent, cand.candidate_id)
    rev_before = await _current_rev(parent)
    # 并发推进（在 adopt 之前）
    drift = await apply_gis_mutation(
        parent,
        PatchLayerStyleIntent(layer_id="schools", paint={"opacity": 0.95}),
        origin="agent", actor="concurrent", expected_revision=rev_before,
    )
    assert not drift.is_error
    with pytest.raises(RegistryError) as ei:
        await adopt_candidate(
            parent, cand.candidate_id,
            AdoptSelection(mode="all", expected_revision=rev_before),
            origin="user", actor="user_workbench",
        )
    assert ei.value.code == "adopt_superseded"


@pytest.mark.asyncio
async def test_n7a_origin_bound_idempotency():
    """Agent 降级采纳的回执不挡用户工作台同选集采纳（origin 进幂等键）。"""
    parent = _sid()
    await _seed(parent)
    from app.contracts.mapspec_intents import PatchLayerPresentationIntent

    rev = await _current_rev(parent)
    hide = await apply_gis_mutation(
        parent,
        PatchLayerPresentationIntent(layer_id="schools", visible=False),
        origin="user", actor="user_panel", expected_revision=rev,
    )
    assert not hide.is_error
    cand = await propose_candidate(
        parent,
        title="降级候选",
        intents=[PatchLayerPresentationIntent(layer_id="schools", visible=True)],
        created_by="agent",
    )
    await apply_candidate_interventions(parent, cand.candidate_id)
    agent_receipt = await adopt_candidate(
        parent, cand.candidate_id, AdoptSelection(mode="all"),
        origin="agent", actor="agent_scenario_adopt",
    )
    assert agent_receipt.applied_count == 0
    assert all(i.verdict == "user_wins_skip" for i in agent_receipt.items)
    # 用户工作台同选集采纳：不同 origin → 不幂等命中 Agent 回执。
    # presentation 对 origin=user 是合法覆盖（守卫只拦 agent）→ applied。
    user_receipt = await adopt_candidate(
        parent, cand.candidate_id, AdoptSelection(mode="all"),
        origin="user", actor="user_workbench",
    )
    assert user_receipt.adoption_id != agent_receipt.adoption_id
    assert user_receipt.applied_count == 1, user_receipt.model_dump(mode="json")
