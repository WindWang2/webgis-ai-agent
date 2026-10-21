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
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base
import app.models.spatial_memory  # noqa: F401 — register gis_spatial_memories
from app.models.project import (
    Artifact,
    ArtifactRevision,
    Project,
    ProjectDataset,
)
from app.models.spatial_memory import GISSpatialMemory
from app.services.project_knowledge import store as ks
from app.services.project_knowledge.contract import (
    AS_ARTIFACT,
    AS_GIS_MEMORY,
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
from app.services.project_knowledge.liveness import (
    live_version_token,
    live_version_tokens,
)
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


# ── #1738：AS_GIS_MEMORY 权威探针按 store 自身作用域口径过滤 ───────────


def _seed_memory(
    db, *, mem_id, scope="project", scope_id="proj_1",
    fingerprint="fp:mem1", status="active", expires_at=None,
) -> GISSpatialMemory:
    row = GISSpatialMemory(
        id=mem_id, org_id="org:7", scope=scope, scope_id=scope_id,
        kind="resolved_place", subject=f"place:{mem_id}", value={}, refs=[],
        evidence={}, fingerprint=fingerprint, status=status,
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    return row


def test_gis_memory_probe_is_scoped_to_the_project_1738(db):
    """AS_GIS_MEMORY 探针不得按主键裸查：GISSpatialMemory 无 project_id
    列，作用域编码在 (scope, scope_id) —— 别人的项目 / 别的 scope 一律
    fail-closed 判 None（不得续命他人的权威行）。"""
    _seed_project(db)
    _seed_memory(db, mem_id="mem_mine", fingerprint="fp:mine")
    _seed_memory(db, mem_id="mem_other_project", scope_id="proj_9",
                 fingerprint="fp:theirs")
    _seed_memory(db, mem_id="mem_session", scope="session",
                 fingerprint="fp:session")
    _seed_memory(db, mem_id="mem_user", scope="user", fingerprint="fp:user")

    assert live_version_token(
        db, project_id="proj_1",
        authority_store=AS_GIS_MEMORY, authority_id="mem_mine",
    ) == "fp:mine"
    for foreign in ("mem_other_project", "mem_session", "mem_user"):
        assert live_version_token(
            db, project_id="proj_1",
            authority_store=AS_GIS_MEMORY, authority_id=foreign,
        ) is None, foreign
    # 未知 id 同样 fail-closed。
    assert live_version_token(
        db, project_id="proj_1",
        authority_store=AS_GIS_MEMORY, authority_id="mem_absent",
    ) is None


def test_gis_memory_probe_batch_matches_single_1738(db):
    """批量版与逐条版同一作用域口径（含非 active / 已过期记忆 → None）。"""
    from datetime import timedelta

    _seed_project(db)
    _seed_memory(db, mem_id="mem_1", fingerprint="fp:1")
    _seed_memory(db, mem_id="mem_2", status="superseded", fingerprint="fp:2")
    _seed_memory(db, mem_id="mem_3", fingerprint="fp:3",
                 expires_at=_NOW - timedelta(days=1))
    _seed_memory(db, mem_id="mem_4", scope_id="proj_9", fingerprint="fp:4")

    probes = [(AS_GIS_MEMORY, f"mem_{i}") for i in (1, 2, 3, 4)]
    batched = live_version_tokens(db, project_id="proj_1", probes=probes)
    for store, aid in probes:
        assert batched[(store, aid)] == live_version_token(
            db, project_id="proj_1", authority_store=store, authority_id=aid,
        )
    assert batched[(AS_GIS_MEMORY, "mem_1")] == "fp:1"
    assert batched[(AS_GIS_MEMORY, "mem_2")] is None
    assert batched[(AS_GIS_MEMORY, "mem_3")] is None
    assert batched[(AS_GIS_MEMORY, "mem_4")] is None


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


def test_ref_tag_fallback_scans_past_the_first_page_1740(db):
    """#1740: ref-tag 兜底不得只扫首页。修复前是无 ORDER BY 的
    ``limit(400)`` —— 每 (org, project) 行预算本身就是 400，多项目 org 的
    受害者落在首页之外即静默漏降级。改为确定性主键游标分页后，排在最后的
    受害者同样被降级。"""
    from app.models.project_knowledge import ProjectKnowledgeEntry
    from app.services.project_knowledge.invalidation import _REF_SCAN_PAGE

    _seed_project(db)
    # 受害者排在最末（主键字典序最大）：修复前 limit(400) 的首页看不到它。
    total = _REF_SCAN_PAGE * 2 + 50
    for i in range(total):
        victim = i == total - 1
        db.add(ProjectKnowledgeEntry(
            id=f"pk-{i:05d}", org_id="org:7", project_id="proj_1",
            entity_kind="artifact",
            subject=f"asset-{i}",
            authority_store="artifact", authority_id=f"art-{i}",
            version_token="t1",
            refs=[{"relation": "verified_by", "authority": "session_ref",
                   "id": "ref:deep", "token": "t1"}] if victim else [],
        ))
    db.flush()

    demoted = handle_ref_invalidation_for_session(
        db, org_id="org:7", ref_id="ref:deep")

    assert demoted == 1
    row = db.get(ProjectKnowledgeEntry, f"pk-{total - 1:05d}")
    assert row.status == "stale" and row.invalidation_rule == "head_changed"
    # 未被引用的兄弟行不受影响（精确降级，不是全场作废）。
    assert db.get(ProjectKnowledgeEntry, "pk-00000").status == "active"


def test_ref_tag_fallback_is_deterministic_across_scans_1740(db):
    """同一 ref 的两次扫描结果一致（分页游标不依赖行序/状态漂移）。"""
    from app.models.project_knowledge import ProjectKnowledgeEntry

    _seed_project(db)
    for i in range(6):
        db.add(ProjectKnowledgeEntry(
            id=f"pk-b{i}", org_id="org:7", project_id="proj_1",
            entity_kind="artifact", subject=f"asset-b{i}",
            authority_store="artifact", authority_id=f"art-b{i}",
            version_token="t1",
            refs=[{"relation": "verified_by", "authority": "session_ref",
                   "id": "ref:multi", "token": "t1"}] if i % 2 else [],
        ))
    db.flush()
    assert handle_ref_invalidation_for_session(
        db, org_id="org:7", ref_id="ref:multi") == 3
    # 幂等：已降级的行不重复计数。
    assert handle_ref_invalidation_for_session(
        db, org_id="org:7", ref_id="ref:multi") == 0


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


# ── #1721 回归：检索热路径 liveness 复核批量（非逐条 N+1） ────────────


def test_batch_probe_matches_per_entry_probe_1721(db):
    """live_version_tokens 与逐条 live_version_token 语义逐字一致。

    含在权威行（artifact 指纹回退 / dataset 指纹）、权威缺失（→ None）、
    空指纹（→ ""）与跨项目探针 —— 批量不得放宽任何 fail-closed 语义。
    """
    _seed_project(db)
    _seed_dataset(db, fp="fp:v1")
    _seed_artifact_with_revision(db, sha="sha256:c1")
    db.add(Artifact(
        id="art-empty", project_id="proj_1", name="no_fp",
        artifact_type="raster", content_fingerprint=None, created_at=_NOW,
    ))
    db.flush()

    probes = [
        (AS_ARTIFACT, "art-1"),
        (AS_PROJECT_DATASET, "ds_1"),
        (AS_ARTIFACT, "art-empty"),
        (AS_ARTIFACT, "art_missing"),        # 权威行不存在 → None
        (AS_PROJECT_DATASET, "ds_missing"),  # 同上
        ("session_ref", "ref:abc"),          # 不可 durable 复核 → None
    ]
    batched = live_version_tokens(db, project_id="proj_1", probes=probes)
    for store, aid in probes:
        assert batched[(store, aid)] == live_version_token(
            db, project_id="proj_1", authority_store=store, authority_id=aid,
        )
    assert batched[(AS_ARTIFACT, "art-1")] == "sha256:c1"
    assert batched[(AS_PROJECT_DATASET, "ds_1")] == "fp:v1"
    assert batched[(AS_ARTIFACT, "art-empty")] == ""
    assert batched[(AS_ARTIFACT, "art_missing")] is None
    assert batched[(AS_PROJECT_DATASET, "ds_missing")] is None


def test_reuse_retrieval_liveness_is_batched_1721(db):
    """查询计数断言：候选数不得再线性放大检索 SQL。

    修复前每条候选一次 live_version_token（artifact 权威 = 2 条 SELECT，
    5 个输入指纹另 10 条）—— 200 条 active 每 chat turn ~600 条语句，而
    结果只留 query.limit 条。批量后语句数对候选数脱钩。
    """
    n = 50
    _seed_project(db)
    for i in range(n):
        aid, ds = f"art-{i}", f"ds_{i}"
        db.add(ProjectDataset(
            id=ds, project_id="proj_1", name=f"dem_{i}",
            source_type="upload", source_ref=f"s3://b/dem_{i}.tif",
            version_fingerprint=f"fp:ds{i}", created_at=_NOW,
        ))
        art = Artifact(
            id=aid, project_id="proj_1", name=f"asset_{i}",
            artifact_type="raster", content_fingerprint="sha256:head1",
            created_at=_NOW,
        )
        db.add(art)
        db.flush()
        ks.upsert_entry(db, KnowledgeUpsert(
            org_id="org:7", project_id="proj_1", entity_kind=EK_ARTIFACT,
            authority_store=AS_ARTIFACT, authority_id=aid,
            subject=f"asset_{i}.tif", version_token="sha256:head1",
        ))

    fingerprints = {f"ds_{i}": f"fp:ds{i}" for i in range(5)}
    counter = {"n": 0}

    def _count(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        counter["n"] += 1

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _count)
    try:
        hits = find_reuse_candidates(
            db, org_id="org:7", project_id="proj_1",
            query=ReuseQuery(dataset_fingerprints=fingerprints, limit=3),
        )
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert len(hits) == 3
    # get_active_entries 1 + dataset IN 1 + artifact IN 1 + revision IN 1
    # = 4；8 是含 ORM 余量的上限，远小于修复前的 ~111（n×2 + 5×2 + 1）。
    assert counter["n"] <= 8, (
        f"liveness 复核必须批量：{n} 条候选 + 5 个输入指纹只允许 O(权威"
        f"类别) 条语句，实际 {counter['n']} 条"
    )
