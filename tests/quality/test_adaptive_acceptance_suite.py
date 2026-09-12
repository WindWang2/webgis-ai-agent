"""自适应验收集（任务书 10 线 P5，ADR-0159）：同需求多轮必须"下一张不更差"。

这是其余 9 条自适应线的**最终验收口径**：同一自然语言需求连续跑 3 轮，

(a) 第 2/3 轮质量分不劣于第 1 轮（质量分 = 套件聚合：100 − 40×fail − 10×warning）；
(b) 收敛：第 3 轮与第 2 轮的符号方案决策差异 ≤ 阈值（决策 = 字段/类型/方法/
    调色板/分级数，断点按 5% 相对容差比 —— 生产上对应 ``SymbologyDecision``；
    03 线落地后用其决策对象替换 :func:`symbology_decision_signature` 的投影）；
(c) 全部轮次无 ``repair_exhausted``（且终态必须是 passed / passed_with_warnings）。

全部走**真实机制**，无 LLM、无浏览器、零随机：
- 制图生产：``build_graduated_spec`` + ``spec_to_paint``（与运行时同一投影面）；
- 评审与有界修复：``review_and_repair_cartography``（AUTO_SAFE 作曲器）；
- 每轮注入同一"生产者缺陷"（单调低 ΔE 调色板 → carto.color.separability
  fail → change_palette 修复），代表 flaky 生产面；
- 记忆通道：``harvest_facts_from_review``（评审通过后才写）→
  ``get_shared_classification`` 先验（下一轮复用验证过的分类方案）；
- 每轮质量 run 落 ADR-0159 事实库（lane=``adaptive``）。
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa

import app.core.database as database
from app.core.config import settings
from app.core.database import Base
from app.lib.cartography.quality_loop import review_and_repair_cartography
from app.lib.cartography.thematic_spec import (
    build_graduated_spec,
    spec_to_paint,
)
from app.services.cartography.project_memory import (
    get_shared_classification,
    harvest_facts_from_review,
)

# ── 套件契约面（03 线落地 SymbologyDecision 后替换此投影） ────────────────

#: 断点相对容差：同方案分类断点漂移 ≤5% 视为同一决策
SYMBIOLOGY_DECISION_TOLERANCE = 0.05
#: 收敛阈值：round3 vs round2 决策距离必须 ≤ 此值
CONVERGENCE_THRESHOLD = 0.25


def symbology_decision_signature(mapspec: dict) -> dict:
    """MapSpec → 每专题层的符号方案决策投影（可 JSON 化）。"""
    decisions: dict = {}
    for layer in mapspec.get("layers") or []:
        if not isinstance(layer, dict):
            continue
        legend = layer.get("legend_spec")
        if not isinstance(legend, dict):
            continue
        decisions[str(layer.get("id"))] = {
            "field": legend.get("field"),
            "type": legend.get("type"),
            "method": legend.get("method"),
            "palette": legend.get("palette"),
            "breaks": [float(b) for b in (legend.get("breaks") or [])],
            "category_keys": [
                c.get("key") for c in (legend.get("categories") or [])
                if isinstance(c, dict)
            ],
        }
    return decisions


def signature_distance(a: dict, b: dict) -> float:
    """决策距离 0..1：逐层逐字段比对，断点按相对容差，不匹配记 1 份。"""
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    mismatched = 0
    for key in keys:
        da, db = a.get(key), b.get(key)
        if da is None or db is None:
            mismatched += 1
            continue
        for field in ("field", "type", "method", "palette"):
            if da.get(field) != db.get(field):
                mismatched += 1
        if da.get("category_keys") != db.get("category_keys"):
            mismatched += 1
        breaks_a, breaks_b = da.get("breaks") or [], db.get("breaks") or []
        if len(breaks_a) != len(breaks_b):
            mismatched += 1
        else:
            for x, y in zip(breaks_a, breaks_b):
                if abs(y) > 1e-12 and abs(x - y) / abs(y) > SYMBIOLOGY_DECISION_TOLERANCE:
                    mismatched += 1
                elif abs(y) <= 1e-12 and abs(x - y) > 1e-12:
                    mismatched += 1
    total_fields = max(1, len(keys) * 6)
    return min(1.0, mismatched / total_fields)


def quality_score(review: dict) -> float:
    """套件聚合质量分：100 − 40×确定性fail − 10×warning（下限 0）。

    只数确定性证据的 fail（与 CartographyReport.status 同口径），warning
    含部分证据未齐（not_evaluated）的诚实披露。
    """
    fails = sum(
        1 for c in review.get("checks") or []
        if c.get("status") == "fail" and c.get("evidence_class") == "deterministic"
    )
    warnings = sum(1 for c in review.get("checks") or [] if c.get("status") == "warning")
    return float(max(0, 100 - 40 * fails - 10 * warnings))


# ── 确定性需求与生产面 ────────────────────────────────────────────────────

REQUIREMENT = "各区街道办人口密度分级专题图（graduated, k=4, YlOrRd）"
FIELD = "density"


def _round_values(seed: int) -> list:
    """同需求下第 seed 轮的数据实现（±3% 确定性扰动，无随机）。"""
    base = [12.0, 18.5, 25.0, 31.0, 40.5, 47.0, 55.5, 63.0, 72.5, 81.0,
            88.5, 95.0, 104.0, 112.5, 121.0, 130.0]
    return [round(v * (1.0 + 0.03 * seed), 3) for v in base]


def _geojson(values: list) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {FIELD: v},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                },
            }
            for v in values
        ],
    }


def _profile(values: list) -> dict:
    return {
        "featureCount": len(values),
        "geometryTypes": ["Polygon"],
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "fields": {
            FIELD: {
                "type": "numeric",
                "min": min(values),
                "max": max(values),
                "null_count": 0,
                "sampleValues": values[:8],
            }
        },
    }


def _producer_mapspec(values: list, prior_breaks: list | None = None) -> dict:
    """"生产者"按当前数据（或记忆先验）建 legend，再投影 paint。

    刻意注入同一生产者缺陷：``palette_colors`` 被换成单调低 ΔE 色
    （``carto.color.separability`` fail，repairability=auto_safe）——
    代表真实世界里 flaky 的符号生成面。
    """
    geojson = _geojson(values)
    if prior_breaks:
        spec = build_graduated_spec(geojson, FIELD, method="quantiles", k=4,
                                    palette="YlOrRd")
        assert spec is not None
        spec["breaks"] = [float(b) for b in prior_breaks]
        spec["labels"] = [
            f"[{spec['breaks'][i]:.0f}, {spec['breaks'][i + 1]:.0f})"
            for i in range(len(spec["breaks"]) - 1)
        ]
    else:
        spec = build_graduated_spec(geojson, FIELD, method="quantiles", k=4,
                                    palette="YlOrRd")
    assert spec is not None, "分类生产失败（需求不可满足）"
    # ── 生产者缺陷：相邻类色几乎不可分（ΔE00 < fail 阈值） ──
    spec["palette_colors"] = ["#f0f0f0", "#f2f2f2", "#f4f4f4", "#f6f6f6"]
    paint, warnings = spec_to_paint(spec)
    assert paint is not None and not warnings
    return {
        "version": 1,
        "sources": {
            "density-src": {
                "type": "geojson",
                "inlineData": geojson,
                "profile": {
                    **_profile(values),
                    "crs": "EPSG:4326",
                    "crs_status": "explicit",
                },
            }
        },
        "layers": [{
            "id": "density-fill",
            "type": "fill",
            "source": "density-src",
            "paint": {"color": paint, "opacity": 1},
            "legend_spec": spec,
        }],
        "view": {"zoom": 10, "center": [0.5, 0.5], "bearing": 0, "pitch": 0},
    }


@pytest.fixture()
def memory_db(tmp_path):
    """项目记忆库（tmp sqlite）+ facts 落库替换。"""
    db_path = Path(tmp_path) / "memory.db"
    import app.models.project  # noqa: F401 — 注册 CartoProjectFact

    engine = sa.create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine)
    from app.services.cartography import memory_harvest

    memory_harvest.set_session_local_factory(factory)
    yield factory
    memory_harvest.set_session_local_factory(None)
    engine.dispose()


@pytest.fixture()
async def facts_db(tmp_path, monkeypatch):
    """ADR-0159 事实库（lane=adaptive 落账用）。"""
    db_path = Path(tmp_path) / "facts.db"
    from app.models.cartography_quality import (  # noqa: F401
        CartographyQualityBaseline,
        CartographyQualityMetric,
        CartographyQualityRun,
        CartographyQualityWaiver,
    )

    engine = sa.create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    engine.dispose()
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool
    )
    async with async_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn)
        )
    monkeypatch.setattr(
        database, "AsyncSessionLocal",
        async_sessionmaker(bind=async_engine, expire_on_commit=False),
    )
    monkeypatch.setattr(settings, "CARTO_METRICS_STORE_ENABLED", True)
    yield
    await async_engine.dispose()


async def _run_round(seed: int, project_id: str, memory_factory, prior_breaks) -> dict:
    """一轮：需求 → 生产 → 评审+有界修复 → 记忆收割 → 事实落账。"""
    values = _round_values(seed)
    mapspec = _producer_mapspec(values, prior_breaks=prior_breaks)
    result = review_and_repair_cartography(mapspec)
    review = result.review
    verdict = result.to_dict()

    score = quality_score(review)
    signature = symbology_decision_signature(result.mapspec)

    # 记忆收割：只有通过评审才产出先验（ADR-0069 决策 2，fail-closed）。
    # 入参是 loop 裁决形态（to_dict：status 为 passed*/partial/failed_*）。
    harvested = 0
    with memory_factory() as db:
        harvested = harvest_facts_from_review(db, project_id, result.mapspec, verdict)
        db.commit()
        fact = get_shared_classification(db, project_id, FIELD)
        prior = (fact.payload or {}).get("breaks") if fact is not None else None

    from app.services.cartography_metrics_store import record_quality_run

    await record_quality_run(
        lane="adaptive",
        source="adaptive_acceptance_suite",
        session_id=project_id,
        scene_id=f"requirement-round-{seed}",
        passed=result.status in ("passed", "passed_with_warnings"),
        checks=review.get("checks") or [],
        summary={
            "round": seed,
            "status": result.status,
            "termination_reason": result.termination_reason,
            "score": score,
            "repair_attempts": len(result.attempts),
            "requirement": REQUIREMENT,
        },
    )
    return {
        "round": seed,
        "status": result.status,
        "termination_reason": result.termination_reason,
        "score": score,
        "signature": signature,
        "harvested": harvested,
        "prior_breaks": prior,
        "repair_attempts": len(result.attempts),
    }


@pytest.mark.anyio
async def test_same_requirement_three_rounds_never_degrades(facts_db, memory_db):
    """验收主线：(a) 不劣化 (b) 收敛 (c) 无 repair_exhausted。"""
    project_id = "ac10-adaptive-suite"
    rounds = []
    prior = None
    for seed in (1, 2, 3):
        outcome = await _run_round(seed, project_id, memory_db, prior)
        rounds.append(outcome)
        prior = outcome["prior_breaks"] or prior

    # (c) 终态诚实：全部 passed / passed_with_warnings，绝不 repair_exhausted。
    for outcome in rounds:
        assert outcome["status"] in ("passed", "passed_with_warnings"), outcome
        assert outcome["termination_reason"] != "repair_exhausted", outcome

    # (a) 下一张不更差：round2/3 质量分不劣于 round1。
    base_score = rounds[0]["score"]
    assert rounds[1]["score"] >= base_score, rounds
    assert rounds[2]["score"] >= base_score, rounds

    # (b) 收敛：round3 与 round2 的符号方案决策差异 ≤ 阈值。
    distance = signature_distance(rounds[2]["signature"], rounds[1]["signature"])
    assert distance <= CONVERGENCE_THRESHOLD, (
        rounds[1]["signature"], rounds[2]["signature"]
    )

    # 记忆通道真实生效：round1 通过后产出先验，后续轮复用同一分类方案。
    assert rounds[0]["harvested"] >= 1
    assert rounds[0]["prior_breaks"] is not None
    assert rounds[1]["harvested"] >= 1

    # 事实库已落 adaptive lane 的 3 次 run。
    from app.services.cartography_metrics_store import latest_runs

    runs = await latest_runs(30)
    adaptive = [r for r in runs if r["lane"] == "adaptive"]
    assert len(adaptive) == 3
    assert all(r["passed"] for r in adaptive)


@pytest.mark.anyio
async def test_untrustworthy_review_never_seeds_memory(facts_db, memory_db):
    """fail-closed：未通过的评审不得产出先验（记忆滞后证据一个身位）。"""
    values = _round_values(9)
    mapspec = _producer_mapspec(values)
    # 把 palette 换成不可修复的劣化（长度不足 → change_palette 无 replacement）
    mapspec["layers"][0]["legend_spec"]["palette_colors"] = ["#f0f0f0"]
    mapspec["layers"][0]["legend_spec"]["colors"] = ["#f0f0f0"]
    result = review_and_repair_cartography(mapspec)
    review = result.to_dict()
    with memory_factory_session(memory_db) as db:
        written = harvest_facts_from_review(db, "proj-x", result.mapspec, review)
        db.commit()
    if result.status in ("passed", "passed_with_warnings"):
        pytest.skip("该劣化在本轮被修复为通过（与用例前提不符）")
    assert written == 0


@contextmanager
def memory_factory_session(factory):
    with factory() as db:
        yield db
