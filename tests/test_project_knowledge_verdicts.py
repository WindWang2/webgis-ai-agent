"""复用判定测试（Oracle ①②③ 的单元面）。

红线：
- exact 需要全部作用域可确认且一致（AOI 覆盖 / temporal 相等 / method
  相等 / 上游指纹一致）；
- 相似名字**永不参与判定**（判定不读 subject 文本）；
- 任何未知（任一侧 None）→ 降级，绝不 wildcard 放行；
- 权威漂移 → stale → 不可 fresh reuse。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base
from app.models.project import Artifact, ProjectDataset
from app.services.project_knowledge import store as ks
from app.services.project_knowledge.contract import (
    AS_ARTIFACT,
    AS_PROJECT_DATASET,
    EK_ARTIFACT,
    KnowledgeUpsert,
    REL_DERIVED_FROM,
    RefTag,
    ReuseQuery,
    VERDICT_EXACT,
    VERDICT_NOT_REUSABLE,
    VERDICT_RECOMPUTE_PARTIAL,
)
from app.services.project_knowledge.retrieval import find_reuse_candidates


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


AOI_2024 = [100.0, 28.0, 104.0, 32.0]
_NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


def _seed_authorities(db, *, artifact_id="art-1", ds_id="ds_1",
                      artifact_fp="sha256:head1", ds_fp="fp:ds1",
                      bbox=None, temporal="2024"):
    """真实权威行：ProjectDataset + Artifact（liveness 探针的查询面）。"""
    db.add(ProjectDataset(
        id=ds_id, project_id="proj_1", name="input_2024",
        source_type="upload", source_ref="s3://bucket/input.tif",
        version_fingerprint=ds_fp, created_at=_NOW,
    ))
    meta = {}
    if bbox is not None:
        meta["bbox"] = bbox
    if temporal:
        meta["temporal_label"] = temporal
    db.add(Artifact(
        id=artifact_id, project_id="proj_1", name="landcover_2024.tif",
        artifact_type="raster", format="GeoTIFF",
        content_fingerprint=artifact_fp, metadata_json=meta,
        created_at=_NOW,
    ))
    db.flush()


def _seed_projection(db, *, artifact_id="art-1", artifact_fp="sha256:head1",
                     ds_id="ds_1", ds_fp="fp:ds1", bbox=AOI_2024,
                     temporal="2024", name="landcover_2024.tif",
                     method="landcover:random_forest"):
    """投影行（token = 索引时观察到的权威指纹）。"""
    refs = ()
    if ds_id:
        refs = (RefTag(relation=REL_DERIVED_FROM,
                       authority=AS_PROJECT_DATASET, id=ds_id, token=ds_fp),)
    ks.upsert_entry(db, KnowledgeUpsert(
        org_id="org-a", project_id="proj_1", entity_kind=EK_ARTIFACT,
        authority_store=AS_ARTIFACT, authority_id=artifact_id,
        subject=name, version_token=artifact_fp,
        summary="", bbox=bbox, temporal_label=temporal,
        method_key=method, refs=refs,
    ))


def _query(**kw) -> ReuseQuery:
    base = dict(
        bbox=AOI_2024, temporal_label="2024",
        method_key="landcover:random_forest",
        dataset_fingerprints={"ds_1": "fp:ds1"},
    )
    base.update(kw)
    return ReuseQuery(**base)


# ── Oracle ①：跨 Mission 复用仍有效 refs ─────────────────────────────


def test_exact_when_all_scope_confirmed(db):
    _seed_authorities(db)
    _seed_projection(db)
    hits = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1", query=_query(),
    )
    assert hits, "至少一条候选"
    top = hits[0]
    assert top.verdict == VERDICT_EXACT
    assert top.entry.authority_id == "art-1"
    assert top.stale_causes == []


# ── Oracle ③：相似名不同 AOI/年份/方法不误复用 ───────────────────────


def test_similar_name_different_aoi_never_reused(db):
    _seed_authorities(db)
    _seed_projection(db, name="landcover_2024.tif")
    other_aoi = [110.0, 20.0, 112.0, 22.0]
    hits = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1",
        query=_query(bbox=other_aoi),
    )
    assert all(h.verdict == VERDICT_NOT_REUSABLE for h in hits)
    assert any("aoi_mismatch" in h.stale_causes for h in hits)


def test_similar_name_different_year_never_reused(db):
    _seed_authorities(db)
    _seed_projection(db, name="landcover_2024.tif")
    hits = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1",
        query=_query(temporal_label="2025"),
    )
    assert all(h.verdict == VERDICT_NOT_REUSABLE for h in hits)
    assert any("temporal_mismatch" in h.stale_causes for h in hits)


def test_different_method_not_reusable(db):
    _seed_authorities(db)
    _seed_projection(db)
    hits = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1",
        query=_query(method_key="landcover:deep_lab_v3plus"),
    )
    assert all(h.verdict == VERDICT_NOT_REUSABLE for h in hits)


# ── 未知即降级（fail-closed） ────────────────────────────────────────


def test_unknown_request_scope_degrades_never_exact(db):
    _seed_authorities(db)
    _seed_projection(db)
    hits = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1",
        query=_query(bbox=None, temporal_label=None, method_key=None,
                     dataset_fingerprints=None),
    )
    assert hits
    assert all(h.verdict != VERDICT_EXACT for h in hits)
    assert hits[0].verdict == VERDICT_RECOMPUTE_PARTIAL


def test_entry_missing_bbox_never_exact(db):
    _seed_authorities(db, bbox=None)
    _seed_projection(db, bbox=None)
    hits = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1", query=_query(),
    )
    assert all(h.verdict != VERDICT_EXACT for h in hits)


# ── Oracle ②：权威漂移 → 不可 fresh reuse ────────────────────────────


def test_upstream_drift_gives_recompute_partial(db):
    _seed_authorities(db)
    _seed_projection(db)
    hits = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1",
        query=_query(dataset_fingerprints={"ds_1": "fp:NEW"}),
    )
    top = hits[0]
    assert top.verdict == VERDICT_RECOMPUTE_PARTIAL
    assert any(c.startswith("upstream_drift") for c in top.stale_causes)


def test_authority_head_advanced_demotes_and_blocks_reuse(db):
    # 先有权威（head1）+ 投影（head1）→ exact；
    # 权威 head 前进到 head2 → 检索写回降级 → 不可复用。
    _seed_authorities(db, artifact_fp="sha256:head1")
    _seed_projection(db, artifact_fp="sha256:head1")
    hits = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1", query=_query(),
    )
    assert hits[0].verdict == VERDICT_EXACT

    art = db.get(Artifact, "art-1")
    art.content_fingerprint = "sha256:head2"
    db.flush()

    hits2 = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1", query=_query(),
    )
    assert all(h.verdict == VERDICT_NOT_REUSABLE for h in hits2)
    assert any("authority_advanced" in h.stale_causes for h in hits2)
    rows = ks.get_entries_by_authority(
        db, org_id="org-a", project_id="proj_1",
        authority_store=AS_ARTIFACT, authority_id="art-1",
    )
    assert rows[0].status == "stale"
    assert rows[0].invalidation_rule == "version_bump"


def test_authority_gone_blocks_reuse(db):
    _seed_authorities(db)
    _seed_projection(db)
    art = db.get(Artifact, "art-1")
    db.delete(art)
    db.flush()
    hits = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1", query=_query(),
    )
    assert all(h.verdict == VERDICT_NOT_REUSABLE for h in hits)
    assert any("authority_gone" in h.stale_causes for h in hits)


# ── 排序与截断 ────────────────────────────────────────────────────────


def test_verdict_ordering_and_limit(db):
    for i in range(5):
        aid, ds = f"art-{i}", f"ds_{i}"
        _seed_authorities(db, artifact_id=aid, ds_id=ds,
                          ds_fp=f"fp:ds{i}")
        _seed_projection(db, artifact_id=aid, artifact_fp="sha256:head1",
                         ds_id=ds, ds_fp=f"fp:ds{i}", name=f"asset_{i}.tif")
    hits = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1", query=_query(
            dataset_fingerprints={f"ds_{i}": f"fp:ds{i}" for i in range(5)},
            limit=3,
        ),
    )
    assert len(hits) == 3
    assert all(h.verdict == VERDICT_EXACT for h in hits)


# ── Review P1-1 回归：空上游 token 绝不产生 exact（未知 ≢ 一致） ──────


def test_empty_upstream_token_never_exact(db):
    # 权威 lineage 的 source_dataset_fingerprint 为 NULL → 投影 ref-tag
    # token 为空 —— 未知出处 ≠ 指纹一致，绝不 exact（review P1-1）。
    _seed_authorities(db)
    _seed_projection(db, ds_fp="")
    hits = find_reuse_candidates(
        db, org_id="org-a", project_id="proj_1", query=_query(),
    )
    assert all(h.verdict != VERDICT_EXACT for h in hits)
    top = hits[0]
    assert top.verdict == VERDICT_RECOMPUTE_PARTIAL
    assert any(
        c.startswith("upstream_unverified") for c in top.stale_causes
    )
    assert not any("指纹一致" in r for r in top.reasons)
