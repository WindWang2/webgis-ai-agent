"""Deterministic Bounded Runtime Repair Engine 测试（ADR-0088 P2/P5）。

覆盖对抗场景：
- A（missing rendered layer）：desired ✓ + artifact ✓ + runtime ✗ →
  reassert（不重跑分析）；
- B（expired artifact）：source ref 过期 → 执行债，绝不 remount；
- C（user hidden）：user-owned 隐藏 → no-op（user-wins）；
- E（style reload 语义）：registry 丢失的 spec 层 → reassert 重挂载；
- F（stale observation）：revision 不匹配 → 空计划（不修复旧发散）；
- G（repeated failure）：超预算 → exhausted（不再无限对抗）；
- 组件槽缺席（spec 启用）→ reassert_component；
- 期望可见而观察不可见 → restore_expected_visibility。
"""
import uuid

import pytest

from app.services.gis_harness.runtime_repair import (
    MAX_RUNTIME_REPAIR_PASSES,
    classify_runtime_repairs,
    run_runtime_repair,
)
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"rr-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


def _geojson():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point",
             "coordinates": [104.0, 30.6]}, "properties": {}}
        ],
    }


def _chapter():
    layer = {
        "role": "primary", "layer_id": "poi-main", "enabled": True,
        "source_capability": "poi_query",
    }
    return {
        "plan_id": "plan-test",
        "query": "成都小学分布",
        "data_requirements": [
            {"capability": "poi_query", "status": "available",
             "bound_ref": "ref:geojson-a"},
        ],
        "analysis_steps": [],
        "map_layers": [layer],
        "template_selection": {},
    }


def _mapspec(layer_id="poi-main", *, ref="ref:geojson-a", owner=None, visible=True):
    layer = {
        "id": layer_id, "source": "s-poi", "type": "circle",
        "layout": {"visibility": "visible" if visible else "none"},
    }
    if owner is not None:
        layer["cartographic_intent"] = {
            "presentation_owner": "user", "expected_visible": visible,
        }
    return {
        "layers": [layer],
        "sources": {"s-poi": {"type": "geojson", "ref_id": ref}},
        "layout": {"components": []},
    }


def _observation(revision, *, layers=None, components=None):
    return {
        "source": "frontend_runtime",
        "mapspec_revision": revision,
        "mapspec_fingerprint": "fp-test",
        "layers": layers if layers is not None else [],
        "components": components or [],
    }


def _descriptors(alive=True):
    return {"ref:geojson-a": {"feature_count": 2} if alive else None}


async def _seed_revision(sid) -> int:
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(sid, InitProjectIntent())
    await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={
                "id": "poi-main", "source": "s-poi", "type": "circle",
                "layout": {"visibility": "visible"},
            },
            source_data=_geojson(),
        ),
    )
    state = await session_data_manager.get_map_state(sid)
    return int(state.get("_cartographic_mutation_revision") or 0)


# ── Scenario A：render 缺席 + artifact 存活 → reassert ────────────────


def test_scenario_a_missing_render_reasserts_layer():
    plan = classify_runtime_repairs(
        _chapter(), _mapspec(),
        descriptors=_descriptors(alive=True),
        observation=_observation(3),
        current_revision=3,
    )
    assert plan.reassert_layers == ["poi-main"]
    assert not plan.execution_debts
    assert plan.has_actions


def test_scenario_a_runtime_layer_count_zero_reasserts():
    layers = [{"id": "poi-main", "runtime_layer_count": 0, "visible": False}]
    plan = classify_runtime_repairs(
        _chapter(), _mapspec(),
        descriptors=_descriptors(alive=True),
        observation=_observation(3, layers=layers),
        current_revision=3,
    )
    assert plan.reassert_layers == ["poi-main"]


async def test_scenario_a_repair_via_real_mutation_channel(clean_session):
    """reassert 走真实 mutation 通道：revision 前进、返回修复后 spec。"""
    revision = await _seed_revision(clean_session)
    obs = _observation(revision)
    outcome = await run_runtime_repair(
        clean_session,
        chapter=_chapter(),
        mapspec=_mapspec(),
        descriptors=_descriptors(alive=True),
        observation=obs,
        current_revision=revision,
        map_state=await session_data_manager.get_map_state(clean_session),
    )
    assert outcome.applied == ["reassert_spec_layer:poi-main"]
    assert not outcome.exhausted
    assert outcome.mutation_revision is not None
    assert int(outcome.mutation_revision) > revision
    assert isinstance(outcome.mapspec, dict)
    fresh = await session_data_manager.get_map_state(clean_session)
    assert int(fresh.get("_cartographic_mutation_revision") or 0) > revision


# ── Scenario B：artifact 过期 → 执行债，绝不 remount ──────────────────


def test_scenario_b_expired_artifact_is_execution_debt():
    plan = classify_runtime_repairs(
        _chapter(), _mapspec(),
        descriptors=_descriptors(alive=False),
        observation=_observation(3),
        current_revision=3,
    )
    assert not plan.has_actions  # 不 reassert 死 ref
    assert len(plan.execution_debts) == 1
    assert plan.execution_debts[0]["capability"] == "poi_query"
    assert plan.execution_debts[0]["ref"] == "ref:geojson-a"


def test_scenario_b_unknown_ref_is_not_debt():
    """存储探测未知（抖动）≠ 过期 —— 不误判执行债，reassert 收敛。"""
    plan = classify_runtime_repairs(
        _chapter(), _mapspec(),
        descriptors={},  # ref 不在表中 = unknown
        observation=_observation(3),
        current_revision=3,
    )
    assert plan.reassert_layers == ["poi-main"]
    assert not plan.execution_debts


# ── Scenario C：user hidden → no-op ──────────────────────────────────


def test_scenario_c_user_hidden_layer_is_noop():
    layers = [{"id": "poi-main", "runtime_layer_count": 2, "visible": False}]
    plan = classify_runtime_repairs(
        _chapter(), _mapspec(owner="user", visible=False),
        descriptors=_descriptors(),
        observation=_observation(3, layers=layers),
        current_revision=3,
    )
    assert plan.user_owned == ["poi-main"]
    assert not plan.has_actions


def test_spec_hidden_not_user_owned_is_neutral():
    """spec 期望隐藏（非 user-owned）→ 观察如实，无发散无动作。"""
    layers = [{"id": "poi-main", "runtime_layer_count": 2, "visible": False}]
    plan = classify_runtime_repairs(
        _chapter(), _mapspec(owner=None, visible=False),
        descriptors=_descriptors(),
        observation=_observation(3, layers=layers),
        current_revision=3,
    )
    assert not plan.has_actions
    assert not plan.user_owned


def test_mounted_invisible_not_user_owned_restores_visibility():
    layers = [{"id": "poi-main", "runtime_layer_count": 2, "visible": False}]
    plan = classify_runtime_repairs(
        _chapter(), _mapspec(visible=True),
        descriptors=_descriptors(),
        observation=_observation(3, layers=layers),
        current_revision=3,
    )
    assert plan.visibility_restores == ["poi-main"]


# ── Scenario F：stale observation → 空计划 ───────────────────────────


def test_scenario_f_stale_observation_never_repaired():
    plan = classify_runtime_repairs(
        _chapter(), _mapspec(),
        descriptors=_descriptors(),
        observation=_observation(2),  # 旧 revision
        current_revision=3,
    )
    assert not plan.has_actions
    assert not plan.execution_debts


def test_fully_rendered_layer_is_noop():
    layers = [{"id": "poi-main", "runtime_layer_count": 3, "visible": True}]
    plan = classify_runtime_repairs(
        _chapter(), _mapspec(),
        descriptors=_descriptors(),
        observation=_observation(3, layers=layers),
        current_revision=3,
    )
    assert not plan.has_actions


# ── 组件槽 ───────────────────────────────────────────────────────────


def test_component_enabled_but_not_observed_reasserts():
    spec = _mapspec()
    spec["layout"]["components"] = [{"id": "title", "type": "title", "enabled": True}]
    plan = classify_runtime_repairs(
        _chapter(), spec,
        descriptors=_descriptors(),
        observation=_observation(3),
        current_revision=3,
        required_slots=[["title"], ["scale_bar"]],
    )
    assert "title" in plan.reassert_components


def test_component_observed_is_noop():
    components = [
        {"id": "title", "type": "title", "mounted": True},
        {"id": "scale-bar", "type": "scale_bar", "mounted": True},
    ]
    plan = classify_runtime_repairs(
        _chapter(), _mapspec(),
        descriptors=_descriptors(),
        observation=_observation(3, components=components),
        current_revision=3,
        required_slots=[["title"], ["scale_bar"]],
    )
    assert not plan.reassert_components


def test_component_absent_from_spec_is_desired_state_debt():
    """spec 缺组件 → finalizer add_component 通道，runtime 修复不越界。"""
    mapspec = _mapspec()
    mapspec["layout"]["components"] = []
    plan = classify_runtime_repairs(
        _chapter(), mapspec,
        descriptors=_descriptors(),
        observation=_observation(3),
        current_revision=3,
        required_slots=[["categorical_legend"]],
    )
    assert not plan.reassert_components


# ── Scenario G：repeated failure → exhausted ─────────────────────────


async def _patch_mutations(monkeypatch, fail=False):
    from app.services.gis_world_state import mutation as mutation_mod

    calls = {"n": 0}

    class _Res:
        is_error = fail
        mutation_revision = 99
        superseded = False

    async def fake_apply(sid, intent, **kwargs):
        calls["n"] += 1
        return _Res() if not fail else _Res()

    async def fail_apply(sid, intent, **kwargs):
        calls["n"] += 1
        raise RuntimeError("mutation down")

    impl = fail_apply if fail else fake_apply
    monkeypatch.setattr(mutation_mod, "apply_gis_mutation", impl)
    return calls


async def test_scenario_g_exhausted_after_bounded_passes(clean_session, monkeypatch):
    await _patch_mutations(monkeypatch)
    revision = await _seed_revision(clean_session)
    outcomes = []
    for _ in range(MAX_RUNTIME_REPAIR_PASSES + 1):
        outcomes.append(await run_runtime_repair(
            clean_session,
            chapter=_chapter(),
            mapspec=_mapspec(),
            descriptors=_descriptors(),
            observation=_observation(revision),
            current_revision=revision,
            map_state=await session_data_manager.get_map_state(clean_session),
        ))
    assert outcomes[0].applied and not outcomes[0].exhausted
    assert outcomes[1].applied and not outcomes[1].exhausted
    assert outcomes[-1].exhausted and not outcomes[-1].applied


async def test_scenario_g_failing_mutations_still_bounded(clean_session, monkeypatch):
    """突变通道持续失败也必须收敛到 exhausted（无重试上限的失败重放=无限循环）。"""
    await _patch_mutations(monkeypatch, fail=True)
    revision = await _seed_revision(clean_session)
    for i in range(MAX_RUNTIME_REPAIR_PASSES + 1):
        outcome = await run_runtime_repair(
            clean_session,
            chapter=_chapter(),
            mapspec=_mapspec(),
            descriptors=_descriptors(),
            observation=_observation(revision),
            current_revision=revision,
            map_state=await session_data_manager.get_map_state(clean_session),
        )
        if i < MAX_RUNTIME_REPAIR_PASSES:
            assert not outcome.exhausted
        else:
            assert outcome.exhausted


# ── Scenario H：大数据不扫描 features ────────────────────────────────


def test_scenario_h_large_dataset_costs_no_feature_scan():
    """150k features：分类只读 descriptor 元数据与 ID/布尔 —— 复杂度
    O(layers + sources + components)，与 feature 数无关。"""
    big_descriptors = {
        "ref:geojson-a": {
            "feature_count": 150_000,
            # 不放真实 features —— 分类器契约上根本不读
        },
    }
    small = classify_runtime_repairs(
        _chapter(), _mapspec(),
        descriptors={"ref:geojson-a": {"feature_count": 2}},
        observation=_observation(3),
        current_revision=3,
    )
    big = classify_runtime_repairs(
        _chapter(), _mapspec(),
        descriptors=big_descriptors,
        observation=_observation(3),
        current_revision=3,
    )
    assert small.reassert_layers == big.reassert_layers
    assert small.execution_debts == big.execution_debts
    assert small.action_fingerprint() == big.action_fingerprint()
    assert small.reassert_layers == ["poi-main"]


# ── 有界性 ───────────────────────────────────────────────────────────


def test_plan_is_bounded():
    chapter = _chapter()
    chapter["map_layers"] = [
        {"role": "primary", "layer_id": f"poi-{i}", "enabled": True,
         "source_capability": "poi_query"}
        for i in range(50)
    ]
    mapspec = _mapspec()
    mapspec["layers"] = [
        {"id": f"poi-{i}", "source": "s-poi", "type": "circle"} for i in range(50)
    ]
    plan = classify_runtime_repairs(
        chapter, mapspec,
        descriptors=_descriptors(),
        observation=_observation(3),
        current_revision=3,
    )
    assert len(plan.reassert_layers) == 50  # 分类完整（O(layers)）
    assert plan.action_fingerprint().startswith("rr-sha256:")


async def test_superseded_mutation_is_not_recorded_as_applied(
    clean_session, monkeypatch
):
    """CAS：快照与提交之间 spec 被并发推进 → superseded 不算 applied
    （旧内容绝不覆盖新编辑），尝试仍入账（预算不因失败重置）。"""
    await _seed_revision(clean_session)
    from app.services.gis_world_state import mutation as mutation_mod

    class _Superseded:
        is_error = False
        superseded = True
        mutation_revision = 5

    async def superseded_apply(sid, intent, **kwargs):
        return _Superseded()

    monkeypatch.setattr(mutation_mod, "apply_gis_mutation", superseded_apply)
    revision = await _seed_revision(clean_session)
    outcome = await run_runtime_repair(
        clean_session,
        chapter=_chapter(),
        mapspec=_mapspec(),
        descriptors=_descriptors(),
        observation=_observation(revision),
        current_revision=revision,
        map_state=await session_data_manager.get_map_state(clean_session),
    )
    assert outcome.applied == []
    assert not outcome.exhausted
    # 尝试已入账（下一次同发散观察继续消耗预算直至 exhausted）
    from app.services.gis_harness.runtime_repair import REPAIR_STATE_KEY

    state = await session_data_manager.get_map_state(clean_session)
    ledger = state.get(REPAIR_STATE_KEY) or {}
    assert ledger.get("passes"), "superseded attempt must still be ledgered"


async def test_reassert_passes_expected_revision_cas(clean_session, monkeypatch):
    """reassert 必须携带 expected_revision（观察盖章 revision）—— CAS 守卫。"""
    from app.services.gis_world_state import mutation as mutation_mod

    captured = {}

    async def capture_apply(sid, intent, **kwargs):
        captured["expected_revision"] = kwargs.get("expected_revision")

        class _Ok:
            is_error = False
            superseded = False
            mutation_revision = 42

        return _Ok()

    monkeypatch.setattr(mutation_mod, "apply_gis_mutation", capture_apply)
    revision = await _seed_revision(clean_session)
    await run_runtime_repair(
        clean_session,
        chapter=_chapter(),
        mapspec=_mapspec(),
        descriptors=_descriptors(),
        observation=_observation(revision),
        current_revision=revision,
        map_state=await session_data_manager.get_map_state(clean_session),
    )
    assert captured["expected_revision"] == revision
