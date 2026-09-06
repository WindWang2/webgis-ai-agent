"""Analysis Reuse V3 —— revision 复核与 cacheability 声明测试。"""

from app.lib.gis.analysis_reuse import (
    compute_analysis_key,
    find_reusable_artifact,
    snapshot_ref_revisions,
)
from app.services.artifact_registry import register_tool_artifact
from app.services.session_data import session_data_manager


async def _seed_production(sid: str, *, n: int = 5):
    """生产场景：输入 ref + 工具产物（含 shapes + revisions 快照）。"""
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"geometry": {"type": "Point", "coordinates": [116.0 + i * 0.1, 39.0]},
             "properties": {"v": i}}
            for i in range(n)
        ],
    }
    input_ref = await session_data_manager.store(sid, fc, prefix="geojson")
    args = {"data": input_ref, "radius": 300}
    analysis_key = compute_analysis_key("h3_aggregate", args)
    shapes = {"feature_count": n, "geometry_types": ["Point"]}
    out_ref = await session_data_manager.store(
        sid,
        {"type": "FeatureCollection", "features": fc["features"][:2]},
        prefix="geojson",
    )
    await register_tool_artifact(
        sid,
        out_ref,
        tool="h3_aggregate",
        analysis_key=analysis_key,
        input_shapes={input_ref: shapes},
        ref_revisions={input_ref: 1},
    )
    return input_ref, out_ref, analysis_key


class TestReuseRevisionGuard:
    async def test_hit_when_input_unchanged(self):
        sid = "reuse-hit"
        input_ref, out_ref, key = await _seed_production(sid)
        hit = await find_reusable_artifact(
            sid,
            analysis_key=key,
            input_shapes={input_ref: {"feature_count": 5, "geometry_types": ["Point"]}},
        )
        assert hit is not None
        assert hit["artifact_id"] == out_ref

    async def test_miss_when_revision_advanced_same_shape(self):
        """属性编辑保形状：revision 前进 → 不可复用（V3 新守卫）。"""
        sid = "reuse-rev"
        input_ref, out_ref, key = await _seed_production(sid)
        # 同形状覆写：feature_count 相同、geometry 相同（形状指纹盲区）
        fc2 = {
            "type": "FeatureCollection",
            "features": [
                {"geometry": {"type": "Point", "coordinates": [116.0 + i * 0.1, 39.0]},
                 "properties": {"v": i * 100}}  # 属性变了
                for i in range(5)
            ],
        }
        await session_data_manager.overwrite(sid, input_ref, fc2)
        hit = await find_reusable_artifact(
            sid,
            analysis_key=key,
            input_shapes={input_ref: {"feature_count": 5, "geometry_types": ["Point"]}},
        )
        assert hit is None  # V3 revision 守卫生效

    async def test_legacy_record_without_revisions_still_hits(self):
        """旧记录无 input_ref_revisions 证据 → 不因缺席而误判 miss。"""
        sid = "reuse-legacy"
        input_ref, out_ref, key = await _seed_production(sid)
        # 把记录降级为 legacy（去掉 revision 证据）
        from app.services.artifact_registry import update_record_metadata

        await update_record_metadata(sid, out_ref, metadata={"input_ref_revisions": {}})
        hit = await find_reusable_artifact(
            sid,
            analysis_key=key,
            input_shapes={input_ref: {"feature_count": 5, "geometry_types": ["Point"]}},
        )
        assert hit is not None

    async def test_cacheable_false_blocks_reuse(self):
        sid = "reuse-cacheable"
        input_ref, out_ref, key = await _seed_production(sid)
        from app.services.artifact_registry import update_record_metadata

        await update_record_metadata(sid, out_ref, metadata={"cacheable": False})
        hit = await find_reusable_artifact(
            sid,
            analysis_key=key,
            input_shapes={input_ref: {"feature_count": 5, "geometry_types": ["Point"]}},
        )
        assert hit is None


class TestSnapshotRefRevisions:
    async def test_captures_positive_revisions_only(self):
        sid = "reuse-snap"
        fc = {"type": "FeatureCollection", "features": []}
        ref = await session_data_manager.store(sid, fc, prefix="geojson")
        revs = await snapshot_ref_revisions(sid, {"data": ref, "other": "not-a-ref"})
        assert revs.get(ref) == 1  # store 时 revision 从 1 起（V5-E 语义）
        # 嵌套也要能找到
        revs2 = await snapshot_ref_revisions(sid, {"a": [{"b": ref}]})
        assert revs2.get(ref) == 1

    async def test_missing_ref_excluded(self):
        revs = await snapshot_ref_revisions("reuse-snap-x", {"d": "ref:geojson-ghost"})
        assert revs == {}
