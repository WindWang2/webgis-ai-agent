"""制图质量事实库（ADR-0159 P1）回归锁。

覆盖任务书 §5 验收：
- 事实库可写入与查询：最近 30 次 run 可检索；
- 证据缺失诚实落 NULL（缺观测 ≠ 0 分）；
- 保留策略（默认 90 天 / 5000 run，可配）两条删除路径；
- 停用开关零写入；账本故障绝不反噬评审主流程；
- 落库钩子只写账本、不改判定（runtime / eval 两个产出处）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

import app.core.database as database
from app.core.config import settings
from app.services import cartography_metrics_store as store


def _check(rule: str, status: str, evidence: dict) -> dict:
    return {
        "rule": rule,
        "status": status,
        "evidence_class": "deterministic",
        "evidence": evidence,
    }


@pytest.mark.anyio
async def test_record_and_query_roundtrip_recent_30(facts_db):
    """验收口径：写入后最近 30 次 run 可检索，趋势查询按检查项命中。"""
    for i in range(7):
        run_id = await store.record_quality_run(
            lane="desired_state" if i % 2 else "runtime",
            source="test",
            session_id=f"s-{i}",
            scene_id="heatmap-basic",
            passed=i % 2 == 0,
            checks=[
                _check("carto.load.ratio", "pass" if i % 2 else "warning",
                       {"load_ratio": 0.10 + i * 0.01}),
                _check("carto.legend.completeness", "pass", {}),
            ],
            gate_scores={"CartographicQuality": 100.0 if i % 2 == 0 else 0.0},
            summary={"status": "passed_with_warnings"},
        )
        assert run_id
    runs = await store.latest_runs(30)
    assert len(runs) == 7
    newest = runs[0]
    assert newest["lane"] in {"desired_state", "runtime"}
    assert newest["summary"]["status"] == "passed_with_warnings"
    # 每条 run：1 数值观测 + 1 无观测 verdict 行 + 1 gate 得分行
    assert len(newest["metrics"]) == 3

    trend = await store.query_quality_trend("carto.load.ratio")
    assert [row["value"] for row in trend] == [
        pytest.approx(0.10 + i * 0.01) for i in range(7)
    ]
    assert all(row["check_id"].startswith("carto.load.ratio") for row in trend)
    assert {row["lane"] for row in trend} == {"desired_state", "runtime"}


@pytest.mark.anyio
async def test_missing_evidence_records_null_not_zero(facts_db):
    """无证据 ≠ 0 分：verdict 落行、value=NULL。"""
    await store.record_quality_run(
        lane="runtime",
        source="test",
        checks=[_check("carto.scale.svs", "not_evaluated", {})],
    )
    trend = await store.query_quality_trend("carto.scale.svs")
    assert len(trend) == 1
    assert trend[0]["value"] is None
    assert trend[0]["verdict"] == "not_evaluated"


@pytest.mark.anyio
async def test_evidence_numbers_bounded_and_finite_only(facts_db):
    """bool/非有限值不进账本；逐检查项数值封顶 8 个。"""
    evidence = {"load_ratio": 0.2, "is_ok": True, "bad": float("nan")}
    evidence.update({f"extra_{i}": float(i) for i in range(12)})
    await store.record_quality_run(
        lane="runtime", source="test",
        checks=[_check("carto.load.ratio", "warning", evidence)],
    )
    trend = await store.query_quality_trend("carto.load.ratio")
    keys = {row["check_id"] for row in trend}
    assert "carto.load.ratio.is_ok" not in keys
    assert "carto.load.ratio.bad" not in keys
    assert len(keys) == 8  # load_ratio + 7 个 extra_*（封顶）


@pytest.mark.anyio
async def test_retention_by_count_and_age(facts_db, monkeypatch):
    """max_runs 截断保留最新；age 过期删除。双方言 SQL。"""
    monkeypatch.setattr(settings, "CARTO_METRICS_MAX_RUNS", 3)
    monkeypatch.setattr(settings, "CARTO_METRICS_RETENTION_DAYS", 90)
    for i in range(5):
        assert await store.record_quality_run(
            lane="runtime", source="test",
            checks=[_check("carto.load.ratio", "pass", {"load_ratio": i})],
        )
    runs = await store.latest_runs(30)
    assert len(runs) == 3
    values = sorted(
        m["value"] for r in runs for m in r["metrics"]
        if m["check_id"] == "carto.load.ratio.load_ratio"
    )
    assert values == [2.0, 3.0, 4.0]

    # age 路径：手工把一条 run 推到 91 天前再触发。
    from app.models.cartography_quality import CartographyQualityRun

    old_cut = datetime.now(timezone.utc) - timedelta(days=91)
    async_engine = sa.create_engine(
        f"sqlite:///{facts_db}", connect_args={"check_same_thread": False}
    )
    with async_engine.begin() as conn:
        conn.execute(
            sa.update(CartographyQualityRun)
            .where(CartographyQualityRun.id == runs[-1]["run_id"])
            .values(ts=old_cut.replace(tzinfo=None))
        )
    async_engine.dispose()
    deleted = await store.maybe_enforce_retention()
    assert deleted["expired_runs"] == 1
    runs_after = await store.latest_runs(30)
    assert len(runs_after) == 2


@pytest.mark.anyio
async def test_disabled_store_short_circuits(facts_db, monkeypatch):
    monkeypatch.setattr(settings, "CARTO_METRICS_STORE_ENABLED", False)
    run_id = await store.record_quality_run(
        lane="runtime", source="test",
        checks=[_check("carto.load.ratio", "pass", {"load_ratio": 1.0})],
    )
    assert run_id is None
    assert await store.latest_runs(30) == []


@pytest.mark.anyio
async def test_store_failure_never_raises(facts_db, monkeypatch):
    """账本故障绝不反噬评审：坏 sessionmaker → 返回 None 不抛。"""
    class _Broken:
        def __call__(self):
            raise RuntimeError("db unavailable")

    monkeypatch.setattr(database, "AsyncSessionLocal", _Broken())
    run_id = await store.record_quality_run(
        lane="runtime", source="test",
        checks=[_check("carto.load.ratio", "pass", {"load_ratio": 1.0})],
    )
    assert run_id is None


@pytest.mark.anyio
async def test_runtime_hook_records_without_altering_verdict(facts_db, monkeypatch):
    """``_cartographic_review`` 产出处钩子：只写账本，result 原样返回。"""
    import app.services.cartography_runtime as runtime

    seen: list[dict] = []

    async def _fake_record(**kwargs):
        seen.append(kwargs)
        return "run-id"

    monkeypatch.setattr(runtime, "record_quality_run", _fake_record)
    result = {
        "session_id": "s-hook",
        "cartography": {
            "status": "passed_with_warnings",
            "termination_reason": "review_only",
            "desired_status": "warning",
            "runtime_status": "not_evaluated",
            "checks": [_check("carto.load.ratio", "warning", {"load_ratio": 0.3})],
        },
        "gate": {"score": 0.0, "reason": "evaluated_failure"},
        "overall_passed": False,
    }
    runtime._spawn_quality_fact_record(result)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    await asyncio.gather(*pending)
    assert len(seen) == 1
    call = seen[0]
    assert call["lane"] == "runtime"
    assert call["passed"] is False
    assert call["gate_scores"] == {"CartographicQuality": 0.0}
    # 判定逻辑未被改写：result 本体没被钩子动过。
    assert result["cartography"]["status"] == "passed_with_warnings"
    assert result["overall_passed"] is False


def test_harness_runner_records_eval_lane(facts_db, monkeypatch):
    """HarnessEvaluator 产出处（eval suite）钩子：eval lane + 维度得分。"""
    from app.tools import harness_runner

    seen: list[dict] = []

    def _fake_sync(**kwargs):
        seen.append(kwargs)
        return "run-id"

    monkeypatch.setattr(store, "record_quality_run_sync", _fake_sync)
    out = harness_runner.run_benchmark_scenario(
        scenario_id="scenario-eval-lane",
        expected_tools=["st_dbscan"],
        ideal_step_count=1,
        simulated_tool_calls=[
            {"id": "c1", "name": "st_dbscan", "arguments": {}}
        ],
        simulated_tool_results=[
            {"id": "c1", "name": "st_dbscan", "result": {"ok": True},
             "is_error": False}
        ],
    )
    overall = bool(out["evaluation"]["overall_passed"])
    assert len(seen) == 1
    call = seen[0]
    assert call["lane"] == "eval"
    assert call["source"] == "harness_evaluator"
    assert call["passed"] is overall
    assert "ToolChoiceAccuracy" in call["gate_scores"]


def test_rows_from_checks_caps_run_size():
    """超长 checks 列表封顶 96 行，防大载荷入账本。"""
    checks = [
        _check(f"carto.rule.{i}", "pass", {"value": 1.0, "v2": 2.0})
        for i in range(200)
    ]
    rows = store._rows_from_checks(checks)
    assert len(rows) == store._MAX_METRICS_PER_RUN


def test_bounded_summary_projection():
    summary = {f"k{i}": ("x" * 500 if i == 0 else i) for i in range(30)}
    bounded = store._bounded_summary(summary)
    assert bounded is not None
    assert len(bounded) == store._MAX_SUMMARY_KEYS
    assert len(bounded["k0"]) == store._MAX_SUMMARY_STR


@pytest.mark.anyio
async def test_trend_prefix_query_escapes_like_wildcards(facts_db):
    """check_id 前缀里的 _ 不当通配符（review 修复）：只命中真前缀。"""
    await store.record_quality_run(
        lane="runtime", source="test",
        checks=[
            _check("carto.load.ratio", "pass", {"load_ratio": 1.0}),
            _check("carto_load_ratioX", "pass", {"load_ratio": 2.0}),
        ],
    )
    trend = await store.query_quality_trend("carto.load.ratio")
    ids = {row["check_id"] for row in trend}
    assert any(i.startswith("carto.load.ratio") for i in ids)
    assert not any(i.startswith("carto_load_ratioX") for i in ids)
