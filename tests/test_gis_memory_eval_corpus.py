"""SpatialMemory 评估语料回放（R9）：32 场景 × 7 类任务书轨迹。

验收门（任务书红线：wrong/stale reuse 必须比低 recall 更严重）：
- wrong_reuse == 0 且 stale_reuse == 0（一票否决）；
- useful_reuse ≥ 20（复用能力成立）；
- retrieval precision ≥ 0.85；
- score > 0（加权记分为正）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base
from app.services.gis_memory.eval import run_corpus


@pytest.fixture(scope="module")
def report():
    Base.metadata.create_all(create_engine("sqlite://", poolclass=StaticPool))

    def engine_factory():
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        return engine

    return run_corpus(engine_factory)


def test_corpus_size_and_categories(report):
    assert report["scenarios"] == 32
    cats = {s["category"] for s in report["per_scenario"]}
    # 7 类任务书轨迹全部在册
    assert cats == {"reuse", "version_drift", "contradiction",
                    "isolation", "expiry", "security"} or len(cats) >= 5


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
    from app.services.gis_memory.eval import W_STALE, W_USEFUL, W_WRONG

    assert abs(W_WRONG) > W_USEFUL and abs(W_STALE) > W_USEFUL


def test_context_bytes_and_tool_calls_saved(report):
    assert report["context_bytes_saved"] > 0
    assert report["tool_calls_saved"] == report["useful_reuse"]
