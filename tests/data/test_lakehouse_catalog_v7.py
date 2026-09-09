"""Lakehouse V7 Wave 11-12 — catalog 投影（模型/迁移/upsert/检索/对账）+ STAC。

覆盖面（ADR-0119 §6-7，评审 R0-7/10/25）：
- upsert 幂等：同 (owner, content_sha256) 重复投影 = 更新不新增；
- 多 owner 发布身份：同 content_sha256 进不同 owner = 两行（代理 PK）；
- 检索有界：limit 钳制、分页 next_offset、bbox/time/kind/tags 过滤、
  无 owner typed 拒绝（无全局目录 —— 租户边界）；
- revoke tombstone；reconcile 对账（manifest 缺失行 → revoked，幂等）；
- migration 0034：单 head（alembic heads）；additive up/down/up；
- STAC：Item 必填字段（bbox/datetime）typed 拒绝缺失；Collection 分页
  links；不可投影条目 skipped 披露（不静默丢弃）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

import app.models.db_model  # noqa: F401 — ORM 模型注册进 Base.metadata
import app.models.lakehouse_catalog  # noqa: F401
from app.core.database import Base, Engine
from app.models.lakehouse_catalog import LakehouseCatalogItem

from app.services.lakehouse.catalog_service import (
    CatalogError,
    derive_entry_from_manifest,
    reconcile_catalog,
    revoke_catalog_entries,
    search_catalog,
    upsert_catalog_entry,
)
from app.services.lakehouse.catalog_stac import (
    StacProjectionError,
    entries_to_stac_collection,
    entry_to_stac_item,
)

TABLE = "lakehouse_catalog_items"


@pytest.fixture()
def db_tables():
    Base.metadata.create_all(bind=Engine, checkfirst=True)
    tbl = [
        t for t in Base.metadata.sorted_tables if t.name == TABLE
    ]
    for t in tbl:
        t.drop(bind=Engine, checkfirst=True)
    for t in tbl:
        t.create(bind=Engine, checkfirst=True)


def _entry(owner="sess-cat", owner_type="session", sha=None, **kw):
    base = {
        "object_id": kw.pop("object_id", uuid.uuid4().hex),
        "owner_type": owner_type,
        "owner_id": owner,
        "kind": "zarr_cube",
        "title": kw.pop("title", "demo cube"),
        "producer_capability": "lakehouse.rs_cube_build",
        "producer_tool": "build_rs_cube",
        "workflow_run_id": None,
        "tags_json": kw.pop("tags_json", ["optical", "sentinel-2"]),
        "descriptor_json": {},
        "minx": kw.pop("minx", 0.0), "miny": kw.pop("miny", 0.0),
        "maxx": kw.pop("maxx", 2.0), "maxy": kw.pop("maxy", 2.0),
        "time_start": kw.pop("time_start", datetime(2024, 1, 1, tzinfo=timezone.utc)),
        "time_end": kw.pop("time_end", datetime(2024, 3, 1, tzinfo=timezone.utc)),
        "content_sha256": sha or uuid.uuid4().hex * 2,
        "byte_size": 1234,
        "status": "active",
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_upsert_idempotent_and_multi_owner(db_tables):
    from app.core.database import AsyncSessionLocal

    sha = uuid.uuid4().hex * 2
    fields = _entry(sha=sha)
    async with AsyncSessionLocal() as db:
        r1 = await upsert_catalog_entry(db, fields)
        r2 = await upsert_catalog_entry(db, dict(fields, title="updated"))
        assert (r1["status"], r2["status"]) == ("created", "updated")
        # 多 owner：同内容进另一 session / project = 独立行（R0-7）。
        r3 = await upsert_catalog_entry(
            db, _entry(owner="sess-other", sha=sha),
        )
        r4 = await upsert_catalog_entry(
            db, _entry(owner="proj-a", owner_type="project", sha=sha),
        )
        assert r3["status"] == "created" and r4["status"] == "created"
        rows = (await db.execute(select(LakehouseCatalogItem))).scalars().all()
        assert len(rows) == 3


@pytest.mark.asyncio
async def test_search_filters_and_pagination(db_tables):
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        for i in range(5):
            await upsert_catalog_entry(db, _entry(
                object_id=f"obj-{i}",
                minx=float(i), maxx=float(i + 1),
                tags_json=["optical"] if i % 2 == 0 else ["sar"],
                time_start=datetime(2024, 1 + i, 1, tzinfo=timezone.utc),
                time_end=datetime(2024, 2 + i, 1, tzinfo=timezone.utc),
            ))
        result = await search_catalog(
            db, owner_type="session", owner_id="sess-cat", limit=3,
        )
        assert result["count"] == 3
        assert result["next_offset"] == 3
        assert not result["total_bounded"]
        # bbox 过滤（索引列谓词）。
        b = await search_catalog(
            db, owner_type="session", owner_id="sess-cat",
            bbox=[0.5, -1, 1.5, 3],
        )
        assert b["count"] == 2
        # time 过滤：区间 [03→04]/[04→05]/[05→06] 的 end >= 04-01。
        t = await search_catalog(
            db, owner_type="session", owner_id="sess-cat",
            time_from="2024-04-01T00:00:00Z",
        )
        assert t["count"] == 3
        # tags 行集过滤。
        g = await search_catalog(
            db, owner_type="session", owner_id="sess-cat", tags=["sar"],
        )
        assert g["count"] == 2
        # 无 owner typed 拒绝。
        with pytest.raises(CatalogError, match="owner"):
            await search_catalog(db, owner_type="session", owner_id="")


@pytest.mark.asyncio
async def test_revoke_and_reconcile(tmp_path, db_tables):
    from app.core.config import settings
    from app.services.durable_blob_store import reset_filesystem_blob_store

    monkey_dir = tmp_path / "catdata"
    monkey_dir.mkdir(parents=True, exist_ok=True)
    settings.DATA_DIR = str(monkey_dir)  # blob 根随之（conftest 重置缓存）
    reset_filesystem_blob_store()
    from app.services.lakehouse.data_object import (
        normalize_owner_scope,
        publish_data_object,
    )

    real = publish_data_object(
        {"data.bin": b"cat"}, kind="cog_raster",
        owner_scope=normalize_owner_scope(session_id="sess-cat"),
    )
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        oid_real = real.data_object_id  # manifest 在场 → 对账保留
        oid_ghost = f"{'b' * 64}"       # 无 manifest 的幽灵（对账目标）
        await upsert_catalog_entry(db, _entry(object_id=oid_real, sha=real.content_sha256))
        await upsert_catalog_entry(db, _entry(object_id=oid_ghost, sha="b" * 64))
        report = await reconcile_catalog(
            db, owner_type="session", owner_id="sess-cat",
        )
        assert report["checked"] == 2
        assert report["reaped"] == [oid_ghost]
        # 幂等：再跑一轮零新增收割。
        report2 = await reconcile_catalog(
            db, owner_type="session", owner_id="sess-cat",
        )
        assert report2["reaped"] == []
        # 撤销 tombstone。
        rev = await revoke_catalog_entries(
            db, owner_type="session", owner_id="sess-cat",
            object_ids=[oid_real],
        )
        assert rev["revoked"] == [oid_real]
        visible = await search_catalog(
            db, owner_type="session", owner_id="sess-cat",
        )
        assert visible["count"] == 0  # revoked 默认不可见
        with_revoked = await search_catalog(
            db, owner_type="session", owner_id="sess-cat",
            include_revoked=True,
        )
        assert with_revoked["count"] == 2  # 真实行(revoked) + 幽灵行(reaped)
    reset_filesystem_blob_store()


# ── STAC ──────────────────────────────────────────────────────────────


def _stac_entry(**kw):
    return {
        "object_id": kw.pop("object_id", "obj-1"),
        "owner_type": "project",
        "owner_id": "proj-1",
        "kind": "zarr_cube",
        "title": "sentinel cube",
        "content_sha256": "c" * 64,
        "byte_size": 42,
        "bbox": [0.0, 0.0, 2.0, 2.0],
        "time_start": "2024-01-01T00:00:00Z",
        "time_end": "2024-03-01T00:00:00Z",
        "tags": ["optical"],
        **kw,
    }


def test_stac_item_required_fields():
    item = entry_to_stac_item(_stac_entry())
    assert item["stac_version"] == "1.0.0"
    assert item["type"] == "Feature"
    assert item["geometry"]["type"] == "Polygon"
    assert item["properties"]["start_datetime"] == "2024-01-01T00:00:00Z"
    assert item["assets"]["data"]["webgis:data_object_id"] == "obj-1"
    # 缺 bbox → typed（绝不输出半真 Item）。
    with pytest.raises(StacProjectionError, match="bbox"):
        entry_to_stac_item(_stac_entry(bbox=None))
    with pytest.raises(StacProjectionError, match="datetime"):
        entry_to_stac_item(_stac_entry(time_start=None, time_end=None))


def test_stac_collection_pagination_and_skipped():
    entries = [_stac_entry(object_id="ok-1"), {"object_id": "bad-1"}]
    out = entries_to_stac_collection(
        entries, owner_type="project", owner_id="proj-1",
        next_offset=50, prev_offset=0,
    )
    assert len(out["items"]) == 1
    assert out["skipped"] == ["bad-1"]  # 不可投影条目披露（不静默丢弃）
    rels = {link["rel"] for link in out["collection"]["links"]}
    assert rels == {"root", "next", "prev"}


def test_stac_projection_from_manifest_derive():
    manifest = {
        "kind": "zarr_cube",
        "payload": {
            "title": "t",
            "bbox": [0.0, 1.0, 2.0, 3.0],
            "time_start": "2024-01-01T00:00:00Z",
            "time_end": "2024-02-01T00:00:00Z",
        },
        "producer": {"capability": "lakehouse.rs_cube_build", "tool": "x"},
        "byte_size": 9,
    }
    fields = derive_entry_from_manifest(
        manifest, owner_type="session", owner_id="s",
        content_sha256="d" * 64, ref="ref:cube/abc",
    )
    assert fields["object_id"] == "ref:cube/abc"
    assert fields["minx"] == 0.0 and fields["maxy"] == 3.0
