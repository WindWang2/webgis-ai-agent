"""W1 意图学习基座测试（V11，ADR-0161，缺口 G11）。

覆盖：证据库（记/查/回放 + fail-safe）、反馈信号（半衰期衰减 + 词表闭集）、
配方亲和（Laplace 权重 + 稳定重排 + 链求解先验缝）、记忆置信度/过期、
澄清台账、capture_intent_adjudication 生产缝。

纪律红线（全部有断言锁定）：
- 学习信号是**先验而非证据**：亲和只重排，不增删候选、不推翻规则裁决；
- fail-safe：DB 不可用时主链路零影响；
- 确定性：同状态恒同权重/同排序。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base
from app.models.intent_learning import CartoIntentEvidence
from app.models.project import Project
from app.services.cartography import intent_learning as il
from app.services.cartography.intent_learning import (
    capture_intent_adjudication,
    clarification_metrics,
    effective_signal_weight,
    query_intent_evidence,
    recipe_affinity_weight,
    record_feedback_signal,
    record_intent_adjudication,
    record_recipe_outcome,
    reorder_by_affinity,
    replay_intent_adjudication,
    set_session_local_factory,
    signal_decay_factor,
)
from app.services.gis_harness.intent import resolve_map_request_intent


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture()
def db(factory):
    session = factory()
    yield session
    session.close()


def _project(db, pid="p1"):
    db.add(Project(id=pid, name="t"))
    db.commit()


# ── W1.1 证据库 ──────────────────────────────────────────────────────────

def _adjudicate(query: str):
    return resolve_map_request_intent(query, record_metrics=False)


def test_evidence_roundtrip_and_replay(db):
    intent = _adjudicate("成都市学校分布图")
    row_id = record_intent_adjudication(
        db,
        query="成都市学校分布图",
        task=intent.task,
        fallback=bool(intent.fallback_decision or {}).get("fallback", False)
        if isinstance(intent.fallback_decision, dict) else False,
        matched_rules=list(intent.matched_rules or []),
        confidence=float(intent.confidence or 0.0),
        slots={"subject": getattr(intent.subject, "name", "")},
        lang=intent.lang,
    )
    assert row_id is not None
    rows = query_intent_evidence(db, task=intent.task)
    assert len(rows) == 1 and rows[0].query_text == "成都市学校分布图"

    replay = replay_intent_adjudication(db, row_id)
    assert replay["replayed"] is not None
    # 同引擎状态下回放与历史裁决完全一致（diff 为空 = 确定性契约成立）
    assert replay["diff"] == {}
    assert replay["replayed"]["task"] == intent.task


def test_capture_seam_writes_from_contract(db, factory):
    set_session_local_factory(factory)
    try:
        intent = _adjudicate("北京人口密度分级图")
        row_id = capture_intent_adjudication(
            query="北京人口密度分级图", intent=intent, session_id="s1")
        assert row_id is not None
        rows = query_intent_evidence(db, session_id="s1")
        assert rows and rows[0].task == intent.task
        assert rows[0].query_hash  # 可按 hash 聚合
    finally:
        set_session_local_factory(None)


def test_evidence_fail_safe_on_broken_db(monkeypatch):
    class _Broken:
        def __call__(self):
            raise RuntimeError("db down")

        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *a):
            return False

    set_session_local_factory(_Broken)
    try:
        intent = _adjudicate("上海交通流量图")
        # fail-safe：不抛、返回 None，主链路零影响
        assert capture_intent_adjudication(
            query="上海交通流量图", intent=intent) is None
    finally:
        set_session_local_factory(None)


# ── W1.2 反馈信号 ────────────────────────────────────────────────────────

def test_feedback_signal_decay(db):
    now = datetime.now(timezone.utc)
    fresh = CartoIntentEvidence  # noqa: F841 — 占位以示同模块
    from app.models.intent_learning import CartoFeedbackSignal
    db.add(CartoFeedbackSignal(
        created_at=now - timedelta(days=0), signal_type="palette_change",
        weight=4.0, decay_days=10.0, target="recipe:alpha", payload={}))
    db.add(CartoFeedbackSignal(
        created_at=now - timedelta(days=30), signal_type="palette_change",
        weight=4.0, decay_days=10.0, target="recipe:alpha", payload={}))
    db.commit()
    eff = effective_signal_weight(db, target="recipe:alpha", now=now)
    # 新信号全额、旧信号半衰 3 期（0.5^3 = 0.125）→ 4 + 0.5
    assert eff["negative"] == pytest.approx(4.0 + 0.5, abs=1e-3)
    assert eff["positive"] == 0.0


def test_feedback_decay_factor_pure():
    assert signal_decay_factor(0.0, 30.0) == 1.0
    assert signal_decay_factor(30.0, 30.0) == 0.5
    assert signal_decay_factor(60.0, 30.0) == 0.25
    assert signal_decay_factor(999.0, 0.0) == 1.0  # 不衰减档


def test_feedback_signal_closed_vocabulary(db):
    with pytest.raises(ValueError):
        record_feedback_signal(db, signal_type="vibes", target="recipe:x")
    with pytest.raises(ValueError):
        record_feedback_signal(db, signal_type="accept", target="recipe:x", weight=0)


# ── W1.3 配方亲和 ────────────────────────────────────────────────────────

def test_affinity_weight_formula():
    assert recipe_affinity_weight((0, 0)) == 0.0        # 冷启动中性
    assert recipe_affinity_weight((1, 0)) == pytest.approx(1 / 3)
    assert recipe_affinity_weight((0, 1)) == pytest.approx(-1 / 3)
    assert recipe_affinity_weight((10, 0)) == pytest.approx(10 / 12)
    assert -1.0 < recipe_affinity_weight((1, 10)) < 0.0


def test_affinity_record_and_reorder(db):
    for _ in range(3):
        record_recipe_outcome(db, recipe_id="beta", success=True)
    record_recipe_outcome(db, recipe_id="beta", success=False)
    record_recipe_outcome(db, recipe_id="gamma", success=False)
    record_recipe_outcome(db, recipe_id="gamma", success=False)

    weights = il.recipe_affinity_weights(db, ["alpha", "beta", "gamma"])
    assert weights["alpha"] == 0.0                    # 未登记 → 冷启动中性
    assert weights["beta"] > weights["gamma"]

    ordered = reorder_by_affinity(db, ["alpha", "gamma", "beta"])
    assert ordered == ["beta", "alpha", "gamma"]      # gamma 负权沉底；alpha 中性保序

    # 重排不增删候选（先验非证据红线）
    assert sorted(ordered) == sorted(["alpha", "beta", "gamma"])


def test_affinity_outcome_deterministic(db):
    a = record_recipe_outcome(db, recipe_id="x", success=True)
    b = record_recipe_outcome(db, recipe_id="x", success=True)
    # 同状态恒同权重：权重是计数的纯函数（Laplace 平滑），随样本演进可复算
    assert a["weight"] == pytest.approx(1 / 3)
    assert b["weight"] == pytest.approx(2 / 4)
    assert b["success_count"] == 2
    assert recipe_affinity_weight((2, 0)) == pytest.approx(b["weight"])


def test_chain_solver_accepts_affinity_prior():
    """链求解器的先验缝：同 priority 并列时按权重取优（落选者留痕）。"""
    from app.services.gis_harness.recipes import (
        FallbackLink, CartographyRecipe, RecipeRegistry, resolve_fallback_chain,
    )

    def _recipe(**kw):
        base = dict(id="t_recipe", name="t")
        base.update(kw)
        return CartographyRecipe(**base)

    reg = RecipeRegistry()
    a = _recipe(id="aff_a", required_geometry=["Polygon"])
    b1 = _recipe(id="aff_b", required_geometry=[], priority=10)
    b2 = _recipe(id="aff_c", required_geometry=[], priority=10)
    a.fallback_links = [FallbackLink(to="aff_b"), FallbackLink(to="aff_c")]
    for r in (a, b1, b2):
        reg.register(r)
    try:
        # 无先验 → id 字典序取 aff_b（行为不变契约）
        r1 = resolve_fallback_chain(a, profile={
            "geometryTypes": ["Point"], "featureCount": 50}, registry=reg)
        assert r1.final_recipe == "aff_b"
        # 有先验（aff_c 正权重）→ 同 priority 并列改道 aff_c
        r2 = resolve_fallback_chain(a, profile={
            "geometryTypes": ["Point"], "featureCount": 50}, registry=reg,
            affinity={"aff_b": 0.0, "aff_c": 0.9})
        assert r2.final_recipe == "aff_c"
        demoted = [x for x in r2.attempts
                   if x.to_recipe == "aff_b" and "demoted_by_sort_key" in x.note]
        assert len(demoted) == 1
    finally:
        for rid in ("aff_a", "aff_b", "aff_c"):
            reg.unregister(rid)


# ── W1.4 记忆置信度/过期 ────────────────────────────────────────────────

def test_memory_expiry_and_confidence(db):
    from app.services.cartography.project_memory import get_active_facts, record_fact
    _project(db)
    now = datetime.now(timezone.utc)
    record_fact(db, "p1", "preference", "palette", {"value": "YlOrRd"},
                confidence=0.9)
    record_fact(db, "p1", "preference", "layout", {"value": "compact"},
                confidence=0.5,
                expires_at=now - timedelta(days=1))
    db.commit()
    facts = {f.subject: f for f in get_active_facts(db, "p1")}
    assert "palette" in facts
    assert "layout" not in facts          # 过期 → 不再注入
    assert facts["palette"].confidence == pytest.approx(0.9)


# ── W1.6 澄清台账 ────────────────────────────────────────────────────────

def test_clarification_metrics(db):
    now = datetime.now(timezone.utc)
    for i, clar in enumerate([
        {"request": {"questions": [{"slot": "subject"}]}, "answers": {"subject": "学校"}},
        {"request": {"questions": [{"slot": "area"}]}, "answers": {}},
        {"request": {"questions": []}, "answers": {}},
        {},
        {},
        {},
    ]):
        db.add(CartoIntentEvidence(
            created_at=now - timedelta(minutes=i), task="t",
            clarification=clar, matched_rules=[], task_candidates=[],
            slots={}, confidence_components={}))
    db.commit()
    m = clarification_metrics(db)
    assert m["total"] == 6
    assert m["clarified"] == 3
    assert m["clarify_hit"] == 1
    assert m["hit_rate"] == pytest.approx(1 / 3)
    assert m["false_trigger_rate"] == pytest.approx(0.5)


def test_clarification_metrics_empty_is_honest(db):
    m = clarification_metrics(db)
    assert m["total"] == 0
    assert m["false_trigger_rate"] == 0.0
    assert m["hit_rate"] == 1.0          # 未澄清 = 未误触
