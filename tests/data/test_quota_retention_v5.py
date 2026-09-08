"""Wave 12 — Per-project quota / retention age policy / GC protection parity.

覆盖 brief 要求：
- quota：限额内晋升 ok；超限 → ``quota_exceeded`` 且零字节落盘；默认无限额
  行为不变；去重命中不收费；超限项目 clone 仍可用（零新增字节）；
  用量口径精确（distinct-sha 物理字节 + 账本字节 + head 指针兜底）；
- retention：超龄 unpinned 修订入计划；pinned / head / workspace 层 /
  血缘根保留；plan/execute parity（同集合）；宽限期 honored；
  默认 keep-forever；
- **保护 parity 矩阵（旗舰）**：pinned + workspace 层 + 血缘根 + 纯垃圾
  并存的场景库上，跑全部 planner（session GC / promotion-store GC /
  retention cleanup）→ 各 planner 保护同一受保护集合、只有垃圾不同 →
  逐一 execute → 受保护对象全部幸存（快照文件亦然）；
- endpoints：data-usage 真实数字；execute 无 confirm → 400；外部项目 → 404；
- 孤儿修订行清理有界。
"""
import asyncio
import hashlib
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

import app.models.db_model  # noqa: F401 — 在 fixture 之前把 ORM 模型注册进 Base.metadata
import app.models.project  # noqa: F401
from app.core.database import Base, Engine, SessionLocal
from app.services.session_data import session_data_manager

_PROJECT_DOMAIN_TABLES = (
    "artifact_revisions",
    "map_products", "artifact_lineages", "artifacts",
    "workflow_runs", "workflow_revisions", "workflows",
    "project_datasets", "carto_project_facts", "projects",
)

_OWNER = "u_w12"
_FC = {"type": "FeatureCollection", "features": []}


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


@pytest.fixture(autouse=True)
def _default_env(monkeypatch):
    """默认部署基线：无 quota / keep-forever（每用例显式覆写自己需要的键）。"""
    for key in ("WEBGIS_PROJECT_ARTIFACT_MAX_BYTES",
                "WEBGIS_PROJECT_ARTIFACT_MAX_COUNT",
                "WEBGIS_PROJECT_ARTIFACT_MAX_REVISION_BYTES",
                "WEBGIS_RETENTION_MAX_AGE_DAYS",
                "WEBGIS_RETENTION_GRACE_HOURS"):
        monkeypatch.delenv(key, raising=False)


# ── fixture helpers ───────────────────────────────────────────────────────


def _mk_project(project_id, owner_id=_OWNER):
    from app.models.db_model import User
    from app.models.project import Project

    with SessionLocal() as s:
        s.merge(User(id=owner_id, username=owner_id, email=f"{owner_id}@example.com",
                     password_hash="x", role="viewer", is_active=True))
        s.add(Project(id=project_id, name="p", owner_id=owner_id))
        s.commit()


def _mk_artifact(project_id, artifact_id, metadata_json=None):
    from app.models.project import Artifact

    with SessionLocal() as s:
        s.add(Artifact(id=artifact_id, project_id=project_id, name="out",
                       artifact_type="vector", metadata_json=metadata_json or {}))
        s.commit()


def _mk_blob(store, name, *, age_hours=None):
    """blob 落盘（可选 backdate mtime）；返回 (key, location, bytes)。"""
    data = f"blob-{name}-{uuid.uuid4().hex[:8]}".encode()
    key = hashlib.sha256(data).hexdigest()
    store.put_blob(key, data, "json")
    location = f"{key[:4]}/{key}.json"
    if age_hours is not None:
        old = time.time() - float(age_hours) * 3600.0
        os.utime(store.root / location, (old, old))
    return key, location, len(data)


_rev_counter = [0]


def _mk_revision(artifact_id, key, location, *, byte_size=10, age_days=None,
                 pinned=False, revision_no=None):
    """直接插一条修订行（可 backdate created_at / 置 pin）。"""
    from app.models.project import ArtifactRevision

    _rev_counter[0] += 1
    created = (
        datetime.now(timezone.utc) - timedelta(days=float(age_days or 0))
    )
    with SessionLocal() as s:
        s.add(ArtifactRevision(
            id=str(uuid.uuid4()), artifact_id=artifact_id,
            revision_no=revision_no or _rev_counter[0],
            content_sha256=key, content_location=location,
            content_type="json", byte_size=byte_size,
            pinned_at=datetime.now(timezone.utc) if pinned else None,
            created_at=created,
        ))
        s.commit()


def _mk_run_with_artifact(project_id, *, storage_ref):
    """project/workflow/run/artifact 行（run_manifest 绑定 artifact）。"""
    from app.models.project import Artifact, Workflow, WorkflowRun

    wf_id, run_id, art_id = (
        f"wf_{uuid.uuid4().hex[:8]}", f"run_{uuid.uuid4().hex[:8]}",
        f"art_{uuid.uuid4().hex[:8]}",
    )
    manifest = {"artifacts": [{"id": art_id, "producing_step": "s1"}]}
    with SessionLocal() as s:
        s.add(Workflow(id=wf_id, project_id=project_id, name="wf", version=1))
        s.add(WorkflowRun(
            id=run_id, workflow_id=wf_id, workflow_version=1, project_id=project_id,
            status="completed", run_manifest=manifest,
        ))
        s.add(Artifact(
            id=art_id, project_id=project_id, name="out", artifact_type="vector",
            metadata_json={"step_id": "s1"}, storage_ref=storage_ref,
        ))
        s.commit()
    return run_id, art_id


# ── 1. Quota：用量口径 / 限额内 / 超限 / 默认无限 / 去重 / clone ───────────


def test_usage_accounting_exactness(db):
    """distinct-sha 物理字节 + 账本总字节 + 无修订行 head 指针兜底。"""
    from app.services.artifact_revisions import project_quota_usage

    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)
    art1, art2, art3 = (f"art_{uuid.uuid4().hex[:8]}" for _ in range(3))
    _mk_artifact(pid, art1)
    _mk_artifact(pid, art2)
    # art3：旧晋升行 —— 只有 metadata head 指针，无修订行 → 兜底口径
    _mk_artifact(pid, art3, metadata_json={
        "content_status": "promoted",
        "content_location": "cafe/" + "0" * 62 + ".json",
        "content_summary": {"payload_bytes": 77},
    })
    key1, loc1 = "a" * 64, "aaaa/" + "a" * 64 + ".json"
    key2, loc2 = "b" * 64, "bbbb/" + "b" * 64 + ".json"
    _mk_revision(art1, key1, loc1, byte_size=100)
    _mk_revision(art2, key2, loc2, byte_size=200)
    # art2 再记一条同 sha（CAS 去重：物理零新增，账本 +100）
    _mk_revision(art2, key1, loc1, byte_size=100)

    with SessionLocal() as s:
        usage = project_quota_usage(s, pid)
    assert usage["bytes"] == 377, "distinct sha 物理字节 300 + head 指针兜底 77"
    assert usage["artifact_count"] == 3
    assert usage["revision_bytes"] == 400, "账本口径不去重（含重复修订行）"
    assert usage["revision_bytes_by_artifact"][art2] == 300
    assert usage["max_per_artifact_revision_bytes"] == 300
    # 兜底只对「无修订行 + 有 head 指针」的行生效；有修订行的 artifact
    # 不重复计兜底（art1 已有修订 → 加一条同 sha 小修订不改变字节口径）
    _mk_revision(art1, key2, loc2, byte_size=5)
    with SessionLocal() as s:
        usage2 = project_quota_usage(s, pid)
    assert usage2["bytes"] == 377, "sha 已按 max 计 200，小修订不改变 distinct 口径"
    # 兜底不虚构：无 content_location 的 metadata 行不计
    with SessionLocal() as s:
        from app.models.project import Artifact

        art = s.execute(select(Artifact).where(Artifact.id == art3)).scalars().first()
        art.metadata_json = {"content_status": "session_expired"}
        s.commit()
        usage3 = project_quota_usage(s, pid)
    assert usage3["bytes"] == 300, "兜底移除后仅剩 distinct sha 字节（100 + 200）"


def test_promotion_under_limit_ok_and_over_limit_refuses_bytes(
    db, monkeypatch, tmp_path
):
    from app.models.project import Artifact, WorkflowRun
    from app.services import project_artifact_promotion as pap
    from app.services.artifact_revisions import list_revisions
    from app.services.durable_blob_store import get_filesystem_blob_store

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    store = get_filesystem_blob_store()
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    payload_small = {"type": "FeatureCollection", "tag": "tiny", "features": []}
    ref_small = asyncio.run(
        session_data_manager.store("sess_w12a", payload_small, prefix="geojson"))
    run_id, art_id = _mk_run_with_artifact(project_id, storage_ref=ref_small)

    # 限额内：promoted，行为与 W12 之前一致
    monkeypatch.setenv("WEBGIS_PROJECT_ARTIFACT_MAX_BYTES", "1000000")
    with SessionLocal() as s:
        run = s.execute(select(WorkflowRun).where(
            WorkflowRun.id == run_id)).scalars().first()
        report = asyncio.run(pap.promote_run_artifacts(
            s, run, session_id="sess_w12a", project_id=project_id))
    assert report[0]["status"] == "promoted"
    with SessionLocal() as s:
        art = s.execute(select(Artifact).where(
            Artifact.id == art_id)).scalars().first()
        assert art.metadata_json["content_status"] == "promoted"

    # 超限：新 artifact + 大载荷 + 小限额 → quota_exceeded，零字节落盘
    monkeypatch.setenv("WEBGIS_PROJECT_ARTIFACT_MAX_BYTES", "10")
    payload_big = {"type": "FeatureCollection", "tag": "x" * 4096, "features": []}
    ref_big = asyncio.run(
        session_data_manager.store("sess_w12a", payload_big, prefix="geojson"))
    run_id2, art_id2 = _mk_run_with_artifact(project_id, storage_ref=ref_big)
    with SessionLocal() as s:
        run2 = s.execute(select(WorkflowRun).where(
            WorkflowRun.id == run_id2)).scalars().first()
        report2 = asyncio.run(pap.promote_run_artifacts(
            s, run2, session_id="sess_w12a", project_id=project_id))
    assert report2[0]["status"] == "quota_exceeded"
    blob = pap.canonical_dumps(payload_big)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    assert store.exists(digest) is False, "超限晋升绝不写 payload 字节"
    with SessionLocal() as s:
        art2 = s.execute(select(Artifact).where(
            Artifact.id == art_id2)).scalars().first()
        assert art2.metadata_json["content_status"] == "quota_exceeded"
        meta_quota = art2.metadata_json["quota"]
        assert meta_quota["limit_bytes"] == 10
        assert meta_quota["incoming_bytes"] == len(blob.encode("utf-8"))
        assert len(meta_quota) <= 8, "typed details 有界"
        # 行存活（metadata-only），无修订行
        assert s.execute(select(Artifact).where(
            Artifact.id == art_id2)).scalars().first() is not None
        assert list_revisions(s, art_id2) == []


def test_promotion_unlimited_default_unchanged(db, monkeypatch, tmp_path):
    from app.models.project import WorkflowRun
    from app.services import project_artifact_promotion as pap
    from app.services.session_data import session_data_manager

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    ref = asyncio.run(session_data_manager.store("sess_w12b", _FC, prefix="geojson"))
    run_id, _art = _mk_run_with_artifact(project_id, storage_ref=ref)
    with SessionLocal() as s:
        run = s.execute(select(WorkflowRun).where(
            WorkflowRun.id == run_id)).scalars().first()
        report = asyncio.run(pap.promote_run_artifacts(
            s, run, session_id="sess_w12b", project_id=project_id))
    assert report[0]["status"] == "promoted", "默认（无 env）= 无限额，行为不变"


def test_promotion_dedup_hit_never_charged(db, monkeypatch, tmp_path):
    """同内容已在 BlobStore（去重命中）→ 零新增字节 → 超限也不拒。"""
    from app.models.project import WorkflowRun
    from app.services import project_artifact_promotion as pap
    from app.services.durable_blob_store import get_filesystem_blob_store
    from app.services.session_data import session_data_manager

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    store = get_filesystem_blob_store()
    project_id = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(project_id)
    payload = {"type": "FeatureCollection", "features": [], "dedup": True}
    blob = pap.canonical_dumps(payload)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    store.put_blob(digest, blob.encode("utf-8"), "json")  # 内容已在场

    monkeypatch.setenv("WEBGIS_PROJECT_ARTIFACT_MAX_BYTES", "10")
    ref = asyncio.run(session_data_manager.store("sess_w12c", payload, prefix="geojson"))
    run_id, _art = _mk_run_with_artifact(project_id, storage_ref=ref)
    with SessionLocal() as s:
        run = s.execute(select(WorkflowRun).where(
            WorkflowRun.id == run_id)).scalars().first()
        report = asyncio.run(pap.promote_run_artifacts(
            s, run, session_id="sess_w12c", project_id=project_id))
    assert report[0]["status"] == "promoted", "去重命中不产生新字节，不收费"


def test_clone_over_quota_project_allowed_no_new_bytes(db, monkeypatch, tmp_path):
    from app.models.project import Artifact
    from app.services import project_artifact_promotion as pap
    from app.services.artifact_revisions import (
        clone_artifact,
        project_quota_usage,
    )
    from app.services.durable_blob_store import get_filesystem_blob_store

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    store = get_filesystem_blob_store()
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(pid, art_id)
    key, loc, size = _mk_blob(store, "cloneable", age_hours=48)
    _mk_revision(art_id, key, loc, byte_size=size)
    with SessionLocal() as s:
        art = s.execute(select(Artifact).where(
            Artifact.id == art_id)).scalars().first()
        art.metadata_json = {"content_status": "promoted",
                             "content_location": loc,
                             "content_payload_sha256": key}
        s.commit()

    monkeypatch.setenv("WEBGIS_PROJECT_ARTIFACT_MAX_BYTES", "10")  # 已超限
    with SessionLocal() as s:
        before = project_quota_usage(s, pid)
        assert before["bytes"] >= size
        blobs_before = sorted(p.name for p in tmp_path.rglob("*.json"))
        clone = clone_artifact(s, project_id=pid, artifact_id=art_id,
                               user_id=_OWNER)
        assert clone is not None, "clone 复用内容（零新字节）→ 超限项目仍可用"
    blobs_after = sorted(p.name for p in tmp_path.rglob("*.json"))
    assert blobs_after == blobs_before, "clone 绝不写新字节（指针复用同 blob）"
    with SessionLocal() as s:
        after = project_quota_usage(s, pid)
    assert after["bytes"] == before["bytes"], "clone 不收字节费（distinct-sha 口径）"
    assert after["artifact_count"] == before["artifact_count"] + 1
    assert after["revision_bytes"] == before["revision_bytes"] + size
    assert store.exists(key) is True


def test_per_artifact_revision_byte_limit(db, monkeypatch):
    from app.services.data_lifecycle.quota import check_quota

    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(pid, art_id)
    _mk_revision(art_id, "c" * 64, "cccc/" + "c" * 64 + ".json", byte_size=60)
    _mk_revision(art_id, "d" * 64, "dddd/" + "d" * 64 + ".json", byte_size=60)

    monkeypatch.setenv("WEBGIS_PROJECT_ARTIFACT_MAX_REVISION_BYTES", "100")
    with SessionLocal() as s:
        decision = check_quota(s, pid, 0, artifact_id=art_id)
        assert decision.allowed is False
        assert decision.reason == "per-artifact revision byte quota exceeded"
        # 其它 artifact（无修订历史）不受该 artifact 的历史拖累
        other = f"art_{uuid.uuid4().hex[:8]}"
        _mk_artifact(pid, other)
        decision2 = check_quota(s, pid, 10, artifact_id=other)
        assert decision2.allowed is True


# ── 2. Retention：候选 / 保护 / parity / 宽限 / 默认 keep-forever ──────────


def _retention_env(monkeypatch, *, max_age_days=1, grace_hours=1):
    monkeypatch.setenv("WEBGIS_RETENTION_MAX_AGE_DAYS", str(max_age_days))
    monkeypatch.setenv("WEBGIS_RETENTION_GRACE_HOURS", str(grace_hours))


def test_retention_plan_execute_parity_and_protections(db, monkeypatch, tmp_path):
    from app.services import project_artifact_promotion as pap
    from app.services.data_lifecycle.quota import (
        execute_retention_cleanup,
        plan_retention_cleanup,
    )
    from app.services.durable_blob_store import get_filesystem_blob_store

    _retention_env(monkeypatch)
    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    store = get_filesystem_blob_store()
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)

    art_victim = f"art_{uuid.uuid4().hex[:8]}"
    art_pinned = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(pid, art_victim)
    _mk_artifact(pid, art_pinned)
    key_v, loc_v, _ = _mk_blob(store, "victim", age_hours=48)
    key_p, loc_p, _ = _mk_blob(store, "pinned", age_hours=48)
    key_h, loc_h, _ = _mk_blob(store, "head", age_hours=48)
    _mk_revision(art_victim, key_v, loc_v, age_days=10, revision_no=1)
    _mk_revision(art_victim, key_h, loc_h, revision_no=2)  # head（新鲜）
    _mk_revision(art_pinned, key_p, loc_p, age_days=10, pinned=True, revision_no=1)
    _mk_revision(art_pinned, "e" * 64, "eeee/" + "e" * 64 + ".json", revision_no=2)

    with SessionLocal() as s:
        plan = plan_retention_cleanup(s, pid)
    cand = plan["candidate_revisions"]
    assert len(cand) == 1, "只有超龄 unpinned 非 head 修订入计划"
    assert cand[0]["content_sha256"] == key_v
    assert plan["protected_counts"].get("pinned") == 1, "pin 保护在计划中如实披露"
    assert [b["key"] for b in plan["candidate_blobs"]] == [key_v]
    # dry-run：不删任何行 / 字节
    assert store.exists(key_v) and store.exists(key_p)

    with SessionLocal() as s:
        exec_result = execute_retention_cleanup(plan)
    assert exec_result["deleted_revisions"] == [cand[0]["revision_id"]]
    assert exec_result["deleted_blobs"] == [key_v], "零存活引用 + 超宽限 → blob 删除"
    assert exec_result["failed"] == []
    # parity：plan == execute（无交错写入）
    assert plan["candidate_revision_count"] == len(exec_result["deleted_revisions"])
    with SessionLocal() as s:
        from app.models.project import ArtifactRevision

        remaining = s.execute(select(ArtifactRevision)).scalars().all()
        ids = {r.id for r in remaining}
        assert cand[0]["revision_id"] not in ids
        pinned_row = s.execute(
            select(ArtifactRevision).where(
                ArtifactRevision.content_sha256 == key_p)).scalars().first()
        assert pinned_row is not None and pinned_row.pinned_at is not None
    assert store.exists(key_v) is False
    assert store.exists(key_p) is True, "pinned 修订引用的 blob 永不删"


def test_retention_grace_period_honored(db, monkeypatch, tmp_path):
    """宽限期 honored（双侧）：plan 阶段 fresh blob 不是候选；plan 后被
    touch 的新鲜 blob 在 execute 复检时被宽限保护（新鲜 DB/文件状态）。"""
    from app.services import project_artifact_promotion as pap
    from app.services.data_lifecycle.quota import (
        execute_retention_cleanup,
        plan_retention_cleanup,
    )
    from app.services.durable_blob_store import get_filesystem_blob_store

    _retention_env(monkeypatch, grace_hours=168)  # 7d 宽限：fresh blob 受保护
    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    store = get_filesystem_blob_store()
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(pid, art_id)
    key, loc, _ = _mk_blob(store, "fresh-blob")  # mtime = now
    _mk_revision(art_id, key, loc, age_days=30, revision_no=1)
    _mk_revision(art_id, "f" * 64, "ffff/" + "f" * 64 + ".json", revision_no=2)

    with SessionLocal() as s:
        plan = plan_retention_cleanup(s, pid)
    assert plan["candidate_revision_count"] == 1
    assert plan["candidate_blob_count"] == 0, "blob 在宽限期内 → 不是删除候选"
    assert (plan.get("blob_protected_counts") or {}).get("grace period") == 1
    result = execute_retention_cleanup(plan)
    assert len(result["deleted_revisions"]) == 1
    assert result["deleted_blobs"] == []
    assert store.exists(key) is True

    # 交错写入复检：plan 时 blob 超龄（候选；400h > 7d 宽限），plan →
    # execute 之间被 touch（例如内容被重物化）→ execute 以新鲜 mtime 复检
    # → 宽限保护，不删。
    key2, loc2, _ = _mk_blob(store, "touched", age_hours=400)
    art2 = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(pid, art2)
    _mk_revision(art2, key2, loc2, age_days=30, revision_no=1)
    _mk_revision(art2, "e" * 64, "eeee/" + "e" * 64 + ".json", revision_no=2)
    with SessionLocal() as s:
        plan2 = plan_retention_cleanup(s, pid)
    assert [b["key"] for b in plan2["candidate_blobs"]] == [key2]
    now = time.time()
    os.utime(store.root / loc2, (now, now))  # 交错写入：touch
    result2 = execute_retention_cleanup(plan2)
    assert len(result2["deleted_revisions"]) == 1
    assert result2["deleted_blobs"] == []
    assert any(s.get("reason") == "grace period"
               for s in result2["skipped_protected"])
    assert store.exists(key2) is True


def test_retention_keep_forever_default(db, tmp_path, monkeypatch):
    from app.services import project_artifact_promotion as pap
    from app.services.data_lifecycle.quota import (
        execute_retention_cleanup,
        plan_retention_cleanup,
    )

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(pid, art_id)
    _mk_revision(art_id, "0" * 64, "0000/" + "0" * 64 + ".json", age_days=3650)
    with SessionLocal() as s:
        plan = plan_retention_cleanup(s, pid)
    assert plan["disabled"] is True, "默认（无 env）= keep-forever"
    assert plan["candidate_revision_count"] == 0
    assert execute_retention_cleanup(plan)["deleted_revisions"] == []


def test_retention_workspace_tier_and_lineage_root_protected(
    db, monkeypatch, tmp_path
):
    from app.models.project import ArtifactLineage
    from app.services import project_artifact_promotion as pap
    from app.services.data_lifecycle.quota import plan_retention_cleanup
    from app.services.durable_blob_store import get_filesystem_blob_store

    _retention_env(monkeypatch)
    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    store = get_filesystem_blob_store()
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)
    art_ws = f"art_{uuid.uuid4().hex[:8]}"
    art_root = f"art_{uuid.uuid4().hex[:8]}"
    art_child = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(pid, art_ws, metadata_json={"persistence_tier": "workspace"})
    _mk_artifact(pid, art_root)
    _mk_artifact(pid, art_child)
    with SessionLocal() as s:
        s.add(ArtifactLineage(
            id=str(uuid.uuid4()), artifact_id=art_child,
            parent_artifact_id=art_root, producing_tool="t",
        ))
        s.commit()
    key_ws, loc_ws, _ = _mk_blob(store, "ws-tier", age_hours=48)
    key_root, loc_root, _ = _mk_blob(store, "lineage-root", age_hours=48)
    _mk_revision(art_ws, key_ws, loc_ws, age_days=10, revision_no=1)
    _mk_revision(art_ws, "9" * 64, "9999/" + "9" * 64 + ".json", revision_no=2)
    _mk_revision(art_root, key_root, loc_root, age_days=10, revision_no=1)
    _mk_revision(art_root, "8" * 64, "8888/" + "8" * 64 + ".json", revision_no=2)

    with SessionLocal() as s:
        plan = plan_retention_cleanup(s, pid)
    assert plan["candidate_revision_count"] == 0, "workspace 层 + 血缘根全保护"
    assert plan["protected_counts"].get("persistence_tier=workspace") == 1
    assert plan["protected_counts"].get("lineage root (has downstream)") == 1
    assert store.exists(key_ws) and store.exists(key_root)


# ── 3. 旗舰：保护 parity 矩阵（所有 planner 同保护集合，只有垃圾不同）──────


def test_protection_parity_matrix_across_all_gc_paths(db, monkeypatch, tmp_path):
    """场景库：pinned + workspace 层 + 血缘根 + 纯垃圾（DB 修订/blob/会话
    ref/快照并存）。跑全部 planner → 各 planner 保护同一受保护集合、
    只有垃圾候选不同 → 逐一 execute → 受保护对象全部幸存。"""
    from app.models.project import ArtifactRevision
    from app.services import project_artifact_promotion as pap
    from app.services.artifact_lifecycle import (
        execute_promotion_store_gc_sync,
        plan_promotion_store_gc_sync,
    )
    from app.services.artifact_registry import (
        register_artifact,
        update_record_metadata,
    )
    from app.services.data_lifecycle.gc import execute_session_gc, plan_session_gc
    from app.services.data_lifecycle.quota import (
        execute_retention_cleanup,
        plan_retention_cleanup,
    )
    from app.services.durable_blob_store import get_filesystem_blob_store
    from app.services.session_data import session_data_manager

    _retention_env(monkeypatch, max_age_days=1, grace_hours=1)
    # blob 根与 DATA_DIR 分离：快照 manifest 与 blob 互不落入对方扫描域
    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path / "blobs")
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    store = get_filesystem_blob_store()

    # ── durable 层：5 个 artifact + 若干 blob ────────────────────────────
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)
    art_pin, art_ws, art_root, art_garbage, art_headptr = (
        f"art_{uuid.uuid4().hex[:8]}" for _ in range(5))
    _mk_artifact(pid, art_pin)
    _mk_artifact(pid, art_ws, metadata_json={"persistence_tier": "workspace"})
    _mk_artifact(pid, art_root)
    _mk_artifact(pid, art_garbage)
    # head 指针 blob：无修订行，仅 Artifact.metadata_json.content_location
    key_headptr, loc_headptr, _ = _mk_blob(store, "headptr", age_hours=48)
    _mk_artifact(pid, art_headptr, metadata_json={
        "content_status": "promoted", "content_location": loc_headptr})

    key_pin1, loc_pin1, _ = _mk_blob(store, "pin1", age_hours=48)
    key_pin2, loc_pin2, _ = _mk_blob(store, "pin2", age_hours=48)
    key_ws1, loc_ws1, _ = _mk_blob(store, "ws1", age_hours=48)
    key_ws2, loc_ws2, _ = _mk_blob(store, "ws2", age_hours=48)
    key_root1, loc_root1, _ = _mk_blob(store, "root1", age_hours=48)
    key_root2, loc_root2, _ = _mk_blob(store, "root2", age_hours=48)
    key_garb1, loc_garb1, _ = _mk_blob(store, "garb1", age_hours=48)
    key_garb2, loc_garb2, _ = _mk_blob(store, "garb2", age_hours=48)
    key_orphan, loc_orphan, _ = _mk_blob(store, "orphan", age_hours=48)
    key_fresh, loc_fresh, _ = _mk_blob(store, "fresh-orphan")  # 无主但新鲜

    # 血缘根：art_root 是 art_pin 的 parent（DB 侧血缘下游）
    from app.models.project import ArtifactLineage

    with SessionLocal() as s:
        s.add(ArtifactLineage(
            id=str(uuid.uuid4()), artifact_id=art_pin,
            parent_artifact_id=art_root, producing_tool="t"))
        s.commit()

    # 修订行：art_pin(r1 pinned aged, r2 head) / art_ws(r1 aged tier 保护,
    # r2 head) / art_root(r1 aged 血缘根保护, r2 head) / art_garbage(r1 aged
    # → 唯一垃圾候选, r2 head)
    _mk_revision(art_pin, key_pin1, loc_pin1, age_days=10, pinned=True,
                 revision_no=1)
    _mk_revision(art_pin, key_pin2, loc_pin2, revision_no=2)
    _mk_revision(art_ws, key_ws1, loc_ws1, age_days=10, revision_no=1)
    _mk_revision(art_ws, key_ws2, loc_ws2, revision_no=2)
    _mk_revision(art_root, key_root1, loc_root1, age_days=10, revision_no=1)
    _mk_revision(art_root, key_root2, loc_root2, revision_no=2)
    _mk_revision(art_garbage, key_garb1, loc_garb1, age_days=10, revision_no=1)
    # 垃圾 artifact 的 head 也超龄 —— 验证 head 保护（保护 ≠ 删除候选）
    _mk_revision(art_garbage, key_garb2, loc_garb2, age_days=10, revision_no=2)
    with SessionLocal() as s:
        garbage_rev = s.execute(
            select(ArtifactRevision).where(
                ArtifactRevision.content_sha256 == key_garb1)
        ).scalars().first()
        garbage_rev_id = garbage_rev.id

    # ── 会话层：live / garbage / persistent 层 / 血缘根 ──────────────────
    sid = f"s_w12_{uuid.uuid4().hex[:6]}"
    ref_live = asyncio.run(
        session_data_manager.store(sid, _FC, prefix="geojson"))
    asyncio.run(register_artifact(sid, artifact_id=ref_live, producer_tool="t"))
    ref_garbage = asyncio.run(
        session_data_manager.store(sid, _FC, prefix="geojson"))
    asyncio.run(register_artifact(sid, artifact_id=ref_garbage, producer_tool="t"))
    asyncio.run(update_record_metadata(sid, ref_garbage, status="stale"))
    ref_persist = asyncio.run(
        session_data_manager.store(sid, _FC, prefix="geojson"))
    asyncio.run(register_artifact(
        sid, artifact_id=ref_persist, producer_tool="t",
        metadata={"persistence_tier": "persistent"}))
    asyncio.run(update_record_metadata(sid, ref_persist, status="superseded"))
    ref_root = asyncio.run(
        session_data_manager.store(sid, _FC, prefix="geojson"))
    asyncio.run(register_artifact(sid, artifact_id=ref_root, producer_tool="t"))
    ref_child = asyncio.run(
        session_data_manager.store(sid, _FC, prefix="geojson"))
    asyncio.run(register_artifact(
        sid, artifact_id=ref_child, producer_tool="t", inputs=[ref_root]))
    asyncio.run(update_record_metadata(sid, ref_root, status="stale"))

    # ── 快照（项目域 manifest，created_by 所有者）────────────────────────
    from app.services.workspace.snapshot import (
        WorkspaceSnapshot,
        _atomic_write_json,
        _project_snapshots_dir,
        get_workspace_snapshot_service,
    )

    snapshot_svc = get_workspace_snapshot_service()

    snapshot_id = f"ws-{uuid.uuid4().hex[:16]}"
    pdir = _project_snapshots_dir(pid)
    assert pdir is not None
    _atomic_write_json(pdir / f"{snapshot_id}.json", WorkspaceSnapshot(
        snapshot_id=snapshot_id, session_id=sid, project_id=pid,
        created_by=_OWNER, label="protected",
    ).model_dump(mode="json"))

    # ══ 阶段 1：全部 planner（dry-run，零删除）═════════════════════════
    gc_plan = asyncio.run(plan_session_gc(sid))
    promo_plan = plan_promotion_store_gc_sync(grace_hours=1.0)
    with SessionLocal() as s:
        retention_plan = plan_retention_cleanup(s, pid)

    gc_candidates = set(gc_plan.candidate_ids)
    gc_protected = {p.artifact_id for p in gc_plan.protected}
    promo_deletable = [d["key"] for d in promo_plan["deletable"]]
    retention_candidates = [c["revision_id"]
                            for c in retention_plan["candidate_revisions"]]
    retention_blobs = [b["key"] for b in retention_plan["candidate_blobs"]]

    # 垃圾各归其位：每个 planner 的候选只含自己域的垃圾
    assert gc_candidates == {ref_garbage}
    assert promo_deletable == [key_orphan], "blob 域唯一垃圾 = 无主超龄 blob"
    assert retention_candidates == [garbage_rev_id]
    assert retention_blobs == [key_garb1], "垃圾修订删除后 blob 才可回收"
    # fresh 无主 blob 的宽限保护在 promotion GC 域内披露（retention 的 blob
    # 阶段只看候选修订引用的 blob）
    assert (promo_plan["protected_counts"] or {}).get("grace period") == 1

    # 同一受保护集合：跨 planner 交集校验 —— 任何 planner 都不得把其它
    # planner 的受保护对象列入候选。
    protected_blob_keys = {key_pin1, key_pin2, key_ws1, key_ws2, key_root1,
                           key_root2, key_garb2, key_headptr, key_fresh}
    assert not (set(promo_deletable) & protected_blob_keys)
    assert not (set(retention_blobs) & protected_blob_keys)
    assert gc_protected >= {ref_persist, ref_root}
    assert ref_live not in gc_candidates and ref_live not in gc_protected

    # planner 域内保护理由逐一可见
    retention_protected = retention_plan["protected_counts"]
    assert retention_protected.get("pinned") == 1
    assert retention_protected.get("persistence_tier=workspace") == 1
    assert retention_protected.get("lineage root (has downstream)") == 1
    assert retention_protected.get("head revision (current content)") == 1, \
        "超龄 head 受 head 保护（保护 ≠ 候选）"
    promo_protected = promo_plan["protected_counts"]
    assert promo_protected.get("pinned") == 1
    assert promo_protected.get(
        "head pointer (Artifact.metadata_json.content_location)") == 1
    assert promo_protected.get("grace period") == 1

    # ══ 阶段 2：逐一 execute（无交错写入）→ parity ═════════════════════
    deleted_refs = asyncio.run(execute_session_gc(sid))
    promo_exec = execute_promotion_store_gc_sync(promo_plan)
    retention_exec = execute_retention_cleanup(retention_plan)

    assert deleted_refs == [ref_garbage] and set(deleted_refs) == gc_candidates
    assert promo_exec["deleted"] == promo_deletable
    assert promo_exec["failed"] == []
    assert retention_exec["deleted_revisions"] == retention_candidates
    assert retention_exec["deleted_blobs"] == retention_blobs

    # ══ 阶段 3：受保护对象全部幸存 ═══════════════════════════════════
    for key in protected_blob_keys:
        assert store.exists(key) is True, f"受保护 blob {key[:8]} 必须幸存"
    with SessionLocal() as s:
        surviving = s.execute(select(ArtifactRevision)).scalars().all()
        surviving_ids = {r.id for r in surviving}
        assert garbage_rev_id not in surviving_ids
        assert len(surviving) == 7, "8 条场景修订 − 1 条垃圾 = 7 条全幸存"
    for ref in (ref_live, ref_persist, ref_root, ref_child):
        payload = asyncio.run(session_data_manager.get(sid, ref))
        assert payload is not None, f"受保护会话 ref {ref} 必须幸存"
    # 快照文件幸存；外来 owner 的 delete_snapshot 拒绝（不删别人的资产）
    assert (pdir / f"{snapshot_id}.json").is_file()
    assert asyncio.run(snapshot_svc.delete_snapshot(
        sid, snapshot_id, project_id=pid, owner_id="u_attacker")) is None
    assert asyncio.run(snapshot_svc.delete_snapshot(
        sid, snapshot_id, project_id=pid, owner_id=_OWNER)) is not None
    assert not (pdir / f"{snapshot_id}.json").exists()


# ── 4. Endpoints ──────────────────────────────────────────────────────────


def test_data_usage_endpoint_real_numbers(db):
    from app.api.routes.project import get_project_data_usage
    from fastapi import HTTPException

    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)
    art1, art2 = f"art_{uuid.uuid4().hex[:8]}", f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(pid, art1)
    _mk_artifact(pid, art2)
    _mk_revision(art1, "1" * 64, "1111/" + "1" * 64 + ".json", byte_size=100)
    _mk_revision(art2, "2" * 64, "2222/" + "2" * 64 + ".json", byte_size=200)

    with SessionLocal() as s:
        body = get_project_data_usage(
            project_id=pid, db=s, user={"user_id": _OWNER})
    assert body["usage"] == {"bytes": 300, "artifact_count": 2,
                             "revision_bytes": 300}
    assert body["limits"]["max_bytes"] == 0, "默认无限额"
    assert body["quota"]["allowed"] is True
    assert body["retention"]["upcoming_candidates"] == 0, "keep-forever"

    # 外部身份（无权）→ 404 不泄露存在性
    with SessionLocal() as s:
        with pytest.raises(HTTPException) as ei:
            get_project_data_usage(
                project_id=pid, db=s, user={"user_id": "u_stranger"})
    assert ei.value.status_code == 404


def test_data_gc_endpoints_plan_and_confirm_gate(db, monkeypatch, tmp_path):
    """review round-1 SEC CRITICAL-2 更新后的端点契约：

    - plan/execute 都是**项目域**：plan 绝不返回全局 blob key/location
      （只给 sha 前缀 + 字节），也绝不再运行 GLOBAL promotion-store GC；
    - 本项目候选修订 + 其 blob 被清理，**其他项目的无主 blob 不动**
      （全局清扫只属于周期 sweep —— 服务级由 promotion GC 测试直接钉住）。
    """
    from app.api.routes.project import (
        execute_project_data_gc,
        plan_project_data_gc,
    )
    from app.services import project_artifact_promotion as pap
    from app.services.durable_blob_store import get_filesystem_blob_store
    from app.schemas.project_schema import DataGcExecuteRequest
    from fastapi import HTTPException

    _retention_env(monkeypatch, max_age_days=1, grace_hours=1)
    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path)
    store = get_filesystem_blob_store()
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(pid, art_id)
    key, loc, _ = _mk_blob(store, "endpoint-garbage", age_hours=48)
    _mk_revision(art_id, key, loc, age_days=10, revision_no=1)
    # 新鲜 head 修订（否则 r1 就是 head → 受 head 保护，不构成候选）
    _mk_revision(art_id, "3" * 64, "3333/" + "3" * 64 + ".json", revision_no=2)
    # 「其他项目的无主 blob」：无任何修订行引用 —— 旧实现里项目路由会把它
    # 当 GLOBAL promotion-GC 候选删掉；新契约下路由绝不动它。
    key_orphan, loc_orphan, _ = _mk_blob(store, "endpoint-orphan", age_hours=48)

    with SessionLocal() as s:
        plan_body = plan_project_data_gc(
            project_id=pid, db=s, user={"user_id": _OWNER})
    assert plan_body["scoped_to_project"] is True
    assert plan_body["retention"]["candidate_revision_count"] == 1
    # 项目域投影：只有本项目候选 blob，只给 sha 前缀 + 字节（key/location
    # 全局字符串绝不出现在 promotion_store_gc 段）。
    promo = plan_body["promotion_store_gc"]
    assert promo["deletable_count"] == 1
    assert promo["deletable"][0]["sha_prefix"] == key[:16]
    assert set(promo["deletable"][0].keys()) == {"sha_prefix", "bytes"}
    assert key_orphan not in str(promo)
    assert loc_orphan not in str(promo)
    # round-2 review SEC MAJOR-F3：retention 段同样是脱敏投影 —— 候选修订
    # 只有 {artifact_id, revision_no, age_days, byte_size}，候选 blob 只有
    # {sha_prefix(12), byte_size}；完整 sha / location / revision_id 绝不出现。
    ret = plan_body["retention"]
    assert len(ret["candidate_revisions"]) == 1
    assert set(ret["candidate_revisions"][0].keys()) == {
        "artifact_id", "revision_no", "age_days", "byte_size"}
    assert ret["candidate_revisions"][0]["age_days"] >= 1
    assert set(ret["candidate_blobs"][0].keys()) == {"sha_prefix", "byte_size"}
    assert ret["candidate_blobs"][0]["sha_prefix"] == key[:12]
    plan_str = str(plan_body)
    assert key not in plan_str, "完整 content sha 绝不出网"
    assert loc not in plan_str, "content_location 绝不出网"
    assert plan_body["retention"]["protection_scan_truncated"] is False

    # 无 confirm → 400（dry-run 纪律）
    with SessionLocal() as s:
        with pytest.raises(HTTPException) as ei:
            execute_project_data_gc(
                project_id=pid, data=DataGcExecuteRequest(confirm=False),
                db=s, user={"user_id": _OWNER})
    assert ei.value.status_code == 400
    # 外部项目（有 confirm）→ 404
    with SessionLocal() as s:
        with pytest.raises(HTTPException) as ei2:
            execute_project_data_gc(
                project_id=pid, data=DataGcExecuteRequest(confirm=True),
                db=s, user={"user_id": "u_stranger"})
    assert ei2.value.status_code == 404
    # store 未被前两笔请求动过（400/404 都不执行删除）
    assert store.exists(key_orphan) is True

    with SessionLocal() as s:
        result = execute_project_data_gc(
            project_id=pid, data=DataGcExecuteRequest(confirm=True),
            db=s, user={"user_id": _OWNER})
    assert result["retention"]["deleted_revision_count"] == 1
    assert result["retention"]["deleted_blobs"] == [key]
    # 反转断言（round-1）：路由不删其他项目/无主 blob —— 全局 promotion
    # GC 只属于周期 sweep（artifact_lifecycle._sweep_promotion_store）。
    assert store.exists(key_orphan) is True
    assert "promotion_store_gc" not in result
    assert result["orphan_revisions"]["deleted_count"] == 0


# ── 5. 孤儿修订行（FK-less drift）有界清理 ────────────────────────────────


def test_orphan_revision_cleanup_bounded(db):
    from app.models.project import ArtifactRevision
    from app.services.data_lifecycle.quota import (
        execute_orphan_revision_cleanup,
        plan_orphan_revision_cleanup,
    )

    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(pid, art_id)
    _mk_revision(art_id, "7" * 64, "7777/" + "7" * 64 + ".json", revision_no=1)
    orphan_ids = []
    with SessionLocal() as s:
        # 孤儿修订在 FK 模型下只能人为制造：约束删除后插入（本套件
        # fixture 每测重建域表，约束状态自愈；SQLite 不强制 FK，跳过）。
        if Engine.dialect.name == "postgresql":
            s.execute(text(
                "ALTER TABLE artifact_revisions "
                "DROP CONSTRAINT artifact_revisions_artifact_id_fkey"))
        for i in range(3):
            rid = str(uuid.uuid4())
            orphan_ids.append(rid)
            s.add(ArtifactRevision(
                id=rid, artifact_id=f"art_gone_{i}", revision_no=1,
                content_sha256=str(i) * 64,
                content_location=f"{i}000/{str(i) * 64}.json",
                content_type="json", byte_size=1,
            ))
        s.commit()

    with SessionLocal() as s:
        plan = plan_orphan_revision_cleanup(s)
    assert plan["candidate_count"] == 3
    assert set(plan["candidate_revision_ids"]) == set(orphan_ids)
    with SessionLocal() as s:
        result = execute_orphan_revision_cleanup(plan)
    assert result["deleted_count"] == 3
    with SessionLocal() as s:
        remaining = s.execute(select(ArtifactRevision)).scalars().all()
        assert {r.artifact_id for r in remaining} == {art_id}, "活 artifact 的修订不动"

    # 有界：limit=2 只计划/删除 2 条
    with SessionLocal() as s:
        for i in range(3, 6):
            s.add(ArtifactRevision(
                id=str(uuid.uuid4()), artifact_id=f"art_gone_{i}", revision_no=1,
                content_sha256=str(i) * 64,
                content_location=f"{i}000/{str(i) * 64}.json",
                content_type="json", byte_size=1))
        s.commit()
    with SessionLocal() as s:
        plan_bounded = plan_orphan_revision_cleanup(s, limit=2)
    assert plan_bounded["candidate_count"] == 2
    with SessionLocal() as s:
        result_bounded = execute_orphan_revision_cleanup(plan_bounded)
    assert result_bounded["deleted_count"] == 2


# ── 6. 快照 manifest 指针完整性披露（read-only helper）────────────────────


def test_snapshot_pointer_integrity_disclosure(db, tmp_path, monkeypatch):
    from app.services import project_artifact_promotion as pap
    from app.services.data_lifecycle.quota import snapshot_pointer_integrity
    from app.services.durable_blob_store import get_filesystem_blob_store
    from app.services.workspace.snapshot import (
        SnapshotDurablePointer,
        WorkspaceSnapshot,
        _atomic_write_json,
        _project_snapshots_dir,
    )

    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path / "blobs")
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    store = get_filesystem_blob_store()
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    key, loc, _ = _mk_blob(store, "alive")
    pdir = _project_snapshots_dir(pid)
    _atomic_write_json(pdir / "ws-ok.json", WorkspaceSnapshot(
        snapshot_id="ws-ok", session_id="s_x", project_id=pid,
        durable_pointers={"ref:alive": SnapshotDurablePointer(
            content_location=loc, content_payload_sha256=key)},
    ).model_dump(mode="json"))
    _atomic_write_json(pdir / "ws-orphaned.json", WorkspaceSnapshot(
        snapshot_id="ws-orphaned", session_id="s_x", project_id=pid,
        durable_pointers={"ref:dead": SnapshotDurablePointer(
            content_location="dead0/" + "d" * 60 + ".json",
            content_payload_sha256="d" * 64)},
    ).model_dump(mode="json"))

    report = snapshot_pointer_integrity(pid)
    assert report["snapshots_checked"] == 2
    assert report["pointers_missing_total"] == 1
    assert report["items"][0]["snapshot_id"] == "ws-orphaned"
    assert report["items"][0]["missing_pointers"] == ["ref:dead"]
    # 只读：两次调用结果一致（无副作用）
    assert snapshot_pointer_integrity(pid)["pointers_missing_total"] == 1


# ── 6b. 快照 manifest 指针 = retention blob 保护输入（round-1 CRITICAL）───


def test_retention_respects_workspace_snapshot_manifest_pointers(
    db, tmp_path, monkeypatch,
):
    """manifest 物化的 blob（零 DB 行引用）在 retention 的 plan 与 execute
    双侧都被保护；manifest 删除后同一 blob 恢复可删（超龄 + 超宽限）。"""
    from app.services import project_artifact_promotion as pap
    from app.services.data_lifecycle.quota import (
        execute_retention_cleanup,
        plan_retention_cleanup,
        workspace_snapshot_protected_pointers,
    )
    from app.services.durable_blob_store import get_filesystem_blob_store
    from app.services.workspace.snapshot import (
        SnapshotDurablePointer,
        WorkspaceSnapshot,
        _atomic_write_json,
        _project_snapshots_dir,
    )

    _retention_env(monkeypatch)  # max_age_days=1, grace_hours=1
    monkeypatch.setattr(pap, "content_store_root", lambda: tmp_path / "blobs")
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    store = get_filesystem_blob_store()
    pid = f"proj_{uuid.uuid4().hex[:8]}"
    _mk_project(pid)
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    _mk_artifact(pid, art_id)
    # 超龄修订 + 超龄 blob（无 pin、非 head、零其它引用 —— 本可删）
    key_v, loc_v, _ = _mk_blob(store, "victim", age_hours=48)
    _mk_revision(art_id, key_v, loc_v, age_days=10, revision_no=1)
    _mk_revision(art_id, "f" * 64, "ffff/" + "f" * 64 + ".json", revision_no=2)

    # 快照 manifest 引用同一个 blob（blob 无任何 DB 行引用它）
    pdir = _project_snapshots_dir(pid)
    manifest = pdir / "ws-protect.json"
    _atomic_write_json(manifest, WorkspaceSnapshot(
        snapshot_id="ws-protect", session_id="s_x", project_id=pid,
        durable_pointers={"ref:kept": SnapshotDurablePointer(
            content_location=loc_v, content_payload_sha256=key_v)},
    ).model_dump(mode="json"))
    locations, shas, _truncated = workspace_snapshot_protected_pointers()
    assert loc_v in locations and key_v in shas

    # plan 侧：manifest 在 → blob 不入候选（dry-run，不删任何字节）
    with SessionLocal() as s:
        plan = plan_retention_cleanup(s, pid)
    assert plan["candidate_revision_count"] == 1
    assert plan["candidate_blob_count"] == 0, "manifest 指针保护 blob 不入候选"
    assert (plan.get("blob_protected_counts") or {}).get(
        "workspace snapshot manifest pointer") == 1
    assert store.exists(key_v) is True

    # execute 侧复检（plan → execute 之间 manifest 又回来了）：新鲜扫描
    # 再保护一次 —— 删除修订行，但 blob 绝不删
    manifest.unlink()
    with SessionLocal() as s:
        plan_x = plan_retention_cleanup(s, pid)
    assert [b["key"] for b in plan_x["candidate_blobs"]] == [key_v]
    _atomic_write_json(manifest, WorkspaceSnapshot(
        snapshot_id="ws-protect", session_id="s_x", project_id=pid,
        durable_pointers={"ref:kept": SnapshotDurablePointer(
            content_location=loc_v, content_payload_sha256=key_v)},
    ).model_dump(mode="json"))
    result_x = execute_retention_cleanup(plan_x)
    assert len(result_x["deleted_revisions"]) == 1
    assert result_x["deleted_blobs"] == []
    assert any(s.get("reason") == "workspace snapshot manifest pointer"
               for s in result_x["skipped_protected"])
    assert store.exists(key_v) is True

    # manifest 删除 → 保护输入消失 → blob 恢复可删（plan+execute parity）
    manifest.unlink()
    with SessionLocal() as s:
        plan2 = plan_retention_cleanup(s, pid)
    assert [b["key"] for b in plan2["candidate_blobs"]] == []
    assert store.exists(key_v) is True, "修订行已删：retention 不再独立候选 blob"

    # 兜底口径：promotion GC 以同一谓词复判（同一新鲜扫描）→ 可删
    from app.services.artifact_lifecycle import (
        execute_promotion_store_gc,
        plan_promotion_store_gc,
    )

    plan3 = asyncio.run(plan_promotion_store_gc(grace_hours=1.0))
    assert [d["key"] for d in plan3["deletable"]] == [key_v]
    result3 = asyncio.run(execute_promotion_store_gc(plan3))
    assert result3["deleted"] == [key_v]
    assert store.exists(key_v) is False


# ── 7. 保护常量同源（docs-level invariant 的代码锚点）─────────────────────


def test_protected_tier_constants_are_uniform():
    """跨模块保护元组必须完全一致（parity 的静态前提）。"""
    from app.services.artifact_lifecycle import (
        _promotion_blob_protection,
    )
    from app.services.artifact_registry import _GC_PROTECTED_TIERS
    from app.services.data_lifecycle.gc import _PROTECTED_TIERS
    from app.services.data_lifecycle.quota import (
        PROTECTED_PERSISTENCE_TIERS,
        promotion_blob_protection,
    )

    assert _GC_PROTECTED_TIERS == _PROTECTED_TIERS == PROTECTED_PERSISTENCE_TIERS
    assert promotion_blob_protection is not None
    assert _promotion_blob_protection is not None
