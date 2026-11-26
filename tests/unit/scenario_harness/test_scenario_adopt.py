"""G05 WP4 单测：选择性采纳 / 回退事务（真实引擎播种，不 mock）。

播种方式（与 test_whatif_branching 同款机器）：MapSpecLifecycleEngine 真实
upsert 播种父会话 → ``ScenarioBranchManager().create_branch`` 真实 fork →
分支上真实 ``apply_gis_mutation`` 干预 → 候选注册表条目 + delta 用
``serialize_intent`` 序列化（base_values 记录提案时父会话观察值）手工登记
（candidates.py 属并行代理 A，不 import）。

场景矩阵（每条一个 test）：
1. 无冲突采纳：checkpoint 锚在场、revision 推进、候选转 adopted；
2. 选择性：groups 只应用所选组；seqs 模式 + 新层 upsert 重放；
3. 用户锁定：命中条目 lock_blocked（lock_id 在 detail），其余照常 applied；
4. 三方冲突：父会话真实改过 base 记录的同层同 paint 键 → agent_conflict，
   其余条照常 applied（部分成功如实）；
5. 幂等：同选集二次 adopt → 同一 adoption_id，revision 不再推进；
6. CAS/stale：expected_revision 过期 → adopt_superseded（零副作用）；
7. revert：父字段回到采纳前、候选回 comparable、回退后可重采纳；
8. 全拒绝决议：全条 lock_blocked → applied_count=0 回执照落、父 revision
   不变、候选仍 comparable；
9. 非法状态：draft 候选 → adopt_not_comparable；未知 candidate_id 报错。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.services.gis_world_state import apply_gis_mutation
from app.services.mapspec.intents import (
    PatchLayerStyleIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine
from app.services.mapspec.store import BASE_STORAGE_DIR, mapspec_store_instance
from app.services.scenario_harness.contracts import (
    REGISTRY_ADOPTIONS,
    REGISTRY_BASELINES,
    REGISTRY_CANDIDATES,
    AdoptSelection,
    DeltaIntent,
    ScenarioBaseline,
    ScenarioCandidate,
)
from app.services.scenario_harness.intents_codec import serialize_intent
from app.services.scenario_harness.locks import set_lock
from app.services.scenario_harness.adopt import adopt_candidate, revert_adoption
from app.services.scenario_harness.store import (
    RegistryError,
    get_registry_entry,
    load_registry,
    upsert_registry_entry,
    utc_now_iso,
)
from app.services.session_data import session_data_manager
from app.services.simulation.whatif.branch_manager import (
    ScenarioBranchManager,
    fingerprint_mapspec,
)


# ────────────────────────── fixtures / 播种助手 ──────────────────────────


def _sid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _poly_fc(cells: List[Tuple[str, float, float, float, Dict[str, Any]]]) -> dict:
    """以经纬度半宽构造方形 Polygon FeatureCollection（inlineData 用）。"""
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
    """父会话播种两层（绿地 fill + 道路 line），真实引擎逐层 upsert。"""
    engine = MapSpecLifecycleEngine()
    seeds = [
        (
            "park-a",
            "park-src",
            "fill",
            _poly_fc([("park-1", 104.0, 30.6, 0.001, {"kind": "park"})]),
        ),
        (
            "road-b",
            "road-src",
            "line",
            _poly_fc([("road-1", 104.0, 30.5, 0.001, {"kind": "arterial"})]),
        ),
    ]
    for layer_id, source_id, geom, fc in seeds:
        res = await engine.apply_mutation(
            sid,
            UpsertLayerIntent(
                layer={"id": layer_id, "type": geom, "source": source_id},
                source_data={"type": "geojson", "inlineData": fc},
            ),
        )
        assert not res.is_error, res.error_msg


async def _parent_revision(sid: str) -> int:
    state = await session_data_manager.get_map_state(sid)
    return int(state.get("_cartographic_mutation_revision", 0) or 0)


async def _parent_paint(sid: str, layer_id: str, key: str) -> Optional[Any]:
    """读父会话层 paint 值（键缺席 → None；层缺席 → 断言失败）。"""
    spec = await mapspec_store_instance.get_mapspec(sid)
    for layer in spec.get("layers", []) or []:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            return (layer.get("paint") or {}).get(key)
    raise AssertionError(f"layer {layer_id} missing in parent spec")


async def _parent_layer_ids(sid: str) -> List[str]:
    spec = await mapspec_store_instance.get_mapspec(sid)
    return [
        str(layer.get("id"))
        for layer in spec.get("layers", []) or []
        if isinstance(layer, dict)
    ]


async def _make_baseline(sid: str) -> str:
    """共享基准登记（真实 spec 指纹 + 当前 revision 锚）。"""
    spec = await mapspec_store_instance.get_mapspec(sid)
    baseline = ScenarioBaseline(
        baseline_id="b1",
        session_id=sid,
        mapspec_revision=await _parent_revision(sid),
        mapspec_fingerprint=fingerprint_mapspec(spec),
        captured_at=utc_now_iso(),
    )
    await upsert_registry_entry(sid, REGISTRY_BASELINES, baseline)
    return baseline.baseline_id


async def _build_candidate(
    sid: str,
    branch_id: str,
    baseline_id: str,
    plan: List[Tuple[Any, Dict[str, Any]]],
) -> ScenarioCandidate:
    """真实 fork + 分支干预 + delta 序列化 + 注册表登记 → comparable 候选。

    plan: [(intent, base_values)] —— base_values 是提案时对父会话的观察值
    （键缺失项显式记 None = 观察到缺席；新对象记 layer.absent=True）。
    """
    mgr = ScenarioBranchManager()
    meta = await mgr.create_branch(sid, branch_id)
    deltas: List[DeltaIntent] = []
    for i, (intent, base_values) in enumerate(plan, start=1):
        res = await apply_gis_mutation(
            meta.branch_session_id, intent, origin="agent", actor="scenario-test"
        )
        assert not res.is_error, res.error_msg
        delta = serialize_intent(intent, seq=i)
        delta.base_values = dict(base_values)
        deltas.append(delta)
    cand = ScenarioCandidate(
        candidate_id=f"cand-{branch_id}",
        session_id=sid,
        branch_session_id=meta.branch_session_id,
        branch_id=branch_id,
        baseline_id=baseline_id,
        fork_revision=meta.fork_revision,
        state="comparable",
        delta=deltas,
        applied_seqs=[d.seq for d in deltas],
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    await upsert_registry_entry(sid, REGISTRY_CANDIDATES, cand)
    return cand


async def _build_style_candidate_with_observed_base(
    sid: str,
    branch_id: str,
    baseline_id: str,
    entries: List[Tuple[str, Dict[str, Any]]],
) -> ScenarioCandidate:
    """样式候选：base_values 取提案时刻父会话真实 paint 值（缺席记 None）。"""
    plan: List[Tuple[Any, Dict[str, Any]]] = []
    for layer_id, paint in entries:
        base_values = {}
        for key in paint:
            base_values[f"paint.{key}"] = await _parent_paint(sid, layer_id, key)
        intent = PatchLayerStyleIntent(layer_id=layer_id, paint=paint)
        plan.append((intent, base_values))
    return await _build_candidate(sid, branch_id, baseline_id, plan)


# ────────────────────────────── 场景矩阵 ──────────────────────────────


async def test_t1_clean_adopt_applies_and_anchors_checkpoint():
    """无冲突采纳：父会话字段已变、revision 推进、checkpoint 锚在场。"""
    sid = _sid("adopt-t1")
    await _seed_parent(sid)
    await _make_baseline(sid)
    cand = await _build_style_candidate_with_observed_base(
        sid, "scc1", "b1", [("park-a", {"fill-opacity": 0.4})]
    )
    rev0 = await _parent_revision(sid)

    receipt = await adopt_candidate(sid, cand.candidate_id, AdoptSelection())

    assert receipt.applied_count == 1
    assert receipt.skipped_count == 0
    assert receipt.session_id == sid
    assert receipt.candidate_id == cand.candidate_id
    item = receipt.find_item(1)
    assert item is not None and item.verdict == "applied"
    # checkpoint 本身是 mutation（+1），样式重放再 +1
    assert receipt.base_revision == rev0 + 1
    assert receipt.final_revision == rev0 + 2
    assert await _parent_revision(sid) == rev0 + 2
    # 父会话权威态已变
    assert await _parent_paint(sid, "park-a", "fill-opacity") == 0.4
    # 采纳前锚真实落盘（磁盘 checkpoint 目录在场）
    ckpt_dir = BASE_STORAGE_DIR / sid / "checkpoints" / receipt.pre_adopt_checkpoint_id
    assert receipt.pre_adopt_checkpoint_id and ckpt_dir.is_dir()
    assert receipt.idempotency_key
    assert receipt.selected_groups == ["style"]
    # 候选转 adopted 并指向回执
    cand_after = await get_registry_entry(sid, REGISTRY_CANDIDATES, cand.candidate_id)
    assert cand_after is not None
    assert cand_after.state == "adopted"
    assert cand_after.adoption_id == receipt.adoption_id


async def test_t2_groups_selection_applies_only_selected_group():
    """选择性：groups=["style"] 只应用样式组；seqs 模式可单选 upsert 项。"""
    sid = _sid("adopt-t2")
    await _seed_parent(sid)
    await _make_baseline(sid)
    zones_fc = _poly_fc([("zone-1", 104.0, 30.7, 0.001, {"kind": "zone"})])
    upsert = UpsertLayerIntent(
        layer={"id": "zones-x", "type": "fill", "source": "zones-src"},
        source_data={"type": "geojson", "inlineData": zones_fc},
    )
    cand = await _build_style_candidate_with_observed_base(
        sid, "scc1", "b1", [("park-a", {"fill-color": "#006400"})]
    )
    # 追加 analysis upsert 干预（新层：提案时父层缺席）
    style_delta = cand.delta[0]
    upsert_delta = serialize_intent(upsert, seq=2)
    upsert_delta.base_values = {"layer.absent": True}
    cand.delta = [style_delta, upsert_delta]
    cand.applied_seqs = [1, 2]
    await upsert_registry_entry(sid, REGISTRY_CANDIDATES, cand)

    receipt = await adopt_candidate(
        sid, cand.candidate_id, AdoptSelection(mode="groups", groups=["style"])
    )

    assert receipt.applied_count == 1
    assert receipt.find_item(1).verdict == "applied"
    assert receipt.find_item(2).verdict == "not_selected"
    assert receipt.selected_groups == ["style"]
    assert await _parent_paint(sid, "park-a", "fill-color") == "#006400"
    assert "zones-x" not in await _parent_layer_ids(sid)

    # seqs 模式：另一候选单选 upsert 序号 → 新层重放进父会话
    cand2 = await _build_style_candidate_with_observed_base(
        sid, "scc2", "b1", [("road-b", {"line-opacity": 0.9})]
    )
    cand2.delta = [cand2.delta[0], serialize_intent(upsert, seq=2)]
    cand2.delta[1].base_values = {"layer.absent": True}
    cand2.applied_seqs = [1, 2]
    await upsert_registry_entry(sid, REGISTRY_CANDIDATES, cand2)

    r2 = await adopt_candidate(
        sid, cand2.candidate_id, AdoptSelection(mode="seqs", seqs=[2])
    )
    assert r2.applied_count == 1
    assert r2.find_item(1).verdict == "not_selected"
    assert r2.find_item(2).verdict == "applied"
    assert "zones-x" in await _parent_layer_ids(sid)
    assert await _parent_paint(sid, "road-b", "line-opacity") is None


async def test_t3_user_lock_blocks_target_item_only():
    """用户锁定：命中条目 lock_blocked（lock_id 在 detail），其余照常 applied。"""
    sid = _sid("adopt-t3")
    await _seed_parent(sid)
    await _make_baseline(sid)
    cand = await _build_style_candidate_with_observed_base(
        sid,
        "scc1",
        "b1",
        [("park-a", {"fill-opacity": 0.4}), ("road-b", {"line-opacity": 0.5})],
    )
    lock = await set_lock(
        sid,
        kind="layer_field",
        target_id="park-a",
        field_paths=["paint.fill-opacity"],
        reason="绿地配色定死",
    )

    receipt = await adopt_candidate(sid, cand.candidate_id, AdoptSelection())

    blocked = receipt.find_item(1)
    applied = receipt.find_item(2)
    assert blocked is not None and blocked.verdict == "lock_blocked"
    assert lock.lock_id in blocked.detail
    assert applied is not None and applied.verdict == "applied"
    assert receipt.applied_count == 1
    # 被锁层未动，未锁层已应用
    assert await _parent_paint(sid, "park-a", "fill-opacity") is None
    assert await _parent_paint(sid, "road-b", "line-opacity") == 0.5


async def test_t4_parent_mutation_conflicts_base_recorded_value():
    """三方冲突：父会话真实改过 base 记录的同层同 paint 键 → agent_conflict。"""
    sid = _sid("adopt-t4")
    await _seed_parent(sid)
    await _make_baseline(sid)
    cand = await _build_style_candidate_with_observed_base(
        sid,
        "scc1",
        "b1",
        [("park-a", {"fill-opacity": 0.4}), ("road-b", {"line-opacity": 0.5})],
    )
    # 提案后父会话真实 mutation：同层同 paint 键（base 记录的缺席值被改 0.9）
    res = await apply_gis_mutation(
        sid,
        PatchLayerStyleIntent(layer_id="park-a", paint={"fill-opacity": 0.9}),
        origin="user",
        actor="user-panel",
        expected_revision=await _parent_revision(sid),
    )
    assert not res.is_error, res.error_msg

    receipt = await adopt_candidate(sid, cand.candidate_id, AdoptSelection())

    conflicted = receipt.find_item(1)
    applied = receipt.find_item(2)
    assert conflicted is not None and conflicted.verdict == "agent_conflict"
    assert "fill-opacity" in conflicted.detail
    assert applied is not None and applied.verdict == "applied"
    assert receipt.applied_count == 1
    assert receipt.skipped_count == 1
    # 部分成功如实：冲突层保持用户改后的值，其余层重放
    assert await _parent_paint(sid, "park-a", "fill-opacity") == 0.9
    assert await _parent_paint(sid, "road-b", "line-opacity") == 0.5


async def test_t5_idempotent_readopt_returns_same_receipt():
    """幂等：同选集二次 adopt → 同一 adoption_id，revision 不再推进。"""
    sid = _sid("adopt-t5")
    await _seed_parent(sid)
    await _make_baseline(sid)
    cand = await _build_style_candidate_with_observed_base(
        sid, "scc1", "b1", [("park-a", {"fill-opacity": 0.4})]
    )
    receipt1 = await adopt_candidate(sid, cand.candidate_id, AdoptSelection())
    rev_after = await _parent_revision(sid)
    assert receipt1.applied_count == 1

    receipt2 = await adopt_candidate(sid, cand.candidate_id, AdoptSelection())

    assert receipt2.adoption_id == receipt1.adoption_id
    assert receipt2.idempotency_key == receipt1.idempotency_key
    assert await _parent_revision(sid) == rev_after, "幂等命中不得重放 mutation"
    entries = await load_registry(sid, REGISTRY_ADOPTIONS)
    assert len(entries) == 1


async def test_t6_stale_expected_revision_superseded_without_side_effects():
    """CAS/stale：expected_revision 过期 → adopt_superseded，零副作用。"""
    sid = _sid("adopt-t6")
    await _seed_parent(sid)
    await _make_baseline(sid)
    cand = await _build_style_candidate_with_observed_base(
        sid, "scc1", "b1", [("park-a", {"fill-opacity": 0.4})]
    )
    rev0 = await _parent_revision(sid)

    with pytest.raises(RegistryError) as ei:
        await adopt_candidate(
            sid, cand.candidate_id, AdoptSelection(expected_revision=0)
        )
    assert ei.value.code == "adopt_superseded"
    # 零副作用：revision 不动、无回执、候选状态不变
    assert await _parent_revision(sid) == rev0
    assert await load_registry(sid, REGISTRY_ADOPTIONS) == {}
    cand_after = await get_registry_entry(sid, REGISTRY_CANDIDATES, cand.candidate_id)
    assert cand_after is not None and cand_after.state == "comparable"


async def test_t7_revert_restores_parent_and_allows_readopt():
    """revert：父字段回到采纳前、候选回 comparable、回退后重采纳允许。"""
    sid = _sid("adopt-t7")
    await _seed_parent(sid)
    await _make_baseline(sid)
    cand = await _build_style_candidate_with_observed_base(
        sid, "scc1", "b1", [("park-a", {"fill-opacity": 0.4})]
    )
    receipt = await adopt_candidate(sid, cand.candidate_id, AdoptSelection())
    assert await _parent_paint(sid, "park-a", "fill-opacity") == 0.4
    rev_adopted = await _parent_revision(sid)

    reverted = await revert_adoption(sid, receipt.adoption_id)

    assert reverted.adoption_id == receipt.adoption_id
    assert reverted.reverted_by
    # 父会话字段回到采纳前（checkpoint 快照恢复）
    assert await _parent_paint(sid, "park-a", "fill-opacity") is None
    assert await _parent_revision(sid) == rev_adopted + 1
    # 候选回 comparable（分支仍在），adoption_id 保留审计
    cand_after = await get_registry_entry(sid, REGISTRY_CANDIDATES, cand.candidate_id)
    assert cand_after is not None
    assert cand_after.state == "comparable"
    assert cand_after.adoption_id == receipt.adoption_id

    # 回退后重采纳：新回执（revision 已变 → 新幂等键），照常应用
    receipt2 = await adopt_candidate(sid, cand.candidate_id, AdoptSelection())
    assert receipt2.adoption_id != receipt.adoption_id
    assert receipt2.applied_count == 1
    assert await _parent_paint(sid, "park-a", "fill-opacity") == 0.4


async def test_t8_all_rejected_resolution_writes_receipt_without_mutation():
    """全拒绝决议：全条 lock_blocked → applied_count=0 回执照落，父态不动。"""
    sid = _sid("adopt-t8")
    await _seed_parent(sid)
    await _make_baseline(sid)
    cand = await _build_style_candidate_with_observed_base(
        sid,
        "scc1",
        "b1",
        [("park-a", {"fill-opacity": 0.4}), ("road-b", {"line-opacity": 0.5})],
    )
    await set_lock(
        sid,
        kind="layer_field",
        target_id="park-a",
        field_paths=["paint.fill-opacity"],
        reason="配色定死",
    )
    await set_lock(
        sid,
        kind="layer_field",
        target_id="road-b",
        field_paths=["paint.line-opacity"],
        reason="道路样式定死",
    )
    rev0 = await _parent_revision(sid)

    receipt = await adopt_candidate(sid, cand.candidate_id, AdoptSelection())

    assert receipt.applied_count == 0
    assert receipt.skipped_count == 2
    assert all(item.verdict == "lock_blocked" for item in receipt.items)
    assert receipt.pre_adopt_checkpoint_id == ""
    assert receipt.base_revision == rev0
    assert receipt.final_revision == rev0, "全拒绝不执行 mutation"
    assert await _parent_revision(sid) == rev0
    # 回执照常落账；候选状态不变（仍 comparable，可换选集重试）
    entries = await load_registry(sid, REGISTRY_ADOPTIONS)
    assert len(entries) == 1
    cand_after = await get_registry_entry(sid, REGISTRY_CANDIDATES, cand.candidate_id)
    assert cand_after is not None and cand_after.state == "comparable"


async def test_t9_invalid_state_and_unknown_candidate_rejected():
    """非法状态：draft 候选 → adopt_not_comparable；未知 candidate_id 报错。"""
    sid = _sid("adopt-t9")
    await _seed_parent(sid)
    draft = ScenarioCandidate(
        candidate_id="cand-draft",
        session_id=sid,
        branch_session_id=f"{sid}__wif_sccdraft",
        branch_id="sccdraft",
        baseline_id="b1",
        state="draft",
        created_at=utc_now_iso(),
    )
    await upsert_registry_entry(sid, REGISTRY_CANDIDATES, draft)

    with pytest.raises(RegistryError) as ei1:
        await adopt_candidate(sid, "cand-draft", AdoptSelection())
    assert ei1.value.code == "adopt_not_comparable"
    assert "draft" in ei1.value.message

    with pytest.raises(RegistryError) as ei2:
        await adopt_candidate(sid, "cand-unknown", AdoptSelection())
    assert ei2.value.code == "candidate_not_found"


async def test_t10_base_value_missing_is_conservative_conflict():
    """诚实性：base_values 无记录的字段（未观察基线）按 agent_conflict 拒。"""
    sid = _sid("adopt-t10")
    await _seed_parent(sid)
    await _make_baseline(sid)
    intent = PatchLayerStyleIntent(layer_id="park-a", paint={"fill-opacity": 0.4})
    # base_values 留空 = 提案时未观察父会话基线
    cand = await _build_candidate(sid, "scc1", "b1", [(intent, {})])
    rev0 = await _parent_revision(sid)

    receipt = await adopt_candidate(sid, cand.candidate_id, AdoptSelection())

    item = receipt.find_item(1)
    assert item is not None and item.verdict == "agent_conflict"
    assert "base_value_missing" in item.detail
    assert receipt.applied_count == 0
    assert await _parent_revision(sid) == rev0
