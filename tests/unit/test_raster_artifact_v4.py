"""Raster Artifact V4（ADR-0091 §22-23）契约测试。

不变量：
- ref:raster/<id> 是一等产物：注册 → 记录携带血缘/存储语义；
- 磁盘 stat 探测（O(1)）驱动 sweep 状态（存活=valid；缺失=expired）——
  不再因「不在 session store」恒判缺失；
- GC 只删「GC 态且不在活引用集合」的栅格 PNG（活引用保护与 store ref
  同纪律）；
- 非法 raster id（../、分隔符）在路径构造即拒绝（路径拼接面防御）。
"""
from __future__ import annotations

import pytest

from app.services.artifact_registry import (
    is_raster_ref,
    probe_ref,
    raster_png_path,
    raster_ref_exists,
)


@pytest.fixture
def raster_session(tmp_path, monkeypatch):
    """会话 raster 目录（复用 BASE_STORAGE_DIR 注入点）。"""
    sid = "raster-v4-session"
    raster_dir = tmp_path / sid / "raster"
    raster_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "app.services.mapspec.store.BASE_STORAGE_DIR", tmp_path,
    )
    (raster_dir / "abc123.png").write_bytes(b"\x89PNG-fake")
    return sid


class TestRasterRefHelpers:
    def test_is_raster_ref(self):
        assert is_raster_ref("ref:raster/abc123")
        assert not is_raster_ref("ref:geojson-abc")
        assert not is_raster_ref("")

    def test_path_rejects_traversal(self):
        assert raster_png_path("s", "ref:raster/../evil") is None
        assert raster_png_path("s", "ref:raster/a/b") is None
        assert raster_png_path("s", "ref:raster/") is None
        assert raster_png_path("../evil", "ref:raster/ok") is None

    def test_path_shape(self, raster_session, tmp_path):
        path = raster_png_path(raster_session, "ref:raster/abc123")
        assert path == tmp_path / raster_session / "raster" / "abc123.png"

    def test_exists_stat(self, raster_session):
        assert raster_ref_exists(raster_session, "ref:raster/abc123")
        assert not raster_ref_exists(raster_session, "ref:raster/missing")


class TestProbeRef:
    async def test_probe_raster_alive(self, raster_session):
        desc = await probe_ref(raster_session, "ref:raster/abc123")
        assert desc == {"kind": "raster_png", "exists": True}

    async def test_probe_raster_missing(self, raster_session):
        assert await probe_ref(raster_session, "ref:raster/none") is None

    async def test_probe_non_raster_uses_store(self, raster_session, monkeypatch):
        """非 raster ref 走 session store descriptor（既有语义不变）。"""
        from app.services.session_data import session_data_manager

        async def fake_desc(sid, ref):
            return {"feature_count": 3}

        monkeypatch.setattr(
            session_data_manager, "get_ref_descriptor", fake_desc,
        )
        desc = await probe_ref(raster_session, "ref:geojson-x")
        assert desc == {"feature_count": 3}


class TestSweepAndGC:
    async def test_sweep_raster_liveness(self, raster_session):
        """sweep 对磁盘栅格不再恒 expired：存活 → valid。"""
        from app.services.artifact_registry import (
            register_artifact,
            sweep_statuses,
        )

        await register_artifact(
            raster_session,
            artifact_id="ref:raster/abc123",
            artifact_type="raster_surface",
            producer_tool="add_layer",
        )
        result = await sweep_statuses(raster_session)
        # 无行/spec 引用 → stale（不是 expired）—— stat 探测生效的证明。
        assert "ref:raster/abc123" in result["stale"]
        assert "ref:raster/abc123" not in result["expired"]

    async def test_sweep_detects_deleted_png(self, raster_session, tmp_path):
        from app.services.artifact_registry import (
            register_artifact,
            sweep_statuses,
        )

        await register_artifact(
            raster_session,
            artifact_id="ref:raster/abc123",
            artifact_type="raster_surface",
        )
        (tmp_path / raster_session / "raster" / "abc123.png").unlink()
        result = await sweep_statuses(raster_session)
        assert "ref:raster/abc123" in result["expired"]

    async def test_gc_respects_live_mapspec_reference(self, raster_session):
        """活引用保护：MapSpec source imageRef 指向的 PNG 不被 GC unlink。"""
        from app.services.artifact_registry import (
            collect_orphan_refs,
            register_artifact,
            sweep_statuses,
        )

        mapspec = {
            "sources": {
                "heat": {"type": "raster", "imageRef": "ref:raster/abc123"},
            },
            "layers": [{"id": "heat", "source": "heat", "type": "raster"}],
        }
        await register_artifact(
            raster_session,
            artifact_id="ref:raster/abc123",
            artifact_type="raster_surface",
        )
        # 先 sweep（stale 化）再 GC —— spec 引用在场，PNG 必须存活。
        await sweep_statuses(raster_session, mapspec=mapspec)
        deleted = await collect_orphan_refs(raster_session, mapspec=mapspec)
        assert deleted == []
        assert raster_ref_exists(raster_session, "ref:raster/abc123")

    async def test_gc_unlinks_orphan_png(self, raster_session):
        from app.services.artifact_registry import (
            collect_orphan_refs,
            register_artifact,
            sweep_statuses,
        )

        await register_artifact(
            raster_session,
            artifact_id="ref:raster/abc123",
            artifact_type="raster_surface",
        )
        # 无任何行/spec 引用 → sweep stale → GC 删 PNG。
        await sweep_statuses(raster_session)
        deleted = await collect_orphan_refs(raster_session)
        assert "ref:raster/abc123" in deleted
        assert not raster_ref_exists(raster_session, "ref:raster/abc123")


class TestRegistrationMetadata:
    async def test_register_carries_raster_semantics(self, raster_session):
        from app.services.artifact_registry import list_artifacts

        from app.services.artifact_registry import register_artifact

        await register_artifact(
            raster_session,
            artifact_id="ref:raster/abc123",
            artifact_type="raster_surface",
            producer_tool="add_layer",
            descriptor={"bbox": [104.0, 30.6, 104.2, 30.8]},
            metadata={
                "storage": "disk_png",
                "layer_id": "heat-layer",
            },
        )
        records = {r.artifact_id: r for r in await list_artifacts(raster_session)}
        rec = records["ref:raster/abc123"]
        assert rec.artifact_type == "raster_surface"
        assert rec.metadata["storage"] == "disk_png"
        assert rec.metadata["layer_id"] == "heat-layer"
        assert rec.bbox == [104.0, 30.6, 104.2, 30.8]
