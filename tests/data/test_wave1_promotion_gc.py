"""Wave 1 promotion×BlobStore + 引用计数 GC 测试。

覆盖 brief 要求：
- JSON 载荷以**载荷摘要**为主键晋升 + artifact_revisions 修订行；
- raster-ref 磁盘 PNG 的 binary lane（此前永远 session_expired）；
- 幂等重晋升；
- 引用计数清扫器：零引用删除 / 有修订引用保留 / pinned 保留 /
  head 指针保留 / 宽限期保护 / plan 与 execute 同谓词 parity。
"""
import asyncio
import hashlib
import time
import uuid

import pytest
from sqlalchemy import select

import app.models.db_model  # noqa: F401 — 在 fixture 之前把 ORM 模型注册进 Base.metadata
import app.models.project  # noqa: F401
from app.core.database import Base, Engine, SessionLocal

_PROJECT_DOMAIN_TABLES = (
    "artifact_revisions",
    "map_products", "artifact_lineages", "artifacts",
    "workflow_runs", "workflow_revisions", "workflows",
    "project_datasets", "carto_project_facts", "projects",
)


@pytest.fixture
def db():
    from pathlib import Path

    Path("./data").mkdir(parents=True, exist_ok=True)
    metadata_tables = [
        t for t in Base.metadata.sorted_tables if t.name in _PROJECT_DOMAIN_TABLES
    ]
    for tbl in reversed(metadata_tables):
        tbl.drop(bind=Engine, checkfirst=True)
    for tbl in metadata_tables:
        tbl.create(bind=Engine, checkfirst=True)


def _mk_run_with_artifact(project_id, *, storage_ref, content_fingerprint=None):
    """建 project/workflow/run/artifact 行（run_manifest 绑定 artifact）。"""
    from app.models.db_model import User
    from app.models.project import Artifact, Project, Workflow, WorkflowRun

    pid, wf_id, run_id, art_id = (
        project_id, f"wf_{uuid.uuid4().hex[:8]}",
        f"run_{uuid.uuid4().hex[:8]}", f"art_{uuid.uuid4().hex[:8]}",
    )
    manifest = {"artifacts": [{"id": art_id, "producing_step": "s1"}]}
    with SessionLocal() as s:
        s.merge(User(id="u_wv1", username="wv1", email="wv1@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=pid, name="p", owner_id="u_wv1"))
        s.add(Workflow(id=wf_id, project_id=pid, name="wf", version=1))
        s.add(WorkflowRun(
            id=run_id, workflow_id=wf_id, workflow_version=1, project_id=pid,
            status="completed", run_manifest=manifest,
        ))
        s.add(Artifact(
            id=art_id, project_id=pid, name="out", artifact_type="vector",
            metadata_json={"step_id": "s1"}, content_fingerprint=content_fingerprint,
            storage_ref=storage_ref,
        ))
        s.commit()
    return run_id, art_id


def test_promotion_uses_payload_digest_key_and_records_revision(db, monkeypatch, tmp_path):
    from app.models.project import Artifact, WorkflowRun
    from app.services import project_artifact_promotion as pap
    from app.services.artifact_revisions import head_revision, list_revisions
    from app.services.durable_blob_store import get_filesystem_blob_store
    from app.services.session_data import session_data_manager

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    payload = {"type": "FeatureCollection",
               "features": [{"type": "Feature",
                             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
                             "properties": {"district": "锦江区"}}]}
    ref = asyncio.run(session_data_manager.store("sess_wv1", payload, prefix="poi"))
    run_id, art_id = _mk_run_with_artifact(project_id, storage_ref=ref,
                                           content_fingerprint="cfp_wv1")

    with SessionLocal() as s:
        run = s.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
        report = asyncio.run(
            pap.promote_run_artifacts(s, run, session_id="sess_wv1", project_id=project_id)
        )
    assert report[0]["status"] == "promoted"

    blob = pap.canonical_dumps(payload)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    with SessionLocal() as s:
        art = s.execute(select(Artifact).where(Artifact.id == art_id)).scalars().first()
        meta = art.metadata_json
        # 载荷摘要是主键（audit §7.1：descriptor 指纹降级为次级索引）
        assert meta["content_payload_sha256"] == digest
        assert meta["content_location"] == f"{digest[:4]}/{digest}.json"
        assert meta["content_status"] == "promoted"
        assert meta["content_fingerprint"] == "cfp_wv1", "descriptor 指纹留作次级索引"
        # DB 列不被删除（向后兼容）
        assert art.content_fingerprint == "cfp_wv1"
        # 修订行：内容历史 + 引用计数真相
        head = head_revision(s, art_id)
        assert head.content_sha256 == digest
        assert head.content_location == meta["content_location"]
        assert head.content_type == "json"
        assert head.byte_size == len(blob.encode("utf-8"))
        assert head.workflow_run_id == run_id
        assert head.revision_no == 1
        assert (head.revision_metadata or {}).get("content_fingerprint") == "cfp_wv1"
        # 读回往返
        assert pap.read_content(meta["content_location"]) == payload
        assert get_filesystem_blob_store().get_blob(digest) == blob.encode("utf-8")

    # 幂等重晋升：already_promoted，修订行不重复
    with SessionLocal() as s:
        run = s.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
        report2 = asyncio.run(
            pap.promote_run_artifacts(s, run, session_id="sess_wv1", project_id=project_id)
        )
    assert report2[0]["status"] == "already_promoted"
    with SessionLocal() as s:
        assert len(list_revisions(s, art_id)) == 1


def test_raster_binary_artifact_promotes(db, monkeypatch, tmp_path):
    """raster-ref 磁盘 PNG（store.get() 恒 None 的 cursor）走 binary lane
    —— 此前永久 session_expired（audit §6.2.3）。"""
    from app.services import project_artifact_promotion as pap
    from app.services.artifact_revisions import head_revision
    from app.services.durable_blob_store import get_filesystem_blob_store
    from app.services.mapspec import store as mapspec_store_module
    from app.models.project import Artifact, WorkflowRun

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", tmp_path)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"0" * 128
    png_path = tmp_path / "sess_raster" / "raster" / "map0001.png"
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.write_bytes(png_bytes)

    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    run_id, art_id = _mk_run_with_artifact(project_id, storage_ref="ref:raster/map0001")

    with SessionLocal() as s:
        run = s.execute(select(WorkflowRun).where(WorkflowRun.id == run_id)).scalars().first()
        report = asyncio.run(
            pap.promote_run_artifacts(s, run, session_id="sess_raster", project_id=project_id)
        )
    assert report[0]["status"] == "promoted", "raster 产物必须能晋升（不再是 session_expired）"

    digest = hashlib.sha256(png_bytes).hexdigest()
    with SessionLocal() as s:
        art = s.execute(select(Artifact).where(Artifact.id == art_id)).scalars().first()
        meta = art.metadata_json
        assert meta["content_status"] == "promoted"
        assert meta["content_payload_sha256"] == digest
        assert meta["content_location"] == f"{digest[:4]}/{digest}.bin"
        head = head_revision(s, art_id)
        assert head.content_type == "binary"
        assert head.content_sha256 == digest
        assert head.byte_size == len(png_bytes)
    store = get_filesystem_blob_store()
    assert store.get_blob(digest, expected_sha256=digest) == png_bytes

    # 磁盘文件已消失的 raster ref：诚实 session_expired（不伪造内容指针）
    run_id2, art_id2 = _mk_run_with_artifact(f"proj_{uuid.uuid4().hex[:8]}",
                                             storage_ref="ref:raster/gone001")
    with SessionLocal() as s:
        run2 = s.execute(select(WorkflowRun).where(WorkflowRun.id == run_id2)).scalars().first()
        report2 = asyncio.run(
            pap.promote_run_artifacts(s, run2, session_id="sess_raster",
                                      project_id=run2.project_id)
        )
    assert report2[0]["status"] == "session_expired"
    with SessionLocal() as s:
        art2 = s.execute(select(Artifact).where(Artifact.id == art_id2)).scalars().first()
        assert art2.metadata_json["content_status"] == "session_expired"
        assert "content_location" not in art2.metadata_json


# ── 引用计数清扫器（plan/execute 同谓词）─────────────────────────────────


def _mk_gc_fixture(tmp_path, monkeypatch):
    """6 个 blob：孤儿(超龄) / 修订引用 / pinned / head 指针 / 宽限期内 /
    仅 sha 引用（修订行的 location 与 blob 物理位置不一致的防御口径）。"""
    from app.models.db_model import User
    from app.models.project import Artifact, ArtifactRevision, Project
    from app.services import project_artifact_promotion as pap
    from app.services.durable_blob_store import get_filesystem_blob_store

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    store = get_filesystem_blob_store()

    keys = {}
    for name in ("orphan", "referenced", "pinned", "headptr", "fresh", "shafallback"):
        data = f"blob-{name}".encode()
        digest = hashlib.sha256(data).hexdigest()
        store.put_blob(digest, data, "json")
        keys[name] = digest

    # 孤儿 backdate 到宽限期外；fresh 保持现在（grace 保护）
    orphan_path = tmp_path / keys["orphan"][:4] / f"{keys['orphan']}.json"
    old = time.time() - 48 * 3600
    import os as _os

    _os.utime(orphan_path, (old, old))

    pid = f"proj_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        s.merge(User(id="u_gc", username="gc", email="gc@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=pid, name="p", owner_id="u_gc"))
        art_ids = {}
        for name in ("referenced", "pinned", "headptr", "shafallback"):
            aid = f"art_{uuid.uuid4().hex[:8]}"
            art_ids[name] = aid
            s.add(Artifact(id=aid, project_id=pid, name=name,
                           artifact_type="vector", metadata_json={}))
        s.commit()
        from datetime import datetime, timezone

        def _rev(aid, sha, location=None, pinned=False):
            s.add(ArtifactRevision(
                id=str(uuid.uuid4()), artifact_id=aid, revision_no=1,
                content_sha256=sha,
                content_location=location or f"{sha[:4]}/{sha}.json",
                content_type="json", byte_size=1,
                pinned_at=datetime.now(timezone.utc) if pinned else None,
            ))

        _rev(art_ids["referenced"], keys["referenced"])
        _rev(art_ids["pinned"], keys["pinned"], pinned=True)
        # sha 兜底保护：修订行 sha 命中但 location 是旧布局位置
        _rev(art_ids["shafallback"], keys["shafallback"], location="legacy/old.json")
        # head 指针保护：无修订行，仅 Artifact.metadata_json.content_location
        art = s.execute(select(Artifact).where(
            Artifact.id == art_ids["headptr"])).scalars().first()
        sha = keys["headptr"]
        art.metadata_json = {"content_location": f"{sha[:4]}/{sha}.json",
                             "content_status": "promoted"}
        s.commit()
    return keys, store


def test_plan_and_execute_promotion_gc_share_predicate(db, monkeypatch, tmp_path):
    from app.services.artifact_lifecycle import (
        execute_promotion_store_gc,
        plan_promotion_store_gc,
    )

    keys, store = _mk_gc_fixture(tmp_path, monkeypatch)

    plan = asyncio.run(plan_promotion_store_gc(grace_hours=1.0))
    deletable_keys = [d["key"] for d in plan["deletable"]]
    # 只有零引用 + 无 head 指针 + 未 pin + 超宽限期的孤儿可删
    assert deletable_keys == [keys["orphan"]]
    protected = plan["protected_counts"]
    assert protected.get("pinned") == 1
    assert protected.get("referenced by artifact_revisions") == 1
    assert protected.get("referenced by artifact_revisions (sha)") == 1
    assert protected.get("head pointer (Artifact.metadata_json.content_location)") == 1
    # grace 保护的是 fresh（宽限期内无主 blob）；孤儿已 backdate 超龄可删
    assert protected.get("grace period") == 1
    # planner 是 dry-run：不删任何字节
    for key in keys.values():
        assert store.exists(key) is True

    result = asyncio.run(execute_promotion_store_gc(plan))
    # parity：同 DB 状态下 dry-run 计划 == 实际删除
    assert result["deleted"] == deletable_keys
    assert result["failed"] == []
    assert store.exists(keys["orphan"]) is False
    # 引用保护与层级无关：任何 revision/Artifact 行引用的 blob 都在
    for name in ("referenced", "pinned", "headptr", "fresh", "shafallback"):
        assert store.exists(keys[name]) is True, f"{name} blob must survive GC"


def test_execute_recheck_protects_newly_referenced_blob(db, monkeypatch, tmp_path):
    """execute 用新鲜 DB 状态复检：plan 后新落地的引用即刻受保护。"""
    from app.models.project import ArtifactRevision
    from app.services.artifact_lifecycle import (
        execute_promotion_store_gc,
        plan_promotion_store_gc,
    )

    keys, store = _mk_gc_fixture(tmp_path, monkeypatch)
    plan = asyncio.run(plan_promotion_store_gc(grace_hours=1.0))
    assert [d["key"] for d in plan["deletable"]] == [keys["orphan"]]

    # plan 与 execute 之间：孤儿被一条新修订引用（例如 REST 重晋升刚落地）
    with SessionLocal() as s:
        from app.models.project import Artifact

        art = s.execute(select(Artifact).limit(1)).scalars().first()
        s.add(ArtifactRevision(
            id=str(uuid.uuid4()), artifact_id=art.id, revision_no=2,
            content_sha256=keys["orphan"],
            content_location=f"{keys['orphan'][:4]}/{keys['orphan']}.json",
            content_type="json", byte_size=1,
        ))
        s.commit()

    result = asyncio.run(execute_promotion_store_gc(plan))
    assert result["deleted"] == []
    assert result["skipped_protected"] and "artifact_revisions" in (
        result["skipped_protected"][0]["reason"]
    )
    assert store.exists(keys["orphan"]) is True


# ── 快照 manifest 指针保护（round-1 review CRITICAL）──────────────────────


def test_workspace_snapshot_materialized_blob_survives_gc(db, monkeypatch, tmp_path):
    """workspace save 物化的 blob 被**快照 manifest 指针**保护：超宽限期、
    零 DB 引用也绝不删；manifest 删除后（同参数）变为可删。plan 与
    execute 共享同一 manifest 扫描（quota.workspace_snapshot_protected_pointers）。"""
    import os
    import time as _time

    from app.core.config import settings
    from app.services import project_artifact_promotion as pap
    from app.services.artifact_lifecycle import (
        execute_promotion_store_gc,
        plan_promotion_store_gc,
    )
    from app.services.artifact_registry import register_artifact
    from app.services.data_lifecycle.quota import (
        workspace_snapshot_protected_pointers,
    )
    from app.services.durable_blob_store import get_filesystem_blob_store
    from app.services.session_data import session_data_manager
    from app.services.workspace.snapshot import (
        get_workspace_snapshot_service,
        reset_workspace_snapshot_service,
    )

    # DATA_DIR → tmp：workspaces 快照根 + 内容库根同域（调用时解析）
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(data_root))
    monkeypatch.setattr(pap, "content_store_root", lambda: data_root / "project_artifacts")
    reset_workspace_snapshot_service()
    try:
        sid = "sess-snap-gc"
        project_id = f"proj_snap_{uuid.uuid4().hex[:8]}"
        payload = {"type": "FeatureCollection",
                   "features": [{"type": "Feature",
                                 "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                                 "properties": {"k": "v"}}]}
        ref = asyncio.run(session_data_manager.store(sid, payload, prefix="geojson"))
        asyncio.run(register_artifact(sid, artifact_id=ref, producer_tool="buffer"))
        snap = asyncio.run(get_workspace_snapshot_service().save_snapshot(
            sid, project_id=project_id, materialize="claimed",
        ))
        assert snap is not None and ref in snap.durable_pointers
        ptr = snap.durable_pointers[ref]
        locations, shas = workspace_snapshot_protected_pointers()
        assert ptr.content_location in locations
        assert ptr.content_payload_sha256 in shas

        store = get_filesystem_blob_store()
        blob_path = store.root / ptr.content_location
        assert blob_path.is_file()
        # blob 超出宽限期（无 DB 行 / 无 head 指针 —— manifest 是唯一账面）
        old = _time.time() - 48 * 3600
        os.utime(blob_path, (old, old))

        plan = asyncio.run(plan_promotion_store_gc(grace_hours=1.0))
        assert all(d["key"] != ptr.content_payload_sha256 for d in plan["deletable"])
        assert plan["protected_counts"].get(
            "workspace snapshot manifest pointer") == 1
        result = asyncio.run(execute_promotion_store_gc(plan))
        assert result["deleted"] == []
        assert store.exists(ptr.content_payload_sha256) is True, (
            "快照物化 blob 绝不因宽限期过期被 GC")
        # manifest 删除 → 保护输入消失 → blob 超龄可删（plan+execute parity）
        deleted = asyncio.run(get_workspace_snapshot_service().delete_snapshot(
            sid, snap.snapshot_id, project_id=project_id,
        ))
        assert deleted is not None and deleted["deleted"] is True
        plan2 = asyncio.run(plan_promotion_store_gc(grace_hours=1.0))
        assert [d["key"] for d in plan2["deletable"]] == [ptr.content_payload_sha256]
        result2 = asyncio.run(execute_promotion_store_gc(plan2))
        assert result2["deleted"] == [ptr.content_payload_sha256]
        assert store.exists(ptr.content_payload_sha256) is False
    finally:
        reset_workspace_snapshot_service()
