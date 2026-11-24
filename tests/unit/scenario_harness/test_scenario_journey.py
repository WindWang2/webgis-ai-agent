"""G06 端到端黄金旅程（G05 验收旅程 1/2/3 的单层闭环）：

「成都小学分布」多轮：真实播种地图 A（权威）→ 开候选 B（隔离试算，父零突变）
→ 用户锁定 → 候选执行命中锁定被拒 → 父会话漂移 → 影响分级/失效披露 →
局部重算 → 选择性采纳（真实 revision 推进 + 回执）→ 幂等重采纳 → 回退 →
指代解析。全部走真实 MapSpec 引擎/mutation 机器，零 mock。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.gis_world_state import apply_gis_mutation
from app.services.mapspec.intents import PatchLayerStyleIntent, UpsertLayerIntent
from app.services.mapspec.store import mapspec_store_instance
from app.services.scenario_harness.candidates import (
    abandon_candidate,
    apply_candidate_interventions,
    get_candidate,
    list_candidates,
    propose_candidate,
)
from app.services.scenario_harness.contracts import AdoptSelection
from app.services.scenario_harness.adopt import adopt_candidate, revert_adoption
from app.services.scenario_harness.intents_codec import serialize_intent
from app.services.scenario_harness.locks import find_blocking_lock, set_lock
from app.services.scenario_harness.projection import resolve_scenario_reference
from app.services.scenario_harness.staleness import (
    classify_candidate_impacts,
    observe_baseline_move,
)
from app.services.session_data import session_data_manager


def _sid() -> str:
    return f"g05-journey-{uuid.uuid4().hex[:8]}"


async def _seed_schools_map(parent: str) -> None:
    """真实播种「成都小学分布」底图：面（行政区底色）+ 点（学校）。"""
    zones_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "zone-wuhou",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [104.02, 30.60], [104.06, 30.60],
                        [104.06, 30.64], [104.02, 30.64], [104.02, 30.60],
                    ]],
                },
                "properties": {"id": "zone-wuhou", "name": "武侯区"},
            },
        ],
    }
    schools_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature", "id": f"sch-{i}",
                "geometry": {"type": "Point", "coordinates": [104.03 + i * 0.01, 30.61 + i * 0.005]},
                "properties": {"id": f"sch-{i}", "name": f"小学{i}", "students": 400 + i * 60},
            }
            for i in range(4)
        ],
    }
    for layer, geom, fc in (
        ("zones", "fill", zones_fc),
        ("schools", "circle", schools_fc),
    ):
        res = await apply_gis_mutation(
            parent,
            UpsertLayerIntent(
                layer={"id": layer, "type": geom, "source": layer},
                source_data={"type": "geojson", "inlineData": fc},
            ),
            origin="agent",
            actor="journey",
        )
        assert not res.is_error, res.error_msg


@pytest.mark.asyncio
async def test_golden_journey_propose_lock_drift_adopt_revert():
    parent = _sid()
    await _seed_schools_map(parent)

    # ── 阶段 1：候选 B（换 schools 分级表达）——开候选后父会话零突变 ──
    parent_rev_before = (await session_data_manager.get_map_state(parent)).get(
        "_cartographic_mutation_revision"
    )
    parent_spec_before = await mapspec_store_instance.get_mapspec(parent)
    cand_b = await propose_candidate(
        parent,
        title="B 方案：学生数分级色带",
        hypothesis="按 students 分级更均匀",
        intents=[PatchLayerStyleIntent(layer_id="schools", paint={"opacity": 0.85})],
        created_by="agent",
    )
    assert cand_b.state == "draft"
    assert cand_b.delta and cand_b.delta[0].base_values, "提案必须观察基准值"
    parent_spec_after = await mapspec_store_instance.get_mapspec(parent)
    assert parent_spec_after == parent_spec_before, "开候选不得改权威地图"
    assert (await session_data_manager.get_map_state(parent)).get(
        "_cartographic_mutation_revision"
    ) == parent_rev_before

    # ── 阶段 2：分支试算 → 隔离证据 ──
    results = await apply_candidate_interventions(parent, cand_b.candidate_id)
    assert all(r.get("status") == "applied" for r in results)
    branch_spec = await mapspec_store_instance.get_mapspec(cand_b.branch_session_id)
    branch_school = next(
        layer for layer in branch_spec.get("layers", []) if layer.get("id") == "schools"
    )
    assert branch_school["paint"]["opacity"] == 0.85, "分支真实生效"
    parent_school = next(
        layer for layer in parent_spec_after.get("layers", []) if layer.get("id") == "schools"
    )
    assert parent_school.get("paint", {}).get("opacity") != 0.85, "父会话不受分支影响"

    # ── 阶段 3：用户锁定 → 执行命中锁定的干预被拒 ──
    lock = await set_lock(
        parent,
        kind="layer_field",
        target_id="schools",
        field_paths=["paint.opacity"],
        observed_value=str(parent_school.get("paint", {}).get("opacity")),
        reason="这个透明度定死",
    )
    assert lock.active
    blocked = await apply_candidate_interventions(
        parent,
        cand_b.candidate_id,
        deltas=[serialize_intent(
            PatchLayerStyleIntent(layer_id="schools", paint={"opacity": 0.4}), seq=99,
        )],
    )
    assert blocked and blocked[0]["status"] == "lock_blocked"
    assert await find_blocking_lock(parent, "schools", ["paint.opacity"]) is not None

    # ── 阶段 4：用户手动改父会话（真实 mutation）→ 基准漂移 ──
    drift_res = await apply_gis_mutation(
        parent,
        PatchLayerStyleIntent(layer_id="zones", paint={"opacity": 0.9}),
        origin="user",
        actor="user_map_panel",
        expected_revision=int(parent_rev_before),
    )
    assert not drift_res.is_error and not drift_res.superseded
    move = await observe_baseline_move(parent, cand_b.baseline_id)
    assert move.moved and move.fingerprint_changed
    assert move.revision_to > move.revision_from

    # ── 阶段 5：影响分级 + 失效披露 ──
    impacts = await classify_candidate_impacts(parent)
    impact_b = next(i for i in impacts if i.candidate_id == cand_b.candidate_id)
    assert impact_b.level != "current"
    refreshed = await get_candidate(parent, cand_b.candidate_id)
    assert refreshed.state == "stale"
    assert refreshed.stale_reasons, "失效必须有原因披露"

    # ── 阶段 6：选择性采纳（无冲突字段仍可落；逐项裁决如实）──
    # 用户漂移改的是 zones 层；候选目标是 schools 层 → 无字段冲突。
    # 锁定在 schools.paint.opacity：候选 delta[0] 恰好命中 → lock_blocked。
    # 先释放锁（用户显式决定放行），保留"锁定曾拦截"的阶段证据。
    from app.services.scenario_harness.locks import release_lock

    await release_lock(parent, lock.lock_id, reason="放行透明度", origin="user")
    receipt = await adopt_candidate(
        parent, cand_b.candidate_id, AdoptSelection(mode="all"),
        origin="user", actor="user_workbench",
    )
    assert receipt.applied_count >= 1
    assert receipt.pre_adopt_checkpoint_id, "采纳必须留回退锚"
    adopted_spec = await mapspec_store_instance.get_mapspec(parent)
    adopted_school = next(
        layer for layer in adopted_spec.get("layers", []) if layer.get("id") == "schools"
    )
    assert adopted_school["paint"]["opacity"] == 0.85, "采纳真实落到权威地图"
    final = await get_candidate(parent, cand_b.candidate_id)
    assert final.state == "adopted" and final.adoption_id == receipt.adoption_id

    # ── 阶段 7：幂等重采纳（同选集不重放）──
    receipt_again = await adopt_candidate(
        parent, cand_b.candidate_id, AdoptSelection(mode="all"),
        origin="user", actor="user_workbench",
    )
    assert receipt_again.adoption_id == receipt.adoption_id

    # ── 阶段 8：回退 → 父会话回采纳前、候选回 comparable ──
    reverted = await revert_adoption(parent, receipt.adoption_id, origin="user", actor="user")
    assert reverted.reverted_by
    back_spec = await mapspec_store_instance.get_mapspec(parent)
    back_school = next(
        layer for layer in back_spec.get("layers", []) if layer.get("id") == "schools"
    )
    assert back_school.get("paint", {}).get("opacity") != 0.85, "回退真实还原"
    final = await get_candidate(parent, cand_b.candidate_id)
    assert final.state == "comparable"

    # ── 阶段 9：指代解析（稳定 id，不靠回忆）──
    assert await resolve_scenario_reference(parent, "current") == cand_b.candidate_id
    assert await resolve_scenario_reference(parent, cand_b.candidate_id) == cand_b.candidate_id

    # ── 阶段 10：放弃候选 → 分支回收幂等 ──
    assert await abandon_candidate(parent, cand_b.candidate_id, reason="旅程结束") is True
    assert await abandon_candidate(parent, cand_b.candidate_id) is False
    assert await list_candidates(parent) == []


@pytest.mark.asyncio
async def test_golden_journey_lock_blocks_adopt_while_user_owns_field():
    """验收旅程 2：候选完成不能覆盖用户锁定 —— 采纳逐项披露 lock_blocked。"""
    parent = _sid()
    await _seed_schools_map(parent)
    cand = await propose_candidate(
        parent,
        title="C 方案：透明度试验",
        intents=[PatchLayerStyleIntent(layer_id="schools", paint={"opacity": 0.3})],
        created_by="user",
    )
    await apply_candidate_interventions(parent, cand.candidate_id)
    await set_lock(
        parent, kind="layer_field", target_id="schools",
        field_paths=["paint.opacity"], reason="用户锁定",
    )
    receipt = await adopt_candidate(
        parent, cand.candidate_id, AdoptSelection(mode="all"),
        origin="user", actor="user_workbench",
    )
    assert receipt.applied_count == 0
    assert all(i.verdict == "lock_blocked" for i in receipt.items)
    spec = await mapspec_store_instance.get_mapspec(parent)
    school = next(l for l in spec.get("layers", []) if l.get("id") == "schools")
    assert school.get("paint", {}).get("opacity") != 0.3, "锁定字段绝不被候选覆盖"
    # 全拒绝决议：候选状态不动（仍可比较），父 revision 严格不变
    assert (await get_candidate(parent, cand.candidate_id)).state == "comparable"
