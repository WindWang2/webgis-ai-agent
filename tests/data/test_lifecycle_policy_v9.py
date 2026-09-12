"""V9 P3 —— 统一生命周期策略引擎测试（等价性验证为本文件核心）。

ADR-0140 行为保持红线（任务书 §5 门禁）：
- 五类对象全部登记注册表（适配器枚举）；
- **默认策略与现状行为等价**：全 observe、零候选、零删除调用 ——
  适配器只读（无任何删除路径可触达）即结构等价；本文件同时断言
  assess 报告的 per-kind action=observe / policy_enabled=False / candidates=0。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import Base, Engine, SessionLocal
from app.models.data_lifecycle import LifecycleObject, LifecyclePolicy
from app.models.db_model import GeoComputeWorkerCache
from app.services.data_lifecycle import adapters
from app.services.data_lifecycle.adapters import LCObject
from app.services.data_lifecycle.gc_plan import (
    GcPlanError,
    create_gc_plan,
    execute_plan,
    rollback_plan,
    transition,
)
from app.services.data_lifecycle.policy import (
    assess,
    classify_tier,
    ensure_defaults,
    revive_object,
)

ALL_KINDS = ("lakehouse_dataset", "fabric_materialization", "artifact_cache",
             "cog_output", "worker_cache")


@pytest.fixture(autouse=True)
def _setup():
    Base.metadata.create_all(bind=Engine)
    # 文件 sqlite 跨测试共享（conftest 约定）：本线三表 + worker_cache 先清
    with SessionLocal() as db:
        from app.models.data_lifecycle import GcPlan, LifecycleObject, LifecyclePolicy

        db.query(GcPlan).delete()
        db.query(LifecycleObject).delete()
        db.query(LifecyclePolicy).delete()
        db.query(GeoComputeWorkerCache).delete()
        db.commit()
    yield


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """文件类适配器根目录重定向到 tmp（不 chdir，保 DB 相对路径稳定）。"""
    art = tmp_path / "artifacts"
    cog = tmp_path / "cog"
    data = tmp_path / "data"
    for p in (art, cog, data):
        p.mkdir(parents=True)
    monkeypatch.setattr(adapters, "_artifact_root", lambda: art)
    monkeypatch.setattr(adapters, "_cog_root", lambda: cog)
    monkeypatch.setattr(adapters, "_data_root", lambda: data)
    monkeypatch.setattr(adapters, "_spill_root", lambda: data / "ref_spill")
    return {"artifacts": art, "cog": cog, "data": data}


def _seed_worker_cache(worker="w1", key="k1", hit_days_ago=30):
    with SessionLocal() as db:
        db.merge(GeoComputeWorkerCache(
            worker_id=worker, cache_key=key, owner_scope="scope-x",
            size_bytes=1234, cached_at=datetime.utcnow(),
            last_hit_at=datetime.utcnow() - timedelta(days=hit_days_ago),
        ))
        db.commit()


# ── 分级 ─────────────────────────────────────────────────────────────


def test_classify_tier_thresholds():
    now = datetime.now(timezone.utc)
    th = {"warm_after_s": 3600, "cold_after_s": 86400}
    assert classify_tier(now - timedelta(minutes=10), th, now=now) == "hot"
    assert classify_tier(now - timedelta(hours=2), th, now=now) == "warm"
    assert classify_tier(now - timedelta(days=2), th, now=now) == "cold"
    assert classify_tier(None, th, now=now) == "hot"  # 无证据 → 不冤枉


def test_ensure_defaults_idempotent_and_covers_all_kinds():
    with SessionLocal() as db:
        n1 = ensure_defaults(db)
        n2 = ensure_defaults(db)
        assert n1 == 5 and n2 == 0
        kinds = {p.kind for p in db.query(LifecyclePolicy).all()}
        assert kinds == set(ALL_KINDS)
        assert all(p.action == "observe" for p in db.query(LifecyclePolicy).all())


# ── 等价性验证（默认策略 = 现状） ────────────────────────────────────


def test_default_policies_equivalent_to_status_quo(roots):
    """默认全 observe：登记一切、判定分级、但零删除候选。"""
    (roots["artifacts"] / "deadbeef.tif").write_bytes(b"x" * 100)
    (roots["cog"] / "sess-a").mkdir()
    (roots["cog"] / "sess-a" / "out.tif").write_bytes(b"y" * 200)
    spill = roots["data"] / "ref_spill" / "sesshash"
    spill.mkdir(parents=True)
    (spill / "refhash.json").write_text("{}", encoding="utf-8")
    _seed_worker_cache()
    with SessionLocal() as db:
        summary = assess(db, persist=True,
                         now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert set(summary["kinds"].keys()) == set(ALL_KINDS)
    for kind in ALL_KINDS:
        seg = summary["kinds"][kind]
        assert seg["action"] == "observe", kind
        assert seg["policy_enabled"] is False, kind
        assert seg["candidate_count"] == 0, kind
        assert seg["candidates"] == [], kind
    # 登记视图真实落库
    with SessionLocal() as db:
        kinds_seen = {r.kind for r in db.query(LifecycleObject).all()}
    assert {"artifact_cache", "cog_output", "fabric_materialization",
            "worker_cache"} <= kinds_seen


def test_adapters_enumerate_real_mechanisms(roots):
    (roots["artifacts"] / "a.tif").write_bytes(b"x")
    (roots["cog"] / "s" / "b.tif").parent.mkdir(parents=True)
    (roots["cog"] / "s" / "b.tif").write_bytes(b"y")
    art = adapters.enumerate_artifacts()
    cog = adapters.enumerate_cog()
    assert [o.object_id for o in art.objects] == ["a"]
    assert art.objects[0].byte_size == 1
    assert [o.object_id for o in cog.objects] == ["s/b.tif"]
    assert cog.objects[0].owner_scope == "s"


def test_revive_object_promotes_to_hot(roots):
    stale = LCObject(kind="artifact_cache", object_id="old.tif", byte_size=1,
                     last_used_at=datetime.now(timezone.utc) - timedelta(days=30))
    with SessionLocal() as db:
        db.add(LifecycleObject(kind=stale.kind, object_id=stale.object_id,
                               byte_size=1, tier="cold",
                               last_used_at=stale.last_used_at.replace(tzinfo=None)))
        db.commit()
        assert revive_object(db, "artifact_cache", "old.tif")
        row = db.query(LifecycleObject).filter_by(
            kind="artifact_cache", object_id="old.tif").one()
        assert row.tier == "hot"
        assert not revive_object(db, "artifact_cache", "missing")


# ── GC 闭环（P5） ────────────────────────────────────────────────────


def _enable_cog_stage_delete(staging_hours=0):
    with SessionLocal() as db:
        ensure_defaults(db)
        p = db.query(LifecyclePolicy).filter_by(kind="cog_output").one()
        p.action = "stage_delete"
        p.enabled = True
        p.staging_hours = staging_hours
        p.tier_thresholds = {"warm_after_s": 3600, "cold_after_s": 7200}
        db.commit()


def _seed_old_cog(roots, name="s/old.tif"):
    path = roots["cog"] / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"z" * 10)
    old = time.time() - 10 * 86400
    import os
    os.utime(path, (old, old))


def test_gc_plan_lakehouse_observe_only():
    with SessionLocal() as db:
        with pytest.raises(GcPlanError):
            create_gc_plan(db, kinds=["lakehouse_dataset"])


def test_gc_full_loop_approve_execute_rollback_purge(roots, monkeypatch):
    monkeypatch.setattr("app.services.data_lifecycle.gc_plan.STAGING_DIR",
                        roots["data"] / ".gc-staging")
    _enable_cog_stage_delete(staging_hours=0)
    _seed_old_cog(roots, "s/old.tif")

    with SessionLocal() as db:
        plan = create_gc_plan(db, kinds=["cog_output"], created_by="op")
        assert plan.status == "pending_approval"
        assert plan.candidate_count == 1
        entry = plan.plan_tree["objects"][0]
        assert entry["kind"] == "cog_output" and entry["tier"] == "cold"
        assert "reason" in entry and "dependencies" in entry
        assert (roots["cog"] / "s" / "old.tif").exists(), "dry-run 绝不删文件"

        transition(plan, "approve", actor="admin")
        result = execute_plan(db, plan, actor="admin")
        assert plan.status == "done"
        assert result["ok"] and result["staged_count"] == 1
        assert not (roots["cog"] / "s" / "old.tif").exists(), "执行后源文件进 staging"

        # 回滚：staging 文件回原位
        r = rollback_plan(db, plan, actor="admin")
        assert plan.status == "rolled_back"
        assert r["restored_count"] == 1
        assert (roots["cog"] / "s" / "old.tif").exists()

        # 观察期（staging_hours=0）已过 → purge 允许且 no-op（目录已空）
        p = rollback_plan  # noqa: F841 — 语义占位
        from app.services.data_lifecycle.gc_plan import purge_staging
        purge_result = purge_staging(plan)
        assert purge_result["purged"] is True


def test_gc_state_machine_full_matrix(roots, monkeypatch):
    monkeypatch.setattr("app.services.data_lifecycle.gc_plan.STAGING_DIR",
                        roots["data"] / ".gc-staging")
    _enable_cog_stage_delete()
    _seed_old_cog(roots, "s/x.tif")

    with SessionLocal() as db:
        # reject 路径
        plan = create_gc_plan(db, kinds=["cog_output"])
        transition(plan, "reject")
        assert plan.status == "rejected"
        with pytest.raises(GcPlanError):
            transition(plan, "approve")  # 非法转移

        # cancel 路径（pending）
        plan2 = create_gc_plan(db, kinds=["cog_output"])
        transition(plan2, "cancel")
        assert plan2.status == "cancelled"

        # execute 前必须 approve；执行后 cancel 不允许
        plan3 = create_gc_plan(db, kinds=["cog_output"])
        with pytest.raises(GcPlanError):
            transition(plan3, "start_execute")
        transition(plan3, "approve")
        transition(plan3, "cancel")
        assert plan3.status == "cancelled"


def test_gc_plan_idempotent_reuse_same_digest(roots, monkeypatch):
    monkeypatch.setattr("app.services.data_lifecycle.gc_plan.STAGING_DIR",
                        roots["data"] / ".gc-staging")
    _enable_cog_stage_delete()
    _seed_old_cog(roots, "s/same.tif")
    with SessionLocal() as db:
        p1 = create_gc_plan(db, kinds=["cog_output"])
        p2 = create_gc_plan(db, kinds=["cog_output"])
        assert p1.id == p2.id, "同 digest 未终态计划必须复用"


def test_plan_tree_bounded(roots, monkeypatch):
    monkeypatch.setattr("app.services.data_lifecycle.gc_plan.STAGING_DIR",
                        roots["data"] / ".gc-staging")
    _enable_cog_stage_delete()
    for i in range(6):
        _seed_old_cog(roots, f"s/f{i}.tif")
    with SessionLocal() as db:
        plan = create_gc_plan(db, kinds=["cog_output"])
        assert plan.candidate_count == 6
        assert len(plan.plan_tree["objects"]) == 6
        # 大清单截断旗标
        from app.services.data_lifecycle import gc_plan as gp
        monkeypatch.setattr(gp, "MAX_TREE_OBJECTS", 3)
        plan2 = create_gc_plan(db, kinds=["cog_output", "artifact_cache",
                                          "worker_cache"])
        if plan2.id != plan.id:
            assert plan2.plan_tree["truncated"] is True
            assert len(plan2.plan_tree["objects"]) == 3
