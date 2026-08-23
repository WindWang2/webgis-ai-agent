"""Recipe recommendation × project memory convergence (spec 开放问题 3).

单一推荐路径 + 记忆加成：``select_candidates`` 的排序元组在 priority 之前
插入"项目验证"层——语义同分时本项目验证过的 recipe 前置；语义弱一档的
验证 recipe **不会**压过语义更强的候选（记忆是先验，不是评审）。
写入侧：turn 评审通过 + session 计划带 recipe_id → ``recipe_outcome`` 事实。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.database import Base
from app.models.project import Project
from app.services.cartography.project_memory import (
    get_verified_recipe_ids,
    record_fact,
)
from app.services.gis_harness.recipes import CartographyRecipe, RecipeRegistry
from app.services.gis_harness.intent import resolve_map_request_intent


class _Intent:
    task = "t"
    cartography_intents: list = []
    geometry_expectation = ""


def _registry_with_tie():
    reg = RecipeRegistry()
    reg.register(CartographyRecipe(id="alpha", name="A", intent_tasks=["t"], priority=10))
    reg.register(CartographyRecipe(id="beta", name="B", intent_tasks=["t"], priority=20))
    return reg


# ─── 排序语义 ─────────────────────────────────────────────────────────────

def test_verified_breaks_semantic_ties_ahead_of_static_priority():
    reg = _registry_with_tie()
    assert [r.id for r in reg.select_candidates(_Intent())] == ["alpha", "beta"]
    boosted = [r.id for r in reg.select_candidates(_Intent(), project_verified={"beta"})]
    assert boosted == ["beta", "alpha"]


def test_verified_never_overrides_stronger_semantic_match():
    reg = RecipeRegistry()
    reg.register(CartographyRecipe(id="wide", name="W", intent_tasks=["t"], priority=10))
    reg.register(CartographyRecipe(
        id="narrow", name="N", intent_tasks=["t"],
        intent_cartography=["aggregate_grid"], priority=20,
    ))
    intent = type("I", (), {
        "task": "t",
        "cartography_intents": ["aggregate_grid"],
        "geometry_expectation": "",
    })()
    base = [r.id for r in reg.select_candidates(intent)]
    # The cartography hit outranks verification — memory is a prior, not a
    # verdict (ADR-0069 decision 2).
    boosted = [r.id for r in reg.select_candidates(intent, project_verified={"wide"})]
    assert base == boosted == ["narrow", "wide"]


def test_no_project_context_is_byte_identical():
    reg = _registry_with_tie()
    base = [r.id for r in reg.select_candidates(_Intent())]
    none_ctx = [r.id for r in reg.select_candidates(_Intent(), project_verified=None)]
    empty_ctx = [r.id for r in reg.select_candidates(_Intent(), project_verified=set())]
    assert none_ctx == empty_ctx == base


def test_real_intent_recommendation_unchanged_without_memory():
    from app.services.gis_harness.recipes import get_recipe_registry, reset_recipe_registry

    reset_recipe_registry()
    reg = get_recipe_registry()
    intent = resolve_map_request_intent("看看各区餐厅数量分布")
    base = [r.id for r in reg.select_candidates(intent)]
    no_mem = [r.id for r in reg.select_candidates(intent, project_verified=None)]
    assert base == no_mem and base  # 非空且不因参数缺省变化


# ─── 读取口（账本 → 验证集）──────────────────────────────────────────────

@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Project(id="pr-1", name="Recipe Project"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def test_verified_recipe_ids_reads_only_active_recipe_outcomes(db):
    record_fact(db, "pr-1", "recipe_outcome", "alpha", {"task": "t"},
                validity_tier="SEMANTIC_VALID")
    stale = record_fact(db, "pr-1", "recipe_outcome", "beta", {"task": "t"})
    stale.status = "stale"
    db.commit()
    assert get_verified_recipe_ids(db, "pr-1") == {"alpha"}
    assert get_verified_recipe_ids(db, "other") == set()


# ─── 写入侧（harvest 记 recipe_outcome）──────────────────────────────────

def test_harvest_records_recipe_outcome_on_passing_review(db, monkeypatch):
    from app.services.cartography import memory_harvest as mh

    engine = db.get_bind()
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(mh, "_session_local_factory", factory)
    mapspec = {
        "version": "1.0",
        "sources": {"s1": {"type": "geojson", "ref": "ref:geojson-x"}},
        "layers": [],
    }
    result = mh._harvest_sync(
        "pr-1", mapspec, {"overall_passed": True}, recipe_id="alpha",
    )
    assert result["facts_written"] == 1
    ids = get_verified_recipe_ids(db, "pr-1")
    assert ids == {"alpha"}


def test_harvest_skips_recipe_outcome_on_failing_review(db, monkeypatch):
    from app.services.cartography import memory_harvest as mh

    engine = db.get_bind()
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(mh, "_session_local_factory", factory)
    mapspec = {"version": "1.0", "sources": {}, "layers": []}
    result = mh._harvest_sync(
        "pr-1", mapspec,
        {"overall_passed": False, "cartography": {"status": "failed_unrepairable"}},
        recipe_id="alpha",
    )
    assert result["facts_written"] == 0
    assert get_verified_recipe_ids(db, "pr-1") == set()


def test_harvest_without_recipe_id_records_nothing(db, monkeypatch):
    from app.services.cartography import memory_harvest as mh

    engine = db.get_bind()
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(mh, "_session_local_factory", factory)
    mapspec = {"version": "1.0", "sources": {}, "layers": []}
    result = mh._harvest_sync("pr-1", mapspec, {"overall_passed": True}, recipe_id="")
    assert result["facts_written"] == 0
