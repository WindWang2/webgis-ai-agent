"""Lakehouse V8 — dataset registry（版本层：dataset/version/branch/tag/rollback）。

覆盖面（ADR-0130，验收标准对应）：
- 描述符确定性：同 (owner, name, 契约) ⇒ 同 dataset_id；owner 参与身份；
- commit 原子协议：中断（manifest 后 / 版本行后）不产生可见半成品版本；
  重试同 version_id 幂等收敛；
- branch generation CAS：并发移动 → typed 冲突（绝不静默丢更新）；
- tag 不可变：重复创建 → 冲突；rollback = revert（历史只增不减）；
- GC root 扩展：仅被版本历史引用的 manifest / 内容不被回收；版本行
  移除后（retention 语义）内容回到可回收候选；
- owner 隔离：跨 owner 内容提交 / 版本解析 fail-closed。
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


def _publish_content(tag: bytes, session: str = "sess-v8") -> str:
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


def _make_dataset(name: str = "dem-cubes", session: str = "sess-v8"):
    from app.services.lakehouse import dataset_registry as reg

    with SessionLocal() as db:
        row, created = reg.create_dataset(
            db, name=name, description="test dataset", session_id=session,
        )
        db.commit()
        return row.id, row.dataset_id, created


# ── 描述符身份 ────────────────────────────────────────────────────────────


def test_dataset_descriptor_deterministic_and_owner_scoped(v8_env):
    from app.services.lakehouse import dataset_registry as reg

    a = reg.build_dataset_descriptor(name="dem", session_id="s1")
    b = reg.build_dataset_descriptor(name="dem", session_id="s1")
    assert reg.dataset_descriptor_id(a) == reg.dataset_descriptor_id(b)
    # owner 参与身份：跨 owner 同名 = 不同逻辑数据集。
    c = reg.build_dataset_descriptor(name="dem", session_id="s2")
    assert reg.dataset_descriptor_id(a) != reg.dataset_descriptor_id(c)
    d = reg.build_dataset_descriptor(name="dem", project_id="p1")
    assert reg.dataset_descriptor_id(a) != reg.dataset_descriptor_id(d)
    # 描述符可解析回读。
    did = reg.dataset_descriptor_id(a)
    reg.publish_dataset_descriptor(a)
    parsed = reg.resolve_dataset_descriptor(did)
    assert parsed is not None and parsed["name"] == "dem"


def test_create_dataset_idempotent_and_charset_gated(v8_env):
    with SessionLocal() as db:
        from app.services.lakehouse import dataset_registry as reg

        row1, created1 = reg.create_dataset(
            db, name="ndvi", session_id="sess-v8")
        row2, created2 = reg.create_dataset(
            db, name="ndvi", session_id="sess-v8")
        db.commit()
        assert created1 is True and created2 is False
        assert row1.id == row2.id
        with pytest.raises(reg.DatasetRegistryError):
            reg.create_dataset(db, name="bad name!", session_id="sess-v8")
        # 跨 owner 同名 = 不同数据集。
        row3, created3 = reg.create_dataset(db, name="ndvi", session_id="s-other")
        db.commit()
        assert created3 is True and row3.dataset_id != row1.dataset_id


# ── commit 原子协议 ───────────────────────────────────────────────────────


def test_commit_chain_and_head_pointer(v8_env):
    from app.services.lakehouse import dataset_registry as reg

    ds_row, _did, _ = _make_dataset()
    obj1 = _publish_content(b"v1")
    obj2 = _publish_content(b"v2")
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        r1 = reg.commit_version(
            db, row, branch="main", data_object_id=obj1,
            provenance={"algorithm": "ingest", "parameters": {"scale": 2}},
        )
        r2 = reg.commit_version(db, row, branch="main", data_object_id=obj2)
        db.commit()
        assert r1.parent_version_id is None
        assert r2.parent_version_id == r1.version_id
        head = reg.branch_head(db, ds_row, "main")
        assert head.version_id == r2.version_id
        assert head.generation == 2
        versions = reg.list_versions(db, ds_row, branch="main")
        assert [v.version_id for v in versions] == [
            r2.version_id, r1.version_id]


def test_commit_is_deterministic_and_deduped(v8_env):
    from app.services.lakehouse import dataset_registry as reg

    ds_row, _did, _ = _make_dataset()
    base = _publish_content(b"base")
    obj = _publish_content(b"same")
    prov = {"algorithm": "ingest"}
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        v1 = reg.commit_version(db, row, branch="main", data_object_id=base,
                                provenance=prov)
        db.commit()
        # 同一 parent（v1）的两条分支提交同内容同 provenance
        # → 同 version_id（重放/复现 = 幂等收敛，账本不重复计版）。
        reg.create_branch(db, row, name="exp-a", from_version_id=v1.version_id)
        reg.create_branch(db, row, name="exp-b", from_version_id=v1.version_id)
        db.commit()
        a = reg.commit_version(db, row, branch="exp-a", data_object_id=obj,
                               provenance=prov)
        b = reg.commit_version(db, row, branch="exp-b", data_object_id=obj,
                               provenance=prov)
        db.commit()
        assert a.version_id == b.version_id
        assert b.version_deduped is True
        # 复现重放：显式指定历史 parent 重放同一提交 → 同一 version_id
        # （实验复现契约）；exp-b head 已在该版本 → 指针不再移动。
        replay = reg.commit_version(db, row, branch="exp-b",
                                    data_object_id=obj, provenance=prov,
                                    parent_version_id=v1.version_id)
        db.commit()
        assert replay.version_id == a.version_id
        assert replay.ref_moved is False
        # 全库只有 2 个版本（v1 + 共享 v2）。
        assert len(reg.list_versions(db, ds_row)) == 2
        # provenance 参与身份：同内容同 parent 不同 provenance = 新版本。
        c = reg.commit_version(db, row, branch="exp-b",
                               data_object_id=obj,
                               provenance={"algorithm": "ingest-v2"})
        db.commit()
        assert c.version_id != a.version_id
        assert len(reg.list_versions(db, ds_row)) == 3


def test_interrupted_commit_leaves_no_visible_version(v8_env):
    """中断注入：commit manifest 已发布 / 版本行已 flush 后崩溃 →
    分支 head 不动、无可见半成品版本；重试同 version_id 收敛。"""
    from app.services.lakehouse import dataset_registry as reg

    ds_row, _did, _ = _make_dataset()
    obj = _publish_content(b"crashy")
    obj2 = _publish_content(b"after-crash")

    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        first = reg.commit_version(db, row, branch="main",
                                   data_object_id=obj)
        db.commit()

    # 中断点 1：manifest 发布后、版本行插入前。
    with pytest.raises(RuntimeError, match="inject-manifest"):
        with SessionLocal() as db:
            row = reg.get_dataset(db, ds_row)
            reg.commit_version(
                db, row, branch="main", data_object_id=obj2,
                _hooks={"after_manifest_publish": lambda: (_ for _ in ()).throw(
                    RuntimeError("inject-manifest"))},
            )
    # 中断点 2：版本行 flush 后、分支指针移动前。
    with pytest.raises(RuntimeError, match="inject-record"):
        with SessionLocal() as db:
            row = reg.get_dataset(db, ds_row)
            reg.commit_version(
                db, row, branch="main", data_object_id=obj,
                provenance={"algorithm": "delta-run"},
                _hooks={"after_version_record": lambda: (_ for _ in ()).throw(
                    RuntimeError("inject-record"))},
            )
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        # head 未动：没有可见的半成品版本。
        head = reg.branch_head(db, ds_row, "main")
        assert head.version_id == first.version_id
        assert head.generation == 1
        # commit manifest 可能已在 CAS（孤儿，GC 可收），但版本链未见。
        # 重试原提交 → 同 version_id 幂等收敛。
        retried = reg.commit_version(
            db, row, branch="main", data_object_id=obj,
            provenance={"algorithm": "delta-run"},
        )
        db.commit()
        assert retried.version_id != first.version_id  # parent 已是 first
        assert retried.parent_version_id == first.version_id
        head = reg.branch_head(db, ds_row, "main")
        assert head.version_id == retried.version_id
        assert head.generation == 2
        assert len(reg.list_versions(db, ds_row)) == 2


def test_concurrent_branch_move_conflicts(v8_env):
    """generation CAS：并发以同一观察代数移动 → 后者 typed 冲突。"""
    from app.services.lakehouse import dataset_registry as reg

    ds_row, _did, _ = _make_dataset()
    obj = _publish_content(b"c1")
    obj2 = _publish_content(b"c2")
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        reg.commit_version(db, row, branch="main", data_object_id=obj)
        db.commit()
    # 两个"进程"同时观察到 generation=1 的 head。
    with SessionLocal() as db:
        observed = reg.branch_head(db, ds_row, "main")
        gen1 = observed.generation
        assert gen1 == 1
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        reg.commit_version(db, row, branch="main", data_object_id=obj2)
        db.commit()
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        # 用过期的代数快照（stale generation=1）移动 → typed 冲突。
        from types import SimpleNamespace

        from app.services.lakehouse.dataset_registry import DatasetRefConflict

        with pytest.raises(DatasetRefConflict):
            reg._move_branch_ref(
                db, row, branch="main",
                version_id=reg.branch_head(db, ds_row, "main").version_id,
                expect=SimpleNamespace(generation=1),
            )


# ── branch / tag / rollback ──────────────────────────────────────────────


def test_branch_fork_and_tag_immutability(v8_env):
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.dataset_registry import DatasetRefConflict

    ds_row, _did, _ = _make_dataset()
    obj1 = _publish_content(b"base")
    obj2 = _publish_content(b"next")
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        v1 = reg.commit_version(db, row, branch="main", data_object_id=obj1)
        reg.commit_version(db, row, branch="main", data_object_id=obj2)
        exp_branch, created = reg.create_branch(
            db, row, name="experiment-a", from_version_id=v1.version_id)
        db.commit()
        assert created is True
        assert exp_branch.version_id == v1.version_id
        # 幂等分支创建。
        _, created2 = reg.create_branch(db, row, name="experiment-a")
        assert created2 is False
        # tag 创建 + 不可变。
        tag_row, _ = reg.create_tag(
            db, row, name="release-1", version_id=v1.version_id)
        assert tag_row.version_id == v1.version_id
        with pytest.raises(DatasetRefConflict):
            reg.create_tag(db, row, name="release-1", version_id=v1.version_id)
        # 空历史分支创建拒绝。
        row_b, _, _ = _make_dataset(name="empty-ds")
        row_b_full = reg.get_dataset(db, row_b)
        from app.services.lakehouse.dataset_registry import DatasetRegistryError

        with pytest.raises(DatasetRegistryError):
            reg.create_branch(db, row_b_full, name="x")


def test_rollback_is_revert_not_reset(v8_env):
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.dataset_registry import DatasetRegistryError

    ds_row, _did, _ = _make_dataset()
    v = []
    for tag in (b"r1", b"r2", b"r3"):
        with SessionLocal() as db:
            row = reg.get_dataset(db, ds_row)
            r = reg.commit_version(db, row, branch="main",
                                   data_object_id=_publish_content(tag))
            db.commit()
            v.append(r.version_id)
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        rb = reg.rollback_branch(db, row, branch="main", to_version_id=v[0])
        db.commit()
        # 新 commit：parent = r3（回滚前 head），内容 = r1 的对象。
        assert rb.parent_version_id == v[2]
        head = reg.branch_head(db, ds_row, "main")
        assert head.version_id == rb.version_id
        # 历史只增不减：4 个版本都在账本。
        versions = reg.list_versions(db, ds_row)
        assert len(versions) == 4
        rollback_row = [
            x for x in versions if x.version_id == rb.version_id][0]
        assert rollback_row.action == "rollback"
        assert rollback_row.data_object_id == _resolved_object_of(v[0])
    # 回滚到当前 head = typed NOOP。
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        head = reg.branch_head(db, ds_row, "main")
        with pytest.raises(DatasetRegistryError):
            reg.rollback_branch(db, row, branch="main",
                                to_version_id=head.version_id)
    # 未知回滚目标。
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        from app.services.lakehouse.dataset_registry import (
            DatasetVersionNotFound,
        )

        with pytest.raises(DatasetVersionNotFound):
            reg.rollback_branch(db, row, branch="main",
                                to_version_id="f" * 64)


def _resolved_object_of(version_id: str) -> str:
    from app.services.lakehouse import dataset_registry as reg

    record = reg.resolve_commit_record(version_id)
    assert record is not None
    return record["data_object_id"]


# ── lineage / 解析 / owner 隔离 ──────────────────────────────────────────


def test_version_lineage_chain(v8_env):
    from app.services.lakehouse import dataset_registry as reg

    ds_row, _did, _ = _make_dataset()
    ids = []
    for tag in (b"L1", b"L2", b"L3"):
        with SessionLocal() as db:
            row = reg.get_dataset(db, ds_row)
            r = reg.commit_version(db, row, branch="main",
                                   data_object_id=_publish_content(tag))
            db.commit()
            ids.append(r.version_id)
    with SessionLocal() as db:
        view = reg.version_lineage(db, ds_row, ids[-1])
        assert [c["version_id"] for c in view["chain"]] == list(reversed(ids))
        assert view["truncated"] is False
        short = reg.version_lineage(db, ds_row, ids[-1], max_depth=2)
        assert short["truncated"] is True and short["depth"] == 2


def test_resolve_version_owner_fail_closed(v8_env):
    from app.services.lakehouse import dataset_registry as reg

    ds_row, _did, _ = _make_dataset()
    obj = _publish_content(b"owned")
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        r = reg.commit_version(db, row, branch="main", data_object_id=obj)
        db.commit()
        resolved = reg.resolve_version(
            db, row, version_id=r.version_id, session_id="sess-v8")
        assert resolved is not None
        assert resolved["content_available"] is True
        assert resolved["manifest"]["content_sha256"]
        # 非 owner → None（不泄漏存在性）。
        assert reg.resolve_version(
            db, row, version_id=r.version_id, session_id="intruder") is None


def test_commit_rejects_foreign_owner_content(v8_env):
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.dataset_registry import (
        DatasetContentUnresolved,
    )

    ds_row, _did, _ = _make_dataset(session="sess-v8")
    foreign = _publish_content(b"stolen", session="sess-other")
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        with pytest.raises(DatasetContentUnresolved):
            reg.commit_version(db, row, branch="main", data_object_id=foreign)
        with pytest.raises(DatasetContentUnresolved):
            reg.commit_version(db, row, branch="main",
                               data_object_id="f" * 64)


# ── GC 保护（版本历史 = 引用）────────────────────────────────────────────


def test_gc_protects_version_history(v8_env):
    from app.services.lakehouse.data_object import resolve_data_object
    from app.services.lakehouse.lakehouse_gc import plan_gc
    from app.services.lakehouse import dataset_registry as reg

    ds_row, did, _ = _make_dataset()
    obj = _publish_content(b"protected-by-history")
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        r = reg.commit_version(db, row, branch="main", data_object_id=obj)
        db.commit()
    plan = plan_gc(grace_hours=0.0, ttl_floor=0.0)
    # 版本 commit manifest / 内容 manifest / 描述符 全部受保护。
    assert r.version_id not in plan["candidates"]
    assert obj not in plan["candidates"]
    assert did not in plan["candidates"]
    assert resolve_data_object(obj) is not None
    # retention 语义：版本行移除后，内容与 commit manifest 回到候选。
    import hashlib

    blob_digest = hashlib.sha256(b"protected-by-history").hexdigest()
    with SessionLocal() as db:
        from sqlalchemy import delete

        from app.models.lakehouse_datasets import (
            LakehouseDatasetRef,
            LakehouseDatasetVersion,
        )

        db.execute(delete(LakehouseDatasetRef))
        db.execute(delete(LakehouseDatasetVersion))
        db.commit()
    plan2 = plan_gc(grace_hours=0.0, ttl_floor=0.0)
    assert r.version_id in plan2["candidates"]
    assert obj in plan2["candidates"]
    assert blob_digest in plan2["deletable_blobs"]


# ── provenance 契约 ──────────────────────────────────────────────────────


def test_provenance_redacted_and_bounded(v8_env):
    from app.services.lakehouse import dataset_registry as reg

    ds_row, _did, _ = _make_dataset()
    obj = _publish_content(b"prov")
    with SessionLocal() as db:
        row = reg.get_dataset(db, ds_row)
        r = reg.commit_version(
            db, row, branch="main", data_object_id=obj,
            provenance={
                "algorithm": "ndvi-composite",
                "parameters": {"api_token": "sekret", "window": 3},
                "workflow_run_id": "run-123",
                "coverage": {"bbox": [0, 0, 1, 1]},
            },
            workflow_run_id="run-123",
        )
        db.commit()
        vrow = reg.get_version_row(db, ds_row, r.version_id)
        assert vrow.workflow_run_id == "run-123"
        prov = vrow.provenance_json
        assert prov["algorithm"] == "ndvi-composite"
        # secret 不入台账（redact 口径）。
        import json as _json

        dumped = _json.dumps(prov)
        assert "sekret" not in dumped
    # 超界 provenance → typed 拒绝。
    from app.services.lakehouse.dataset_registry import DatasetRegistryError

    with pytest.raises(DatasetRegistryError):
        reg.build_commit_record(
            dataset_id="a" * 64, parent_version_id=None,
            data_object_id="b" * 64, content_sha256="",
            provenance={f"k{i}": i for i in range(reg.MAX_PROVENANCE_KEYS + 1)},
        )


def test_migration_single_head_and_reentrant():
    """0035 保持单 head（subprocess alembic；0034 守卫同款）。"""
    import os
    import subprocess
    import sys

    Path("./data").mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[2]
    url = f"sqlite:///{Path('./data/mig_v8_check.db').resolve()}"
    env = {**os.environ, "DATABASE_URL": url, "WEBGIS_MIGRATION_DB": url}
    def _alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=str(repo_root), env=env, capture_output=True, text=True,
            timeout=120,
        )
    heads = _alembic("heads")
    assert heads.returncode == 0, heads.stderr
    assert heads.stdout.strip().count("(head)") == 1, heads.stdout
    up = _alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    down = _alembic("downgrade", "c0d8322aa2cb")
    assert down.returncode == 0, down.stderr
    up2 = _alembic("upgrade", "head")
    assert up2.returncode == 0, up2.stderr
