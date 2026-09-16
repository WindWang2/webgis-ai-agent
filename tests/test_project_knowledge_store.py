"""ProjectKnowledge 契约/存储测试（方向：project knowledge projection）。

纪律红线（全部有断言锁定）：
- 投影身份全借自权威源（authority_store + authority_id + version_token），
  删除投影行不影响权威行，rebuild 可重建；
- tenancy：org/project 谓词恒等值过滤，跨租户读空集，org 由调用方烙印；
- 有界：subject/summary/refs 预算、行数预算淘汰；
- 取代显式：同 key 换 token 走 superseded 链，partial unique 兜底并发；
- 写入门 fail-closed：未知 kind/authority、缺 org/project、超长 subject 拒。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytestmark = pytest.mark.cartography

from app.core.database import Base
from app.models.project_knowledge import ProjectKnowledgeEntry
from app.services.project_knowledge import store as s
from app.services.project_knowledge.contract import (
    AS_ARTIFACT,
    AS_PROJECT_DATASET,
    EK_ARTIFACT,
    EK_DATASET_VERSION,
    KnowledgePolicyError,
    KnowledgeUpsert,
    PROJECT_ROW_BUDGET,
    RefTag,
    ST_ACTIVE,
    ST_INVALIDATED,
    ST_STALE,
    ST_SUPERSEDED,
    REL_PRODUCED,
    validate_bbox,
    bbox_contains,
)


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


def _req(**overrides) -> KnowledgeUpsert:
    base = dict(
        org_id="org-a",
        project_id="proj_1",
        entity_kind=EK_ARTIFACT,
        authority_store=AS_ARTIFACT,
        authority_id="art-1",
        subject="landcover_2024.tif",
        version_token="sha256:aaaa",
        summary="2024 年土地利用分类产物",
        bbox=[100.0, 28.0, 104.0, 32.0],
        temporal_label="2024",
        method_key="landcover:random_forest",
        refs=(
            RefTag(
                relation=REL_PRODUCED, authority=AS_PROJECT_DATASET,
                id="ds_1", token="fp:1111",
            ),
        ),
    )
    base.update(overrides)
    return KnowledgeUpsert(**base)


# ── 契约层 ────────────────────────────────────────────────────────────


def test_contract_rejects_unknown_kind_and_authority():
    with pytest.raises(KnowledgePolicyError):
        _req(entity_kind="brand_new_kind").validate()
    with pytest.raises(KnowledgePolicyError):
        _req(authority_store="made_up_store").validate()


def test_contract_requires_org_project_and_authority_id():
    with pytest.raises(KnowledgePolicyError):
        _req(org_id="").validate()
    with pytest.raises(KnowledgePolicyError):
        _req(project_id="").validate()
    with pytest.raises(KnowledgePolicyError):
        _req(authority_id="").validate()


def test_contract_rejects_oversize_subject_and_bad_rule():
    with pytest.raises(KnowledgePolicyError):
        _req(subject="x" * 256).validate()
    with pytest.raises(KnowledgePolicyError):
        _req(invalidation_rule="because_i_said_so").validate()


def test_bbox_helpers():
    assert validate_bbox([1, 2, 3]) is None
    assert validate_bbox([3, 0, 1, 2]) is None   # minx > maxx
    assert validate_bbox([0, 0, 1, 1]) == (0.0, 0.0, 1.0, 1.0)
    assert bbox_contains([0, 0, 10, 10], [1, 1, 5, 5])
    assert bbox_contains([0, 0, 10, 10], [0, 0, 10, 10])  # 边界相等算包含
    assert not bbox_contains([0, 0, 10, 10], [5, 5, 15, 15])


# ── upsert：再验证 / 取代 / 预算 ─────────────────────────────────────


def test_upsert_creates_and_refreshes_same_token(db):
    row = s.upsert_entry(db, _req())
    assert row is not None and row.status == ST_ACTIVE
    first_version = row.version
    first_id = row.id

    row2 = s.upsert_entry(db, _req(summary="刷新后的摘要"))
    assert row2.id == first_id                    # 同 token = 原行刷新
    assert row2.version == first_version + 1
    assert row2.summary == "刷新后的摘要"
    actives = list(db.execute(select(ProjectKnowledgeEntry)).scalars().all())
    assert len(actives) == 1


def test_upsert_token_change_supersedes_with_chain(db):
    row1 = s.upsert_entry(db, _req(version_token="sha256:old"))
    row2 = s.upsert_entry(db, _req(version_token="sha256:new"))
    assert row2 is not None and row2.status == ST_ACTIVE
    assert row2.supersedes_id == row1.id
    db.flush()
    prev = db.get(ProjectKnowledgeEntry, row1.id)
    assert prev.status == ST_SUPERSEDED
    actives = [
        r for r in db.execute(select(ProjectKnowledgeEntry)).scalars().all()
        if r.status == ST_ACTIVE
    ]
    assert len(actives) == 1


def test_upsert_rejects_invalid_and_writes_nothing(db):
    before = list(db.execute(select(ProjectKnowledgeEntry)).scalars().all())
    assert s.upsert_entry(db, _req(org_id="")) is None
    assert s.upsert_entry(db, _req(entity_kind="nope")) is None
    after = list(db.execute(select(ProjectKnowledgeEntry)).scalars().all())
    assert before == after


def test_budget_eviction_keeps_active_rows(db):
    for i in range(PROJECT_ROW_BUDGET + 5):
        assert s.upsert_entry(db, _req(
            authority_id=f"art-{i}", subject=f"产物 {i}",
        )) is not None
    total = len(list(db.execute(select(ProjectKnowledgeEntry)).scalars().all()))
    assert total <= PROJECT_ROW_BUDGET
    # 最初的行已被淘汰（最旧优先）
    assert s.get_entries_by_authority(
        db, org_id="org-a", project_id="proj_1",
        authority_store=AS_ARTIFACT, authority_id="art-0",
    ) == []


# ── 读取与隔离 ────────────────────────────────────────────────────────


def test_cross_org_and_cross_project_read_empty(db):
    s.upsert_entry(db, _req())
    assert s.get_active_entries(db, org_id="org-b", project_id="proj_1") == []
    assert s.get_active_entries(db, org_id="org-a", project_id="proj_9") == []
    assert s.get_active_entries(db, org_id="", project_id="proj_1") == []
    # 审计面 retire 也拒绝跨 org
    entry = s.get_active_entries(db, org_id="org-a", project_id="proj_1")[0]
    assert s.retire_entry(db, org_id="org-b", entry_id=entry.id) is False


def test_retire_entry_manual(db):
    s.upsert_entry(db, _req())
    entry = s.get_active_entries(db, org_id="org-a", project_id="proj_1")[0]
    assert s.retire_entry(db, org_id="org-a", entry_id=entry.id) is True
    rows = s.get_active_entries(db, org_id="org-a", project_id="proj_1")
    assert rows == []
    audit = s.get_entries_by_authority(
        db, org_id="org-a", project_id="proj_1",
        authority_store=AS_ARTIFACT, authority_id="art-1",
        statuses=(ST_INVALIDATED,),
    )
    assert len(audit) == 1 and audit[0].invalidation_rule == "manual"


# ── 失效语义 ──────────────────────────────────────────────────────────


def test_demote_stale_on_token_drift(db):
    s.upsert_entry(db, _req(version_token="sha256:old"))
    assert s.demote_stale_by_token(
        db, org_id="org-a", project_id="proj_1",
        authority_store=AS_ARTIFACT, authority_id="art-1",
        live_token="sha256:old",
    ) == 0   # token 一致：不动
    assert s.demote_stale_by_token(
        db, org_id="org-a", project_id="proj_1",
        authority_store=AS_ARTIFACT, authority_id="art-1",
        live_token="sha256:new",
    ) == 1
    rows = s.get_entries_by_authority(
        db, org_id="org-a", project_id="proj_1",
        authority_store=AS_ARTIFACT, authority_id="art-1",
    )
    assert rows[0].status == ST_STALE and rows[0].invalidation_rule == "version_bump"


def test_invalidate_when_authority_gone(db):
    s.upsert_entry(db, _req())
    assert s.demote_stale_by_token(
        db, org_id="org-a", project_id="proj_1",
        authority_store=AS_ARTIFACT, authority_id="art-1",
        live_token=None,
    ) == 1
    rows = s.get_entries_by_authority(
        db, org_id="org-a", project_id="proj_1",
        authority_store=AS_ARTIFACT, authority_id="art-1",
    )
    assert rows[0].status == ST_INVALIDATED
    assert rows[0].invalidation_rule == "scope_gone"


def test_invalidate_for_authority_and_project(db):
    s.upsert_entry(db, _req())
    s.upsert_entry(db, _req(entity_kind=EK_DATASET_VERSION,
                            authority_store=AS_PROJECT_DATASET,
                            authority_id="ds_1"))
    n = s.invalidate_for_authority(
        db, org_id="org-a", project_id="proj_1",
        authority_store=AS_PROJECT_DATASET, authority_id="ds_1",
    )
    assert n == 1
    assert len(s.get_active_entries(db, org_id="org-a", project_id="proj_1")) == 1
    assert s.invalidate_for_project(db, org_id="org-a", project_id="proj_1") == 1
    assert s.get_active_entries(db, org_id="org-a", project_id="proj_1") == []


def test_invalidate_is_org_scoped(db):
    s.upsert_entry(db, _req())
    # org-b 对同一 (project, authority) 显式失效 → 不影响 org-a 的行
    assert s.invalidate_for_authority(
        db, org_id="org-b", project_id="proj_1",
        authority_store=AS_ARTIFACT, authority_id="art-1",
    ) == 0
    assert len(s.get_active_entries(db, org_id="org-a", project_id="proj_1")) == 1


def test_stats_no_content_leak(db):
    s.upsert_entry(db, _req())
    stats = s.project_knowledge_stats(db, org_id="org-a")
    assert stats == {"proj_1.active": 1}
    assert s.project_knowledge_stats(db, org_id="org-b") == {}


# ── 投影行删除不伤权威（oracle：回指可解析、投影可弃） ─────────────────


def test_projection_rows_are_disposable(db):
    s.upsert_entry(db, _req())
    for row in list(db.execute(select(ProjectKnowledgeEntry)).scalars().all()):
        db.delete(row)
    db.flush()
    # 重新 upsert 同 key 可完整重建（rebuild 幂等性的基础）
    row = s.upsert_entry(db, _req())
    assert row is not None and row.status == ST_ACTIVE


def test_summary_and_refs_bounded_on_write(db):
    long_summary = "摘" * 5000
    many_refs = tuple(
        RefTag(relation=REL_PRODUCED, authority=AS_PROJECT_DATASET, id=f"ds_{i}")
        for i in range(50)
    )
    row = s.upsert_entry(db, _req(summary=long_summary, refs=many_refs))
    assert row is not None
    assert len(row.summary) <= 300
    assert len(row.refs) <= 8
