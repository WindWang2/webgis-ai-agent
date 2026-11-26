"""G05 WP3 单测：多候选结构化对比（diff / 指标 / digest 写回）。

指标口径自洽：直接用 whatif 的 ``extract_layer_payloads +
compute_metric_deltas`` 对同一组 spec 重算期望值断言（ADR-0193 同源），
不手算投影数字 —— 对比层与候选层消费同一个引擎，无第二套口径。
"""

from __future__ import annotations

import uuid

import pytest

from app.services.mapspec.intents import PatchLayerStyleIntent
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.scenario_harness.candidates import (
    apply_candidate_interventions,
    get_candidate,
    propose_candidate,
)
from app.services.scenario_harness.compare import compare_candidates
from app.services.scenario_harness.store import RegistryError
from app.services.session_data import session_data_manager
from app.services.simulation.whatif.spatial_diff_engine import (
    compute_metric_deltas,
    extract_layer_payloads,
)


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


def _point_fc(points: list[tuple[str, float, float, dict]]) -> dict:
    features = []
    for fid, lng, lat, props in points:
        features.append(
            {
                "type": "Feature",
                "id": fid,
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": {"id": fid, **props},
            }
        )
    return {"type": "FeatureCollection", "features": features}


async def _apply_layer(sid: str, layer_id: str, source_id: str, fc: dict, geom: str = "fill") -> None:
    res = await MapSpecLifecycleEngine().apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": layer_id, "type": geom, "source": source_id},
            source_data={"type": "geojson", "inlineData": fc},
        ),
    )
    assert not res.is_error, res.error_msg


async def _seed_parent(sid: str) -> None:
    """主干预播种：绿地 + 设施点 + 人口格网（指标面）+ 一层普通面。"""
    await _apply_layer(
        sid, "park-main", "park-main",
        _poly_fc([("park-1", 104.0, 30.6, 0.001, {"kind": "park"})]),
    )
    await _apply_layer(
        sid, "facility-main", "facility-main",
        _point_fc([("clinic-1", 104.0, 30.6, {"kind": "clinic"})]),
        geom="circle",
    )
    await _apply_layer(
        sid, "population-main", "population-main",
        _poly_fc(
            [
                ("cell-a", 104.0, 30.604, 0.00025, {"population": 100}),
                ("cell-b", 104.0, 30.59, 0.00025, {"population": 300}),
            ]
        ),
    )


async def _branch_spec(sid: str, branch_sid: str) -> dict:
    state = await session_data_manager.get_map_state(branch_sid)
    return state.get("mapspec") or {}


# ── 结构化 diff ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compare_rows_layer_adds_and_style_fields():
    """两个候选各改不同面：layer_adds / style_fields 各自正确且互不串行。"""
    sid = _sid("g05-cmp")
    await _seed_parent(sid)
    cand_a = await propose_candidate(
        sid,
        title="加新绿地",
        intents=[
            UpsertLayerIntent(
                layer={"id": "park-new", "type": "fill", "source": "park-new"},
                source_data={
                    "type": "geojson",
                    "inlineData": _poly_fc([("p", 104.01, 30.6, 0.001, {})]),
                },
            )
        ],
    )
    await apply_candidate_interventions(sid, cand_a.candidate_id)
    cand_b = await propose_candidate(
        sid,
        title="绿地降透明度",
        intents=[PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.5})],
    )
    await apply_candidate_interventions(sid, cand_b.candidate_id)

    result = await compare_candidates(sid, [cand_a.candidate_id, cand_b.candidate_id])
    assert result.session_id == sid
    assert result.baseline_id == cand_a.baseline_id == cand_b.baseline_id
    assert result.move is not None and result.move.moved is False
    rows = {r.candidate_id: r for r in result.rows}
    assert set(rows) == {cand_a.candidate_id, cand_b.candidate_id}
    row_a, row_b = rows[cand_a.candidate_id], rows[cand_b.candidate_id]
    assert row_a.comparable and row_a.incomparable_reason == ""
    assert row_a.layer_adds == ["park-new"]
    assert row_a.layer_removes == []
    assert row_a.style_fields == []
    assert row_b.comparable
    assert row_b.layer_adds == []
    assert row_b.style_fields == ["park-main.paint.opacity"]
    # impacts 分级在场（基准未动 → 全 current）
    assert {i.candidate_id for i in result.impacts} == set(rows)
    assert all(i.level == "current" for i in result.impacts)


@pytest.mark.asyncio
async def test_compare_metrics_consistent_with_whatif_engine():
    """分支加一个设施点层 → facility_count delta 与 whatif 引擎同口径同值。"""
    sid = _sid("g05-cmp-metric")
    await _seed_parent(sid)
    cand = await propose_candidate(
        sid,
        title="新增诊所",
        intents=[
            UpsertLayerIntent(
                layer={"id": "facility-alt", "type": "circle", "source": "facility-alt"},
                source_data={
                    "type": "geojson",
                    "inlineData": _point_fc(
                        [
                            ("clinic-2", 104.001, 30.601, {"kind": "clinic"}),
                            ("clinic-3", 103.999, 30.599, {"kind": "clinic"}),
                        ]
                    ),
                },
            )
        ],
    )
    await apply_candidate_interventions(sid, cand.candidate_id)
    result = await compare_candidates(sid, [cand.candidate_id])
    row = result.rows[0]
    # 与 whatif 引擎直接重算口径自洽（同 payload、同 lat0 联合均值）
    parent_spec = (await session_data_manager.get_map_state(sid)).get("mapspec") or {}
    base_payloads = extract_layer_payloads(parent_spec)
    branch_payloads = extract_layer_payloads(await _branch_spec(sid, cand.branch_session_id))
    expected = {
        md.metric_key: round(md.delta_abs, 6)
        for md in compute_metric_deltas(base_payloads, branch_payloads)
        if md.delta_abs is not None
    }
    assert row.metric_deltas == expected
    assert row.metric_deltas.get("facility_count") == 2.0  # 1 → 3 个设施点


@pytest.mark.asyncio
async def test_non_role_layer_yields_empty_metrics():
    """双侧都无角色几何（green/road/facility）→ 不造假指标，metric_deltas 空 dict。"""
    sid = _sid("g05-cmp-norole")
    await _apply_layer(
        sid, "grid-base", "grid-base",
        _poly_fc([("g0", 104.0, 30.6, 0.0005, {})]),
    )
    cand = await propose_candidate(
        sid,
        title="加参考格网",
        intents=[
            UpsertLayerIntent(
                layer={"id": "grid-overlay", "type": "fill", "source": "grid-overlay"},
                source_data={
                    "type": "geojson",
                    "inlineData": _poly_fc([("g", 104.0, 30.6, 0.0005, {})]),
                },
            )
        ],
    )
    await apply_candidate_interventions(sid, cand.candidate_id)
    result = await compare_candidates(sid, [cand.candidate_id])
    assert result.rows[0].metric_deltas == {}
    assert result.rows[0].layer_adds == ["grid-overlay"]


@pytest.mark.asyncio
async def test_baseline_mismatch_row_and_moved_move():
    """跨基准候选：不同 baseline_id 的行 comparable=False + baseline_mismatch。"""
    sid = _sid("g05-cmp-mismatch")
    await _seed_parent(sid)
    cand_a = await propose_candidate(
        sid, intents=[PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.4})]
    )
    # 父会话真实 mutation → 候选 B 落在新基准上（与 A 不同 baseline_id）
    await _apply_layer(
        sid, "park-second", "park-second",
        _poly_fc([("p2", 104.02, 30.6, 0.001, {})]),
    )
    cand_b = await propose_candidate(
        sid, intents=[PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.6})]
    )
    assert cand_b.baseline_id != cand_a.baseline_id

    result = await compare_candidates(sid, [cand_a.candidate_id, cand_b.candidate_id])
    rows = {r.candidate_id: r for r in result.rows}
    assert result.baseline_id == cand_a.baseline_id
    assert rows[cand_a.candidate_id].comparable is True
    row_b = rows[cand_b.candidate_id]
    assert row_b.comparable is False
    assert row_b.incomparable_reason == "baseline_mismatch"
    # 主基准（A 的）已漂移 → move 披露
    assert result.move is not None and result.move.moved is True


# ── digest 确定性与写回 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compare_digest_deterministic_and_written_back():
    sid = _sid("g05-cmp-digest")
    await _seed_parent(sid)
    cand_a = await propose_candidate(
        sid,
        intents=[
            UpsertLayerIntent(
                layer={"id": "park-new", "type": "fill", "source": "park-new"},
                source_data={
                    "type": "geojson",
                    "inlineData": _poly_fc([("p", 104.01, 30.6, 0.001, {})]),
                },
            )
        ],
    )
    await apply_candidate_interventions(sid, cand_a.candidate_id)
    cand_b = await propose_candidate(
        sid, intents=[PatchLayerStyleIntent(layer_id="park-main", paint={"opacity": 0.5})]
    )
    await apply_candidate_interventions(sid, cand_b.candidate_id)

    ids = [cand_a.candidate_id, cand_b.candidate_id]
    first = await compare_candidates(sid, ids)
    assert first.compare_digest
    assert len(first.compare_digest) == 16
    second = await compare_candidates(sid, ids)
    assert second.compare_digest == first.compare_digest

    a = await get_candidate(sid, cand_a.candidate_id)
    b = await get_candidate(sid, cand_b.candidate_id)
    assert a.compare_digest == first.compare_digest
    assert b.compare_digest == first.compare_digest


# ── 入参界 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compare_bounds_empty_and_over_limit():
    sid = _sid("g05-cmp-bound")
    await _seed_parent(sid)
    with pytest.raises(RegistryError) as exc_empty:
        await compare_candidates(sid, [])
    assert exc_empty.value.code == "compare_empty"
    fake_ids = [f"cand-{i}" for i in range(6)]  # > MAX_COMPARABLE_CANDIDATES
    with pytest.raises(RegistryError) as exc_limit:
        await compare_candidates(sid, fake_ids)
    assert exc_limit.value.code == "compare_limit"
