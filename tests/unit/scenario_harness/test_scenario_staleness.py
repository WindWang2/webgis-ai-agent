"""G05 WP3 单测：基准漂移失效分级 + 重算恢复。

分级判定核心抽为纯函数 ``decide_impact_level``（漂移观察 × 候选干预面
→ level），goal_changed 等难在单测环境真实触发的分支直接对纯函数断言；
其余场景全部走真实链路：真实播种 spec、真实 mutation、真实 ref 版本推进。
"""

from __future__ import annotations

import uuid

import pytest

from app.services.mapspec.intents import PatchLayerStyleIntent
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.scenario_harness.baseline import capture_baseline
from app.services.scenario_harness.candidates import (
    apply_candidate_interventions,
    get_candidate,
    propose_candidate,
)
from app.services.scenario_harness.contracts import BaselineMove
from app.services.scenario_harness.staleness import (
    classify_candidate_impacts,
    decide_impact_level,
    restore_candidate_after_recompute,
)
from app.services.scenario_harness.store import RegistryError
from app.services.session_data import session_data_manager


def _sid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _poly_fc(cells: list[tuple[str, float, float, float, dict]]) -> dict:
    features = []
    for fid, lng, lat, half, props in cells:
        features.append(
            {
                "type": "Feature",
                "id": fid,
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [lng - half, lat - half],
                            [lng + half, lat - half],
                            [lng + half, lat + half],
                            [lng - half, lat + half],
                            [lng - half, lat - half],
                        ]
                    ],
                },
                "properties": {"id": fid, **props},
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _style_intent(layer_id: str, opacity: float) -> PatchLayerStyleIntent:
    return PatchLayerStyleIntent(layer_id=layer_id, paint={"opacity": opacity})


async def _seed_parent(sid: str) -> None:
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "park-main", "type": "fill", "source": "park-main"},
            source_data={
                "type": "geojson",
                "inlineData": _poly_fc(
                    [("park-1", 104.0, 30.6, 0.001, {"kind": "park"})]
                ),
            },
        ),
    )
    assert not res.is_error, res.error_msg


async def _mutate_parent_add_layer(sid: str, layer_id: str) -> None:
    res = await MapSpecLifecycleEngine().apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": layer_id, "type": "fill", "source": layer_id},
            source_data={
                "type": "geojson",
                "inlineData": _poly_fc([("p", 104.02, 30.6, 0.001, {})]),
            },
        ),
    )
    assert not res.is_error, res.error_msg


# ── classify：基准未动 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_all_current_when_baseline_unchanged():
    sid = _sid("g05-stale-calm")
    await _seed_parent(sid)
    candidate = await propose_candidate(sid, intents=[_style_intent("park-main", 0.5)])
    await apply_candidate_interventions(sid, candidate.candidate_id)
    await capture_baseline(sid)  # 最新基准 = 当前状态

    impacts = await classify_candidate_impacts(sid)
    assert len(impacts) == 1
    assert impacts[0].candidate_id == candidate.candidate_id
    assert impacts[0].level == "current"
    assert impacts[0].reasons == []
    # 状态不被扰动（comparable 保持）
    assert (await get_candidate(sid, candidate.candidate_id)).state == "comparable"


@pytest.mark.asyncio
async def test_classify_without_baseline_raises():
    """候选在场但基准档案被清（环形档案清理面）→ 拒绝分级，不静默 current。"""
    sid = _sid("g05-stale-nobase")
    await _seed_parent(sid)
    candidate = await propose_candidate(sid, intents=[_style_intent("park-main", 0.5)])
    # propose 经 get_or_create_baseline 必然落基准；这里模拟清理面移除全部基准
    from app.services.scenario_harness.contracts import REGISTRY_BASELINES
    from app.services.scenario_harness.store import load_registry, remove_registry_entry

    for bid in list(await load_registry(sid, REGISTRY_BASELINES)):
        await remove_registry_entry(sid, REGISTRY_BASELINES, bid)
    with pytest.raises(RegistryError) as exc:
        await classify_candidate_impacts(sid)
    assert exc.value.code == "baseline_missing"
    assert candidate.candidate_id  # 候选确实在场（前置成立）


# ── classify：spec 漂移 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recompute_required_when_drift_does_not_touch_target_fields():
    """父会话加了无关层 → fingerprint 漂移但候选目标字段未变 → recompute_required。"""
    sid = _sid("g05-stale-recomp")
    await _seed_parent(sid)
    await capture_baseline(sid)
    candidate = await propose_candidate(sid, intents=[_style_intent("park-main", 0.5)])
    await apply_candidate_interventions(sid, candidate.candidate_id)

    await _mutate_parent_add_layer(sid, "park-extra")
    impacts = await classify_candidate_impacts(sid)
    assert impacts[0].level == "recompute_required"
    assert impacts[0].reasons
    updated = await get_candidate(sid, candidate.candidate_id)
    assert updated.state == "stale"
    assert updated.stale_reasons == impacts[0].reasons


@pytest.mark.asyncio
async def test_adopt_blocked_when_parent_changed_candidate_target_fields():
    """候选已 applied 且父会话已改同层同 paint 键 → adopt_blocked（冲突裁决面）。"""
    sid = _sid("g05-stale-block")
    await _seed_parent(sid)
    await capture_baseline(sid)
    candidate = await propose_candidate(sid, intents=[_style_intent("park-main", 0.5)])
    await apply_candidate_interventions(sid, candidate.candidate_id)

    # 父会话自己改了 park-main 的 opacity（base_values 里是 None → 现在有值）
    res = await MapSpecLifecycleEngine().apply_mutation(
        sid, _style_intent("park-main", 0.9)
    )
    assert not res.is_error, res.error_msg

    impacts = await classify_candidate_impacts(sid)
    assert impacts[0].level == "adopt_blocked"


@pytest.mark.asyncio
async def test_draft_candidate_gets_recompute_not_adopt_blocked():
    """未 applied 的候选即使目标字段已被父改 → recompute_required（不是 adopt_blocked）。"""
    sid = _sid("g05-stale-draft")
    await _seed_parent(sid)
    await capture_baseline(sid)
    candidate = await propose_candidate(sid, intents=[_style_intent("park-main", 0.5)])
    assert candidate.state == "draft"

    res = await MapSpecLifecycleEngine().apply_mutation(
        sid, _style_intent("park-main", 0.9)
    )
    assert not res.is_error, res.error_msg

    impacts = await classify_candidate_impacts(sid)
    assert impacts[0].level == "recompute_required"
    updated = await get_candidate(sid, candidate.candidate_id)
    assert updated.state == "stale"


@pytest.mark.asyncio
async def test_failed_candidate_classified_but_not_remarked_stale():
    """failed 候选参与分级但状态不再同步为 stale（failed 是独立事实）。"""
    sid = _sid("g05-stale-failed")
    await _seed_parent(sid)
    await capture_baseline(sid)
    candidate = await propose_candidate(
        sid,
        intents=[
            _style_intent("park-main", 0.5),
            _style_intent("no-such-layer", 1.0),  # 必败 → failed
        ],
    )
    await apply_candidate_interventions(sid, candidate.candidate_id)
    assert (await get_candidate(sid, candidate.candidate_id)).state == "failed"

    await _mutate_parent_add_layer(sid, "park-extra")
    impacts = await classify_candidate_impacts(sid)
    assert impacts[0].level == "recompute_required"
    assert (await get_candidate(sid, candidate.candidate_id)).state == "failed"


# ── classify：数据集版本漂移 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_dataset_drift_unresolvable_only_for_analysis_data_groups():
    """数据版本推进：analysis 组 → unresolvable；style 组不受数据漂移牵连。"""
    sid = _sid("g05-stale-data")
    ref_id = await session_data_manager.store(
        sid, {"type": "FeatureCollection", "features": []}, prefix="data"
    )
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "pop-main", "type": "fill", "source": "pop-src"},
            source_data={"type": "geojson", "ref": ref_id},
        ),
    )
    assert not res.is_error, res.error_msg
    await capture_baseline(sid)

    cand_analysis = await propose_candidate(
        sid,
        intents=[
            UpsertLayerIntent(
                layer={"id": "pop-new", "type": "fill", "source": "pop-new"},
                source_data={"type": "geojson", "inlineData": _poly_fc([("p", 104.0, 30.6, 0.001, {})])},
            )
        ],
    )
    cand_style = await propose_candidate(sid, intents=[_style_intent("pop-main", 0.4)])

    # 数据版本推进（spec 不动 → fingerprint 不变，纯数据漂移）
    await session_data_manager.overwrite(
        sid, ref_id, {"type": "FeatureCollection", "features": [], "v": 2}
    )
    impacts = {i.candidate_id: i for i in await classify_candidate_impacts(sid)}
    assert impacts[cand_analysis.candidate_id].level == "unresolvable"
    assert impacts[cand_style.candidate_id].level == "current"


# ── 纯函数分支：goal_changed（单测环境无 working-context DB → 直接断言）──


class DeltaIntentHolder:
    """最小候选面（delta 为空 → 分组交集恒空；目标字段观察集为空）。"""

    delta: list = []
    applied_seqs: list = []


def test_goal_changed_yields_recompute_required():
    """goal digest 变化（目标条件变了）→ 局部重算，与目标字段是否相交无关。"""
    move = BaselineMove(
        baseline_id="sb-x",
        moved=True,
        revision_from=1,
        revision_to=1,
        fingerprint_changed=False,
        goal_changed=True,
        observed_at="2026-01-01T00:00:00Z",
    )
    candidate = DeltaIntentHolder()
    level, reasons = decide_impact_level(move, candidate, parent_spec=None)
    assert level == "recompute_required"
    assert "goal.digest_changed" in reasons


def test_unmoved_move_yields_current():
    move = BaselineMove(baseline_id="sb-x", moved=False)
    level, reasons = decide_impact_level(move, DeltaIntentHolder(), parent_spec=None)
    assert level == "current"
    assert reasons == []


# ── restore ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_to_comparable_after_baseline_refresh():
    """基准已刷新（候选绑定基准重判 current）+ stale 候选 → comparable + 清 reasons。"""
    sid = _sid("g05-restore-ok")
    await _seed_parent(sid)
    await _mutate_parent_add_layer(sid, "park-seed")  # 基准锚定在两层的现状
    await capture_baseline(sid)
    candidate = await propose_candidate(sid, intents=[_style_intent("park-main", 0.5)])
    await apply_candidate_interventions(sid, candidate.candidate_id)
    # 模拟一次已解除的失效（状态遗留 stale）
    from app.services.scenario_harness.candidates import mark_candidate_state

    await mark_candidate_state(
        sid, candidate.candidate_id, "stale", stale_reasons=["mapspec.revision:1->2"]
    )
    restored = await restore_candidate_after_recompute(sid, candidate.candidate_id)
    assert restored.state == "comparable"
    assert restored.stale_reasons == []


@pytest.mark.asyncio
async def test_restore_still_stale_raises():
    sid = _sid("g05-restore-stale")
    await _seed_parent(sid)
    await capture_baseline(sid)
    candidate = await propose_candidate(sid, intents=[_style_intent("park-main", 0.5)])
    await apply_candidate_interventions(sid, candidate.candidate_id)
    await _mutate_parent_add_layer(sid, "park-extra")  # 基准仍漂移
    from app.services.scenario_harness.candidates import mark_candidate_state

    await mark_candidate_state(
        sid, candidate.candidate_id, "stale", stale_reasons=["mapspec.revision:1->2"]
    )
    with pytest.raises(RegistryError) as exc:
        await restore_candidate_after_recompute(sid, candidate.candidate_id)
    assert exc.value.code == "still_stale"
    assert exc.value.message  # reasons 可读


@pytest.mark.asyncio
async def test_restore_graduated_state_by_applied_progress():
    """恢复态与 applied 进度一致：未应用 → draft（不谎报 comparable）。"""
    sid = _sid("g05-restore-draft")
    await _seed_parent(sid)
    await capture_baseline(sid)
    candidate = await propose_candidate(sid, intents=[_style_intent("park-main", 0.5)])
    from app.services.scenario_harness.candidates import mark_candidate_state

    await mark_candidate_state(
        sid, candidate.candidate_id, "stale", stale_reasons=["mapspec.revision:1->2"]
    )
    restored = await restore_candidate_after_recompute(sid, candidate.candidate_id)
    assert restored.state == "draft"
    assert restored.stale_reasons == []
