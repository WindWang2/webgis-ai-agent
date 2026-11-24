"""G05 WP2 单测：候选生命周期（propose / apply / 状态机 / abandon）。

纪律：隔离验证用真实机制 —— ScenarioBranchManager 分支 + 真实
MapSpecLifecycleEngine 播种，断言"分支变、父不变"的结构性事实；
锁定拦截用 locks.set_lock 落真实 ScenarioLock，不 mock。
"""

from __future__ import annotations

import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.scenario_harness.baseline import capture_baseline
from app.services.scenario_harness.candidates import (
    abandon_candidate,
    apply_candidate_interventions,
    get_candidate,
    list_candidates,
    mark_candidate_state,
    propose_candidate,
)
from app.services.scenario_harness.contracts import (
    REGISTRY_CANDIDATES,
    DeltaIntent,
)
from app.services.scenario_harness.intents_codec import DeltaCodecError
from app.services.scenario_harness.locks import release_lock, set_lock
from app.services.scenario_harness.store import (
    RegistryError,
    load_registry,
)
from app.services.session_data import session_data_manager
from app.services.simulation.whatif.branch_manager import (
    ScenarioBranchManager,
    fingerprint_mapspec,
)
from app.services.mapspec.intents import PatchLayerStyleIntent


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


async def _parent_layer_paint(sid: str, layer_id: str) -> dict | None:
    state = await session_data_manager.get_map_state(sid)
    spec = state.get("mapspec") or {}
    for layer in spec.get("layers") or []:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            paint = layer.get("paint")
            return paint if isinstance(paint, dict) else None
    return None


# ── propose ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_propose_creates_draft_with_branch_and_base_values():
    """propose：draft 态 + sc 分支 + base_values 真实观察（存在键/缺席键）。"""
    sid = _sid("g05-cand")
    await _seed_parent(sid)
    baseline = await capture_baseline(sid)

    candidate = await propose_candidate(
        sid,
        title="调透明度",
        hypothesis="绿地降透明度后视觉层级更清晰",
        intents=[
            PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.5}),
            UpsertLayerIntent(
                layer={"id": "park-new", "type": "fill", "source": "park-new"},
                source_data={
                    "type": "geojson",
                    "inlineData": _poly_fc([("p", 104.01, 30.6, 0.001, {})]),
                },
            ),
        ],
        turn_id="t1",
    )
    assert candidate.state == "draft"
    assert candidate.branch_id == "scc1"
    assert candidate.branch_session_id == f"{sid}__wif_scc1"
    assert candidate.baseline_id == baseline.baseline_id
    assert candidate.created_at and candidate.updated_at
    assert candidate.turn_id == "t1"

    d_style, d_upsert = candidate.delta[0], candidate.delta[1]
    assert d_style.seq == 1 and d_style.target_id == "park-main"
    # 父层尚无 paint → 逐键诚实 None（键名在场，值缺席）
    assert d_style.base_values == {"paint.opacity": None}
    # 新层在父 spec 不存在 → absent 标记
    assert d_upsert.base_values == {"layer.absent": True}
    assert d_upsert.target_id == "park-new"

    # 分支隔离真实生效：分支 spec 物化，父 spec 未被触碰
    branch_spec = await session_data_manager.get_map_state(
        candidate.branch_session_id
    )
    assert branch_spec.get("mapspec")
    parent_state = await session_data_manager.get_map_state(sid)
    parent_ids = {
        layer.get("id")
        for layer in (parent_state.get("mapspec") or {}).get("layers") or []
    }
    assert "park-new" not in parent_ids


@pytest.mark.asyncio
async def test_propose_existing_layer_base_values_digest():
    """UpsertLayerIntent 命中父 spec 同 id 层 → base_values 存层指纹摘要。"""
    sid = _sid("g05-cand-dig")
    await _seed_parent(sid)
    state = await session_data_manager.get_map_state(sid)
    spec = state.get("mapspec") or {}
    existing = next(
        layer
        for layer in spec.get("layers") or []
        if layer.get("id") == "park-main"
    )

    candidate = await propose_candidate(
        sid,
        intents=[
            UpsertLayerIntent(
                layer={"id": "park-main", "type": "fill", "source": "park-main"},
                source_data={
                    "type": "geojson",
                    "inlineData": _poly_fc([("p2", 104.0, 30.6, 0.001, {})]),
                },
            )
        ],
    )
    digest = candidate.delta[0].base_values.get("layer.digest")
    assert digest
    assert digest == fingerprint_mapspec(existing)[:16]


@pytest.mark.asyncio
async def test_propose_empty_intents_rejected_without_orphan_branch():
    """空提案整笔拒绝（DeltaCodecError）；失败路径不留孤儿分支。"""
    sid = _sid("g05-cand-empty")
    await _seed_parent(sid)
    with pytest.raises(DeltaCodecError) as exc:
        await propose_candidate(sid, intents=[])
    assert exc.value.code == "delta_empty"
    mgr = ScenarioBranchManager()
    assert await mgr.list_branches(sid) == []


@pytest.mark.asyncio
async def test_propose_branch_id_avoids_whatif_namespace_collision():
    """命名防撞车：whatif 注册表已有 scc1 → 候选跳到 scc2（共享派生命名空间）。"""
    sid = _sid("g05-cand-clash")
    await _seed_parent(sid)
    mgr = ScenarioBranchManager()
    await mgr.create_branch(sid, "scc1", title="用户直连分支")
    candidate = await propose_candidate(
        sid,
        intents=[
            PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.9})
        ],
    )
    assert candidate.branch_id == "scc2"
    # 第二个候选继续递增
    candidate2 = await propose_candidate(
        sid,
        intents=[
            PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.8})
        ],
    )
    assert candidate2.branch_id == "scc3"


# ── apply / 状态机 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_changes_branch_not_parent_and_completes_to_comparable():
    sid = _sid("g05-apply")
    await _seed_parent(sid)
    candidate = await propose_candidate(
        sid,
        intents=[
            PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.5})
        ],
    )
    results = await apply_candidate_interventions(sid, candidate.candidate_id)
    assert results == [{"seq": 1, "status": "applied"}]

    # 分支变：分支 spec 的 park-main 有 paint.opacity=0.5
    branch_state = await session_data_manager.get_map_state(
        candidate.branch_session_id
    )
    branch_spec = branch_state.get("mapspec") or {}
    branch_layer = next(
        layer
        for layer in branch_spec.get("layers") or []
        if layer.get("id") == "park-main"
    )
    assert branch_layer.get("paint", {}).get("opacity") == 0.5
    # 父不变：父 spec 的 park-main 无 paint
    assert await _parent_layer_paint(sid, "park-main") is None

    updated = await get_candidate(sid, candidate.candidate_id)
    assert updated.state == "comparable"
    assert updated.applied_seqs == [1]


@pytest.mark.asyncio
async def test_apply_incremental_delta_merge_and_seq_conflict():
    sid = _sid("g05-apply-inc")
    await _seed_parent(sid)
    candidate = await propose_candidate(
        sid,
        intents=[
            PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.5})
        ],
    )
    incremental = DeltaIntent(
        seq=2,
        kind="PatchLayerStyleIntent",
        target_id="park-main",
        field_paths=["paint.fill-opacity"],
        payload={"layer_id": "park-main", "paint": {"fill-opacity": 0.7}},
    )
    # 先重放提案 seq1（deltas=None），再增量补 seq2 —— 两段路径都覆盖
    first = await apply_candidate_interventions(sid, candidate.candidate_id)
    assert [r["status"] for r in first] == ["applied"]
    results = await apply_candidate_interventions(sid, candidate.candidate_id, deltas=[incremental])
    assert [r["status"] for r in results] == ["applied"]
    updated = await get_candidate(sid, candidate.candidate_id)
    assert [d.seq for d in updated.delta] == [1, 2]
    assert updated.applied_seqs == [1, 2]
    assert updated.state == "comparable"

    # seq 冲突整笔拒绝（seq 是选择键，不可覆写）
    clash = DeltaIntent(
        seq=1,
        kind="PatchLayerStyleIntent",
        target_id="park-main",
        field_paths=["paint.opacity"],
        payload={"layer_id": "park-main", "paint": {"opacity": 1.0}},
    )
    with pytest.raises(RegistryError) as exc:
        await apply_candidate_interventions(sid, candidate.candidate_id, deltas=[clash])
    assert exc.value.code == "delta_seq_conflict"


@pytest.mark.asyncio
async def test_apply_lock_blocked():
    """锁拦截：set_lock 后 apply → lock_blocked，分支不动、态不推进。"""
    sid = _sid("g05-apply-lock")
    await _seed_parent(sid)
    candidate = await propose_candidate(
        sid,
        intents=[
            PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.5})
        ],
    )
    lock = await set_lock(
        sid,
        kind="layer_field",
        target_id="park-main",
        field_paths=["paint.opacity"],
        reason="配色定死",
    )
    results = await apply_candidate_interventions(sid, candidate.candidate_id)
    assert results == [
        {"seq": 1, "status": "lock_blocked", "error": f"lock:{lock.lock_id}"}
    ]
    updated = await get_candidate(sid, candidate.candidate_id)
    # 首次 apply 即 draft→running（契约字面迁移序；无 seq 落账、无 failed）
    assert updated.state == "running"
    assert updated.applied_seqs == []

    # 释放锁后同候选可应用（增量调用重放全部 seq）
    await release_lock(sid, lock.lock_id, reason="用户改主意")
    results2 = await apply_candidate_interventions(sid, candidate.candidate_id)
    assert results2[0]["status"] == "applied"
    updated2 = await get_candidate(sid, candidate.candidate_id)
    assert updated2.state == "comparable"


@pytest.mark.asyncio
async def test_apply_partial_failure_marks_failed_and_keeps_applied():
    """任一条 is_error → failed，且已成功的 seq 保留（不掩盖部分成功）。"""
    sid = _sid("g05-apply-fail")
    await _seed_parent(sid)
    candidate = await propose_candidate(
        sid,
        intents=[
            PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.5}),
            # 目标层在分支 spec 不存在 → handler is_error
            PatchLayerStyleIntent(layer_id="no-such-layer", paint={"opacity": 1.0}),
        ],
    )
    results = await apply_candidate_interventions(sid, candidate.candidate_id)
    by_seq = {r["seq"]: r for r in results}
    assert by_seq[1]["status"] == "applied"
    assert by_seq[2]["status"] == "error"
    updated = await get_candidate(sid, candidate.candidate_id)
    assert updated.state == "failed"
    assert updated.applied_seqs == [1]


@pytest.mark.asyncio
async def test_apply_on_archived_candidate_rejected():
    sid = _sid("g05-apply-arch")
    await _seed_parent(sid)
    candidate = await propose_candidate(
        sid,
        intents=[
            PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.5})
        ],
    )
    await abandon_candidate(sid, candidate.candidate_id, hard_delete=False)
    with pytest.raises(RegistryError) as exc:
        await apply_candidate_interventions(sid, candidate.candidate_id)
    assert exc.value.code == "candidate_state_invalid"


# ── abandon / mark / list / get ──────────────────────────────────────


@pytest.mark.asyncio
async def test_abandon_hard_delete_clears_branch_state_and_idempotent():
    sid = _sid("g05-abandon")
    await _seed_parent(sid)
    candidate = await propose_candidate(
        sid,
        intents=[
            PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.5})
        ],
    )
    await apply_candidate_interventions(sid, candidate.candidate_id)
    branch_sid = candidate.branch_session_id

    assert await abandon_candidate(sid, candidate.candidate_id, hard_delete=True) is True
    # 幂等：再删返回 False
    assert await abandon_candidate(sid, candidate.candidate_id, hard_delete=True) is False
    entries = await load_registry(sid, REGISTRY_CANDIDATES)
    assert candidate.candidate_id not in entries
    # 分支会话 map_state 已 clear（无 spec / 无 fork 存证）
    branch_state = await session_data_manager.get_map_state(branch_sid)
    assert not branch_state.get("mapspec")
    assert not branch_state.get("_whatif_fork")
    # whatif 注册表也移除
    mgr = ScenarioBranchManager()
    assert await mgr.list_branches(sid) == []


@pytest.mark.asyncio
async def test_abandon_soft_archives_keeps_branch():
    sid = _sid("g05-abandon-soft")
    await _seed_parent(sid)
    candidate = await propose_candidate(
        sid,
        intents=[
            PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.5})
        ],
    )
    assert await abandon_candidate(sid, candidate.candidate_id, hard_delete=False) is True
    updated = await get_candidate(sid, candidate.candidate_id)
    assert updated.state == "archived"
    # 分支保留（防采纳但仍可审计）
    meta = await ScenarioBranchManager().get_branch_meta(sid, candidate.branch_id)
    assert meta.branch_id == candidate.branch_id


@pytest.mark.asyncio
async def test_mark_candidate_state_roundtrip():
    sid = _sid("g05-mark")
    await _seed_parent(sid)
    candidate = await propose_candidate(
        sid,
        intents=[
            PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.5})
        ],
    )
    marked = await mark_candidate_state(
        sid, candidate.candidate_id, "stale", stale_reasons=["mapspec.revision:1->2"]
    )
    assert marked.state == "stale"
    assert marked.stale_reasons == ["mapspec.revision:1->2"]
    restored = await mark_candidate_state(sid, candidate.candidate_id, "draft", stale_reasons=[])
    assert restored.state == "draft"
    assert restored.stale_reasons == []
    with pytest.raises(RegistryError) as exc:
        await mark_candidate_state(sid, candidate.candidate_id, "bogus-state")
    assert exc.value.code == "candidate_state_invalid"


@pytest.mark.asyncio
async def test_list_candidates_stable_order_and_get_missing():
    sid = _sid("g05-list")
    await _seed_parent(sid)
    c1 = await propose_candidate(
        sid,
        intents=[PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.3})],
    )
    c2 = await propose_candidate(
        sid,
        intents=[PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.6})],
    )
    listed = await list_candidates(sid)
    assert [c.candidate_id for c in listed] == sorted(
        [c1.candidate_id, c2.candidate_id]
    )
    assert len(listed) == 2
    with pytest.raises(RegistryError) as exc:
        await get_candidate(sid, "cand-nope")
    assert exc.value.code == "candidate_not_found"
