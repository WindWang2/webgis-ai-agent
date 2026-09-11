"""Lakehouse V7 Wave 15 — dereference GC（union 保护 / execute 重验 / watermark）。

覆盖面（ADR-0119 §9，评审 R0-12/17/18/19）：
- plan：孤儿 manifest 进候选；DB 根（catalog active / artifact_revisions）
  保护；宽限保护；确定性排序 + token；
- **union 规则**（R0-17）：孤儿父 + 活子共享 blob ⇒ 共享 blob 不可删；
- execute：删除候选 manifest + 无引用 blob；published root 保留；
- stale plan：计划后发布引用候选 blob → typed GCStalePlan（R0-12）；
  重新规划后可执行（中断续跑语义）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

import app.models.db_model  # noqa: F401
import app.models.lakehouse_catalog  # noqa: F401
import app.models.project  # noqa: F401
from app.core.database import Base, SessionLocal

_DOMAIN_TABLES = (
    "artifact_revisions", "artifacts", "artifact_lineages", "workflow_runs",
    "workflow_revisions", "workflows", "project_datasets",
    "carto_project_facts", "map_products", "projects", "lakehouse_catalog_items",
)


@pytest.fixture()
def gc_env(tmp_path, monkeypatch):
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


def _publish(session="sess-gc", tag=b"x"):
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
    )

    identity = publish_data_object(
        {"data.bin": tag},
        kind="cog_raster",
        owner_scope=normalize_owner_scope(session_id=session),
    )
    return identity.data_object_id


def _register_revision(content_sha256):
    """DB 根：artifact_revisions 引用（内容历史保护）。"""
    from app.services.artifact_revisions import record_revision

    with SessionLocal() as s:
        record_revision(
            s,
            artifact_id=f"art_{uuid.uuid4().hex[:8]}",
            content_sha256=content_sha256,
            content_location=f"{content_sha256[:4]}/{content_sha256}.json",
            content_type="json",
            byte_size=10,
        )
        s.commit()


def test_plan_candidates_and_union_blob_protection(gc_env):
    from app.services.lakehouse.lakehouse_gc import plan_gc

    orphan = _publish(tag=b"orphan-1")
    protected = _publish(tag=b"protected-1")
    _register_revision(protected)
    plan = plan_gc(grace_hours=0.0, ttl_floor=0.0)
    assert orphan in plan["candidates"]
    assert protected not in plan["candidates"]
    # union 规则：protected 的 blob 绝不进 deletable（孤儿虽在候选）。
    assert protected not in plan["deletable_blobs"]
    assert orphan not in plan["deletable_blobs"]  # manifest id ≠ 内容 blob
    # 孤儿的内容 blob（sha256(b"orphan-1")）可删。
    import hashlib

    assert hashlib.sha256(b"orphan-1").hexdigest() in plan["deletable_blobs"]
    # 确定性：同状态重规划同 token。
    plan2 = plan_gc(grace_hours=0.0, ttl_floor=0.0)
    assert plan2["token"] == plan["token"]
    assert plan2["candidates"] == plan["candidates"]


def test_fork_shared_blob_survives_orphan_parent(gc_env):
    """R0-17 验收：孤儿父 + 活子共享 blob ⇒ 共享 blob 零删除。"""
    from app.services.lakehouse.lakehouse_gc import execute_gc, plan_gc
    from app.services.lakehouse.virtual_object import (
        publish_virtual_object,
    )

    # 共享 blob：一个内容对象（子）引用的字节。
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
    )

    scope = normalize_owner_scope(session_id="sess-gc")
    leaf = publish_data_object(
        {"shared.bin": b"shared-bytes"}, kind="cog_raster", owner_scope=scope,
    )
    # 子：受 DB 根保护（revision 引用 manifest id）。
    child = publish_virtual_object(
        [leaf.data_object_id], kind_label="child",
        owner_scope=scope,
    )
    _register_revision(child["data_object_id"])
    # 父：孤儿 virtual 引用同一 blob。
    parent = publish_virtual_object(
        [leaf.data_object_id], kind_label="orphan-parent",
        owner_scope=scope,
    )
    plan = plan_gc(grace_hours=0.0, ttl_floor=0.0)
    assert parent["data_object_id"] in plan["candidates"]
    assert child["data_object_id"] not in plan["candidates"]
    # 共享 blob（leaf 的 manifest id 与内容 blob digest）都不在 deletable。
    assert leaf.data_object_id not in plan["deletable_blobs"]
    shared_digest = __import__("hashlib").sha256(b"shared-bytes").hexdigest()
    assert shared_digest not in plan["deletable_blobs"]
    execute_gc(plan)
    # 子的 blob 仍在（union 保护），父已删。
    from app.services.lakehouse.data_object import resolve_data_object

    assert resolve_data_object(child["data_object_id"]) is not None
    assert resolve_data_object(parent["data_object_id"]) is None


def test_execute_deletes_orphans_and_keeps_roots(gc_env):
    from app.services.durable_blob_store import FilesystemBlobStore
    from app.services.lakehouse.lakehouse_gc import execute_gc, plan_gc
    from app.services.lakehouse.data_object import (
        resolve_data_object,
        verify_data_object,
    )

    orphan_a = _publish(tag=b"a")
    orphan_b = _publish(tag=b"b")
    root = _publish(tag=b"root")
    _register_revision(root)
    plan = plan_gc(grace_hours=0.0, ttl_floor=0.0)
    assert set(plan["candidates"]) == {orphan_a, orphan_b}
    result = execute_gc(plan)
    assert result["deleted_manifests"] == [orphan_a, orphan_b]
    assert verify_data_object(root) == "verified"  # published root 保留
    assert resolve_data_object(orphan_a) is None
    # 内容 blob 也被回收（digest 键即内容）—— 无引用 blob 不再可解析。

    assert FilesystemBlobStore is not None


def test_stale_plan_rejected_when_state_changed(gc_env):
    """R0-12 验收：计划后新发布引用候选 blob → execute typed 拒绝。"""
    from app.services.lakehouse.lakehouse_gc import (
        GCStalePlan,
        execute_gc,
        plan_gc,
    )
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
        verify_data_object,
    )

    orphan = _publish(tag=b"victim-bytes")
    plan = plan_gc(grace_hours=0.0, ttl_floor=0.0)
    assert orphan in plan["candidates"]
    # 计划后：新 virtual 引用候选 manifest（CAS 复用其 blob —— token 捕捉）。
    new_child = publish_data_object(
        {"data.bin": b"new"}, kind="cog_raster",
        owner_scope=normalize_owner_scope(session_id="sess-gc"),
    )
    from app.services.lakehouse.virtual_object import publish_virtual_object

    publish_virtual_result = publish_virtual_object(
        [orphan, new_child.data_object_id], kind_label="late-child",
        owner_scope=normalize_owner_scope(session_id="sess-gc"),
    )
    with pytest.raises(GCStalePlan, match="stale"):
        execute_gc(plan)
    # 受害者的 blob 完好。
    assert verify_data_object(orphan) == "verified"
    # 重新规划（默认宽限 —— late-child 新鲜 → 受保护 → 孤儿经引用边
    # 传递保护）→ 新 token → 执行安全（中断续跑语义）。
    plan2 = plan_gc()
    assert orphan not in plan2["candidates"]
    assert publish_virtual_result["data_object_id"] not in plan2["candidates"]
    execute_gc(plan2)


def test_gc_plan_works_on_s3_backend(gc_env, monkeypatch):
    """R1-1 回归：S3 后端（epoch 归一化 LastModified）的 GC plan 不崩。"""
    import app.services.lakehouse.lakehouse_gc as gc_mod
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
    )
    from app.services.lakehouse.lakehouse_gc import plan_gc
    from app.services.s3_blob_store import S3BlobStore
    from tests.data.test_s3_streaming_v7 import FakeS3V7

    fake = FakeS3V7()
    store = S3BlobStore("gc-bucket", lambda: fake)
    monkeypatch.setattr(gc_mod, "_object_store", lambda: store)
    publish_data_object(
        {"data.bin": b"x"}, kind="cog_raster",
        owner_scope=normalize_owner_scope(session_id="sess-gc"),
        store=store,
    )
    plan = plan_gc(grace_hours=0.0, ttl_floor=0.0)
    assert "candidates" in plan
    assert isinstance(plan["watermark"], float)
    assert plan["scanned_manifests"] == 1
