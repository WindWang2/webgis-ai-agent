"""Synthetic 多 Mission 端到端场景（Oracle ①–⑤ 的集成面）。

场景：同一项目（岷江流域）两个 Mission ——
- Mission A 产出 2024 土地覆盖产物（数据集 + artifact revision + mission
  权威行齐全），rebuild 建立项目知识投影；
- Mission B 预检：同 AOI/同年/同方法/同上游指纹 → exact 复用（回指同一
  权威行）；
- 数据版本前进 → Mission C 预检：不可 fresh reuse（stale/advanced）；
- 相似名不同 AOI/年份 → 不误复用；
- 跨租户读空集；context card 有界且只含指针。
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
from app.models.mission import GISMissionRow
from app.models.project import (
    Artifact,
    ArtifactLineage,
    ArtifactRevision,
    Project,
    ProjectDataset,
)
from app.services.project_knowledge import store as ks
from app.services.project_knowledge.card import render_project_knowledge_card
from app.services.project_knowledge.contract import (
    AS_ARTIFACT,
    CARD_CHAR_BUDGET,
    EK_MISSION,
    ReuseQuery,
    VERDICT_EXACT,
    VERDICT_NOT_REUSABLE,
)
from app.services.project_knowledge.indexer import rebuild_project_knowledge
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


_NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
AOI_CHENGDU_2024 = [103.0, 30.1, 104.9, 31.4]


def _seed_mission_a_world(db) -> None:
    """Mission A 之后的权威世界：项目 + 数据集 + 产物(revision) + mission。"""
    db.add(Project(
        id="proj_minjiang", org_id=7, owner_id=None, name="岷江流域项目",
        status="active", metadata_json={}, created_at=_NOW, updated_at=_NOW,
    ))
    db.add(ProjectDataset(
        id="ds_dem2024", project_id="proj_minjiang", name="dem_2024",
        source_type="upload", source_ref="s3://b/dem2024.tif",
        version_fingerprint="fp:dem:2024:v1", created_at=_NOW,
    ))
    db.add(Artifact(
        id="art_lc2024", project_id="proj_minjiang",
        name="landcover_2024.tif", artifact_type="raster", format="GeoTIFF",
        content_fingerprint="sha256:lc2024:c1",
        metadata_json={
            "bbox": AOI_CHENGDU_2024, "temporal_label": "2024",
        },
        created_at=_NOW,
    ))
    db.add(ArtifactRevision(
        id="rev-1", artifact_id="art_lc2024", revision_no=1,
        content_sha256="sha256:lc2024:c1",
        content_location="shard4/sha256:lc2024:c1.json",
        content_type="json", byte_size=16, created_at=_NOW,
    ))
    # Mission A 的生产血缘：输入数据集 + capability/algorithm（方法投影来源）
    db.add(ArtifactLineage(
        id="lin-1", artifact_id="art_lc2024",
        source_dataset_id="ds_dem2024",
        source_dataset_fingerprint="fp:dem:2024:v1",
        producing_capability="landcover",
        producing_algorithm="random_forest",
        content_fingerprint="sha256:lc2024:c1", created_at=_NOW,
    ))
    db.add(GISMissionRow(
        mission_id="msn-a", org_id="org:7", user_id="u1",
        project_id="proj_minjiang", owner_scope="u1",
        root_goal="制作 2024 年岷江流域土地覆盖图",
        state="complete", revision=2,
        refs={
            "artifact_refs": ["art_lc2024"],
            "map_product_refs": [],
            "evidence_refs": [],
            "workflow_instance_refs": [],
            "swarm_run_refs": [],
            "session_plan_refs": [],
            "active_session_ids": [],
        },
        frontier={}, resource_budget={}, failure_state={}, recovery_state={},
        created_at=_NOW, updated_at=_NOW,
    ))
    db.flush()


def test_two_mission_reuse_story(db):
    _seed_mission_a_world(db)
    project = db.get(Project, "proj_minjiang")

    # ── Mission A 之后：建索引 ──────────────────────────────────────
    report = rebuild_project_knowledge(db, project=project, org_id="org:7")
    assert report.errors == 0
    assert report.upserted.get("artifact") == 1
    assert report.upserted.get("dataset_version") == 1
    assert report.upserted.get("mission") == 1
    db.commit()

    # ── Mission B 预检：同 AOI/年/方法/上游 → exact，回指同一权威行 ──
    query_exact = ReuseQuery(
        bbox=AOI_CHENGDU_2024, temporal_label="2024",
        method_key="landcover:random_forest",
        dataset_fingerprints={"ds_dem2024": "fp:dem:2024:v1"},
    )
    hits = find_reuse_candidates(
        db, org_id="org:7", project_id="proj_minjiang", query=query_exact,
    )
    db.commit()
    top = hits[0]
    assert top.entry.authority_id == "art_lc2024"
    assert top.verdict == VERDICT_EXACT, top.stale_causes
    assert top.stale_causes == []

    # 权威行未被复用路径修改（回指 oracle）
    art = db.get(Artifact, "art_lc2024")
    assert art.content_fingerprint == "sha256:lc2024:c1"

    # ── 数据版本前进：Mission C 预检 → 不可 fresh reuse ─────────────
    ds = db.get(ProjectDataset, "ds_dem2024")
    ds.version_fingerprint = "fp:dem:2024:v2"
    db.flush()

    # (a) Mission C 以**权威当前**输入（v2）规划：产物上游漂移 →
    #     recompute_partial（不再 exact）；数据集知识 → stale。
    query_v2 = ReuseQuery(
        bbox=AOI_CHENGDU_2024, temporal_label="2024",
        method_key="landcover:random_forest",
        dataset_fingerprints={"ds_dem2024": "fp:dem:2024:v2"},
    )
    hits2 = find_reuse_candidates(
        db, org_id="org:7", project_id="proj_minjiang", query=query_v2,
    )
    db.commit()
    art_hit = next(
        h for h in hits2 if h.entry.authority_id == "art_lc2024"
    )
    assert art_hit.verdict == "recompute_partial"
    assert any(
        c.startswith("upstream_drift") for c in art_hit.stale_causes
    )
    assert all(h.verdict != VERDICT_EXACT for h in hits2)

    # (b) 若规划者仍拿旧 v1 指纹规划：请求级输入 stale 警告 → 亦不得 exact
    hits2b = find_reuse_candidates(
        db, org_id="org:7", project_id="proj_minjiang", query=query_exact,
    )
    assert all(h.verdict != VERDICT_EXACT for h in hits2b)
    assert any(
        "request_input_stale:ds_dem2024" in h.stale_causes for h in hits2b
    )

    # 投影行已诚实降级
    entries = ks.get_entries_by_authority(
        db, org_id="org:7", project_id="proj_minjiang",
        authority_store=AS_ARTIFACT, authority_id="art_lc2024",
    )
    assert entries[0].status == "active"
    ds_entries = ks.get_entries_by_authority(
        db, org_id="org:7", project_id="proj_minjiang",
        authority_store="project_dataset", authority_id="ds_dem2024",
    )
    assert ds_entries[0].status == "stale"
    assert ds_entries[0].invalidation_rule == "version_bump"

    # ── 相似名不同 AOI/年份 → 不误复用 ──────────────────────────────
    query_other_aoi = ReuseQuery(
        bbox=[110.0, 20.0, 112.0, 22.0], temporal_label="2024",
    )
    hits3 = find_reuse_candidates(
        db, org_id="org:7", project_id="proj_minjiang", query=query_other_aoi,
    )
    assert all(h.verdict == VERDICT_NOT_REUSABLE for h in hits3)

    query_other_year = ReuseQuery(bbox=AOI_CHENGDU_2024, temporal_label="2019")
    hits4 = find_reuse_candidates(
        db, org_id="org:7", project_id="proj_minjiang", query=query_other_year,
    )
    assert all(h.verdict == VERDICT_NOT_REUSABLE for h in hits4)

    # ── 跨租户：org:9 看不到 org:7 的知识 ──────────────────────────
    hits5 = find_reuse_candidates(
        db, org_id="org:9", project_id="proj_minjiang", query=query_exact,
    )
    assert hits5 == []

    # ── context card：有界、含指针、失败/mission 上下文 ─────────────
    fresh = ks.get_active_entries(
        db, org_id="org:7", project_id="proj_minjiang", limit=200,
    )
    card = render_project_knowledge_card(
        fresh, project_id="proj_minjiang", project_name="岷江流域项目",
    )
    assert card.chars <= CARD_CHAR_BUDGET
    assert "art_lc2024" in card.text          # 权威指针出现
    assert "sha256:lc2024" not in card.text   # 摘要面不携带指纹载荷以外的泄漏面（token 不在卡内）
    mission_entries = [e for e in fresh if e.entity_kind == EK_MISSION]
    assert mission_entries and mission_entries[0].authority_id == "msn-a"


def test_mission_failure_projected_as_bounded_warning(db):
    _seed_mission_a_world(db)
    db.add(GISMissionRow(
        mission_id="msn-fail", org_id="org:7", user_id="u1",
        project_id="proj_minjiang", owner_scope="u1",
        root_goal="2019 年洪水淹没分析",
        state="failed", revision=3,
        refs={
            "artifact_refs": [], "map_product_refs": [], "evidence_refs": [],
            "workflow_instance_refs": [], "swarm_run_refs": [],
            "session_plan_refs": [], "active_session_ids": [],
        },
        frontier={}, resource_budget={},
        failure_state={"error_code": "PROVIDER_TIMEOUT", "detail": "上游 DEM 服务超时"},
        recovery_state={}, created_at=_NOW, updated_at=_NOW,
    ))
    db.flush()
    project = db.get(Project, "proj_minjiang")
    report = rebuild_project_knowledge(db, project=project, org_id="org:7")
    assert report.upserted.get("failure_pattern") == 1
    entries = ks.get_active_entries(
        db, org_id="org:7", project_id="proj_minjiang",
        kinds=("failure_pattern",),
    )
    assert entries and entries[0].subject == "PROVIDER_TIMEOUT"
    card = render_project_knowledge_card(
        entries + ks.get_active_entries(
            db, org_id="org:7", project_id="proj_minjiang", limit=50,
        ),
        project_id="proj_minjiang",
    )
    assert "PROVIDER_TIMEOUT" in card.text
    assert "⚠" in card.text
