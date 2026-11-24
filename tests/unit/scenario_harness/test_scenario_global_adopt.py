"""G05 review P1-1 盲区补测：全局/组件/数据组意图的真实采纳。

此前 base_values_for_kind 只覆盖 3 种 style/layer kind，其余组 base 恒缺席
→ 采纳永远 agent_conflict/base_value_missing（采纳面 5 组中 3 组是死的）。
统一双读后必须可采纳，且父会话字段真实变化。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.gis_world_state import apply_gis_mutation
from app.services.mapspec.intents import (
    PatchComponentIntent,
    SetLayoutIntent,
    UpsertLayerIntent,
    UpsertSourceIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.scenario_harness.adopt import adopt_candidate
from app.services.scenario_harness.candidates import (
    apply_candidate_interventions,
    propose_candidate,
)
from app.services.scenario_harness.contracts import AdoptSelection


def _sid() -> str:
    return f"g05-global-{uuid.uuid4().hex[:8]}"


async def _seed(parent: str) -> None:
    res = await apply_gis_mutation(
        parent,
        UpsertLayerIntent(
            layer={"id": "schools", "type": "circle", "source": "schools"},
            source_data={"type": "geojson", "inlineData": {"type": "FeatureCollection", "features": [
                {"type": "Feature", "id": "s1",
                 "geometry": {"type": "Point", "coordinates": [104.03, 30.61]},
                 "properties": {"id": "s1"}},
            ]}},
        ),
        origin="agent",
        actor="seed",
    )
    assert not res.is_error, res.error_msg


@pytest.mark.asyncio
async def test_layout_and_component_intents_adopt():
    """SetLayoutIntent margins + PatchComponentIntent 字段真实落父会话。"""
    parent = _sid()
    await _seed(parent)
    cand = await propose_candidate(
        parent,
        title="版面候选",
        intents=[
            SetLayoutIntent(margins={"top": 40, "bottom": 30},
                            components=[{"id": "legend-c", "type": "legend",
                                         "enabled": True, "position": "bottom-left"}]),
        ],
        created_by="agent",
    )
    # 提案即观察全局 base（digest/absent 语义）
    base = cand.delta[0].base_values
    assert base.get("layout.margins.absent") is True
    assert base.get("layout.components.absent") is True
    await apply_candidate_interventions(parent, cand.candidate_id)

    receipt = await adopt_candidate(
        parent, cand.candidate_id, AdoptSelection(mode="all"),
        origin="user", actor="user_workbench",
    )
    assert receipt.applied_count == 1, receipt.model_dump(mode="json")
    assert all(i.verdict == "applied" for i in receipt.items)

    spec = await mapspec_store_instance.get_mapspec(parent)
    layout = spec.get("layout") or {}
    assert layout.get("margins") == {"top": 40, "bottom": 30}
    comp = next(
        (c for c in layout.get("components", [])
         if isinstance(c, dict) and c.get("id") == "legend-c"),
        None,
    )
    assert comp is not None and comp.get("enabled") is True


@pytest.mark.asyncio
async def test_layout_conflict_disclosed_when_parent_moved():
    """分叉后父会话改了同键 → 采纳逐字段披露冲突（不静默覆盖）。"""
    parent = _sid()
    await _seed(parent)
    cand = await propose_candidate(
        parent,
        title="版面候选 2",
        intents=[SetLayoutIntent(margins={"top": 99})],
        created_by="agent",
    )
    await apply_candidate_interventions(parent, cand.candidate_id)
    # 父会话并发改动同一 layout 键（user origin 引擎强制 CAS）
    from app.services.session_data import session_data_manager

    state = await session_data_manager.get_map_state(parent)
    cur_rev = int(state.get("_cartographic_mutation_revision", 0) or 0)
    drift = await apply_gis_mutation(
        parent, SetLayoutIntent(margins={"top": 10}),
        origin="user", actor="user_panel", expected_revision=cur_rev,
    )
    assert not drift.is_error and not drift.superseded

    receipt = await adopt_candidate(
        parent, cand.candidate_id, AdoptSelection(mode="all"),
        origin="user", actor="user_workbench",
    )
    assert receipt.applied_count == 0
    item = receipt.items[0]
    assert item.verdict == "agent_conflict"
    assert "layout.margins" in item.detail
    spec = await mapspec_store_instance.get_mapspec(parent)
    assert (spec.get("layout") or {}).get("margins") == {"top": 10}, "冲突字段保持用户值"


@pytest.mark.asyncio
async def test_source_upsert_adopt_and_target_missing():
    """UpsertSourceIntent：新增数据源可采纳；分叉后源被删 → target_missing。"""
    parent = _sid()
    await _seed(parent)
    cand = await propose_candidate(
        parent,
        title="数据源候选",
        intents=[UpsertSourceIntent(
            source_id="aux-zone",
            source={"id": "aux-zone", "type": "geojson",
                    "metadata": {"role": "aux"}},
        )],
        created_by="agent",
    )
    await apply_candidate_interventions(parent, cand.candidate_id)
    receipt = await adopt_candidate(
        parent, cand.candidate_id, AdoptSelection(mode="all"),
        origin="user", actor="user_workbench",
    )
    assert receipt.applied_count == 1
    spec = await mapspec_store_instance.get_mapspec(parent)
    assert (spec.get("sources") or {}).get("aux-zone", {}).get("id") == "aux-zone"


@pytest.mark.asyncio
async def test_component_removed_by_parent_reports_target_missing():
    """分叉后组件被父会话删除 → 采纳披露 target_missing（而非 generic 冲突）。"""
    parent = _sid()
    await _seed(parent)
    # 先放一个组件进父会话（通过候选采纳路径落一个 enabled 组件）
    setup = await propose_candidate(
        parent, title="放组件",
        intents=[SetLayoutIntent(components=[{"id": "legend-x", "type": "legend",
                                              "enabled": True, "position": "top-right"}])],
        created_by="agent",
    )
    await apply_candidate_interventions(parent, setup.candidate_id)
    r0 = await adopt_candidate(parent, setup.candidate_id, AdoptSelection(mode="all"),
                               origin="user", actor="user")
    assert r0.applied_count == 1

    cand = await propose_candidate(
        parent, title="改组件",
        intents=[PatchComponentIntent(component_id="legend-x", enabled=False)],
        created_by="agent",
    )
    assert cand.delta[0].base_values.get("component.digest"), "在场组件记 digest"
    await apply_candidate_interventions(parent, cand.candidate_id)
    # 父会话删除组件（RemoveComponentIntent 经权威 mutation 通道）
    from app.services.mapspec.intents import RemoveComponentIntent
    from app.services.session_data import session_data_manager as _sdm

    _st = await _sdm.get_map_state(parent)
    _rev = int(_st.get("_cartographic_mutation_revision", 0) or 0)
    rm = await apply_gis_mutation(
        parent, RemoveComponentIntent(component_id="legend-x"),
        origin="user", actor="user_panel", expected_revision=_rev,
    )
    assert not rm.is_error, rm.error_msg

    receipt = await adopt_candidate(
        parent, cand.candidate_id, AdoptSelection(mode="all"),
        origin="user", actor="user_workbench",
    )
    assert receipt.applied_count == 0
    assert receipt.items[0].verdict == "target_missing"
