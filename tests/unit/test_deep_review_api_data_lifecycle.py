"""Deep-review API/DATA batch — lifecycle adapter/model/gc-plan regressions.

DATA-04：lakehouse 适配器使用真实 FK 列 dataset_row_id（此前 dataset_id 恒抛）。
DATA-07：ads_fabric server_default 必须是 SQL 函数，不是字面量字符串。
DATA-09：GC 计划默认 kinds 不得包含 observe-only 类。
"""
from __future__ import annotations

import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.models.data_lifecycle  # noqa: F401  (registers lifecycle tables)
from app.api.routes import data_lifecycle as lifecycle_routes
from app.core.database import Base, Engine, SessionLocal


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.create_all(bind=Engine)
    yield


def test_lakehouse_adapter_uses_real_fk_and_single_group_by():
    from app.models.lakehouse_datasets import (
        LakehouseDataset,
        LakehouseDatasetVersion,
    )
    from app.services.data_lifecycle.adapters import enumerate_lakehouse

    ds_row_id = "ds-row-dr-1"
    with SessionLocal() as db:
        db.query(LakehouseDatasetVersion).filter(
            LakehouseDatasetVersion.dataset_row_id == ds_row_id
        ).delete()
        db.query(LakehouseDataset).filter(LakehouseDataset.id == ds_row_id).delete()
        db.add(LakehouseDataset(
            id=ds_row_id,
            dataset_id="a" * 64,
            owner_type="session",
            owner_id="owner-dr",
            org_id="1",
            name="dr dataset",
            default_branch="main",
        ))
        for i in range(2):
            db.add(LakehouseDatasetVersion(
                id=f"dsv-dr-{i}",
                dataset_row_id=ds_row_id,
                org_id="1",
                version_id=f"{i}" * 64,
                data_object_id=f"obj{i}"[:64],
                content_sha256="c" * 64,
                byte_size=10,
                branch="main",
                action="commit",
                provenance_json={},
            ))
        db.commit()

    with SessionLocal() as db:
        report = enumerate_lakehouse(db, limit=50)

    assert report.available is True, report.diagnostics
    assert not report.diagnostics
    match = [o for o in report.objects if o.object_id == "a" * 64]
    assert len(match) == 1
    assert match[0].info["versions"] == 2


def test_ads_fabric_server_defaults_compile_to_sql_functions():
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlalchemy.schema import CreateTable

    from app.models.ads_fabric import AdsAcquisitionFact, AdsAcquisitionSnapshot

    for table in (AdsAcquisitionSnapshot.__table__, AdsAcquisitionFact.__table__):
        pg = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        lite = str(CreateTable(table).compile(dialect=sqlite.dialect()))
        assert "'now()'" not in pg, f"{table.name}: 字面量 'now()' 混进 PG DDL"
        assert "DEFAULT now()" in pg, f"{table.name}: PG 默认值必须是 now()"
        assert "DEFAULT CURRENT_TIMESTAMP" in lite, (
            f"{table.name}: SQLite 默认值必须是 CURRENT_TIMESTAMP"
        )


def test_gc_plan_default_kinds_excludes_observe_only(tmp_path, monkeypatch):
    from app.services.data_lifecycle import adapters
    from app.services.data_lifecycle.gc_plan import GcPlanError, create_gc_plan

    for name in ("_artifact_root", "_cog_root", "_data_root", "_spill_root"):
        root = tmp_path / name
        root.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(adapters, name, lambda root=root: root)
    monkeypatch.setattr(
        "app.services.data_lifecycle.gc_plan.STAGING_DIR", tmp_path / ".gc-staging",
    )

    with SessionLocal() as db:
        plan = create_gc_plan(db, kinds=None)
        status, scope = plan.status, dict(plan.scope)
    assert status == "pending_approval"
    assert "lakehouse_dataset" not in scope["kinds"]
    assert "cog_output" in scope["kinds"]

    with SessionLocal() as db:
        with pytest.raises(GcPlanError, match="observe"):
            create_gc_plan(db, kinds=["lakehouse_dataset"])


def _lifecycle_client(user: dict) -> TestClient:
    application = FastAPI()
    application.include_router(lifecycle_routes.router, prefix="/api/v1")
    application.dependency_overrides[lifecycle_routes.get_current_user] = (
        lambda: user
    )
    return TestClient(application)


def _seed_cold_cog(root):
    for owner in ("lc-user", "other-user"):
        path = root / owner / "cold.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"c" * 8)
        old = time.time() - 10 * 86400
        os.utime(path, (old, old))


def test_non_admin_assess_and_gc_plan_scoped_to_owner(tmp_path, monkeypatch):
    """DATA-03：非 admin 不得经 assess/GC plan 看到他人对象路径。"""
    from app.models.data_lifecycle import LifecyclePolicy
    from app.services.data_lifecycle import adapters

    roots = {}
    for name in ("_artifact_root", "_cog_root", "_data_root", "_spill_root"):
        root = tmp_path / name
        root.mkdir(parents=True, exist_ok=True)
        roots[name] = root
        monkeypatch.setattr(adapters, name, lambda root=root: root)
    _seed_cold_cog(roots["_cog_root"])
    monkeypatch.setattr(
        "app.services.data_lifecycle.gc_plan.STAGING_DIR", tmp_path / ".gc-staging",
    )

    with SessionLocal() as db:
        db.query(LifecyclePolicy).filter(
            LifecyclePolicy.name == "dr.delete.cog"
        ).delete()
        db.add(LifecyclePolicy(
            name="dr.delete.cog", kind="cog_output", action="stage_delete",
            enabled=True, staging_hours=0,
            tier_thresholds={"warm_after_s": 3600, "cold_after_s": 7200},
        ))
        db.commit()

    viewer = _lifecycle_client({"user_id": "lc-user", "role": "viewer"})
    assessed = viewer.post("/api/v1/data-lifecycle/assess", json={"persist": True})
    assert assessed.status_code == 200
    cog = assessed.json()["summary"]["kinds"]["cog_output"]
    ids = {c["object_id"] for c in cog["candidates"]}
    assert ids == {"lc-user/cold.tif"}, ids
    assert cog["total"] == 1

    created = viewer.post("/api/v1/data-lifecycle/gc/plans",
                          json={"kinds": ["cog_output"]})
    assert created.status_code == 200, created.text
    tree_ids = {
        o["object_id"] for o in created.json()["plan"]["plan_tree"]["objects"]
    }
    assert tree_ids == {"lc-user/cold.tif"}, tree_ids

    # admin 不受 scope 过滤（全局评估仍可见两方对象）。
    admin = _lifecycle_client({"user_id": "lc-admin", "role": "admin"})
    admin_body = admin.post(
        "/api/v1/data-lifecycle/assess", json={"persist": False},
    ).json()
    admin_ids = {
        c["object_id"]
        for c in admin_body["summary"]["kinds"]["cog_output"]["candidates"]
    }
    assert admin_ids == {"lc-user/cold.tif", "other-user/cold.tif"}
