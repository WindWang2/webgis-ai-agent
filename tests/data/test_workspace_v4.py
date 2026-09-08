"""Workspace V4（Wave 2）—— 持久工作空间测试。

覆盖 brief 要求：
- save materialize="claimed"：Blob 落库 + persistence_tier=workspace 章；
- restore：会话载荷过期后从持久指针**真实重物化**（含 raster binary lane）；
- degraded restore：指针指向已删 blob → expired + degraded，绝不 valid；
- 项目快照在会话删除 + TTL 清扫下存活（含 MAPSPEC_STORAGE_DIR=DATA_DIR
  的退化配置 —— sweep 白名单保护 workspaces 根）；
- GC interlock：盖 workspace 章的 ref 不被 collect_orphan_refs 删除；
- verify honesty：digest_mismatch / pointer_missing / no_pointer 四态；
- layout chartRef/tableRef 捕获、describe 盘点形状、有界 list、
  定点 delete、路由侧 401/404 鉴权。
"""
import os
import time
import uuid

import pytest

from app.services.artifact_registry import (
    collect_orphan_refs,
    get_artifact,
    register_artifact,
    update_record_metadata,
)
from app.services.session_data import session_data_manager
from app.services.workspace.snapshot import (
    _extract_layer_refs,
    _resolve_snapshot_file,
    get_workspace_snapshot_service,
    reset_workspace_snapshot_service,
)

import app.models.db_model  # noqa: F401 — 路由测试前把 ORM 注册进 Base.metadata
import app.models.project  # noqa: F401

_FC = {
    "type": "FeatureCollection",
    "features": [
        {"geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
         "properties": {"name": "a"}},
    ],
}


@pytest.fixture(autouse=True)
def _reset():
    reset_workspace_snapshot_service()
    yield
    reset_workspace_snapshot_service()


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """DATA_DIR → tmp（项目快照根在调用时解析）；内容库根 → tmp 子目录。"""
    from app.core.config import settings
    from app.services import project_artifact_promotion as pap

    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(settings, "DATA_DIR", str(root))
    monkeypatch.setattr(pap, "content_store_root", lambda: root / "project_artifacts")
    return root


@pytest.fixture()
def session_base(tmp_path, monkeypatch):
    """会话磁盘域 → tmp（快照/栅格/清扫测试不污染仓库 DATA_DIR）。"""
    from app.services.mapspec import store as mapspec_store_module

    base = tmp_path / "webgis-agent"
    base.mkdir()
    monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", base)
    return base


async def _seed(sid: str, n_extra_payloads: int = 0):
    """账本产物 + 存活载荷（外加 ghost 死产物）。返回 (ref, payloads)。"""
    payloads = []
    ref = await session_data_manager.store(sid, _FC, prefix="geojson")
    payloads.append(ref)
    await register_artifact(sid, artifact_id=ref, producer_tool="query_osm_poi",
                            artifact_type="poi_feature_set")
    for i in range(n_extra_payloads):
        extra = await session_data_manager.store(
            sid, {"type": "FeatureCollection", "features": [], "tag": i},
            prefix="geojson",
        )
        payloads.append(extra)
        await register_artifact(sid, artifact_id=extra, producer_tool="buffer")
    await register_artifact(
        sid, artifact_id="ref:geojson-ghost", producer_tool="kde",
        inputs=[ref], artifact_type="density_surface",
    )
    return ref, payloads


# ── 1. save materialize → BlobStore + GC 章 ──────────────────────────────


class TestMaterializeOnSave:
    async def test_claimed_writes_blobs_and_stamps_tier(self, data_dir):
        sid = "wsv4-mat"
        ref, _ = await _seed(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid, project_id="proj-mat", materialize="claimed")
        assert snap is not None
        # 指针进 manifest（ghost 无载荷 → 如实无指针）
        assert ref in snap.durable_pointers
        assert "ref:geojson-ghost" not in snap.durable_pointers
        ptr = snap.durable_pointers[ref]
        assert ptr.content_payload_sha256
        # 字节真在唯一 BlobStore（digest 校验读回 == 原载荷）
        from app.services.project_artifact_promotion import read_content

        assert read_content(ptr.content_location, ptr.content_payload_sha256) == _FC
        # GC 章：registry metadata 里有 persistence_tier=workspace
        rec = await get_artifact(sid, ref)
        assert rec.metadata.get("persistence_tier") == "workspace"

    async def test_binary_lane_for_raster(self, data_dir, session_base):
        sid = "wsv4-raster"
        raster_id = f"abc{uuid.uuid4().hex[:8]}"
        ref = f"ref:raster/{raster_id}"
        png_dir = session_base / sid / "raster"
        png_dir.mkdir(parents=True)
        png_bytes = b"\x89PNG-restorable-bytes"
        (png_dir / f"{raster_id}.png").write_bytes(png_bytes)
        await register_artifact(sid, artifact_id=ref, producer_tool="export_map")
        snap = await get_workspace_snapshot_service().save_snapshot(
            sid, project_id="proj-bin", materialize="claimed"
        )
        assert snap is not None
        ptr = snap.durable_pointers.get(ref)
        assert ptr is not None and ptr.content_type == "binary"
        from app.services.durable_blob_store import get_filesystem_blob_store

        assert get_filesystem_blob_store().get_blob(
            ptr.content_payload_sha256, expected_sha256=ptr.content_payload_sha256
        ) == png_bytes

    async def test_budget_cap_skips(self, data_dir):
        sid = "wsv4-budget"
        _, payloads = await _seed(sid, n_extra_payloads=1)
        # pre-size gate（round-1 review MAJOR）：1 字节预算下第一个载荷就
        # 超限 → 写 BlobStore **之前**跳过并披露；预算不被透支消耗 ——
        # 第二个载荷同样获得量尺机会（两个都进 skipped，一个都不落盘）。
        snap = await get_workspace_snapshot_service().save_snapshot(
            sid, project_id="proj-budget", materialize="claimed",
            max_materialize_bytes=1,
        )
        assert snap is not None
        assert len(snap.durable_pointers) == 0
        assert set(snap.materialize_skipped) == set(payloads)

    async def test_budget_gate_never_lets_single_payload_exceed_budget(
        self, data_dir,
    ):
        """pre-size gate：预算恰好容纳第一个载荷 → 第一个物化；第二个载荷
        超过剩余预算 → 跳过披露，且剩余预算不受第一个载荷之外的侵蚀。"""
        sid = "wsv4-budget2"
        ref, payloads = await _seed(sid, n_extra_payloads=1)
        from app.services.project_artifact_promotion import canonical_dumps

        first_size = len(canonical_dumps(
            await session_data_manager.get(sid, payloads[0])).encode("utf-8"))
        snap = await get_workspace_snapshot_service().save_snapshot(
            sid, project_id="proj-budget2", materialize="claimed",
            max_materialize_bytes=first_size,
        )
        assert snap is not None
        assert set(snap.durable_pointers) == {payloads[0]}
        assert snap.materialize_skipped == [payloads[1]]

    async def test_invalid_materialize_rejected(self, data_dir):
        assert await get_workspace_snapshot_service().save_snapshot(
            "wsv4-bad", materialize="everything"
        ) is None


# ── 2. restore 重物化（audit §6.4：verify-before-write）──────────────────


class TestRestoreRematerializes:
    async def test_expired_payload_restored_from_pointer(self, data_dir):
        sid = "wsv4-restore"
        ref, _ = await _seed(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid, project_id="proj-restore", materialize="claimed")
        # 模拟 TTL：会话载荷已亡
        assert await session_data_manager.delete_ref(sid, ref)
        assert await session_data_manager.get(sid, ref) is None
        result = await svc.restore_snapshot(
            sid, snap.snapshot_id, mode="register", project_id="proj-restore"
        )
        assert result["restored_payloads"] >= 1
        assert ref not in result.get("marked_expired", [])
        # 载荷真回来了（同一 ref id）+ 账本状态 valid（绝不留在 expired）
        assert await session_data_manager.get(sid, ref) == _FC
        rec = await get_artifact(sid, ref)
        assert rec.status == "valid"

    async def test_degraded_when_blob_deleted_never_valid(self, data_dir):
        sid = "wsv4-degraded"
        ref, _ = await _seed(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid, project_id="proj-degraded", materialize="claimed")
        from app.services.durable_blob_store import get_filesystem_blob_store

        store = get_filesystem_blob_store()
        digest = snap.durable_pointers[ref].content_payload_sha256
        # 载荷与持久字节双双消失：血缘可重绑，字节无处可取
        await session_data_manager.delete_ref(sid, ref)
        assert store.delete_blob(digest)
        result = await svc.restore_snapshot(
            sid, snap.snapshot_id, mode="register", project_id="proj-degraded"
        )
        assert ref in result.get("marked_expired", [])
        degraded = {d["artifact_id"]: d["reason"] for d in result.get("degraded", [])}
        assert degraded.get(ref) == "durable_read_failed"
        rec = await get_artifact(sid, ref)
        assert rec.status == "expired"  # 诚实底线：绝不把死 ref 标成 valid

    async def test_verify_mode_touches_nothing(self, data_dir):
        sid = "wsv4-verify-only"
        ref, _ = await _seed(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid, project_id="proj-v", materialize="claimed")
        await session_data_manager.delete_ref(sid, ref)
        result = await svc.restore_snapshot(
            sid, snap.snapshot_id, mode="verify", project_id="proj-v"
        )
        assert "registered" not in result
        assert await session_data_manager.get(sid, ref) is None


# ── 3. 项目快照在会话删除 + TTL 清扫下存活（audit §6.1）─────────────────


class TestProjectHomeDurability:
    async def test_survives_session_delete(self, data_dir, session_base):
        from app.services.mapspec.store import purge_session_disk_state

        sid = "wsv4-purge"
        ref, _ = await _seed(sid)
        # 会话磁盘态（mapspec 目录）也要存在，purge 才有东西可删
        session_dir = session_base / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "mapspec.json").write_text("{}", encoding="utf-8")
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid, project_id="proj-surv", materialize="claimed")
        assert snap is not None
        await purge_session_disk_state(sid)
        assert not session_dir.exists()          # 会话域照常回收
        assert _resolve_snapshot_file(sid, snap.snapshot_id, "proj-surv") is not None
        listed = await svc.list_snapshots(sid, project_id="proj-surv")
        entry = next(x for x in listed if x["snapshot_id"] == snap.snapshot_id)
        assert entry["home"] == "project"        # 项目域快照仍在

    async def test_sweep_leaves_workspaces_root(self, data_dir, monkeypatch):
        """退化配置（BASE_STORAGE_DIR == DATA_DIR）：sweep 白名单保护
        workspaces 根，老会话目录照常回收。"""
        from app.services.mapspec import store as mapspec_store_module
        from app.services.mapspec.store import sweep_expired_session_files

        # 本次测试的退化配置：base 即 DATA_DIR（workspaces 在 base 之内）
        monkeypatch.setattr(mapspec_store_module, "BASE_STORAGE_DIR", data_dir)
        proj_snap_dir = data_dir / "workspaces" / "proj-sweep" / "workspace-snapshots"
        proj_snap_dir.mkdir(parents=True)
        keep = proj_snap_dir / "ws-keep.json"
        keep.write_text("{}", encoding="utf-8")
        old_session = data_dir / "wsv4-old-sess"
        old_session.mkdir()
        (old_session / "mapspec.json").write_text("{}", encoding="utf-8")
        old = time.time() - 10_000
        for path in (keep, proj_snap_dir, old_session, old_session / "mapspec.json", data_dir):
            os.utime(path, (old, old))
        purged = await sweep_expired_session_files(max_age_seconds=0)
        assert "wsv4-old-sess" in purged
        assert not old_session.exists()
        assert keep.is_file()                    # workspaces 根毫发无损


# ── 4. GC interlock（audit §6.5：零 GC 改动）─────────────────────────────


class TestGCInterlock:
    async def test_workspace_tier_ref_survives_orphan_collection(self, data_dir):
        sid = "wsv4-gc"
        ref, _ = await _seed(sid)
        await get_workspace_snapshot_service().save_snapshot(
            sid, project_id="proj-gc", materialize="claimed"
        )
        # 图层被移除 → 记录进 GC 态；盖过 workspace 章 → 不被回收
        await update_record_metadata(sid, ref, status="stale")
        deleted = await collect_orphan_refs(sid, chapter={}, mapspec={})
        assert ref not in deleted
        rec = await get_artifact(sid, ref)
        assert rec.status == "stale"  # 载荷仍在（未被置 expired）


# ── 5. verify honesty（audit §6.7 四态）──────────────────────────────────


class TestVerifyHonesty:
    async def test_digest_mismatch_detected(self, data_dir):
        sid = "wsv4-tamper"
        ref, _ = await _seed(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid, project_id="proj-tamper", materialize="claimed")
        ptr = snap.durable_pointers[ref]
        from app.services.project_artifact_promotion import content_store_root

        blob_path = content_store_root() / ptr.content_location
        blob_path.write_text('{"type": "tampered"}', encoding="utf-8")
        report = await svc.verify_snapshot(sid, snap.snapshot_id, project_id="proj-tamper")
        assert report.integrity[ref] == "digest_mismatch"
        assert report.integrity_ok is False
        assert report.restorable is False

    async def test_pointer_missing_and_no_pointer(self, data_dir):
        sid = "wsv4-pmissing"
        ref, _ = await _seed(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid, project_id="proj-pm", materialize="claimed")
        from app.services.durable_blob_store import get_filesystem_blob_store

        get_filesystem_blob_store().delete_blob(
            snap.durable_pointers[ref].content_payload_sha256
        )
        report = await svc.verify_snapshot(sid, snap.snapshot_id, project_id="proj-pm")
        assert report.integrity[ref] == "pointer_missing"
        assert report.integrity["ref:geojson-ghost"] == "no_pointer"
        assert report.integrity_ok is True  # 可读性未破（载荷仍在会话内）


# ── 6. layout chartRef/tableRef 捕获（audit §5.6 盲区）───────────────────


class TestLayoutCapture:
    def test_extract_captures_component_refs(self):
        mapspec = {
            "sources": [{"id": "l1", "ref": "ref:geojson-src"}],
            "layout": {
                "components": [
                    {"id": "chart1", "type": "chart_panel",
                     "options": {"chartRef": "ref:chart-abc"}},
                    {"id": "tbl1", "type": "table_panel",
                     "options": {"tableRef": "ref:chart-table-data"}},
                    {"id": "deco", "type": "north_arrow", "options": {}},
                ]
            },
        }
        refs = _extract_layer_refs(mapspec)
        kinds = {r.ref_kind: r for r in refs}
        assert set(kinds) == {"source", "chart", "table"}
        assert kinds["chart"].source_ref == "ref:chart-abc"
        assert kinds["table"].source_ref == "ref:chart-table-data"

    async def test_save_captures_layout_from_live_spec(self, data_dir, monkeypatch):
        from app.services import mapspec_store as adapter

        mapspec = {
            "fingerprint": "fp-x",
            "revision": 3,
            "sources": [],
            "layout": {"components": [
                {"id": "c1", "options": {"chartRef": "ref:chart-live"}},
            ]},
        }

        async def _fake_get(session_id, *a, **kw):
            return mapspec

        monkeypatch.setattr(adapter.mapspec_store, "get_mapspec", _fake_get)
        snap = await get_workspace_snapshot_service().save_snapshot(
            "wsv4-layout", project_id="proj-layout", materialize="none"
        )
        assert snap is not None
        chart = [lyr for lyr in snap.layers if lyr.ref_kind == "chart"]
        assert chart and chart[0].source_ref == "ref:chart-live"


# ── 7. describe / retention / delete ─────────────────────────────────────


class TestInventoryAndRetention:
    async def test_describe_workspace_shape(self, data_dir):
        sid = "wsv4-describe"
        ref, _ = await _seed(sid)
        svc = get_workspace_snapshot_service()
        await svc.save_snapshot(sid, project_id="proj-desc", materialize="claimed")
        inv = await svc.describe_workspace(sid, project_id="proj-desc")
        assert set(("snapshots", "artifacts", "layers", "durable")) <= set(inv)
        assert inv["snapshots"]["count"] >= 1
        assert inv["artifacts"]["total"] >= 2
        assert "by_lifecycle" in inv["artifacts"]
        assert inv["durable"]["refs_durable"] >= 1
        assert 0.0 <= inv["durable"]["coverage_pct"] <= 100.0

    async def test_project_retention_cap_evicts_oldest(self, data_dir):
        sid = "wsv4-retention"
        svc = get_workspace_snapshot_service()
        ids = []
        for i in range(4):
            snap = await svc.save_snapshot(
                sid, project_id="proj-ret", label=f"s{i}", retention_cap=2
            )
            ids.append(snap.snapshot_id)
        listed = await svc.list_snapshots(sid, project_id="proj-ret")
        remain = {x["snapshot_id"] for x in listed}
        assert remain == set(ids[2:])  # 最旧的两个被逐出
        assert all(x["home"] == "project" for x in listed)

    async def test_delete_removes_only_target_file(self, data_dir, session_base):
        sid = "wsv4-delete"
        svc = get_workspace_snapshot_service()
        s1 = await svc.save_snapshot(sid, project_id="proj-del", label="one")
        s2 = await svc.save_snapshot(sid, project_id="proj-del", label="two")
        result = await svc.delete_snapshot(sid, s1.snapshot_id, project_id="proj-del")
        assert result == {"snapshot_id": s1.snapshot_id, "home": "project", "deleted": True}
        assert _resolve_snapshot_file(sid, s1.snapshot_id, "proj-del") is None
        assert _resolve_snapshot_file(sid, s2.snapshot_id, "proj-del") is not None
        assert await svc.delete_snapshot(sid, s1.snapshot_id, project_id="proj-del") is None

    async def test_legacy_session_home_still_listed(self, data_dir, session_base):
        """向后兼容：会话域旧快照仍可读，home 如实标注 session。"""
        sid = "wsv4-legacy"
        svc = get_workspace_snapshot_service()
        legacy = await svc.save_snapshot(sid, label="old-home")  # 无 project_id
        assert legacy is not None
        listed = await svc.list_snapshots(sid, project_id="proj-any")
        entry = next(x for x in listed if x["snapshot_id"] == legacy.snapshot_id)
        assert entry["home"] == "session"


# ── 8. 路由鉴权（401 / 跨租户 404）───────────────────────────────────────


def _auth(user_id: str) -> dict:
    from app.core.auth import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': user_id, 'role': 'editor'})}"}


@pytest.fixture()
def route_env():
    """两个用户 × 各自项目 + u1 的会话（文件 sqlite，用后清理）。"""
    from app.core.database import SessionLocal
    from app.models.db_model import Conversation, User
    from app.services.project_service import ProjectService

    uid_a, uid_b = f"ws-a-{uuid.uuid4().hex[:6]}", f"ws-b-{uuid.uuid4().hex[:6]}"
    sess_id = f"wsv4-route-sess-{uuid.uuid4().hex[:6]}"
    db = SessionLocal()
    proj_a_id = proj_b_id = None
    try:
        for uid in (uid_a, uid_b):
            if db.get(User, uid) is None:
                db.add(User(id=uid, username=uid, email=f"{uid}@example.com",
                            password_hash="scrypt$16384$8$1$00$00",
                            role="editor", is_active=True))
        db.commit()
        proj_a = ProjectService.create_project(db, name="ws-a", owner_id=uid_a)
        proj_b = ProjectService.create_project(db, name="ws-b", owner_id=uid_b)
        proj_a_id, proj_b_id = proj_a.id, proj_b.id
        db.add(Conversation(id=sess_id, user_id=uid_a))
        db.commit()
    finally:
        db.close()
    yield {"uid_a": uid_a, "uid_b": uid_b, "sess": sess_id,
           "proj_a": proj_a_id, "proj_b": proj_b_id}
    db = SessionLocal()
    try:
        from app.models.project import Project

        conv = db.get(Conversation, sess_id)
        if conv is not None:
            db.delete(conv)
        for pid in (proj_a_id, proj_b_id):
            proj = db.get(Project, pid)
            if proj is not None:
                db.delete(proj)
        for uid in (uid_a, uid_b):
            u = db.get(User, uid)
            if u is not None:
                db.delete(u)
        db.commit()
    finally:
        db.close()


class TestRouteAuthz:
    def test_unauthenticated_401(self, data_dir, route_env):
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        base = f"/api/v1/projects/{route_env['proj_a']}/workspace"
        assert client.post(
            f"{base}/snapshots",
            json={"session_id": route_env["sess"]},
        ).status_code == 401
        assert client.get(f"{base}/snapshots").status_code == 401
        assert client.get(f"{base}").status_code == 401

    def test_save_list_describe_happy_path(self, data_dir, route_env):
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        headers = _auth(route_env["uid_a"])
        base = f"/api/v1/projects/{route_env['proj_a']}/workspace"
        saved = client.post(
            f"{base}/snapshots",
            json={"session_id": route_env["sess"], "label": "rt", "materialize": "none"},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["snapshot_id"] and body["home"] == "project"
        listed = client.get(
            f"{base}/snapshots", params={"session_id": route_env["sess"]}, headers=headers,
        )
        assert listed.status_code == 200
        assert any(x["snapshot_id"] == body["snapshot_id"] for x in listed.json()["items"])
        described = client.get(
            f"{base}", params={"session_id": route_env["sess"]}, headers=headers,
        )
        assert described.status_code == 200
        assert "durable" in described.json()
        # inspect = verify 报告
        inspected = client.get(
            f"{base}/snapshots/{body['snapshot_id']}",
            params={"session_id": route_env["sess"]}, headers=headers,
        )
        assert inspected.status_code == 200
        assert inspected.json()["exists"] is True

    def test_cross_tenant_session_404_no_leak(self, data_dir, route_env):
        """u2 用自己的项目 + u1 的会话 → 404（存在性不泄露）。"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        base = f"/api/v1/projects/{route_env['proj_b']}/workspace"
        foreign_headers = _auth(route_env["uid_b"])
        saved = client.post(
            f"{base}/snapshots",
            json={"session_id": route_env["sess"]},
            headers=foreign_headers,
        )
        assert saved.status_code == 404
        cloned = client.post(
            f"{base}/snapshots/ws-nope/clone",
            json={"source_session_id": route_env["sess"],
                  "target_session_id": route_env["sess"]},
            headers=foreign_headers,
        )
        assert cloned.status_code == 404

    def test_cross_tenant_project_404(self, data_dir, route_env):
        """u2 访问 u1 的项目（任意 workspace 路由）→ 404。"""
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        base = f"/api/v1/projects/{route_env['proj_a']}/workspace"
        r = client.post(
            f"{base}/snapshots/{uuid.uuid4().hex[:12]}/restore",
            json={"session_id": route_env["sess"], "mode": "verify"},
            headers=_auth(route_env["uid_b"]),
        )
        assert r.status_code == 404
        d = client.delete(
            f"{base}/snapshots/whatever",
            params={"session_id": route_env["sess"]},
            headers=_auth(route_env["uid_b"]),
        )
        assert d.status_code == 404


# ── round-1 review fixes：promoted 指针投影 / alias restore verify / 层章 ──


@pytest.fixture()
def project_tables():
    """项目域表重建（promoted 指针投影需要真实 DB 行）。"""
    from pathlib import Path

    from app.core.database import Base, Engine

    Path("./data").mkdir(parents=True, exist_ok=True)
    project_tables_names = (
        "artifact_revisions",
        "map_products", "artifact_lineages", "artifacts",
        "workflow_runs", "workflow_revisions", "workflows",
        "project_datasets", "carto_project_facts", "projects",
    )
    metadata_tables = [
        t for t in Base.metadata.sorted_tables if t.name in project_tables_names
    ]
    for tbl in reversed(metadata_tables):
        tbl.drop(bind=Engine, checkfirst=True)
    for tbl in metadata_tables:
        tbl.create(bind=Engine, checkfirst=True)


class TestPromotedPointerProjection:
    async def test_raster_promoted_pointer_keeps_binary_content_type(
        self, data_dir, project_tables,
    ):
        """round-1 review MINOR：投影用 head 修订的 content_type —— raster
        的 binary 晋升不得被硬编码成 json（restore 才能走 binary lane）。"""
        from app.core.database import SessionLocal
        from app.models.db_model import User
        from app.models.project import Artifact, ArtifactRevision, Project

        sid = "wsv4-promoted-raster"
        ref = f"ref:raster/{uuid.uuid4().hex[:12]}"
        project_id = f"proj_{uuid.uuid4().hex[:8]}"
        art_id = f"art_{uuid.uuid4().hex[:8]}"
        sha = "ab" * 32
        with SessionLocal() as s:
            s.merge(User(id="u_wsv4", username="wsv4", email="wsv4@example.com",
                         password_hash="x", role="viewer", is_active=True))
            s.add(Project(id=project_id, name="p", owner_id="u_wsv4"))
            s.add(Artifact(
                id=art_id, project_id=project_id, name="map", artifact_type="raster",
                storage_ref=ref,
                metadata_json={"content_status": "promoted",
                               "content_location": f"{sha[:4]}/{sha}.bin",
                               "content_payload_sha256": sha},
            ))
            # PG：unit-of-work 对无 relationship 的两张表不保证 FK 序，
            # flush 钉住 artifacts 先落（SQLite 无 FK 故此前误绿）。
            s.flush()
            s.add(ArtifactRevision(
                id=str(uuid.uuid4()), artifact_id=art_id, revision_no=1,
                content_sha256=sha, content_location=f"{sha[:4]}/{sha}.bin",
                content_type="binary", byte_size=123,
            ))
            s.commit()

        await register_artifact(sid, artifact_id=ref, producer_tool="export_map")
        snap = await get_workspace_snapshot_service().save_snapshot(
            sid, project_id=project_id, materialize="none",
        )
        assert snap is not None
        ptr = snap.durable_pointers.get(ref)
        assert ptr is not None
        assert ptr.content_type == "binary", "head 修订的 content_type 是投影真相"

    async def test_pointer_integrity_wired_into_describe(
        self, data_dir, project_tables,
    ):
        """round-1 review CRITICAL 接线：describe_workspace 披露
        snapshot_pointer_integrity（additive 字段）。"""
        out = await get_workspace_snapshot_service().describe_workspace(
            "wsv4-describe-int", project_id="proj-describe-int"
        )
        assert "pointer_integrity" in out
        assert out["pointer_integrity"]["pointers_missing_total"] == 0
        assert out["pointer_integrity"]["snapshots_checked"] == 0


class TestRestoreAliasMode:
    async def test_verify_reports_alias_restored_ref_live(
        self, data_dir, monkeypatch,
    ):
        """round-1 review MAJOR：alias 模式恢复后，descriptor 探测 miss 的
        ref 经 store.get() 兜底命中 —— verify 不再与事实自相矛盾。"""
        sid = "wsv4-alias-verify"
        ref, _ = await _seed(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(
            sid, project_id="proj-alias", materialize="claimed"
        )
        await session_data_manager.delete_ref(sid, ref)

        async def _no_overwrite(session_id, ref_id, data):
            return False  # 内存后端 replace-only 语义 → 强制走 alias 分支

        monkeypatch.setattr(session_data_manager, "overwrite", _no_overwrite)
        result = await svc.restore_snapshot(
            sid, snap.snapshot_id, mode="register", project_id="proj-alias"
        )
        assert result["restored_payloads"] >= 1
        # 载荷经别名回到原 ref 的读取语义
        assert await session_data_manager.get(sid, ref) == _FC
        # verify：descriptor 探测 miss，但 get() 兜底命中 → 如实 live
        verification = await svc.verify_snapshot(
            sid, snap.snapshot_id, project_id="proj-alias"
        )
        assert ref not in verification.artifacts_missing
        assert verification.artifacts_live >= 1


class TestTierPreservation:
    async def test_persistent_tier_not_downgraded_to_workspace(self, data_dir):
        """round-1 review INFO：持久层只升不降 —— 已盖 persistent 章的 ref
        绝不被 _claim_durability 覆写回 workspace。"""
        sid = "wsv4-tier"
        ref, payloads = await _seed(sid, n_extra_payloads=1)
        await update_record_metadata(
            sid, ref, metadata={"persistence_tier": "persistent"}
        )
        snap = await get_workspace_snapshot_service().save_snapshot(
            sid, project_id="proj-tier", materialize="claimed"
        )
        assert snap is not None
        rec = await get_artifact(sid, ref)
        assert rec.metadata.get("persistence_tier") == "persistent"
        # 其它 ref 照常盖 workspace 章（对照）
        rec2 = await get_artifact(sid, payloads[1])
        assert rec2.metadata.get("persistence_tier") == "workspace"
