"""SpatialMemory 评估语料回放（R9）：32 场景 × 7 类任务书轨迹。

验收门（任务书红线：wrong/stale reuse 必须比低 recall 更严重）：
- wrong_reuse == 0 且 stale_reuse == 0（一票否决）；
- useful_reuse ≥ 20（复用能力成立）；
- retrieval precision ≥ 0.85；
- score > 0（加权记分为正）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base
from scripts import gis_memory_eval
from scripts.gis_memory_eval import run_corpus


@pytest.fixture(scope="module")
def engine_factory():
    engines = []

    def make_engine():
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        engines.append(engine)
        Base.metadata.create_all(engine)
        return engine

    yield make_engine
    for engine in engines:
        engine.dispose()


@pytest.fixture(scope="module")
def report(engine_factory):
    return run_corpus(engine_factory)


def test_corpus_size_and_categories(report):
    assert report["scenarios"] == 32
    # review F15：类别集合精确锁定（7 类任务书轨迹全在册，不得静默漂移）
    cats = {s["category"] for s in report["per_scenario"]}
    assert cats == {"reuse", "version_drift", "contradiction",
                    "isolation", "expiry", "security"}


def test_no_wrong_reuse(report):
    assert report["wrong_reuse"] == 0, json.dumps(
        [s for s in report["per_scenario"] if s["wrong"]], ensure_ascii=False
    )


def test_no_stale_reuse(report):
    assert report["stale_reuse"] == 0, json.dumps(
        [s for s in report["per_scenario"] if s["stale"]], ensure_ascii=False
    )


def test_useful_reuse_threshold(report):
    assert report["useful_reuse"] >= 20, json.dumps(report, ensure_ascii=False)[:800]


def test_retrieval_precision(report):
    consulted = report["useful_reuse"] + report["wrong_reuse"] + report["stale_reuse"]
    if consulted == 0:
        pytest.fail("语料没有产生任何复用判定")
    precision = report["relevant_returns"] / consulted
    assert precision >= 0.85, f"precision={precision:.3f}"


def test_weighted_score_positive(report):
    assert report["score"] > 0
    # 权重红线：wrong/stale 的扣分严格大于 useful 的加分（任务书要求）
    from scripts.gis_memory_eval import W_STALE, W_USEFUL, W_WRONG

    assert abs(W_WRONG) > W_USEFUL and abs(W_STALE) > W_USEFUL


def test_context_bytes_and_tool_calls_saved(report):
    assert report["context_bytes_saved"] > 0
    assert report["tool_calls_saved"] == report["useful_reuse"]


def test_offline_evaluator_is_not_ignored():
    """A moved evaluator must be included in clean checkouts, not only on disk."""
    checked = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "scripts/gis_memory_eval.py"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert checked.returncode == 1, checked.stderr or "offline evaluator is ignored"


def test_corpus_reports_cross_tenant_reuse(monkeypatch, engine_factory, report):
    """Inject a tenant-filter regression at the retrieval seam, not a fake score."""
    retrieve = gis_memory_eval.latest_resolved_place

    def ignore_tenant(db, **context):
        context["org_id"] = gis_memory_eval.ORG_A
        return retrieve(db, **context)

    monkeypatch.setattr(gis_memory_eval, "latest_resolved_place", ignore_tenant)
    faulty = run_corpus(engine_factory)

    assert faulty["useful_reuse"] == report["useful_reuse"]
    assert faulty["wrong_reuse"] == 4
    assert faulty["stale_reuse"] == 0
    assert faulty["score"] == report["score"] - 16
    security = [s for s in faulty["per_scenario"] if s["category"] == "security"]
    assert len(security) == 4
    assert all(s["wrong"] == 1 and s["failures"] for s in security)


def test_corpus_reports_expired_reuse(monkeypatch, engine_factory, report):
    """A failed expiry sweep must incur stale penalties in all four trajectories."""
    monkeypatch.setattr(gis_memory_eval.s, "sweep_expired", lambda *a, **kw: 0)
    faulty = run_corpus(engine_factory)

    assert faulty["useful_reuse"] == report["useful_reuse"]
    assert faulty["wrong_reuse"] == 0
    assert faulty["stale_reuse"] == 4
    assert faulty["score"] == report["score"] - 16
    expiry = [s for s in faulty["per_scenario"] if s["category"] == "expiry"]
    assert len(expiry) == 4
    assert all(s["stale"] == 1 and s["failures"] for s in expiry)


def test_corpus_distinguishes_recall_misses(monkeypatch, engine_factory, report):
    monkeypatch.setattr(gis_memory_eval, "latest_resolved_place", lambda *a, **kw: None)
    faulty = run_corpus(engine_factory)

    # Four city-reuse and four correction trajectories each consult twice;
    # four expiry trajectories each have one legitimate pre-expiry consult.
    assert sum(s["recall_misses"] for s in faulty["per_scenario"]) == 20
    assert faulty["useful_reuse"] == report["useful_reuse"] - 20
    assert faulty["wrong_reuse"] == faulty["stale_reuse"] == 0
    assert faulty["score"] == report["score"] - 60
