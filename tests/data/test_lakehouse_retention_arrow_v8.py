"""Lakehouse V8 — retention（元数据级 plan/execute）+ Arrow IPC adapter。

覆盖面（ADR-0135 §5/§6）：
- retention 谓词：指针保护（branch/tag）恒真、min_age、max_versions
  保留窗口；dry-run 确定性 token；stale plan typed 拒绝；
- retention 只删版本行（元数据级）—— 内容 blob 的删除仍归 GC；
- Arrow IPC：发布 → CAS 身份 → 批粒度局部读（batches_touched 证据）、
  行窗口、列裁剪、owner 隔离、typed 拒绝。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.models.db_model  # noqa: F401 — 模型注册
import app.models.lakehouse_datasets  # noqa: F401
from app.core.database import Base, SessionLocal

_DOMAIN_TABLES = (
    "artifact_revisions", "artifacts", "artifact_lineages", "workflow_runs",
    "workflow_revisions", "workflows", "project_datasets",
    "carto_project_facts", "map_products", "projects", "lakehouse_catalog_items",
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

    return publish_data_object(
        {"data.bin": tag},
        kind="cog_raster",
        owner_scope=normalize_owner_scope(session_id=session),
    ).data_object_id


def _dataset_with_versions(n: int, *, name: str = "ret-ds",
                           session: str = "sess-v8"):
    """dataset + n 个线性版本（返回 (row_id, [version_id...])）。"""
    from app.services.lakehouse import dataset_registry as reg

    with SessionLocal() as db:
        row, _ = reg.create_dataset(db, name=name, session_id=session)
        row_id, did = row.id, row.dataset_id
        vids = []
        for i in range(n):
            r = reg.commit_version(
                db, row, branch="main",
                data_object_id=_publish_content(f"v{i}".encode()),
            )
            vids.append(r.version_id)
        db.commit()
    return row_id, did, vids


def _age_versions(vids: list, hours_per_step: float = 24.0):
    """逐版本差异化拨老（created_at 可分辨 → 保留窗口顺序确定）。

    提交序 vids[i]（i 越大越新）→ created_at = now - (n-1-i)*step。
    """
    from sqlalchemy import select, update

    from app.models.lakehouse_datasets import LakehouseDatasetVersion

    with SessionLocal() as db:
        rows = list(
            db.execute(
                select(LakehouseDatasetVersion).where(
                    LakehouseDatasetVersion.version_id.in_([str(v) for v in vids]),
                )
            ).scalars().all()
        )
        by_order = {str(v): i for i, v in enumerate(vids)}
        now = datetime.now(timezone.utc)
        for row in rows:
            pos = by_order.get(str(row.version_id), 0)
            age_hours = (len(vids) - 1 - pos) * hours_per_step
            db.execute(
                update(LakehouseDatasetVersion)
                .where(LakehouseDatasetVersion.id == row.id)
                .values(created_at=now - timedelta(hours=age_hours))
            )
        db.commit()


# ── retention ────────────────────────────────────────────────────────────


def test_retention_plan_and_execute_prune(v8_env):
    from app.services.lakehouse import dataset_retention as ret
    from app.services.lakehouse import dataset_registry as reg

    row_id, did, vids = _dataset_with_versions(6)
    _age_versions(vids)
    with SessionLocal() as db:
        row = reg.get_dataset(db, row_id)
        plan = ret.plan_retention(db, row, max_versions=3, min_age_hours=0.0)
        # 最新 3 个受窗口保护，最老 3 个为候选。
        assert sorted(plan["candidates"]) == sorted(vids[:3])
        assert plan["protected_by_window"] == 3
        assert plan["token"]
        # 确定性：同状态重规划同 token。
        again = ret.plan_retention(db, row, max_versions=3, min_age_hours=0.0)
        assert again["token"] == plan["token"]
        result = ret.execute_retention(db, plan)
        db.commit()
        assert result["pruned_count"] == 3
        assert sorted(result["pruned"]) == sorted(vids[:3])
        assert len(reg.list_versions(db, row_id)) == 3


def test_retention_min_age_and_ref_protection(v8_env):
    from app.services.lakehouse import dataset_retention as ret
    from app.services.lakehouse import dataset_registry as reg

    row_id, did, vids = _dataset_with_versions(5)
    # 未拨老 → min_age 保护一切（尽管超出 max_versions 窗口）。
    with SessionLocal() as db:
        row = reg.get_dataset(db, row_id)
        plan = ret.plan_retention(db, row, max_versions=2, min_age_hours=72.0)
        assert plan["candidate_count"] == 0
    # tag 指针保护：即使最老且超窗，被 tag 指向的版本绝不 prune。
    _age_versions(vids)
    with SessionLocal() as db:
        row = reg.get_dataset(db, row_id)
        reg.create_tag(db, row, name="keep-me", version_id=vids[0])
        db.commit()
        plan = ret.plan_retention(db, row, max_versions=2, min_age_hours=0.0)
        assert vids[0] not in plan["candidates"]
        # tag(vids[0]) + branch head(vids[-1]) → 两个指针保护版本。
        assert plan["protected_by_refs"] == 2


def test_retention_stale_plan_rejected(v8_env):
    from app.services.lakehouse import dataset_retention as ret
    from app.services.lakehouse import dataset_registry as reg

    row_id, did, vids = _dataset_with_versions(4)
    _age_versions(vids)
    with SessionLocal() as db:
        row = reg.get_dataset(db, row_id)
        plan = ret.plan_retention(db, row, max_versions=2, min_age_hours=0.0)
    # plan 之后状态漂移（新提交）→ token 失配 → typed STALE。
    with SessionLocal() as db:
        row = reg.get_dataset(db, row_id)
        reg.commit_version(db, row, branch="main",
                           data_object_id=_publish_content(b"fresh"))
        db.commit()
        from app.services.lakehouse.dataset_retention import (
            RetentionStalePlan,
        )

        with pytest.raises(RetentionStalePlan):
            ret.execute_retention(db, plan)


def test_retention_prune_exposes_content_to_gc(v8_env):
    """组合语义：retention 裁版本行 → GC 把失去引用的内容列为候选。"""
    from sqlalchemy import delete

    from app.models.lakehouse_datasets import LakehouseDatasetVersion
    from app.services.lakehouse import dataset_retention as ret
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.lakehouse_gc import plan_gc

    row_id, did, vids = _dataset_with_versions(2)
    _age_versions(vids)
    with SessionLocal() as db:
        row = reg.get_dataset(db, row_id)
        plan = ret.plan_retention(db, row, max_versions=1, min_age_hours=0.0)
        assert plan["candidate_count"] == 1
        # 不执行 retention，仅验证 GC 此时仍保护两者 —— 直接裁剪行模拟
        # execute（execute 自身在上一用例已验）。
        db.execute(delete(LakehouseDatasetVersion).where(
            LakehouseDatasetVersion.version_id == vids[0]))
        db.commit()
    gc_plan = plan_gc(grace_hours=0.0, ttl_floor=0.0)
    # 被裁版本的 commit manifest 回到 GC 候选（字节删除归 GC）。
    assert vids[0] in gc_plan["candidates"]
    assert vids[1] not in gc_plan["candidates"]


def test_retention_scan_beyond_display_limit(v8_env):
    """M1 回归：>200 版本的数据集必须仍可 prune（扫描不走展示型上限）。"""
    from app.services.lakehouse import dataset_retention as ret
    from app.services.lakehouse import dataset_registry as reg

    row_id, did, vids = _dataset_with_versions(205, name="many-v")
    _age_versions(vids)
    with SessionLocal() as db:
        row = reg.get_dataset(db, row_id)
        plan = ret.plan_retention(
            db, row, max_versions=10, min_age_hours=0.0)
        # 扫描覆盖全部 205 行；候选 = 超窗且无指针的最老 195 个。
        assert plan["scanned_versions"] == 205
        assert len(plan["candidates"]) == 195
        result = ret.execute_retention(db, plan)
        db.commit()
        assert result["pruned_count"] == 195
        assert len(reg.list_versions(db, row_id)) == 10


# ── Arrow IPC adapter ────────────────────────────────────────────────────


def _arrow_table(n_rows: int = 150_000):
    pa = pytest.importorskip("pyarrow")
    import numpy as np

    return pa.table({
        "id": pa.array(np.arange(n_rows, dtype="int64")),
        "value": pa.array(np.random.default_rng(3).random(n_rows)),
    })


def test_arrow_publish_and_batch_window_read(v8_env, tmp_path):
    from app.services.lakehouse.arrow_adapter import (
        ArrowAdapterError,
        publish_arrow_ipc,
        read_arrow_batches,
    )
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        resolve_data_object,
    )

    scope = normalize_owner_scope(session_id="sess-v8")
    identity = publish_arrow_ipc(
        _arrow_table(), owner_scope=scope,
        payload={"title": "points"}, producer={"capability": "test"},
    )
    # 单 blob manifest；kind 白名单含 arrow_ipc。
    manifest = resolve_data_object(identity.data_object_id)
    assert manifest["kind"] == "arrow_ipc"
    assert manifest["payload"]["num_rows"] == 150_000
    # 同内容同参数重发布 = CAS 命中（payload/producer 参与身份）。
    again = publish_arrow_ipc(
        _arrow_table(), owner_scope=scope,
        payload={"title": "points"}, producer={"capability": "test"},
    )
    assert again.data_object_id == identity.data_object_id
    assert again.deduped is True
    # 批粒度读：只触批范围内的批。
    out = read_arrow_batches(
        identity.data_object_id, batch_range=(1, 2), session_id="sess-v8",
    )
    assert out["batches_touched"] == 1
    assert out["rows"] == 65_535
    assert out["total_batches"] == 3
    # 行窗口：offset/limit → 精确行集（窗口完整落在单批内 → 触 1 批）。
    win = read_arrow_batches(
        identity.data_object_id, row_offset=70_000, row_limit=10,
        session_id="sess-v8",
    )
    assert win["rows"] == 10
    assert win["batches_touched"] == 1
    # 跨批边界窗口（批 0 = 65_535 行）→ 触相邻两批，行数仍精确。
    cross = read_arrow_batches(
        identity.data_object_id, row_offset=65_000, row_limit=1_000,
        session_id="sess-v8",
    )
    assert cross["rows"] == 1_000
    assert cross["batches_touched"] == 2
    # 列裁剪。
    cols = read_arrow_batches(
        identity.data_object_id, columns=["id"], row_offset=0, row_limit=5,
        session_id="sess-v8",
    )
    assert cols["table"].column_names == ["id"]
    # owner 隔离：非 owner 不可读。
    with pytest.raises(ArrowAdapterError):
        read_arrow_batches(
            identity.data_object_id, row_offset=0, row_limit=5,
            session_id="intruder",
        )
    # 空/越界窗口 typed 拒绝。
    with pytest.raises(ArrowAdapterError):
        read_arrow_batches(
            identity.data_object_id, row_offset=10_000_000, row_limit=5,
            session_id="sess-v8",
        )


def test_arrow_offset_only_read_respects_budget(v8_env):
    """M2 回归：row_offset-only 的读也被 max_rows 预算截断（不旁路）。"""
    from app.services.lakehouse.arrow_adapter import (
        publish_arrow_ipc,
        read_arrow_batches,
    )
    from app.services.lakehouse.data_object import normalize_owner_scope

    scope = normalize_owner_scope(session_id="sess-v8")
    obj_id = publish_arrow_ipc(_arrow_table(), owner_scope=scope).data_object_id
    out = read_arrow_batches(
        obj_id, row_offset=100_000, session_id="sess-v8", max_rows=100,
    )
    assert out["rows"] == 100  # 预算生效（否则 = 50_000）
    assert out["total_rows"] == 150_000


def test_arrow_commit_as_dataset_version(v8_env):
    """Arrow 对象可直接作为 dataset 版本内容（格式无关版本层）。"""
    from app.services.lakehouse import dataset_registry as reg
    from app.services.lakehouse.arrow_adapter import publish_arrow_ipc
    from app.services.lakehouse.data_object import normalize_owner_scope

    scope = normalize_owner_scope(session_id="sess-v8")
    obj_id = publish_arrow_ipc(
        _arrow_table(10), owner_scope=scope,
    ).data_object_id
    with SessionLocal() as db:
        row, _ = reg.create_dataset(db, name="points-ds", session_id="sess-v8")
        r = reg.commit_version(
            db, row, branch="main", data_object_id=obj_id,
            provenance={"algorithm": "ingest", "parameters": {"format": "arrow"}},
        )
        db.commit()
        resolved = reg.resolve_version(
            db, row, version_id=r.version_id, session_id="sess-v8")
        assert resolved["content_available"] is True
        assert resolved["manifest"]["kind"] == "arrow_ipc"
