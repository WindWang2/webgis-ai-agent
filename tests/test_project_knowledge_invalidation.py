"""失效语义测试（Oracle ② + 数据/产物/claim 漂移面）。

红线：
- 数据集版本指纹前进 → 投影行 stale，fresh reuse 被阻断；
- artifact head revision（content_sha256）前进 → stale（head_changed 面由
  liveness 探针统一为 version_bump 语义）；
- rebuild 幂等：两遍 active 集合一致；权威行删除不影响投影删除，反之亦然；
- ref 生命周期观察者降级（hook raise 不伤权威）。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base
from app.models.project import (
    Artifact,
    ArtifactRevision,
    Project,
    ProjectDataset,
)
from app.services.project_knowledge import store as ks
from app.services.project_knowledge.contract import (
    AS_ARTIFACT,
    AS_PROJECT_DATASET,
    EK_ARTIFACT,
    KnowledgeUpsert,
    ST_ACTIVE,
    VERDICT_NOT_REUSABLE,
)
from app.services.project_knowledge.indexer import rebuild_project_knowledge
from app.services.project_knowledge.invalidation import (
    handle_ref_invalidation_for_session,
)
from app.services.project_knowledge.liveness import live_version_token
from app.services.project_knowledge.retrieval import find_reuse_candidates
from app.services.project_knowledge.contract import ReuseQuery


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session


_NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


def _seed_project(db) -> Project:
    project = Project(
        id="proj_1", org_id=7, owner_id=None, name="岷江流域项目",
        status="active", metadata_json={}, created_at=_NOW, updated_at=_NOW,
    )
    db.add(project)
    db.flush()
    return project


def _seed_dataset(db, *, fp="fp:v1") -> ProjectDataset:
    row = ProjectDataset(
        id="ds_1", project_id="proj_1", name="dem_30m",
        source_type="upload", source_ref="s3://b/dem.tif",
        version_fingerprint=fp, created_at=_NOW,
    )
    db.add(row)
    db.flush()
    return row


def _seed_artifact_with_revision(
    db, *, artifact_id="art-1", sha="sha256:c1",
) -> Artifact:
    art = Artifact(
        id=artifact_id, project_id="proj_1", name="hillshade",
        artifact_type="raster", content_fingerprint=sha, created_at=_NOW,
    )
    db.add(art)
    db.flush()
    db.add(ArtifactRevision(
        id="rev-1", artifact_id=artifact_id, revision_no=1,
        content_sha256=sha, content_location=f"shard4/{sha}.json",
        content_type="json", byte_size=10, created_at=_NOW,
    ))
    db.flush()
    return art


# ── Oracle ②：数据版本前进 → stale 不可 fresh reuse ──────────────────


def test_dataset_version_bump_demotes_entry(db):
    _seed_project(db)
    _seed_dataset(db, fp="fp:v1")
    project = db.get(Project, "proj_1")
    report = rebuild_project_knowledge(db, project=project, org_id="org:7")
    assert report.upserted.get("dataset_version") == 1
    assert report.errors == 0

    # 版本前进：指纹变化（simulate re-attach）
    row = db.get(ProjectDataset, "ds_1")
    row.version_fingerprint = "fp:v2"
    db.flush()

    # 未 rebuild 的投影行仍是旧 token → liveness 探针报漂移 → 降级。
    token = live_version_token(
        db, project_id="proj_1",
        authority_store=AS_PROJECT_DATASET, authority_id="ds_1",
    )
    assert token == "fp:v2"
    demoted = ks.demote_stale_by_token(
        db, org_id="org:7", project_id="proj_1",
        authority_store=AS_PROJECT_DATASET, authority_id="ds_1",
        live_token=token,
    )
    assert demoted == 1
    entries = ks.get_entries_by_authority(
        db, org_id="org:7", project_id="proj_1",
        authority_store=AS_PROJECT_DATASET, authority_id="ds_1",
    )
    assert entries[0].status == "stale"
    assert entries[0].invalidation_rule == "version_bump"


def test_artifact_head_revision_change_blocks_fresh_reuse(db):
    _seed_project(db)
    _seed_artifact_with_revision(db, sha="sha256:c1")
    project = db.get(Project, "proj_1")
    rebuild_project_knowledge(db, project=project, org_id="org:7")
    entries = ks.get_entries_by_authority(
        db, org_id="org:7", project_id="proj_1",
        authority_store=AS_ARTIFACT, authority_id="art-1",
    )
    assert entries[0].status == ST_ACTIVE
    assert entries[0].version_token == "sha256:c1"

    # 新 head revision（内容变化）
    db.add(ArtifactRevision(
        id="rev-2", artifact_id="art-1", revision_no=2,
        content_sha256="sha256:c2", content_location="shard4/sha256:c2.json",
        content_type="json", byte_size=12, created_at=_NOW,
    ))
    db.flush()

    hits = find_reuse_candidates(
        db, org_id="org:7", project_id="proj_1",
        query=ReuseQuery(limit=8),
    )
    art_hits = [h for h in hits if h.entry.authority_id == "art-1"]
    assert art_hits and art_hits[0].verdict == VERDICT_NOT_REUSABLE
    assert "authority_advanced" in art_hits[0].stale_causes


# ── rebuild 幂等 + 回指可解析 ────────────────────────────────────────


def test_rebuild_idempotent_same_active_set(db):
    _seed_project(db)
    _seed_dataset(db)
    _seed_artifact_with_revision(db)
    project = db.get(Project, "proj_1")

    rebuild_project_knowledge(db, project=project, org_id="org:7")
    first = sorted(
        (e.entity_kind, e.authority_id)
        for e in ks.get_active_entries(db, org_id="org:7", project_id="proj_1")
    )
    rebuild_project_knowledge(db, project=project, org_id="org:7")
    second = sorted(
        (e.entity_kind, e.authority_id)
        for e in ks.get_active_entries(db, org_id="org:7", project_id="proj_1")
    )
    assert first == second
    total_rows = len(list(db.execute(select(ProjectDataset)).scalars().all()))
    assert total_rows == 1   # 权威行未被 rebuild 改动


def test_detached_dataset_invalidates_projection(db):
    _seed_project(db)
    _seed_dataset(db)
    project = db.get(Project, "proj_1")
    rebuild_project_knowledge(db, project=project, org_id="org:7")

    row = db.get(ProjectDataset, "ds_1")
    row.detached_at = _NOW   # soft-detach
    db.flush()
    assert live_version_token(
        db, project_id="proj_1",
        authority_store=AS_PROJECT_DATASET, authority_id="ds_1",
    ) is None
    ks.demote_stale_by_token(
        db, org_id="org:7", project_id="proj_1",
        authority_store=AS_PROJECT_DATASET, authority_id="ds_1",
        live_token=None,
    )
    entries = ks.get_entries_by_authority(
        db, org_id="org:7", project_id="proj_1",
        authority_store=AS_PROJECT_DATASET, authority_id="ds_1",
    )
    assert entries[0].status == "invalidated"
    assert entries[0].invalidation_rule == "scope_gone"


# ── 投影与权威互不伤害（oracle ⑤ 的反面） ────────────────────────────


def test_deleting_projection_leaves_authorities_intact(db):
    _seed_project(db)
    _seed_dataset(db)
    _seed_artifact_with_revision(db)
    project = db.get(Project, "proj_1")
    rebuild_project_knowledge(db, project=project, org_id="org:7")
    for row in list(db.execute(
        select(__import__("app.models.project_knowledge", fromlist=["ProjectKnowledgeEntry"]).ProjectKnowledgeEntry)
    ).scalars().all()):
        db.delete(row)
    db.flush()
    assert db.get(ProjectDataset, "ds_1") is not None
    assert db.get(Artifact, "art-1") is not None
    # 再次 rebuild 完整恢复
    rebuild_project_knowledge(db, project=project, org_id="org:7")
    assert len(ks.get_active_entries(db, org_id="org:7", project_id="proj_1")) >= 2


# ── ref 生命周期观察者 ───────────────────────────────────────────────


def test_ref_invalidation_demotes_session_ref_entries(db):
    _seed_project(db)
    ks.upsert_entry(db, KnowledgeUpsert(
        org_id="org:7", project_id="proj_1", entity_kind=EK_ARTIFACT,
        authority_store="session_ref", authority_id="ref:abc123",
        subject="session 产物 ref", version_token="t1",
    ))
    n = handle_ref_invalidation_for_session(
        db, org_id="org:7", ref_id="ref:abc123"
    )
    assert n == 1
    entries = ks.get_entries_by_authority(
        db, org_id="org:7", project_id="proj_1",
        authority_store="session_ref", authority_id="ref:abc123",
    )
    assert entries[0].status == "stale"


def test_ref_invalidation_scoped_by_org(db):
    ks.upsert_entry(db, KnowledgeUpsert(
        org_id="org:7", project_id="proj_1", entity_kind=EK_ARTIFACT,
        authority_store="session_ref", authority_id="ref:xyz",
        subject="org7 的 ref", version_token="t1",
    ))
    assert handle_ref_invalidation_for_session(
        db, org_id="org:9", ref_id="ref:xyz"
    ) == 0
    entries = ks.get_active_entries(db, org_id="org:7", project_id="proj_1")
    assert len(entries) == 1


# ── Review P2-3 回归：单源失败不中断其余源（rebuild 充分性） ─────────


def test_rebuild_per_source_isolation(db, monkeypatch):
    _seed_project(db)
    _seed_dataset(db)
    _seed_artifact_with_revision(db)
    project = db.get(Project, "proj_1")

    from app.services.project_knowledge import indexer

    def _boom(*a, **kw):
        raise RuntimeError("datasets source exploded")

    monkeypatch.setattr(indexer, "index_datasets", _boom)
    report = indexer.rebuild_project_knowledge(
        db, project=project, org_id="org:7"
    )
    # 数据集源失败，但产物源照常完成。
    assert report.errors == 1
    assert report.upserted.get("artifact") == 1
    assert report.failed_sources == ["datasets"]
