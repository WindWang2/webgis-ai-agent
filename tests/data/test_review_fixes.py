"""Review 修复回归 —— plan/execute 保护一致性、restore 诚实、CRS 优先级、传播接线。"""
import pytest

from app.lib.data.fingerprints import ChangeClass, FingerprintSet, classify_change
from app.lib.data.profile import classify_crs_kind
from app.services.artifact_registry import (
    get_artifact,
    register_artifact,
    update_record_metadata,
)
from app.services.data_lifecycle.gc import execute_session_gc, plan_session_gc
from app.services.data_lifecycle.service import get_lifecycle_service
from app.services.session_data import session_data_manager
from app.services.workspace.snapshot import (
    get_workspace_snapshot_service,
    reset_workspace_snapshot_service,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_workspace_snapshot_service()
    yield
    reset_workspace_snapshot_service()


class TestGCProtectionConsistency:
    async def test_persistent_tier_orphan_survives_execute(self):
        """plan 声明保护的持久层孤儿，执行器同样不删（单删除路径双规则）。"""
        sid = "rf-gc-persist"
        ref = await session_data_manager.store(sid, {"type": "FeatureCollection", "features": []}, prefix="geojson")
        await register_artifact(
            sid, artifact_id=ref, producer_tool="t",
            metadata={"persistence_tier": "persistent"},
        )
        await update_record_metadata(sid, ref, status="stale")
        plan = await plan_session_gc(sid)
        assert ref in {p.artifact_id for p in plan.protected}
        deleted = await execute_session_gc(sid)
        assert ref not in deleted
        rec = await get_artifact(sid, ref)
        assert rec.status == "stale"  # 未被置 expired（载荷仍在）

    async def test_lineage_root_survives_execute(self):
        """valid 下游的血缘根：plan 与 execute 都不删。"""
        sid = "rf-gc-root"
        ref = await session_data_manager.store(sid, {"type": "FeatureCollection", "features": []}, prefix="geojson")
        await register_artifact(sid, artifact_id=ref, producer_tool="t")
        await register_artifact(
            sid, artifact_id="ref:geojson-live-child", producer_tool="t", inputs=[ref]
        )
        await update_record_metadata(sid, ref, status="stale")
        plan = await plan_session_gc(sid)
        assert ref not in plan.candidate_ids
        deleted = await execute_session_gc(sid)
        assert ref not in deleted


class TestRestoreHonesty:
    async def test_expired_payloads_marked_expired_after_register(self):
        """restore register 模式：载荷已亡的产物血缘重绑，但状态如实 expired。"""
        sid = "rf-restore"
        await register_artifact(
            sid, artifact_id="ref:geojson-dead", producer_tool="kde",
            inputs=[], artifact_type="density_surface",
        )
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid)
        result = await svc.restore_snapshot(sid, snap.snapshot_id, mode="register")
        assert result["registered"] >= 1
        assert "ref:geojson-dead" in result.get("marked_expired", [])
        rec = await get_artifact(sid, "ref:geojson-dead")
        assert rec.status == "expired"  # 账本不再伪造 valid


class TestPropagationReportDict:
    async def test_to_dict_roundtrip(self):
        sid = "rf-report"
        await register_artifact(sid, artifact_id="ref:geojson-a", producer_tool="t")
        report = await get_lifecycle_service().propagate_staleness(
            sid, "ref:geojson-missing-source"
        )
        d = report.to_dict()  # 不得 AttributeError（review major 修复）
        assert d["not_in_ledger"] == ["ref:geojson-missing-source"]
        assert d["marked_count"] == 0


class TestFingerprintPriority:
    def test_crs_change_dominates_metadata_when_no_content_evidence(self):
        """crs 优先级高于 metadata-only（review minor 修复）。"""
        old = FingerprintSet(metadata="m1", crs="EPSG:4326")
        new = FingerprintSet(metadata="m2", crs="EPSG:3857")
        assert classify_change(old, new) is ChangeClass.CRS


class TestCRSClassifier:
    def test_review_cases(self):
        assert classify_crs_kind("WGS 84 / UTM zone 50N") == "projected"
        assert classify_crs_kind("+proj=utm +datum=WGS84") == "projected"
        assert classify_crs_kind('GEOGCS["GCS_WGS_1984"]') == "geographic"
        assert classify_crs_kind("EPSG:4214") == "geographic"
        assert classify_crs_kind("EPSG:3857") == "projected"
        assert classify_crs_kind("unknown-custom-crs") is None


class TestStalenessWiring:
    async def test_overwrite_triggers_downstream_stale(self):
        """§十一接线：ref 覆写 → invalidate_ref_caches 钩子 → 下游 stale。"""
        import uuid
        sid = f"rf-wiring-{uuid.uuid4().hex[:8]}"
        fc = {"type": "FeatureCollection", "features": []}
        ref = await session_data_manager.store(sid, fc, prefix="geojson")
        await register_artifact(sid, artifact_id=ref, producer_tool="t")
        await register_artifact(
            sid, artifact_id="ref:geojson-down", producer_tool="kde", inputs=[ref]
        )
        # 覆写为新对象（内容变化）—— 经由 session manager 正常写入路径
        await session_data_manager.overwrite(
            sid, ref, {"type": "FeatureCollection", "features": [{"geometry": None}]}
        )
        # 钩子是 fire-and-forget task —— 给事件循环一拍让它跑完
        import asyncio

        for _ in range(20):
            await asyncio.sleep(0.05)
            rec = await get_artifact(sid, "ref:geojson-down")
            if rec is not None and rec.status == "stale":
                break
        rec = await get_artifact(sid, "ref:geojson-down")
        assert rec.status == "stale"
        assert rec.metadata.get("stale_reason") == "ref_overwritten"
