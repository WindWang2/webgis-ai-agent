"""G05 WP2 单测：共享基准绑定（capture / observe_move / get_or_create）。

纪律：不用 mock 冒充权威状态 —— 父会话 MapSpec 用真实
MapSpecLifecycleEngine 播种（与 test_whatif_branching 同款 fixture），
数据版本锚用 session_data_manager 真实 ref（store + overwrite 推进
content revision）。
"""

from __future__ import annotations

import uuid

import pytest

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.scenario_harness.baseline import (
    capture_baseline,
    get_or_create_baseline,
    latest_baseline,
    observe_baseline_move,
)
from app.services.scenario_harness.contracts import (
    REGISTRY_BASELINES,
    ScenarioBaseline,
)
from app.services.scenario_harness.store import (
    RegistryError,
    load_registry,
    mint_id,
    upsert_registry_entry,
)
from app.services.session_data import session_data_manager
from app.services.simulation.whatif.branch_manager import fingerprint_mapspec


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
    """播种父会话权威 spec：绿地面 + 人口格网（含一个 ref 源供数据锚）。"""
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


async def _seed_ref_dataset(sid: str) -> str:
    """注册一个真实会话 ref（content_revision=1）→ 返回铸造的 ref_id。"""
    return await session_data_manager.store(
        sid, {"type": "FeatureCollection", "features": []}, prefix="data"
    )


async def _seed_spec_with_ref_source(sid: str, ref_id: str) -> None:
    """播种带 ref 外置源的 spec（数据锚场景：inline 之外的版本化数据集）。"""
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "pop-main", "type": "fill", "source": "pop-src"},
            source_data={"type": "geojson", "ref": ref_id},
        ),
    )
    assert not res.is_error, res.error_msg


# ── capture_baseline ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capture_baseline_anchors_come_from_seeded_spec():
    """捕获：revision/指纹来自真实播种 spec；数据锚读真实 ref revision。"""
    sid = _sid("g05-base")
    await _seed_parent(sid)
    ref_id = await _seed_ref_dataset(sid)
    await _seed_spec_with_ref_source(sid, ref_id)

    state = await session_data_manager.get_map_state(sid)
    spec = state.get("mapspec")
    assert isinstance(spec, dict) and spec

    baseline = await capture_baseline(sid, turn_id="t1", note="首次捕获")
    assert baseline.session_id == sid
    assert baseline.baseline_id.startswith("sb-")
    assert baseline.mapspec_revision == int(
        state.get("_cartographic_mutation_revision", 0) or 0
    )
    assert baseline.mapspec_fingerprint == fingerprint_mapspec(spec)
    assert baseline.captured_at
    assert baseline.turn_id == "t1"
    # 数据锚：只收 ref 化数据源（inline 源不进锚清单）
    anchor = baseline.dataset(ref_id)
    assert anchor is not None
    assert anchor.content_revision == "1"
    assert len(baseline.datasets) == 1

    # 注册表落账
    entries = await load_registry(sid, REGISTRY_BASELINES)
    assert baseline.baseline_id in entries


@pytest.mark.asyncio
async def test_capture_baseline_missing_spec_raises():
    sid = _sid("g05-base-none")
    with pytest.raises(RegistryError) as exc:
        await capture_baseline(sid)
    assert exc.value.code == "baseline_missing"


@pytest.mark.asyncio
async def test_ref_missing_reads_honest_empty_anchor():
    """ref 不在会话 refs（未水合）→ 锚 revision 诚实空串，不造默认值。"""
    sid = _sid("g05-base-ghost")
    await _seed_spec_with_ref_source(sid, "ref:data-neverstored")
    baseline = await capture_baseline(sid)
    assert len(baseline.datasets) == 1
    assert baseline.datasets[0].ref_id == "ref:data-neverstored"
    assert baseline.datasets[0].content_revision == ""


# ── observe_baseline_move ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_observe_move_no_drift():
    sid = _sid("g05-move-calm")
    await _seed_parent(sid)
    baseline = await capture_baseline(sid)
    move = await observe_baseline_move(sid, baseline.baseline_id)
    assert move.moved is False
    assert move.fingerprint_changed is False
    assert move.dataset_changes == []
    assert move.goal_changed is False
    assert move.reasons() == []
    assert move.revision_from == move.revision_to


@pytest.mark.asyncio
async def test_observe_move_after_real_mutation():
    """父会话真实 mutation 后 moved=True，reasons 含 revision 对。"""
    sid = _sid("g05-move-drift")
    await _seed_parent(sid)
    baseline = await capture_baseline(sid)
    rev_before = baseline.mapspec_revision

    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "road-main", "type": "line", "source": "road-main"},
            source_data={
                "type": "geojson",
                "inlineData": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": "road-1",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [[104.0, 30.5], [104.0, 30.51]],
                            },
                            "properties": {"id": "road-1"},
                        }
                    ],
                },
            },
        ),
    )
    assert not res.is_error, res.error_msg

    move = await observe_baseline_move(sid, baseline.baseline_id)
    assert move.moved is True
    assert move.fingerprint_changed is True
    assert move.revision_from == rev_before
    assert move.revision_to > rev_before
    reasons = move.reasons()
    assert reasons and reasons[0] == f"mapspec.revision:{rev_before}->{move.revision_to}"


@pytest.mark.asyncio
async def test_observe_move_dataset_revision_change():
    """ref 数据版本推进（overwrite）→ dataset_changes 逐锚披露。"""
    sid = _sid("g05-move-data")
    ref_id = await _seed_ref_dataset(sid)
    await _seed_spec_with_ref_source(sid, ref_id)
    baseline = await capture_baseline(sid)

    await session_data_manager.overwrite(sid, ref_id, {"type": "FeatureCollection", "features": [], "v": 2})
    move = await observe_baseline_move(sid, baseline.baseline_id)
    assert move.moved is True
    assert move.fingerprint_changed is False
    assert move.dataset_changes == [
        {"ref_id": ref_id, "from": "1", "to": "2"}
    ]
    assert move.reasons()[0] == f"dataset.{ref_id}:1->2"


@pytest.mark.asyncio
async def test_observe_move_baseline_not_found():
    sid = _sid("g05-move-missing")
    with pytest.raises(RegistryError) as exc:
        await observe_baseline_move(sid, "sb-nope")
    assert exc.value.code == "baseline_not_found"


# ── get_or_create_baseline ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_create_reuses_latest_when_state_unchanged():
    """同状态第二次调用不新增：最新基准 revision+指纹一致 → 直接复用。"""
    sid = _sid("g05-goc")
    await _seed_parent(sid)
    first = await get_or_create_baseline(sid)
    second = await get_or_create_baseline(sid, turn_id="t2")
    assert second.baseline_id == first.baseline_id
    entries = await load_registry(sid, REGISTRY_BASELINES)
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_get_or_create_captures_new_after_mutation():
    sid = _sid("g05-goc-drift")
    await _seed_parent(sid)
    first = await get_or_create_baseline(sid)
    engine = MapSpecLifecycleEngine()
    res = await engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "park-2", "type": "fill", "source": "park-2"},
            source_data={
                "type": "geojson",
                "inlineData": _poly_fc(
                    [("park-2", 104.01, 30.6, 0.001, {"kind": "park"})]
                ),
            },
        ),
    )
    assert not res.is_error, res.error_msg
    second = await get_or_create_baseline(sid)
    assert second.baseline_id != first.baseline_id
    assert second.mapspec_revision > first.mapspec_revision
    entries = await load_registry(sid, REGISTRY_BASELINES)
    assert len(entries) == 2


@pytest.mark.asyncio
async def test_latest_baseline_prefers_max_captured_at():
    """最新基准选择确定性：captured_at 最大者胜（平局以 id 定序）。

    captured_at 秒级粒度下同秒捕获会随机平局 —— 这里直接落两条显式
    时间戳的条目验证选择逻辑（不依赖 wall-clock 顺序）。
    """
    sid = _sid("g05-latest")
    old = ScenarioBaseline(baseline_id="sb-old", session_id=sid, captured_at="2026-01-01T00:00:00Z")
    new = ScenarioBaseline(baseline_id="sb-new", session_id=sid, captured_at="2026-01-02T00:00:00Z")
    await upsert_registry_entry(sid, REGISTRY_BASELINES, old)
    await upsert_registry_entry(sid, REGISTRY_BASELINES, new)
    latest = await latest_baseline(sid)
    assert latest is not None
    assert latest.baseline_id == "sb-new"


@pytest.mark.asyncio
async def test_get_or_create_missing_spec_raises():
    sid = _sid("g05-goc-none")
    with pytest.raises(RegistryError) as exc:
        await get_or_create_baseline(sid)
    assert exc.value.code == "baseline_missing"


@pytest.mark.asyncio
async def test_mint_id_shape():
    """id 形状：{prefix}-{session 前 8}-{hex6}（确定性可读）。"""
    value = mint_id("sb", "abcdefgh12345678")
    assert value.startswith("sb-abcdefgh-")
    assert len(value.split("-")[-1]) == 6
