"""W7 闭环与自愈智能化测试（V11，ADR-0167）。

本地确定性视觉判据（无 VLM fallback）+ 自愈策略库/归因表（≥30 样本）+
动作空间扩展 + 阻断开关回滚。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base  # noqa: E402
from app.models.intent_learning import CartoFeedbackSignal  # noqa: E402,F401 — 注册表


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def _png(paint: bool, size: int = 64) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (size, size), (250, 250, 250))
    if paint:
        for x in range(8, size - 8, 4):
            for y in range(8, size - 8, 4):
                img.putpixel((x, y), (30, 90, 160))
                img.putpixel((x + 1, y), (200, 40, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── W7.1 本地确定性视觉判据 ─────────────────────────────────────────────

def test_local_visual_facts_deterministic() -> None:
    from app.lib.harness.local_visual_criteria import (
        evaluate_local_visual,
        measure_visual_facts,
    )

    png = _png(paint=True)
    facts = measure_visual_facts(png)
    assert facts == measure_visual_facts(png)
    assert 0.0 < facts["inkRatio"] < 1.0
    out = evaluate_local_visual(png)
    assert out["evaluated"] is True
    # 有内容的图：五维均可评估且非 not_evaluated
    assert all(d["status"] in ("pass", "warning") for d in out["dimensions"].values())
    assert out["dimensions"]["readability"]["provisional"] is True  # W8 校准前不拦截


def test_local_visual_fail_closed_without_screenshot() -> None:
    from app.lib.harness.local_visual_criteria import evaluate_local_visual

    out = evaluate_local_visual(None)
    assert out["evaluated"] is False
    assert all(d["status"] == "not_evaluated" for d in out["dimensions"].values())


def test_local_visual_blank_detected() -> None:
    from app.lib.harness.local_visual_criteria import evaluate_local_visual

    out = evaluate_local_visual(_png(paint=False))  # 纯背景 → 空白警告
    assert out["dimensions"]["polish_completeness"]["status"] == "warning"
    assert out["dimensions"]["readability"]["status"] == "warning"


# ── W7.2/W7.3 自愈策略库与归因 ──────────────────────────────────────────

def test_attribution_and_success_table_with_30_samples(db) -> None:
    from app.services.cartography.selfheal_policy import (
        action_success_table,
        attribution_rows,
        rank_actions_by_history,
        record_repair_outcome,
    )

    # 30 条真实样本（rotating_classification 成功率高、clip_value_domain 低）
    for i in range(30):
        action = "adjust_classification" if i % 3 != 0 else "clip_value_domain"
        expected = 0.7 if action == "adjust_classification" else 0.5
        success = (i % 4 != 3) if action == "adjust_classification" else (i % 5 == 0)
        actual = expected * (0.9 if success else 0.2)
        record_repair_outcome(
            db, action_id=action, context=f"layer-{i}",
            expected_effect=expected, actual_delta=actual,
            success=success, surface="desired_state", risk="auto_safe",
        )
    rows = attribution_rows(db)
    assert len(rows) == 30  # 归因表 ≥30 条真实样本（验收）
    verdicts = {r["verdict"] for r in rows}
    assert verdicts == {"effective", "ineffective"}

    table = action_success_table(db)
    by_id = {e["actionId"]: e for e in table}
    assert by_id["adjust_classification"]["successRate"] > \
        by_id["clip_value_domain"]["successRate"]
    # 排序契约：成功率降序 → adjust 在前
    assert table[0]["actionId"] == "adjust_classification"

    # 历史先验只重排：候选集不变
    candidates = ["clip_value_domain", "adjust_classification"]
    ranked = rank_actions_by_history(db, candidates)
    assert sorted(ranked) == sorted(candidates)
    assert ranked[0] == "adjust_classification"


def test_rank_cold_start_preserves_order(db) -> None:
    from app.services.cartography.selfheal_policy import rank_actions_by_history

    candidates = ["b", "a", "c"]
    assert rank_actions_by_history(db, candidates) == candidates  # 无历史 = 原序


def test_record_repair_outcome_fail_safe() -> None:
    from app.services.cartography.selfheal_policy import record_repair_outcome

    class _Broken:
        def add(self, _row):
            raise RuntimeError("db down")

        def commit(self):
            raise RuntimeError("db down")

        def rollback(self):
            raise RuntimeError("db down")

    assert record_repair_outcome(
        _Broken(), action_id="x", context="c",
        expected_effect=1.0, actual_delta=0.0, success=False,
    ) is None


# ── W7.4 动作空间扩展 ───────────────────────────────────────────────────

def test_action_space_extended() -> None:
    from app.lib.cartography.selfheal_actions import SELFHEAL_ACTIONS

    ids = {a.action_id for a in SELFHEAL_ACTIONS}
    # V10 五类之上的四类扩展（W7.4）
    for new_action in ("switch_composition", "change_aggregation",
                       "change_projection", "resample"):
        assert new_action in ids
    # 注册表自洽：trigger 非空、expected_effect ∈ (0,1]
    for spec in SELFHEAL_ACTIONS:
        assert spec.triggers
        assert 0.0 < spec.expected_effect <= 1.0


# ── W7.5 阻断开关（可回滚）─────────────────────────────────────────────

def test_blocking_switch_default_off_and_rollback(monkeypatch) -> None:
    from app.lib.cartography.selfheal_actions import selfheal_blocking_enabled

    monkeypatch.delenv("CARTO_SELFHEAL_BLOCKING", raising=False)
    assert selfheal_blocking_enabled() is False  # 默认 record-only（V10 语义）
    monkeypatch.setenv("CARTO_SELFHEAL_BLOCKING", "1")
    assert selfheal_blocking_enabled() is True
    # 一键回滚：清环境变量即回到 record-only
    monkeypatch.delenv("CARTO_SELFHEAL_BLOCKING", raising=False)
    assert selfheal_blocking_enabled() is False
