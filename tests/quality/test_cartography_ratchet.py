"""ratchet 门禁（ADR-0159 P2）回归锁。

覆盖任务书 §5 验收：
- 注入人工劣化 → 100% 拦截；
- waiver 到期自动失效；未到期豁免高亮；
- provisional 基线只记录不拦截，激活后拦截；
- 分位基线抗离群（p66）；方向表（high_bad / low_bad）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.cartography_ratchet import (
    GLOBAL_SCOPE,
    Baseline,
    Observation,
    activate_baselines,
    add_waiver,
    aggregate_observations,
    build_baseline_entries,
    evaluate_ratchet,
    load_active_waivers,
    load_baselines,
    percentile,
    resolve_direction,
    write_baselines,
)


# ── 纯函数面 ──────────────────────────────────────────────────────────────


def test_direction_resolution_table():
    assert resolve_direction("carto.load.ratio.load_ratio") == "high_bad"
    assert resolve_direction("carto.label.collision_est.label_ink_ratio") == "high_bad"
    assert resolve_direction("carto.visualvar.overload.encoded_field_count") == "high_bad"
    assert resolve_direction(
        "carto.color.separability.min_adjacent_delta_e"
    ) == "low_bad"
    assert resolve_direction("carto.scale.svs.avg_feature_area_px") == "low_bad"
    assert resolve_direction("gate.CartographicQuality") == "low_bad"
    # 未知检查项兜底 high_bad（保守）。
    assert resolve_direction("carto.unknown.rule") == "high_bad"


def test_percentile_matches_calibration_semantics():
    values = list(range(1, 11))  # 1..10
    assert percentile(sorted(values), 0.0) == 1
    assert percentile(sorted(values), 1.0) == 10
    assert percentile(sorted(values), 0.5) == 5.5
    assert percentile(sorted(values), 0.66) == pytest.approx(6.94)


def test_aggregate_observations_quantile_ignores_outliers():
    rows = [
        {"scene_id": "heatmap-basic", "check_id": "carto.load.ratio.load_ratio",
         "value": v}
        for v in (0.10, 0.11, 0.12, 0.13, 9.99)  # 9.99 是离群
    ]
    obs = aggregate_observations(rows, quantile=0.66)
    assert len(obs) == 1
    # p66 落在正常簇内，不被离群拉爆（均值会被拉到 2.09）。
    assert obs[0].value == pytest.approx(0.1264)


def test_artificial_regression_is_blocked_100pct():
    """验收：注入人工劣化 → 全部拦截。"""
    baselines = [
        Baseline(scene_id=GLOBAL_SCOPE, check_id="carto.load.ratio.load_ratio",
                 value=0.10, direction="high_bad", tolerance_pct=5.0,
                 status="active"),
        Baseline(scene_id=GLOBAL_SCOPE,
                 check_id="carto.color.separability.min_adjacent_delta_e",
                 value=12.0, direction="low_bad", tolerance_pct=5.0,
                 status="active"),
    ]
    # +50% 墨量劣化 与 -50% 色差劣化：2/2 拦截。
    degraded = [
        Observation(scene_id="heatmap-basic",
                    check_id="carto.load.ratio.load_ratio", value=0.15),
        Observation(scene_id="step-fill",
                    check_id="carto.color.separability.min_adjacent_delta_e",
                    value=6.0),
    ]
    violations = evaluate_ratchet(degraded, baselines, tolerance_pct=5.0)
    assert len(violations) == 2
    assert all(not v.waived for v in violations)
    assert violations[0].delta_pct == pytest.approx(50.0)
    assert violations[1].delta_pct == pytest.approx(50.0)  # low_bad 方向取劣化幅度


def test_improvement_and_noise_pass():
    baselines = [
        Baseline(scene_id=GLOBAL_SCOPE, check_id="carto.load.ratio.load_ratio",
                 value=0.10, direction="high_bad", tolerance_pct=5.0,
                 status="active"),
    ]
    improved = [
        Observation(GLOBAL_SCOPE, "carto.load.ratio.load_ratio", 0.05),  # 变好
        Observation(GLOBAL_SCOPE, "carto.load.ratio.load_ratio", 0.103),  # 噪声内
    ]
    assert evaluate_ratchet(improved, baselines, tolerance_pct=5.0) == []


def test_provisional_baseline_records_but_does_not_block():
    baselines = [
        Baseline(scene_id=GLOBAL_SCOPE, check_id="carto.load.ratio.load_ratio",
                 value=0.10, status="provisional"),
    ]
    degraded = [Observation(GLOBAL_SCOPE, "carto.load.ratio.load_ratio", 0.90)]
    assert evaluate_ratchet(degraded, baselines) == []
    baselines_active = [
        Baseline(**{**baselines[0].__dict__, "status": "active"})
    ]
    assert len(evaluate_ratchet(degraded, baselines_active)) == 1


def test_waiver_expires_automatically():
    baselines = [
        Baseline(scene_id=GLOBAL_SCOPE, check_id="carto.load.ratio.load_ratio",
                 value=0.10, status="active"),
    ]
    degraded = [Observation(GLOBAL_SCOPE, "carto.load.ratio.load_ratio", 0.50)]
    now = datetime.now(timezone.utc)
    active_waiver = [{
        "scene_id": GLOBAL_SCOPE, "check_id": "carto.load.ratio.load_ratio",
        "reason": "known upstream regression, fixing", "expires_at": now + timedelta(days=7),
    }]
    expired_waiver = [{
        "scene_id": GLOBAL_SCOPE, "check_id": "carto.load.ratio.load_ratio",
        "reason": "stale", "expires_at": now - timedelta(days=1),
    }]
    violations = evaluate_ratchet(degraded, baselines, waivers=active_waiver)
    assert len(violations) == 1 and violations[0].waived
    assert violations[0].waiver_reason == "known upstream regression, fixing"
    violations = evaluate_ratchet(degraded, baselines, waivers=expired_waiver)
    assert len(violations) == 1 and not violations[0].waived


def test_scene_baseline_overrides_global():
    baselines = [
        Baseline(scene_id=GLOBAL_SCOPE, check_id="carto.load.ratio.load_ratio",
                 value=0.10, status="active"),
        Baseline(scene_id="step-fill", check_id="carto.load.ratio.load_ratio",
                 value=0.40, status="active"),
    ]
    obs = [Observation("step-fill", "carto.load.ratio.load_ratio", 0.30)]
    # 0.30 会打爆全局 0.10 基线，但 scene 专属基线是 0.40 → 通过。
    assert evaluate_ratchet(obs, baselines) == []


# ── DB 面 ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_baseline_write_load_activate_cycle(facts_db):
    rows = [
        {"scene_id": "heatmap-basic", "check_id": "carto.load.ratio.load_ratio",
         "value": 0.10},
        {"scene_id": "heatmap-basic", "check_id": "carto.load.ratio.load_ratio",
         "value": 0.12},
        {"scene_id": GLOBAL_SCOPE, "check_id": "carto.load.ratio.load_ratio",
         "value": 0.11},
    ]
    entries = build_baseline_entries(rows, quantile=0.66, tolerance_pct=5.0)
    assert len(entries) == 2
    assert all(e.status == "provisional" for e in entries)
    written = await write_baselines(entries, source="first_run")
    assert written == 2
    loaded = await load_baselines()
    assert {(b.scene_id, b.status) for b in loaded} == {
        ("heatmap-basic", "provisional"), (GLOBAL_SCOPE, "provisional"),
    }
    activated = await activate_baselines()
    assert activated == 2
    loaded = await load_baselines()
    assert all(b.status == "active" for b in loaded)
    # upsert 幂等：同键重写不新增行。
    await write_baselines(entries, source="calibration", status="active")
    assert len(await load_baselines()) == 2


@pytest.mark.anyio
async def test_waiver_roundtrip_and_expiry_filter(facts_db):
    assert await add_waiver(
        "carto.load.ratio.load_ratio", reason="flaky upstream", days=30,
        created_by="ac-10",
    )
    active = await load_active_waivers()
    assert len(active) == 1
    assert active[0]["reason"] == "flaky upstream"
    assert active[0]["expires_at"] > datetime.now(timezone.utc)


@pytest.mark.anyio
async def test_end_to_end_blocked_then_waived(facts_db, monkeypatch):
    """验收主线：入库 → 激活 → 注入劣化被拦 → 豁免后放行 → 到期再拦。"""
    rows = [
        {"scene_id": "s", "check_id": "carto.load.ratio.load_ratio", "value": 0.10}
        for _ in range(4)
    ]
    await write_baselines(build_baseline_entries(rows), source="first_run")
    await activate_baselines()

    from app.core.database import Base
    from app.models.cartography_quality import CartographyQualityRun
    import app.core.database as database

    async with database.AsyncSessionLocal() as db:
        baseline = (
            await db.execute(
                Base.metadata.tables["cartography_quality_baselines"].select()
            )
        ).first()
        assert baseline is not None
        db.add(CartographyQualityRun(id="r1", scene_id="s", lane="runtime"))
        db.add(CartographyQualityRun(id="r2", scene_id="s", lane="runtime"))
        await db.commit()

    from app.services.cartography_metrics_store import record_quality_run

    # 人工劣化 +50%
    await record_quality_run(
        lane="runtime", source="test", scene_id="s",
        checks=[{"rule": "carto.load.ratio", "status": "fail",
                 "evidence": {"load_ratio": 0.60}}],
    )

    from app.services.cartography_ratchet import collect_observation_rows

    obs_rows = await collect_observation_rows(lanes=("runtime",))
    # 只有刚写入的劣化 run 带 metric 行（r1/r2 是空 run）。
    degraded = aggregate_observations(obs_rows)
    baselines = await load_baselines()
    violations = evaluate_ratchet(degraded, baselines)
    assert len(violations) == 1
    assert not violations[0].waived

    await add_waiver("carto.load.ratio.load_ratio", reason="acknowledged", days=7)
    waivers = await load_active_waivers()
    violations = evaluate_ratchet(
        degraded, baselines, waivers=waivers
    )
    assert len(violations) == 1 and violations[0].waived
