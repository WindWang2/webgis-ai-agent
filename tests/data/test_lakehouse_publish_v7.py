"""Lakehouse V7 Wave 13 — project publishing（零字节发布 / owner 链 / 幂等）。

覆盖面（ADR-0119 §8，评审 R0-7/8/9）：
- 发布：find-or-create Artifact（storage_ref=数据对象）→ record_revision
  → catalog 投影行（R0-8 FK 前提）；重发布 = deduped（revision/catalog
  双唯一键）；
- owner 链：session 归请求者（保真 fake，deny=404 同族）+ project 归
  请求者（他人/不存在 → PROJECT_MISSING 不泄漏）；外来对象 → forbidden；
- 项目域解析（R0-9）：catalog active 行授权读取；撤销后 → None；
- 撤销 tombstone（检索默认不可见）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

import app.models.db_model  # noqa: F401
import app.models.lakehouse_catalog  # noqa: F401
import app.models.project  # noqa: F401
from app.core.database import Base, SessionLocal

_PROJECT_DOMAIN_TABLES = (
    "artifact_revisions",
    "artifacts", "artifact_lineages", "workflow_runs", "workflow_revisions",
    "workflows", "project_datasets", "carto_project_facts", "projects",
    "lakehouse_catalog_items",
)

ACTOR = "user-pub"
OWNED_SESSION = "sess-pub"


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
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
    Base.metadata.create_all(bind=Engine_safe(), checkfirst=True)
    tables = [
        t for t in Base.metadata.sorted_tables
        if t.name in _PROJECT_DOMAIN_TABLES
    ]
    for t in reversed(tables):
        t.drop(bind=Engine_safe(), checkfirst=True)
    for t in tables:
        t.create(bind=Engine_safe(), checkfirst=True)
    with SessionLocal() as s:
        from app.models.db_model import User

        s.merge(User(id=ACTOR, username="pub", email="pub@example.com",
                     password_hash="x", role="viewer", is_active=True))
        project_id = f"proj_{uuid.uuid4().hex[:8]}"
        s.add(__import__("app.models.project", fromlist=["Project"]).Project(
            id=project_id, name="p", owner_id=ACTOR,
        ))
        s.commit()
    yield {"project_id": project_id}
    reset_filesystem_blob_store()
    reset_content_store_root_cache()


def Engine_safe():
    from app.core.database import Engine

    return Engine


@pytest.fixture()
def owned_object(db_env, monkeypatch):
    """session 出身的真实 DataObject + owner 守卫保真 fake。"""
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
    )

    identity = publish_data_object(
        {"data.bin": b"publish-me"},
        kind="cog_raster",
        owner_scope=normalize_owner_scope(session_id=OWNED_SESSION),
    )
    # 保真 owner 守卫（同 V6 API 测试模式）：session 归属判定。
    import app.core.auth as auth_mod

    real_verify = auth_mod.verify_session_owner

    async def fake_verify(db, session_id, **kw):
        if session_id != OWNED_SESSION:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Session not found")
        from types import SimpleNamespace

        return SimpleNamespace(session_id=session_id)

    monkeypatch.setattr(auth_mod, "verify_session_owner", fake_verify)
    yield {"object_id": identity.data_object_id, "db_env": db_env}
    monkeypatch.setattr(auth_mod, "verify_session_owner", real_verify)


@pytest.mark.asyncio
async def test_publish_happy_path_idempotent(owned_object):
    from app.core.database import AsyncSessionLocal, SessionLocal
    from app.models.project import Artifact, ArtifactRevision
    from app.services.lakehouse.project_publish import publish_to_project

    project_id = owned_object["db_env"]["project_id"]
    async with AsyncSessionLocal() as db:
        result = await publish_to_project(
            db,
            session_id=OWNED_SESSION,
            project_id=project_id,
            object_ids=[owned_object["object_id"]],
            actor_id=ACTOR,
        )
    assert len(result["published"]) == 1
    entry = result["published"][0]
    assert entry["deduped"] is False
    assert not result["unknown"] and not result["forbidden"]

    with SessionLocal() as s:
        artifact = s.execute(
            select(Artifact).where(Artifact.id == entry["artifact_id"])
        ).scalar_one()
        assert artifact.project_id == project_id
        assert artifact.storage_ref == owned_object["object_id"]
        revisions = s.execute(
            select(ArtifactRevision).where(
                ArtifactRevision.artifact_id == artifact.id
            )
        ).scalars().all()
        assert len(revisions) == 1
        assert revisions[0].revision_metadata["lakehouse"] is True

    # 重发布 = revision/catalog 命中（幂等）。
    async with AsyncSessionLocal() as db:
        again = await publish_to_project(
            db,
            session_id=OWNED_SESSION,
            project_id=project_id,
            object_ids=[owned_object["object_id"]],
            actor_id=ACTOR,
        )
    assert again["published"][0]["deduped"] is True
    with SessionLocal() as s:
        revisions = s.execute(
            select(ArtifactRevision).where(
                ArtifactRevision.artifact_id == entry["artifact_id"]
            )
        ).scalars().all()
        assert len(revisions) == 1  # 不 bump


@pytest.mark.asyncio
async def test_publish_foreign_project_and_foreign_object(owned_object):
    from app.core.database import AsyncSessionLocal, SessionLocal
    from app.models.project import Project
    from app.services.lakehouse.project_publish import (
        PublishError,
        publish_to_project,
    )

    foreign_project = f"proj_{uuid.uuid4().hex[:8]}"
    with SessionLocal() as s:
        s.add(Project(id=foreign_project, name="other", owner_id="someone-else"))
        s.commit()

    async with AsyncSessionLocal() as db:
        with pytest.raises(PublishError, match="project not found"):
            await publish_to_project(
                db,
                session_id=OWNED_SESSION,
                project_id=foreign_project,
                object_ids=[owned_object["object_id"]],
                actor_id=ACTOR,
            )
        # 他人 session（守卫 deny）→ 404 语义冒泡。
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await publish_to_project(
                db,
                session_id="sess-attacker",
                project_id=owned_object["db_env"]["project_id"],
                object_ids=[owned_object["object_id"]],
                actor_id="user-attacker",
            )
        # 未知对象 → unknown（诚实清单，不静默）。
        result = await publish_to_project(
            db,
            session_id=OWNED_SESSION,
            project_id=owned_object["db_env"]["project_id"],
            object_ids=["f" * 64, "not-a-ref"],
            actor_id=ACTOR,
        )
        assert result["unknown"] == ["f" * 64, "not-a-ref"]


@pytest.mark.asyncio
async def test_project_scoped_resolve_and_revoke(owned_object):
    from app.core.database import AsyncSessionLocal
    from app.services.lakehouse.data_object import resolve_data_object
    from app.services.lakehouse.project_publish import (
        publish_to_project,
        resolve_project_object,
        revoke_project_objects,
    )

    project_id = owned_object["db_env"]["project_id"]
    oid = owned_object["object_id"]
    async with AsyncSessionLocal() as db:
        # 发布前：项目域不可解析（404 语义）。
        assert await resolve_project_object(
            db, project_id=project_id, object_id=oid,
        ) is None
        await publish_to_project(
            db, session_id=OWNED_SESSION, project_id=project_id,
            object_ids=[oid], actor_id=ACTOR,
        )
        resolved = await resolve_project_object(
            db, project_id=project_id, object_id=oid,
        )
        assert resolved is not None
        assert resolved["manifest"]["content_sha256"] is not None
        # 他人项目不可解析（不泄漏存在性）。
        assert await resolve_project_object(
            db, project_id="proj_nonexistent", object_id=oid,
        ) is None
        # 撤销 → tombstone → 不可再解析。
        rev = await revoke_project_objects(
            db, project_id=project_id, object_ids=[oid], actor_id=ACTOR,
        )
        assert rev["revoked"] == [oid]
        assert await resolve_project_object(
            db, project_id=project_id, object_id=oid,
        ) is None
        # 字节仍在（撤销 ≠ 删除 —— 内容真相只在 BlobStore）。
        assert resolve_data_object(oid) is not None


@pytest.mark.asyncio
async def test_revision_location_roundtrips_through_blobstore(owned_object):
    """R1-5 回归：revision 行指向真实 manifest blob（键=位置=digest 自洽，
    经 BlobStore 读回校验通过 —— 恢复/GC 引用计数因此有效）。"""
    from app.core.database import AsyncSessionLocal, SessionLocal
    from app.models.project import ArtifactRevision
    from app.services.durable_blob_store import reset_filesystem_blob_store
    from app.services.lakehouse.project_publish import publish_to_project

    project_id = owned_object["db_env"]["project_id"]
    oid = owned_object["object_id"]
    async with AsyncSessionLocal() as db:
        await publish_to_project(
            db, session_id=OWNED_SESSION, project_id=project_id,
            object_ids=[oid], actor_id=ACTOR,
        )
    reset_filesystem_blob_store()
    try:
        with SessionLocal() as s:
            revision = s.execute(
                select(ArtifactRevision).where(
                    ArtifactRevision.content_sha256 == oid
                )
            ).scalar_one()
            assert revision.content_location == f"{oid[:4]}/{oid}.json"
    finally:
        reset_filesystem_blob_store()
        from app.services.s3_blob_store import get_object_store

        raw = get_object_store().get_blob(
            revision.content_sha256,
            expected_sha256=revision.content_sha256,
        )
        assert raw is not None  # location 键 digest 三点自洽
