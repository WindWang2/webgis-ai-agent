"""WP5 接线端到端 —— WP3 系列 → freeze → durable job（真实 Celery eager）
→ 逐成员真实渲染 → manifest 落盘 → REST 查询。全链真实，无 mock 引擎。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("weasyprint")

from tests.unit.series_production.test_series_production_executor import (
    _seed_member_session,
)


@pytest.fixture()
def celery_eager(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture()
def job_db(tmp_path, monkeypatch):
    """jobs 链 db_session 指向临时 SQLite（atlas fixtures 同款接线点）。"""
    import contextlib

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.db_model import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'series_jobs.db'}")
    Base.metadata.create_all(engine)
    sess = sessionmaker(bind=engine)

    @contextlib.contextmanager
    def fake_db_session():
        s = sess()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr("app.tools._utils.db_session", fake_db_session)
    monkeypatch.setattr("app.services.jobs.submit.db_session", fake_db_session)
    monkeypatch.setattr(
        "app.services.jobs.worker._default_session_factory", fake_db_session)
    yield sess
    engine.dispose()


@pytest.mark.asyncio
async def test_series_production_e2e_submit_job_manifest(
    store_root, exports_dir, celery_eager, job_db,
):
    from app.core.database import Base, Engine, SessionLocal

    from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion
    from app.models.map_series import CartographyMapSeries, CartographyMapSeriesVersion
    from app.services.jobs.store import DurableJobStore
    from app.services.map_series.freeze import build_freeze_plan
    from app.services.map_series.store import create_series
    from app.services.series_production.service import submit_series_production
    from app.services.series_production.submit import (
        execute_series_production_job,
        freeze_plan_from_series_payload,
    )

    Base.metadata.create_all(bind=Engine)
    try:
        # 1) 设计规范 + 两个真实成员会话（print_layout 纸面链）
        with SessionLocal() as db:
            spec = __import__(
                "app.services.design_specs.versioning", fromlist=["create_spec"]
            ).create_spec(
                db,
                {
                    "spec_version": "design_spec.v1",
                    "name": "生产端到端规范",
                    "color": {"theme_id": "cartographic.light_interactive"},
                    "classification": {"default_method": "quantiles",
                                       "shared_required": True},
                },
                creator_id="sp-e2e-owner",
            )
            payload = {
                "series_version": "map_series.v1",
                "name": "生产端到端系列",
                "design_spec_id": spec.id,
                "members": [],
            }
            # 先建两个会话再补成员（成员引用真实 session_id）
            sid_a, fp_a, _ = await _seed_member_session(with_print_layout=True, owner="sp-e2e-owner")
            sid_b, fp_b, _ = await _seed_member_session(with_print_layout=True, owner="sp-e2e-owner")
            payload["members"] = [
                {"member_id": "m1", "title": "图A", "session_id": sid_a},
                {"member_id": "m2", "title": "图B", "session_id": sid_b},
            ]
            series = create_series(db, payload, creator_id="sp-e2e-owner")
            series_id = series.id

        # 2) 冻结计划（WP3 权威）→ 桥接 → 提交 durable job
        with SessionLocal() as db:
            freeze_payload = await build_freeze_plan(db, series_id,
                                                     user_id="sp-e2e-owner")
        plan = freeze_plan_from_series_payload(freeze_payload)
        assert len(plan.members) == 2
        assert plan.members[0].mapspec_fingerprint == fp_a

        submitted = submit_series_production(plan, freeze_payload=freeze_payload)
        job_id = submitted.get("job_id")
        assert job_id, submitted

        # 3) eager 提交时任务已同步首跑（真实生产发生了一次）；
        #    显式再执行同计划 → 全员命中缓存（产物在盘且 checksum 复核）
        result = await execute_series_production_job(job_id)
        assert result["status"] == "completed", result
        produced = (result["summary"]["complete"]
                    + result["summary"]["cached_reused"])
        assert produced == 2, result["summary"]
        assert result["summary"]["failed"] == 0

        # 4) manifest 真实落盘 + 全成员 complete
        manifest_path = Path(exports_dir) / result["manifest_file"]
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["summary"]["all_complete"] is True
        assert manifest["series"]["id"] == series_id
        assert {m["member_id"] for m in manifest["members"]} == {"m1", "m2"}

        # 5) job 行状态可查询（jobs 链专用 db —— REST 面同源）
        with job_db() as jdb:
            record = DurableJobStore.get_sync(jdb, job_id)
            assert record is not None
            assert record.status in ("completed", "running", "queued")

        # 6) 幂等重提：同冻结计划 → 复用同一 job（不产生第二份记录）
        resubmitted = submit_series_production(plan, freeze_payload=freeze_payload)
        assert resubmitted.get("job_id") == job_id
        assert resubmitted.get("idempotent_reuse") is True
    finally:
        with SessionLocal() as db:
            db.query(CartographyMapSeriesVersion).delete()
            db.query(CartographyMapSeries).delete()
            db.query(CartographyDesignSpecVersion).delete()
            db.query(CartographyDesignSpec).delete()
            db.commit()


@pytest.mark.asyncio
async def test_tool_submit_registered_and_dispatches(
    store_root, exports_dir, job_db,
):
    from app.core.database import Base, Engine, SessionLocal
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion
    from app.models.map_series import CartographyMapSeries, CartographyMapSeriesVersion
    from app.services.jobs.context import JobOrigin, use_origin
    from app.services.map_series.store import create_series

    Base.metadata.create_all(bind=Engine)
    registry = ToolRegistry()
    init_tools(registry)
    schemas = {s.get("function", {}).get("name") for s in registry.get_schemas()}
    assert "webgis_series_production_submit" in schemas

    try:
        with SessionLocal() as db:
            sid, fp, _ = await _seed_member_session(with_print_layout=True, owner="sp-tool-user")
            spec = __import__(
                "app.services.design_specs.versioning", fromlist=["create_spec"]
            ).create_spec(
                db,
                {"spec_version": "design_spec.v1", "name": "工具端到端规范"},
                creator_id="sp-tool-user",
            )
            series = create_series(
                db,
                {
                    "series_version": "map_series.v1",
                    "name": "工具端到端系列",
                    "design_spec_id": spec.id,
                    "members": [{"member_id": "m1", "title": "图A",
                                 "session_id": sid}],
                },
                creator_id="sp-tool-user",
            )
            series_id = series.id

        # 工具 dispatch（未解析成员场景走 MEMBERS_UNRESOLVABLE 前置闸）
        with use_origin(JobOrigin(session_id="sess-sp-tools",
                                  owner_id="sp-tool-user")):
            result = await registry.dispatch(
                "webgis_series_production_submit",
                {"series_id": series_id},
            )
        assert result["success"], result
        assert result["member_count"] == 1
        assert result["job_id"]
    finally:
        with SessionLocal() as db:
            db.query(CartographyMapSeriesVersion).delete()
            db.query(CartographyMapSeries).delete()
            db.query(CartographyDesignSpecVersion).delete()
            db.query(CartographyDesignSpec).delete()
            db.commit()
