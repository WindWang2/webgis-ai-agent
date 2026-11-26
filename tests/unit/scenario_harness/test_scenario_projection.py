"""G05 WP1 单测：scenario 情境投影 + 语义指代解析 + compiler 接线。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.scenario_harness.contracts import (
    DeltaIntent,
    ScenarioBaseline,
    ScenarioCandidate,
    group_of_kind,
)
from app.services.scenario_harness.locks import set_lock
from app.services.scenario_harness.projection import (
    resolve_scenario_reference,
    scenario_situation_fact,
    scenario_situation_value,
)
from app.services.scenario_harness.store import (
    upsert_registry_entry,
)
from app.services.session_data import MemorySessionStore


def _cand(cid: str, sid: str, state="draft", baseline="b1", seq=1) -> ScenarioCandidate:
    return ScenarioCandidate(
        candidate_id=cid,
        session_id=sid,
        branch_session_id=f"{sid}__wif_sc{cid}",
        branch_id=f"sc{cid}",
        baseline_id=baseline,
        state=state,
        delta=[
            DeltaIntent(
                seq=1,
                kind="PatchLayerStyleIntent",
                group=group_of_kind("PatchLayerStyleIntent"),
                target_id="L1",
                field_paths=["paint.circle-color"],
            )
        ],
        created_at=f"2026-01-0{seq}T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_projection_unknown_when_registry_absent():
    store = MemorySessionStore()
    fact = await scenario_situation_fact("g05-empty", store=store)
    assert fact.status == "unknown"


@pytest.mark.asyncio
async def test_projection_known_with_rows_and_locks():
    sid = "g05-proj"
    store = MemorySessionStore()
    await upsert_registry_entry(
        sid,
        "_scenario_baselines",
        ScenarioBaseline(
            baseline_id="b1",
            session_id=sid,
            mapspec_revision=3,
            mapspec_fingerprint="f" * 64,
            captured_at="2026-01-01T00:00:00Z",
        ),
        store=store,
    )
    await upsert_registry_entry(
        sid,
        "_scenario_candidates",
        _cand("c1", sid, state="comparable", seq=1),
        store=store,
    )
    await upsert_registry_entry(
        sid,
        "_scenario_candidates",
        _cand("c2", sid, state="adopted", seq=2),
        store=store,
    )
    await set_lock(
        sid,
        kind="layer_field",
        target_id="L1",
        field_paths=["paint.circle-color"],
        reason="配色定死",
        store=store,
    )

    value, corrupt = await scenario_situation_value(sid, store=store)
    assert corrupt == []
    assert value is not None
    assert value["total"] == 2
    assert value["adopted"] == 1
    assert value["baseline"]["baseline_id"] == "b1"
    assert value["baseline"]["mapspec_revision"] == 3
    assert value["locks"][0]["target_id"] == "L1"
    row = value["candidates"][0]
    assert row["candidate_id"] == "c1"
    assert row["state"] == "comparable"
    assert row["groups"] == ["style"]

    fact = await scenario_situation_fact(sid, store=store)
    assert fact.status == "known"
    assert fact.value == value


@pytest.mark.asyncio
async def test_projection_corruption_disclosed_not_swallowed():
    sid = "g05-corrupt"
    store = MemorySessionStore()
    state = store._map_state.setdefault(sid, {})
    state["_scenario_candidates"] = {
        "c1": _cand("c1", sid).model_dump(mode="json"),
        "bad": {"nonsense": True},
    }
    value, corrupt = await scenario_situation_value(sid, store=store)
    assert any("schema" in c for c in corrupt)
    assert value is not None and "corrupt_entries" in value


@pytest.mark.asyncio
async def test_resolve_reference_vocabulary():
    sid = "g05-resolve"
    store = MemorySessionStore()
    await upsert_registry_entry(
        sid,
        "_scenario_candidates",
        _cand("sc-a", sid, state="comparable", seq=1),
        store=store,
    )
    await upsert_registry_entry(
        sid,
        "_scenario_candidates",
        _cand("sc-b", sid, state="comparable", seq=2),
        store=store,
    )
    await upsert_registry_entry(
        sid,
        "_scenario_candidates",
        _cand("sc-c", sid, state="adopted", seq=3),
        store=store,
    )

    assert await resolve_scenario_reference(sid, "current", store) == "sc-b"
    assert await resolve_scenario_reference(sid, "上一个方案", store) == "sc-a"
    assert await resolve_scenario_reference(sid, "已采纳", store) == "sc-c"
    assert await resolve_scenario_reference(sid, "sc-a", store) == "sc-a"
    # 标题全等匹配
    titled = _cand("sc-d", sid, seq=4)
    titled.title = "成都学校 B 方案"
    await upsert_registry_entry(sid, "_scenario_candidates", titled, store=store)
    assert await resolve_scenario_reference(sid, "成都学校 B 方案", store) == "sc-d"
    # 解析不了 → None（绝不随机挑）
    assert await resolve_scenario_reference(sid, "不存在的东西", store) is None
    assert await resolve_scenario_reference(sid, "", store) is None


@pytest.mark.asyncio
async def test_compile_situation_wires_scenarios_fact():
    """端到端：compile_situation 的 analysis.scenarios 来自注册表投影。"""
    from unittest.mock import MagicMock

    from app.services.gis_situation.compiler import compile_situation

    sid = "g05-compile"
    store = MemorySessionStore()
    await upsert_registry_entry(
        sid, "_scenario_candidates", _cand("c1", sid, state="comparable"), store=store
    )

    with patch("app.services.mapspec.store.mapspec_store_instance", new=MagicMock()):
        situation = await compile_situation(sid, store=store)
    fact = situation.analysis.scenarios
    assert fact.status == "known"
    assert fact.source == "map_state._scenario_candidates"
    assert fact.value["total"] == 1
    assert fact.value["candidates"][0]["candidate_id"] == "c1"


@pytest.mark.asyncio
async def test_compile_situation_without_registry_stays_unknown():
    from unittest.mock import MagicMock

    from app.services.gis_situation.compiler import compile_situation

    store = MemorySessionStore()
    with patch("app.services.mapspec.store.mapspec_store_instance", new=MagicMock()):
        situation = await compile_situation("g05-noreg", store=store)
    assert situation.analysis.scenarios.status == "unknown"
