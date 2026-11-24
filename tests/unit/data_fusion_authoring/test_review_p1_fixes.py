"""独立审查 C 第一轮 P1 修复的回归钉（反子午线/高纬 halo/digest 闸/归属闸）。"""
from __future__ import annotations

import pytest
from shapely.geometry import Point

from app.services.data_fusion_authoring import service
from app.services.data_fusion_authoring.errors import DataFusionError
from app.services.data_fusion_authoring.matching import SpatialContext, match_sources
from tests.unit.data_fusion_authoring.conftest import (
    chengdu_pois_a,
    chengdu_pois_b,
    fusion_plan,
    seed_session_source,
)
from tests.unit.data_fusion_authoring.test_matching import _plan


def _single_pair(a_row, b_row, geom_a, geom_b):
    return [a_row], [geom_a], [b_row], [geom_b]


# ── P1#3 反子午线 ─────────────────────────────────────────────

def test_antimeridian_neighbors_are_candidates():
    """±180° 两侧近邻（21.9m）必须生成候选 —— 绝不按平面判远。"""
    rows_a, geoms_a, rows_b, geoms_b = _single_pair(
        {"code": "A1", "name": "白令海岸哨所", "addr": "同街", "cat": "小学",
         "tel": "1"},
        {"osm_id": "B1", "name": "白令海岸哨所", "addr": "同街",
         "cat": "primary_school", "checked": "2026-01-01"},
        Point(179.99985, 10.0), Point(-179.99985, 10.0))
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    assert result.stats["candidates"] >= 1, "反子午线候选丢失"
    assert result.stats["pairs_matched"] == 1
    d_e = next(e for e in result.pairs[0].evidence if e.kind == "distance")
    assert d_e.detail["distance_m"] < 100


def test_base_cell_wraps_across_dateline():
    ctx = SpatialContext("geographic", 150.0)
    east = ctx.base_cell(Point(179.9999, 10.0))
    west = ctx.base_cell(Point(-179.9999, 10.0))
    # 回绕：西侧原始负格键映射到正域，且与东侧相邻（相邻性才是性质）
    assert west[0] == east[0] + 1
    assert (east[0], east[1]) in set(ctx.neighborhood(west))
    assert (west[0], west[1]) in set(ctx.neighborhood(east))


# ── P2 高纬东西向 halo ────────────────────────────────────────

def test_high_latitude_east_west_blocking():
    """65°N 东西向 140m（>2·cell）：动态 halo 必须召回候选。"""
    # 65°N：东西向 140m ≈ 0.003°；cell = 150/111320 ≈ 0.00135° → 跨 2 格
    import math

    deg_per_m = 1.0 / (111_320.0 * math.cos(math.radians(65.0)))
    rows_a, geoms_a, rows_b, geoms_b = _single_pair(
        {"code": "A1", "name": "极北哨所", "addr": "同街", "cat": "小学",
         "tel": "1"},
        {"osm_id": "B1", "name": "极北哨所", "addr": "同街",
         "cat": "primary_school", "checked": "2026-01-01"},
        Point(20.0, 65.0), Point(20.0 + 140 * deg_per_m, 65.0))
    plan = _plan()
    ctx = SpatialContext("geographic", plan.strategy.max_match_distance_m)
    result = match_sources(rows_a, geoms_a, rows_b, geoms_b, plan, ctx)
    # blocking 召回是硬性质：候选必须生成（分值落审阅带是诚实评分）
    assert result.stats["candidates"] >= 1, "高纬东西向候选丢失"
    assert len(result.pairs) == 1


# ── P1#1 commit 计划 digest 闸 ─────────────────────────────────

async def test_commit_blocked_after_plan_drift(seeded_fusion_sources):
    s = seeded_fusion_sources
    view = await service.create_plan(s["session_id"], s["plan"],
                                     actor=s["actor"])
    run = await service.execute_plan(
        s["session_id"], view["plan_id"], expected_revision=1,
        actor=s["actor"], background=False)
    assert run["status"] == "completed"
    # 改计划（降阈值）→ commit 必须 typed 拒绝（不得按新阈值绕过审阅门）
    drifted = dict(s["plan"])
    drifted["strategy"] = dict(s["plan"]["strategy"])
    drifted["strategy"]["auto_match_min"] = 0.5
    drifted["strategy"]["review_min"] = 0.45
    await service.update_plan(s["session_id"], view["plan_id"], drifted,
                              expected_revision=1, actor=s["actor"])
    with pytest.raises(DataFusionError) as ei:
        await service.commit_run(s["session_id"], run["run_id"],
                                 actor=s["actor"])
    assert ei.value.code == "stale_plan_revision"
    assert "重新 execute" in (ei.value.correction_hint or ei.value.message
                              or "")


# ── P1#2 决定归属闸 ───────────────────────────────────────────

async def test_decision_endpoints_enforce_plan_ownership():
    """持有本会话 + 他人 plan_id：读/写/删决定一律 not_owned。"""
    sid_owner = "sess-fusion-owner"
    sid_attacker = "sess-fusion-attacker"
    a = await seed_session_source(sid_owner, chengdu_pois_a(), prefix="owna")
    b = await seed_session_source(sid_owner, chengdu_pois_b(), prefix="ownb")
    owner_actor = {"trusted_session_id": sid_owner}
    plan = fusion_plan(a, b, sid_owner)
    view = await service.create_plan(sid_owner, plan, actor=owner_actor)
    run = await service.execute_plan(
        sid_owner, view["plan_id"], expected_revision=1,
        actor=owner_actor, background=False)
    queue = service.review_queue(sid_owner, run["run_id"])
    pair_key = queue["items"][0]["pair_key"]

    # 攻击者用自己的会话 + 他人 plan_id：写/读/删全部 not_owned
    with pytest.raises(DataFusionError) as ei:
        await service.upsert_decision(
            sid_attacker, view["plan_id"], pair_key=pair_key,
            action="accept", field_values={"name": "越权值"})
    assert ei.value.code == "not_owned"
    with pytest.raises(DataFusionError) as ei:
        await service.list_decisions(sid_attacker, view["plan_id"])
    assert ei.value.code == "not_owned"
    with pytest.raises(DataFusionError) as ei:
        await service.delete_decision(
            sid_attacker, view["plan_id"], pair_key, expected_revision=1)
    assert ei.value.code == "not_owned"
    # 真实属主不受影响
    dec = await service.upsert_decision(
        sid_owner, view["plan_id"], pair_key=pair_key, action="accept")
    assert dec["locked"] is True
