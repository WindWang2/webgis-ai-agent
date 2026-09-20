"""Export Artifact Lineage（ADR-0204）契约测试。

覆盖：ref:export/* 磁盘 cursor（probe / GC 保护）、record_export_lineage
（属主守卫 / 血缘注册 / export_receipts 回执落章 / 幂等覆盖写）、
POST /api/v1/export 路由接线（session_id Form → lineage 披露）。
"""
import json
import os
import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.api.routes import map as _mod
from app.core.auth import get_current_user_with_version
from app.services.artifact_registry import (
    get_artifact,
    is_export_ref,
    probe_ref,
)
from app.services.export_lineage import (
    _merge_receipts,
    export_ref,
    receipt_format,
    record_export_lineage,
)
from app.services.session_data import session_data_manager
from app.services.session_plan import (
    ensure_session_plan_slot,
    load_session_plan,
)

_TEST_EXPORT_DIR = "/var/tmp/test_exports_lineage"
os.makedirs(_TEST_EXPORT_DIR, exist_ok=True)

_owner_user = {"user_id": "lineage-owner"}


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(_mod.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _clean():
    _mod._EXPORT_OWNERS.clear()
    for fn in os.listdir(_TEST_EXPORT_DIR):
        os.remove(os.path.join(_TEST_EXPORT_DIR, fn))
    yield
    _mod._EXPORT_OWNERS.clear()


@pytest.fixture
async def clean_session():
    import shutil

    sid = f"exp-lineage-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    from app.services.mapspec.store import BASE_STORAGE_DIR

    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _auth(client):
    # #1441 起 map.py 全路由走 get_current_user_with_version（严格 ver 校验）；
    # 路由级测试直接 override 该依赖（56ca490f 更新遗漏的既有测试同款修复）。
    client._transport.app.dependency_overrides[get_current_user_with_version] = (
        lambda: _owner_user
    )


def _fake_export_file(filename: str) -> str:
    """在测试 exports 目录伪造成品（真实 DATA_DIR/exports 的替身）。"""
    from app.core.config import settings
    from pathlib import Path

    real = Path(settings.DATA_DIR) / "exports"
    real.mkdir(parents=True, exist_ok=True)
    path = real / filename
    path.write_bytes(b"png")
    return str(path)


# ── 纯函数：回执合并 / format 归一 ──────────────────────────────────────


def test_receipt_format_normalizes_jpg():
    assert receipt_format("jpg") == "jpeg"
    assert receipt_format(".PNG") == "png"
    assert receipt_format("pdf") == "pdf"


def test_merge_receipts_dedupes_by_format_keeps_latest_and_bounded():
    old = [{"format": "png", "revision": "1"}, {"format": "pdf", "revision": "2"}]
    merged = _merge_receipts(old, {"format": "png", "revision": "9"})
    assert [r["format"] for r in merged] == ["png", "pdf"]
    assert merged[0]["revision"] == "9"
    overflow = _merge_receipts(
        [{"format": f"f{i}", "revision": str(i)} for i in range(12)],
        {"format": "new", "revision": "0"},
    )
    assert len(overflow) == 8
    assert overflow[0]["format"] == "new"


# ── 磁盘 cursor：probe + GC 保护 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_export_ref_probe_hits_existing_file(clean_session):
    filename = f"map_export_1_{uuid.uuid4().hex[:12]}.png"
    ref = export_ref(filename)
    assert is_export_ref(ref)
    assert await probe_ref(clean_session, ref) is None  # 文件缺席 = 不存活
    path = _fake_export_file(filename)
    try:
        desc = await probe_ref(clean_session, ref)
        assert desc is not None and desc["kind"] == "map_export"
    finally:
        os.remove(path)


@pytest.mark.asyncio
async def test_export_ref_never_orphan_gced(clean_session, monkeypatch):
    """成品 ref 不在活引用集合 → sweep 可标 stale，但孤儿回收绝不 unlink。"""
    from app.services.artifact_registry import (
        collect_orphan_refs,
        register_artifact,
        sweep_statuses,
    )

    filename = f"map_export_2_{uuid.uuid4().hex[:12]}.png"
    path = _fake_export_file(filename)
    try:
        rec = await register_artifact(
            clean_session, artifact_id=export_ref(filename),
            artifact_type="map_export",
        )
        assert rec is not None
        # 不在任何活引用集合 → stale
        await sweep_statuses(clean_session)
        from app.services.artifact_registry import get_artifact

        assert (await get_artifact(clean_session, export_ref(filename))).status == "stale"
        deleted = await collect_orphan_refs(clean_session)
        assert export_ref(filename) not in deleted
        assert os.path.exists(path), "用户交付物不得被会话孤儿回收删除"
    finally:
        os.remove(path)


# ── record_export_lineage：属主守卫 / 血缘 / 回执 ───────────────────────


@pytest.fixture
def _own_ok(monkeypatch):
    async def _ok(db, sid, user_id=None, owner_token=None):
        return True

    monkeypatch.setattr("app.core.auth.verify_session_owner", _ok)


@pytest.mark.asyncio
async def test_record_lineage_registers_artifact_and_receipt(
    clean_session, _own_ok
):
    await ensure_session_plan_slot(clean_session)
    plan = await load_session_plan(clean_session)
    if not isinstance(plan.gis_chapter, dict):
        plan.gis_chapter = {}
    plan.gis_chapter["product_spec"] = {"digest": "d" * 32, "spec": {"spec_id": "s"}}
    from app.services.session_plan import save_session_plan

    await save_session_plan(plan)

    filename = f"map_export_3_{uuid.uuid4().hex[:12]}.png"
    out = await record_export_lineage(
        clean_session, filename=filename, ext="png",
        user_id="u1", db=object(), title="成都",
        degradation_codes=["label_truncated"],
    )
    assert out is not None and out["artifact_recorded"] and out["receipt_recorded"]

    rec = await get_artifact(clean_session, export_ref(filename))
    assert rec is not None and rec.artifact_type == "map_export"
    assert rec.metadata["format"] == "png"
    assert rec.metadata["spec_digest"] == "d" * 32
    assert rec.metadata["degradation_codes"] == ["label_truncated"]

    stored = await load_session_plan(clean_session)
    receipts = stored.gis_chapter["export_receipts"]
    assert receipts[0]["format"] == "png"
    assert receipts[0]["filename"] == filename
    assert isinstance(receipts[0]["revision"], str)


@pytest.mark.asyncio
async def test_record_lineage_repeated_export_overwrites_receipt(
    clean_session, _own_ok
):
    await ensure_session_plan_slot(clean_session)
    plan = await load_session_plan(clean_session)
    if not isinstance(plan.gis_chapter, dict):
        plan.gis_chapter = {}
    from app.services.session_plan import save_session_plan

    await save_session_plan(plan)
    for i in range(2):
        await record_export_lineage(
            clean_session, filename=f"map_export_a_{i}.png", ext="png",
            user_id="u1", db=object(),
        )
    await record_export_lineage(
        clean_session, filename="map_export_b.pdf", ext="pdf",
        user_id="u1", db=object(), vector=True, pages=3, target_dpi=300,
    )
    stored = await load_session_plan(clean_session)
    receipts = stored.gis_chapter["export_receipts"]
    assert [r["format"] for r in receipts] == ["pdf", "png"], "同 format 覆盖、异 format 追加"
    assert len([r for r in receipts if r["format"] == "png"]) == 1


@pytest.mark.asyncio
async def test_record_lineage_owner_guard_skips(clean_session, monkeypatch):
    """越权 session：整体跳过（无 artifact、无回执），不抛出。"""
    async def _deny(db, sid, user_id=None, owner_token=None):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Session not found")

    monkeypatch.setattr("app.core.auth.verify_session_owner", _deny)
    await ensure_session_plan_slot(clean_session)
    out = await record_export_lineage(
        clean_session, filename="map_export_x.png", ext="png",
        user_id="intruder", db=object(),
    )
    assert out is None
    stored = await load_session_plan(clean_session)
    assert "export_receipts" not in (stored.gis_chapter or {})


@pytest.mark.asyncio
async def test_record_lineage_without_db_or_session_is_noop():
    assert await record_export_lineage(
        "", filename="a.png", ext="png", user_id="u", db=object()) is None
    assert await record_export_lineage(
        "sid", filename="", ext="png", user_id="u", db=object()) is None
    assert await record_export_lineage(
        "sid", filename="a.png", ext="png", user_id="u", db=None) is None


# ── 路由接线：POST /api/v1/export ──────────────────────────────────────


async def _upload(client, data):
    files = {"file": ("map.png", b"png-bytes", "image/png")}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_mod, "EXPORT_DIR", _TEST_EXPORT_DIR)
        return await client.post("/api/v1/export", files=files, data=data)


@pytest.mark.asyncio
async def test_route_without_session_no_lineage_key(client):
    _auth(client)
    resp = await _upload(client, {})
    assert resp.status_code == 200
    assert "lineage" not in resp.json()


@pytest.mark.asyncio
async def test_route_with_session_discloses_lineage(client, monkeypatch):
    _auth(client)
    seen = {}

    async def _fake_record(filename, ext, **kw):
        seen.update(kw, filename=filename, ext=ext)
        from app.schemas.map_schema import ExportLineageInfo

        return ExportLineageInfo(
            ref=export_ref(filename), artifact_recorded=True,
            receipt_recorded=True, format="png",
        )

    monkeypatch.setattr(_mod, "_record_lineage", _fake_record)
    resp = await _upload(client, {"session_id": "sess-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["lineage"]["ref"].startswith("ref:export/")
    assert body["lineage"]["receipt_recorded"] is True
    assert seen["session_id"] == "sess-1"
    assert seen["ext"] == ".png"  # 原始扩展名带点；receipt_format 负责归一


@pytest.mark.asyncio
async def test_route_lineage_failure_does_not_break_export(client, monkeypatch):
    _auth(client)

    async def _boom(*a, **kw):
        raise RuntimeError("registry down")

    monkeypatch.setattr(_mod, "_record_lineage", _boom)
    resp = await _upload(client, {"session_id": "sess-2"})
    assert resp.status_code == 200
    assert resp.json()["success"] is True
