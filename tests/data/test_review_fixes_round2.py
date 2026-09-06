"""Review 修复回归（第二轮）—— 元数据截断方向 / 快照清单校验 / revision 复核外提。"""

from app.lib.gis.analysis_reuse import find_reusable_artifact, compute_analysis_key
from app.services.artifact_registry import (
    get_artifact,
    register_tool_artifact,
    update_record_metadata,
)
from app.services.session_data import session_data_manager
from app.services.workspace.snapshot import get_workspace_snapshot_service


def _fc(n: int = 3) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0 + i, 39.0]},
             "properties": {"v": i}}
            for i in range(n)
        ],
    }


class TestMetadataTruncationDirection:
    async def test_new_keys_survive_full_metadata(self):
        """metadata 满时截断丢旧键（安全方向）：新写入的 stale 证据不丢。"""
        sid = "fix2-meta-full"
        out = await session_data_manager.store(sid, _fc(), prefix="geojson")
        await register_tool_artifact(sid, out, tool="t")
        from app.services.artifact_registry import MAX_RECORD_METADATA_KEYS

        filler = {f"key{i:02d}": i for i in range(MAX_RECORD_METADATA_KEYS)}
        await update_record_metadata(sid, out, metadata=filler)
        ok = await update_record_metadata(sid, out, metadata={"stale_source": "ref:x"})
        assert ok
        rec = await get_artifact(sid, out)
        assert rec is not None
        assert rec.metadata.get("stale_source") == "ref:x"

    async def test_same_key_new_value_wins(self):
        sid = "fix2-meta-override"
        out = await session_data_manager.store(sid, _fc(), prefix="geojson")
        await register_tool_artifact(sid, out, tool="t")
        await update_record_metadata(sid, out, metadata={"verdict": "old"})
        await update_record_metadata(sid, out, metadata={"verdict": "new"})
        rec = await get_artifact(sid, out)
        assert rec is not None and rec.metadata.get("verdict") == "new"


class TestSnapshotListValidation:
    async def test_list_rejects_traversal_session(self):
        assert await get_workspace_snapshot_service().list_snapshots("../evil") == []

    async def test_cap_rejects_traversal_session(self):
        """非法会话的 cap 例程不得触碰文件系统（无异常即通过）。"""
        svc = get_workspace_snapshot_service()
        await svc._enforce_snapshot_cap("../evil")  # 不应抛出/不应产生副作用


class TestRevisionReviewIndependence:
    async def test_revision_advance_misses_without_shapes(self):
        """形状证据缺席（input_shapes=None）时 revision 复核独立生效。"""
        sid = "fix2-rev-hoist"
        fc = _fc(5)
        input_ref = await session_data_manager.store(sid, fc, prefix="geojson")
        args = {"data": input_ref, "radius": 300}
        key = compute_analysis_key("h3_aggregate", args)
        out_ref = await session_data_manager.store(sid, _fc(2), prefix="geojson")
        await register_tool_artifact(
            sid, out_ref, tool="h3_aggregate",
            analysis_key=key,
            ref_revisions={input_ref: 1},
            # 注意：不记录 input_shapes —— 旧逻辑会整段跳过复核
        )
        await session_data_manager.overwrite(sid, input_ref, _fc(5))  # 覆写 → revision 前进
        hit = await find_reusable_artifact(sid, analysis_key=key, input_shapes=None)
        assert hit is None  # revision 复核独立于形状证据生效
