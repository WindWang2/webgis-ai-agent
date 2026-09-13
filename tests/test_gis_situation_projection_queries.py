"""Situation 投影 / 查询 API / 一致性测试（方向 2 S5/S6/S7，ADR-0180）。"""
import json

import pytest

from app.services.gis_situation.compiler import compile_situation
from app.services.gis_situation.consistency import (
    check_consistency,
    reconcile_fact_views,
)
from app.services.gis_situation.facts import STATUS_STALE, unknown
from app.services.gis_situation.projection import (
    SITUATION_BLOCK_MAX_BYTES,
    render_situation_for_context,
)
from app.services.gis_situation import queries as Q
from tests.data.situation_fakes import FakeMapspecStore, FakeSituationStore
from tests.test_gis_situation_contract import _sit
from tests.test_gis_situation_compile_diff import _full_store, _sid, _spec


@pytest.mark.asyncio
async def test_projection_bounded_and_deterministic():
    session_id = _sid()
    store = _full_store(session_id)
    spec_store = FakeMapspecStore(_spec())
    situation = await compile_situation(session_id, store=store,
                                        mapspec_store=spec_store, compiled_at="T")
    p1 = render_situation_for_context(situation)
    p2 = render_situation_for_context(situation)
    assert p1.text == p2.text
    assert p1.byte_len == len(p1.text.encode("utf-8"))
    assert p1.byte_len <= SITUATION_BLOCK_MAX_BYTES
    assert not p1.truncated
    # 结构稳定：固定节名出现。
    for section in ("[GIS 情境", "[地理]", "[地图]"):
        assert section in p1.text
    # 图层名（用户可控）必须走 <layer> fence（HTML 转义 + 定界标签）。
    assert "<layer>" in p1.text
    # DC-3：verdict 不在本投影（由 cartography_context + V6 块注入）。
    assert "[制图]" not in p1.text


@pytest.mark.asyncio
async def test_projection_marks_delta_changed_facts():
    session_id = _sid()
    store = _full_store(session_id)
    spec_store = FakeMapspecStore(_spec())
    s1 = await compile_situation(session_id, store=store, mapspec_store=spec_store)
    store._map_state["base_layer"] = "天地图"
    s2 = await compile_situation(session_id, store=store, mapspec_store=spec_store)
    from app.services.gis_situation.diff import diff_situation

    delta = diff_situation(s1, s2)
    marked = render_situation_for_context(s2, delta=delta)
    assert "本轮变更" in marked.text
    assert "*底图" in marked.text
    plain = render_situation_for_context(s2)
    assert "本轮变更" not in plain.text


@pytest.mark.asyncio
async def test_projection_unknown_viewport_is_explicit():
    session_id = _sid()
    situation = await compile_situation(
        session_id, store=FakeSituationStore(), mapspec_store=FakeMapspecStore({}),
    )
    proj = render_situation_for_context(situation)
    assert "- 视口: 未知" in proj.text
    assert "- 图层: 未知" in proj.text


def test_projection_byte_cap_truncates_with_evidence():
    sit = _sit()
    huge_layers = [
        {"id": f"lyr-{i:04d}", "type": "circle", "visible": True}
        for i in range(200)
    ]
    from app.services.gis_situation.facts import known

    sit = sit.model_copy(update={"map": sit.map.model_copy(update={
        "layers": known(huge_layers, source="mapspec.layers", revision=1),
    })})
    proj = render_situation_for_context(sit, max_bytes=600)
    assert proj.byte_len <= 600
    assert proj.truncated
    assert "truncated" in proj.text


def test_projection_omission_markers_for_long_lists():
    sit = _sit()
    from app.services.gis_situation.facts import known

    layers = [{"id": f"lyr-{i}", "type": "fill", "visible": True} for i in range(9)]
    sit = sit.model_copy(update={"map": sit.map.model_copy(update={
        "layers": known(layers, source="mapspec.layers"),
    })})
    proj = render_situation_for_context(sit)
    assert "(+5 图层省略，共 9 层)" in proj.text


# ── 查询 API（S6）─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queries_over_compiled_situation():
    session_id = _sid()
    store = _full_store(session_id)
    situation = await compile_situation(
        session_id, store=store, mapspec_store=FakeMapspecStore(_spec()),
    )
    assert Q.get_active_dataset_by_role(situation, "base") == "ref:data-heat"
    assert Q.get_active_dataset_by_role(situation, "missing") is None
    visible = Q.get_visible_layers(situation)
    assert [row["id"] for row in visible] == ["lyr-heat"]
    scope = Q.resolve_geographic_scope(situation)
    assert scope["viewport"]["zoom"] == 12
    locks = Q.query_user_locks(situation)
    assert locks["hidden_layers"] == ["lyr-heat"]
    assert locks["focus_layer_id"] == "lyr-heat"
    assert Q.get_selected_feature(situation)["layer_id"] == "lyr-heat"
    assert len(Q.get_pending_mutations(situation)) == 1
    datasets = Q.get_active_datasets(situation)
    assert datasets and datasets[0]["ref_id"] == "ref:data-heat"


def test_queries_never_guess_defaults_on_unknown():
    sit = _sit()
    empty = sit.model_copy(update={
        "data": sit.data.model_copy(update={"active_roles": unknown(source="x")}),
        "interaction": sit.interaction.model_copy(update={
            "selected_feature": unknown(source="x")}),
    })
    assert Q.get_active_dataset_by_role(empty, "base") is None
    assert Q.get_selected_feature(empty) is None
    assert Q.get_delivery_target(empty) is None
    assert Q.get_cartographic_constraints(empty).get("verdict") is not None  # known 的仍可用


# ── 一致性（S7）───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_consistency_observed_behind_desired():
    session_id = _sid()
    store = _full_store(session_id)
    spec = _spec()
    spec_store = FakeMapspecStore(spec)
    situation = await compile_situation(session_id, store=store, mapspec_store=spec_store)
    codes = {c.code for c in check_consistency(situation)}
    assert "observed_behind_desired" not in codes  # obs_rev=4 == desired=4

    from app.services.gis_situation.facts import known

    bumped = situation.model_copy(update={
        "map": situation.map.model_copy(update={
            "desired_revision": known(9, source="x"),
        })})
    codes = {c.code for c in check_consistency(bumped)}
    assert "observed_behind_desired" in codes


@pytest.mark.asyncio
async def test_consistency_dead_selection_and_user_wins():
    session_id = _sid()
    store = _full_store(session_id)
    spec = _spec()
    spec["layers"] = [row for row in spec["layers"] if row["id"] != "lyr-heat"]
    situation = await compile_situation(
        session_id, store=store, mapspec_store=FakeMapspecStore(spec),
    )
    findings = check_consistency(situation)
    codes = {c.code for c in findings}
    assert "selected_feature_source_removed" in codes
    assert "focus_layer_source_removed" in codes
    # user_hidden 指向已移除层 → 不再冲突（层已不存在）。
    assert "user_hidden_layer_conflict" not in codes

    # 用户隐藏但层仍在 → user wins 协调语义。
    situation2 = await compile_situation(
        session_id, store=store, mapspec_store=FakeMapspecStore(_spec()),
    )
    codes2 = {c.code for c in check_consistency(situation2)}
    assert "user_hidden_layer_conflict" in codes2


@pytest.mark.asyncio
async def test_reconcile_downgrades_dead_selection_to_stale():
    session_id = _sid()
    store = _full_store(session_id)
    spec = _spec()
    spec["layers"] = [row for row in spec["layers"] if row["id"] != "lyr-heat"]
    situation = await compile_situation(
        session_id, store=store, mapspec_store=FakeMapspecStore(spec),
    )
    reconciled = reconcile_fact_views(situation)
    assert reconciled.interaction.selected_feature.status == STATUS_STALE
    assert reconciled.interaction.focus_layer_id.status == STATUS_STALE
    # 原情境不被就地修改（纯投影）。
    assert situation.interaction.selected_feature.status == "known"


def test_consistency_sorted_and_serializable():
    sit = _sit()
    findings = check_consistency(sit)
    for f in findings:
        json.dumps(f.to_dict(), ensure_ascii=False)
    codes = [f.code for f in findings]
    assert codes == sorted(codes)
