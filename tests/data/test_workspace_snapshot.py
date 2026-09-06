"""Workspace Snapshot V3 —— 保存 / 核查 / 恢复 / 克隆测试。"""
import pytest

from app.services.artifact_registry import register_artifact
from app.services.session_data import session_data_manager
from app.services.workspace.snapshot import (
    _snapshot_path,
    get_workspace_snapshot_service,
    reset_workspace_snapshot_service,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_workspace_snapshot_service()
    yield
    reset_workspace_snapshot_service()


async def _seed_workspace(sid: str):
    """一个带血缘 + 图层 ref 的最小工作空间。"""
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
             "properties": {"name": "a"}},
        ],
    }
    ref = await session_data_manager.store(sid, fc, prefix="geojson")
    await register_artifact(sid, artifact_id=ref, producer_tool="query_osm_poi",
                            artifact_type="poi_feature_set")
    # 第二个产物：derived（不落 store —— 用于「载荷已失」场景）
    await register_artifact(
        sid, artifact_id="ref:geojson-ghost", producer_tool="kde",
        inputs=[ref], artifact_type="density_surface",
    )
    return ref


class TestSaveAndList:
    async def test_save_captures_ledger_and_layers(self):
        sid = "ws-save"
        ref = await _seed_workspace(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid, label="mid-analysis")
        assert snap is not None
        ids = {c.artifact_id for c in snap.artifact_contracts}
        assert {ref, "ref:geojson-ghost"} <= ids
        # mapspec 缺席时 layers 为空、fingerprint 空（诚实）
        assert snap.layers == []
        assert snap.label == "mid-analysis"
        listed = await svc.list_snapshots(sid)
        assert any(x["snapshot_id"] == snap.snapshot_id for x in listed)

    async def test_cap_enforced(self):
        sid = "ws-cap"
        svc = get_workspace_snapshot_service()
        for _ in range(25):
            await svc.save_snapshot(sid, label="churn")
        assert len(await svc.list_snapshots(sid)) <= 20

    async def test_invalid_session_rejected(self):
        assert await get_workspace_snapshot_service().save_snapshot("../evil") is None


class TestVerify:
    async def test_live_refs_pass_missing_refs_reported(self):
        sid = "ws-verify"
        ref = await _seed_workspace(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid)
        verification = await svc.verify_snapshot(sid, snap.snapshot_id)
        assert verification.exists
        assert verification.artifacts_live >= 1
        # ghost 产物从未落 store → 如实报 missing（§空心图层先见性）
        assert "ref:geojson-ghost" in verification.artifacts_missing
        assert verification.restorable is False

    async def test_missing_snapshot_honest(self):
        v = await get_workspace_snapshot_service().verify_snapshot("ws-none", "ws-nope")
        assert not v.exists
        assert not v.restorable


class TestRestore:
    async def test_verify_mode_touches_nothing(self):
        sid = "ws-restore-v"
        await _seed_workspace(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid)
        result = await svc.restore_snapshot(sid, snap.snapshot_id, mode="verify")
        assert "registered" not in result
        assert result["verification"]["exists"]

    async def test_register_mode_rebinds_ledger(self):
        sid = "ws-restore-r"
        ref = await _seed_workspace(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid)
        # 清掉账本（模拟会话重启后账本 TTL 消失）
        from app.services.session_data import session_data_manager as sdm

        ledger_ref = await sdm.resolve_alias(sid, "artifacts")
        if ledger_ref != "artifacts":
            await sdm.delete_ref(sid, ledger_ref)
        from app.services.artifact_registry import list_artifacts

        assert await list_artifacts(sid) == []
        result = await svc.restore_snapshot(sid, snap.snapshot_id, mode="register")
        assert result["registered"] >= 2
        records = {r.artifact_id: r for r in await list_artifacts(sid)}
        assert ref in records
        # 血缘重绑定：derived 的 inputs 恢复
        derived = records["ref:geojson-ghost"]
        assert ref in derived.inputs
        # 恢复标记进 metadata
        assert "restored_from" in records[ref].metadata

    async def test_unknown_mode_rejected(self):
        sid = "ws-restore-x"
        await _seed_workspace(sid)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(sid)
        result = await svc.restore_snapshot(sid, snap.snapshot_id, mode="nuke")
        assert "error" in result


class TestClone:
    async def test_clone_copies_and_reports_ref_semantics(self):
        src = "ws-clone-src"
        ref = await _seed_workspace(src)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(src)
        clone = await svc.clone_snapshot(src, snap.snapshot_id, "ws-clone-dst")
        assert clone is not None
        verification = await svc.verify_snapshot("ws-clone-dst", clone["snapshot_id"])
        assert verification.exists
        # ref 属于源会话：在目标会话不可解析 → 如实报 missing（ref 不可跨会话）
        assert verification.restorable is False
        assert verification.artifacts_missing  # 至少 ghost + 源 ref 均不可达

    async def test_clone_invalid_target_rejected(self):
        src = "ws-clone-bad"
        await _seed_workspace(src)
        svc = get_workspace_snapshot_service()
        snap = await svc.save_snapshot(src)
        assert await svc.clone_snapshot(src, snap.snapshot_id, "../evil") is None


def test_path_traversal_guarded():
    assert _snapshot_path("ok-session", "ws-abc") is not None
    assert _snapshot_path("../evil", "ws-abc") is None
    assert _snapshot_path("ok-session", "../../etc/passwd") is None
    assert _snapshot_path("", "ws-abc") is None
