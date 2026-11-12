"""T-B10/T-B11/T-B12/T-B13：服务链（inspect/preview/commit）、CAS、undo、
一致性 oracle（验收 1/2/3/4/5/7/9/10/11/12/13/15/16/17/19/22）。

全部走真实 MapSpec 生命周期引擎（临时 store），无 mock 掉 mutation 主链。
"""
from __future__ import annotations

import math

import pytest

from app.services.thematic_authoring import (
    commit_expression,
    inspect_layer,
    preview_expression,
)
from app.services.thematic_authoring.builder import size_for_value
from app.services.thematic_authoring.contract import parse_plan
from app.services.thematic_authoring.errors import ThematicAuthoringError
from app.services.thematic_authoring.service import (
    AUTHORING_KEY,
    expression_fingerprint,
)
from tests.unit.thematic_authoring.helpers import (
    current_revision,
    make_fc,
    read_layer,
    seed_thematic_layer,
)


def _plan(**kw):
    base = {"layer_id": "result-seed"}
    base.update(kw)
    return parse_plan(base)


@pytest.fixture
async def seeded(session_id, store_root):
    rows = [{"pop": v, "name": f"z{i}"} for i, v in enumerate(
        [0, 3, 7, 12, 18, 25, 33, 47, 60, 88])]
    fc = make_fc(rows)
    await seed_thematic_layer(session_id, fc, layer_id="result-seed", field="pop")
    return fc


# ─── inspect（验收 14 口径） ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inspect_returns_current_expression_and_fullsource_stats(session_id, store_root, seeded):
    receipt = await inspect_layer(session_id, "result-seed")
    assert receipt["layer_type"] == "fill"
    assert receipt["current"]["legend_spec"]["type"] == "graduated"
    assert receipt["current"]["fingerprint"].startswith("texp-")
    assert receipt["source"]["ref_id"].startswith("ref:")
    assert receipt["source"]["feature_count"] == 10
    dist = receipt["distribution"]
    assert dist["basis"]["mode"] == "full_scan"
    assert dist["basis"]["approximate"] is False
    assert dist["numeric"]["min"] == 0 and dist["numeric"]["max"] == 88
    field_names = {f["name"] for f in receipt["fields"]}
    assert {"pop", "name"} <= field_names


# ─── preview：零 mutation + 手工断点（验收 2/3/7/8） ───────────────────────


@pytest.mark.asyncio
async def test_preview_manual_breaks_zero_mutation(session_id, store_root, seeded):
    rev0 = await current_revision(session_id)
    plan = _plan(mode="graduated_manual", breaks=[0, 10, 20, 50], palette="Blues")
    r = await preview_expression(session_id, "result-seed", plan)
    assert r["committed"] is False
    assert r["legend_spec"]["breaks"] == [0, 10, 20, 50]
    assert r["paint"]["method"] == "step"
    assert r["paint"]["stops"][0] == [10, r["legend_spec"]["palette_colors"][1]]
    # 类计数与断点一致：[0,10)×4? 手算：0,3,7→c0; 12,18→c1; 25,33,47,60,88→c2
    counts = [c["count"] for c in r["class_table"]]
    assert counts == [3, 2, 5]
    # 颜色/标签/计数/解释同源（同一 legend_spec）
    assert len(r["legend_spec"]["palette_colors"]) == 3
    assert r["requested_k"] == 3 and r["actual_k"] == 3
    assert await current_revision(session_id) == rev0  # 零 mutation


@pytest.mark.asyncio
async def test_preview_boundary_values_class_assignment(session_id, store_root, seeded):
    """验收 2：恰在断点上的值归上类；相邻值不跨类（统一求值器）。"""
    rows = [{"pop": v} for v in (0, 9.999, 10, 10.001, 19.999, 20, 49.999, 50)]
    fc = make_fc(rows)
    await seed_thematic_layer(session_id, fc, layer_id="result-b", field="pop")
    r = await preview_expression(
        session_id, "result-b",
        _plan(mode="graduated_manual", breaks=[0, 10, 20, 50], palette="Blues"))
    counts = [c["count"] for c in r["class_table"]]
    assert counts == [2, 3, 3]  # 0,9.999→c0; 10,10.001,19.999→c1; 20,49.999,50→c2


@pytest.mark.asyncio
async def test_preview_rejects_disorderly_breaks_keeps_state(session_id, store_root, seeded):
    rev0 = await current_revision(session_id)
    with pytest.raises(ThematicAuthoringError) as ei:
        await preview_expression(
            session_id, "result-seed",
            _plan(mode="graduated_manual", breaks=[0, 20, 10]))
    assert ei.value.code == "BREAKS_NOT_STRICTLY_INCREASING"
    assert ei.value.index == 2
    layer = await read_layer(session_id, "result-seed")
    assert expression_fingerprint(layer["legend_spec"])  # 原表达不动
    assert await current_revision(session_id) == rev0


@pytest.mark.asyncio
async def test_preview_out_of_domain_disclosure(session_id, store_root, seeded):
    plan = _plan(mode="graduated_manual", breaks=[10, 30, 60], palette="Greens")
    r = await preview_expression(session_id, "result-seed", plan)
    joined = " ".join(r["disclosures"])
    assert "低于首断点" in joined and "高于末断点" in joined  # 0,3,7 与 88 越界披露


# ─── commit：单笔事务 + 证据 + 保留（验收 1/19/22） ─────────────────────────


@pytest.mark.asyncio
async def test_commit_updates_legend_paint_and_evidence_atomically(session_id, store_root, seeded):
    rev0 = await current_revision(session_id)
    plan = _plan(
        mode="graduated_manual", breaks=[0, 10, 20, 50], palette="Blues",
        labels=["低", "中", "高"], unit="人")
    r = await commit_expression(
        session_id, "result-seed", plan, expected_revision=rev0,
        origin="user", actor="test")
    assert r["committed"] is True and r["mutations"] == 1
    assert r["revision"] == rev0 + 1
    layer = await read_layer(session_id, "result-seed")
    # 1) 地图与图例同源（一次事务）：paint.color 由同一 legend_spec 投影
    from app.lib.cartography.thematic_spec import spec_to_paint
    paint, _ = spec_to_paint(layer["legend_spec"])
    assert layer["paint"]["color"] == paint
    assert layer["legend_spec"]["breaks"] == [0, 10, 20, 50]
    assert layer["legend_spec"]["labels"] == ["低", "中", "高"]
    assert layer["legend_spec"]["unit"] == "人"
    # 2) 证据块：人工断点不残留「自动」裁决；携带源锚与先前指纹
    ev = layer[AUTHORING_KEY]
    assert ev["mode"] == "graduated_manual" and ev["decided_by"] == "user"
    assert ev["source_anchor"]["ref_id"].startswith("ref:")
    assert ev["previous_fingerprint"].startswith("texp-")
    # 3) 只改本域键：source/type/data_binding/非相关 metadata 保留
    assert layer["source"] == "result-seed-source"
    assert layer["type"] == "fill"
    assert layer["data_binding"]["field"] == "pop"
    assert layer["metadata"]["title"] == "pop 专题图"


@pytest.mark.asyncio
async def test_commit_switch_field_same_source_and_layer(session_id, store_root):
    rows = [{"pop": i * 10, "rate": i * 0.5} for i in range(12)]
    fc = make_fc(rows)
    await seed_thematic_layer(session_id, fc, layer_id="result-swap", field="pop")
    rev0 = await current_revision(session_id)
    r = await commit_expression(
        session_id, "result-swap",
        _plan(mode="graduated_auto", field="rate", method="equal_interval", k=4),
        expected_revision=rev0, origin="user", actor="test")
    layer = await read_layer(session_id, "result-swap")
    assert layer["source"] == "result-swap-source"      # 源与层 ID 不变（验收 1）
    assert layer["legend_spec"]["field"] == "rate"
    assert layer["data_binding"]["field"] == "rate"
    assert layer["paint"]["color"]["field"] == "rate"
    assert r["class_table"], "新字段分类表"


@pytest.mark.asyncio
async def test_undo_restores_full_expression(session_id, store_root, seeded):
    """验收 19：undo 恢复完整 legend/paint/evidence，不只是颜色。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UndoAgentCommitIntent
    from app.services.session_data import session_data_manager

    before = await read_layer(session_id, "result-seed")
    rev0 = await current_revision(session_id)
    await commit_expression(
        session_id, "result-seed",
        _plan(mode="graduated_manual", breaks=[0, 10, 50], palette="Reds"),
        expected_revision=rev0, origin="agent", actor="pi:test")
    mid = await read_layer(session_id, "result-seed")
    assert mid["legend_spec"]["breaks"] == [0, 10, 50]
    points = await session_data_manager.get_state_field(
        session_id, "_agent_undo_points")
    assert isinstance(points, list) and points, "agent 提交应记录撤销点"
    last = points[-1]
    result = await apply_gis_mutation(
        session_id,
        UndoAgentCommitIntent(
            base_revision=int(last["base_revision"]),
            agent_revision=int(last["revision"]),
        ),
        origin="user", actor="test:undo",
        expected_revision=await current_revision(session_id),
    )
    assert not result.is_error, result.error_msg
    after = await read_layer(session_id, "result-seed")
    assert after["legend_spec"] == before["legend_spec"]
    assert after["paint"] == before["paint"]
    assert after.get(AUTHORING_KEY) is None


@pytest.mark.asyncio
async def test_cas_conflict_returns_superseded_nothing_written(session_id, store_root, seeded):
    rev0 = await current_revision(session_id)
    # 他人先推进修订
    await commit_expression(
        session_id, "result-seed",
        _plan(mode="graduated_auto", method="equal_interval", k=3),
        expected_revision=rev0, origin="user", actor="other")
    with pytest.raises(ThematicAuthoringError) as ei:
        await commit_expression(
            session_id, "result-seed",
            _plan(mode="graduated_manual", breaks=[0, 10, 50]),
            expected_revision=rev0,  # 陈旧修订
            origin="user", actor="test")
    assert ei.value.code == "CAS_SUPERSEDED"
    assert ei.value.status_code == 409
    layer = await read_layer(session_id, "result-seed")
    assert layer["legend_spec"]["breaks"] != [0, 10, 50]  # nothing was written


@pytest.mark.asyncio
async def test_stale_source_anchor_rejected(session_id, store_root, seeded):
    receipt = await inspect_layer(session_id, "result-seed")
    anchor = receipt["source"]
    plan = parse_plan({
        "layer_id": "result-seed", "mode": "graduated_manual",
        "breaks": [0, 10, 50],
        "source_anchor": {"ref_id": anchor["ref_id"], "content_revision": anchor["content_revision"] + 7},
    })
    with pytest.raises(ThematicAuthoringError) as ei:
        await commit_expression(
            session_id, "result-seed", plan,
            expected_revision=await current_revision(session_id),
            origin="user", actor="test")
    assert ei.value.code == "STALE_SOURCE"


# ─── 一致性 oracle（验收 22） ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_integrity_oracle_detects_legend_only_or_paint_only_divergence(
        session_id, store_root, seeded):
    """强制只更新 legend 或只改 paint 的「实现 bug」必须被 oracle 检出。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent
    from app.services.thematic_authoring.integrity import assert_expression_consistent

    rev0 = await current_revision(session_id)
    layer = await read_layer(session_id, "result-seed")
    # bug A：只改 legend_spec（paint 停留旧表达）
    bugged = dict(layer)
    bugged["legend_spec"] = dict(layer["legend_spec"])
    bugged["legend_spec"]["breaks"] = [0, 1, 2, 999]
    bugged["legend_spec"]["palette_colors"] = ["#000000", "#111111", "#222222"]
    await apply_gis_mutation(
        session_id, UpsertLayerIntent(layer=bugged, source_data=None),  # type: ignore[arg-type]
        origin="agent", actor="test:bug-a", expected_revision=rev0)
    layer_a = await read_layer(session_id, "result-seed")
    problems_a = assert_expression_consistent(layer_a)
    assert any("paint" in p or "legend" in p for p in problems_a), problems_a

    # bug B：只改 paint（legend 停留旧表达）
    rev1 = await current_revision(session_id)
    bugged2 = dict(layer_a)
    paint2 = dict(layer_a["paint"])
    paint2["color"] = {
        "method": "step", "field": "pop", "default": "#abcabc",
        "stops": [[42, "#fedcba"]],
    }
    bugged2["paint"] = paint2
    await apply_gis_mutation(
        session_id, UpsertLayerIntent(layer=bugged2, source_data=None),  # type: ignore[arg-type]
        origin="agent", actor="test:bug-b", expected_revision=rev1)
    layer_b = await read_layer(session_id, "result-seed")
    problems_b = assert_expression_consistent(layer_b)
    assert problems_b, problems_b
    # 正常层（服务提交）无问题
    rev2 = await current_revision(session_id)
    await commit_expression(
        session_id, "result-seed",
        _plan(mode="graduated_auto", method="equal_interval", k=4),
        expected_revision=rev2, origin="user", actor="test")
    clean = await read_layer(session_id, "result-seed")
    assert assert_expression_consistent(clean) == []
