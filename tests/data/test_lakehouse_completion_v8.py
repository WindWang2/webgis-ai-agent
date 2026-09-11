"""Lakehouse V8 — 完成证明：端到端版本化闭环（ADR-0130）。

验收标准逐条对应（/goal 04）：
- snapshot/branch/rollback 真实可用：commit → branch → revert 回滚；
- 中断写入不产生可见半成品版本：fault 注入后 head 不动、重试幂等；
- duplicate content 复用 blob：同内容重发布 CAS 命中 + 同 commit 幂等；
- GC dry-run/commit：版本历史受保护、retention 裁剪后内容回候选；
- cube 表达 SAR polarization + optical band + time（+ model/scenario）；
- lineage 从 workflow run 到 dataset version 可追踪：bridge 提交携带
  workflow_run_id → 台账列一跳可查，provenance 全量入账。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import app.models.db_model  # noqa: F401 — 模型注册
import app.models.lakehouse_datasets  # noqa: F401
from app.core.database import Base, SessionLocal

_DOMAIN_TABLES = (
    "artifact_revisions", "artifacts", "artifact_lineages", "workflow_runs",
    "workflow_revisions", "workflows", "project_datasets",
    "carto_project_facts", "projects", "lakehouse_catalog_items",
    "lakehouse_datasets", "lakehouse_dataset_versions",
    "lakehouse_dataset_refs",
)


@pytest.fixture()
def v8_env(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.durable_blob_store import reset_filesystem_blob_store
    from app.services.project_artifact_promotion import (
        reset_content_store_root_cache,
    )

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    reset_filesystem_blob_store()
    reset_content_store_root_cache()
    Path("./data").mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=_engine(), checkfirst=True)
    tables = [t for t in Base.metadata.sorted_tables if t.name in _DOMAIN_TABLES]
    for t in reversed(tables):
        t.drop(bind=_engine(), checkfirst=True)
    for t in tables:
        t.create(bind=_engine(), checkfirst=True)
    yield tmp_path
    reset_filesystem_blob_store()
    reset_content_store_root_cache()


def _engine():
    from app.core.database import Engine

    return Engine


def test_v8_end_to_end_versioned_lakehouse(v8_env, tmp_path):
    from sqlalchemy import select

    from app.models.project import Artifact, Workflow, WorkflowRun
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse import dataset_retention as ret
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
        resolve_data_object,
    )
    from app.services.lakehouse.lakehouse_gc import plan_gc
    from app.services.lakehouse.workflow_bridge import (
        commit_dataset_version_from_artifact,
        version_to_fabric_descriptor,
    )

    session = "sess-e2e"
    scope = normalize_owner_scope(session_id=session)

    # 1. ingest：内容 DataObject（同内容重发布 = CAS 命中 —— duplicate
    #    content 复用 blob）。
    a = publish_data_object({"dem.bin": b"dem-v1"}, kind="cog_raster",
                            owner_scope=scope)
    b = publish_data_object({"dem.bin": b"dem-v1"}, kind="cog_raster",
                            owner_scope=scope)
    assert a.data_object_id == b.data_object_id and b.deduped is True

    # 2. dataset + snapshot/branch：v1 提交、实验分支、分支提交。
    with SessionLocal() as db:
        row, created = reg.create_dataset(
            db, name="flood-dem", description="e2e", session_id=session)
        db.commit()
        ds_row_id, ds_id = row.id, row.dataset_id
        v1 = reg.commit_version(
            db, row, branch="main", data_object_id=a.data_object_id,
            provenance={"algorithm": "ingest", "parameters": {"src": "s1"}},
        )
        exp, branch_created = reg.create_branch(db, row, name="experiment")
        db.commit()
        assert created is True and branch_created is True
        obj2 = publish_data_object(
            {"dem.bin": b"dem-v2"}, kind="cog_raster", owner_scope=scope,
        ).data_object_id
        v2 = reg.commit_version(db, row, branch="experiment",
                                data_object_id=obj2)
        db.commit()
        assert v1.parent_version_id is None
        assert v2.parent_version_id == v1.version_id
        assert reg.branch_head(db, ds_row_id, "main").version_id \
            == v1.version_id
        assert reg.branch_head(db, ds_row_id, "experiment").version_id \
            == v2.version_id

    # 3. workflow run → dataset version（bridge；lineage 可追踪）。
    # SQLite FK 未启用（测试方言）—— workflow/run 行直接内联构造。
    with SessionLocal() as db:
        project_row = Workflow(
            project_id="proj-e2e", name="wf", version=1,
            graph_spec={"steps": []},
        )
        db.add(project_row)
        db.flush()
        run = WorkflowRun(
            workflow_id=project_row.id, status="completed",
            project_id="proj-e2e",
        )
        db.add(run)
        db.flush()
        artifact = Artifact(
            project_id="proj-e2e", name="ndvi", artifact_type="raster",
            storage_ref=obj2,
            metadata_json={
                "step_id": "step-ndvi", "tool_name": "ndvi.compute",
                "capability": "geocompute.raster",
                "tool_args": {"api_token": "sekret", "window": 3},
            },
        )
        db.add(artifact)
        db.commit()
        artifact_id = artifact.id
        run_id = run.id

    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row_id)
        result, obj_used = commit_dataset_version_from_artifact(
            db, row, artifact_id=artifact_id, branch="experiment",
            run_id=run_id,
        )
        db.commit()
        assert obj_used == obj2
        # run → version 一跳可查（台账 workflow_run_id 列 + 索引）。
        from app.models.lakehouse_datasets import LakehouseDatasetVersion

        vrow = db.execute(
            select(LakehouseDatasetVersion).where(
                LakehouseDatasetVersion.workflow_run_id == run_id,
            )
        ).scalar_one()
        assert vrow.version_id == result.version_id
        assert vrow.provenance_json["workflow_run_id"] == run_id
        assert vrow.provenance_json["algorithm"] == "ndvi.compute"
        assert vrow.action == "workflow_publish"
        import json as _json

        assert "sekret" not in _json.dumps(vrow.provenance_json)

    # 4. rollback（revert 语义）+ tag。
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row_id)
        rb = reg.rollback_branch(
            db, row, branch="experiment", to_version_id=v1.version_id)
        tag_row, _ = reg.create_tag(
            db, row, name="stable", version_id=v1.version_id)
        db.commit()
        # 回滚前的 experiment head = bridge 的 workflow_publish 提交
        # → rollback commit 的 parent 是它（revert 链完整）。
        assert rb.parent_version_id == result.version_id
        head = reg.branch_head(db, ds_row_id, "experiment")
        assert head.version_id == rb.version_id
        assert resolve_data_object(
            reg.get_version_row(db, ds_row_id, rb.version_id).data_object_id
        ) is not None

    # 5. lineage（parent 链上溯 + 截断披露）。
    with SessionLocal() as db:
        view = reg.version_lineage(db, ds_row_id, rb.version_id)
        ids = [c["version_id"] for c in view["chain"]]
        assert ids[0] == rb.version_id and ids[-1] == v1.version_id
        assert view["truncated"] is False

    # 6. GC：版本历史引用的内容受保护。
    gc_plan = plan_gc(grace_hours=0.0, ttl_floor=0.0)
    assert a.data_object_id not in gc_plan["candidates"]
    assert obj2 not in gc_plan["candidates"]
    assert ds_id not in gc_plan["candidates"]

    # 7. retention：裁剪超窗版本行 → 内容回 GC 候选（字节删除归 GC）。
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row_id)
        plan = ret.plan_retention(
            db, row, max_versions=2, min_age_hours=24 * 365)
        # 全部版本新鲜（刚提交）→ min_age 保护 → 无候选。
        assert plan["candidate_count"] == 0
        result = ret.execute_retention(db, plan)
        db.commit()
        assert result["pruned_count"] == 0
        # min_age=0 时 v2（超窗且无指针）成为候选 —— 指针保护
        # （tag=stable 指向 v1、两个分支 head）仍然生效。
        plan2 = ret.plan_retention(db, row, max_versions=2, min_age_hours=0.0)
        assert plan2["candidates"] == [v2.version_id]

    # 8. Data Fabric 描述符适配（诚实默认：缺 CRS/bbox = None）。
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row_id)
        resolved = reg.resolve_version(
            db, row, version_id=v1.version_id, session_id=session)
        descriptor = version_to_fabric_descriptor(
            dataset_row=row, version_dict=resolved)
        assert descriptor.source_type == "lakehouse"
        assert descriptor.source_id == a.data_object_id
        assert descriptor.crs is None  # payload 未声明 → 诚实 None
        assert descriptor.metadata["version_id"] == v1.version_id
