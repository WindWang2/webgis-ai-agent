"""Audit round-2 performance regression tests (#794-#799).

行为保持型断言 + 计数 fake store：
- #794 评估路径 ref 去重/memo 跳过/廉价解析（不再每 cursor 全量 payload get）
- #795 L1 命中重解析原始字段（#749 隔离保持，且不再 deepcopy 整棵解析树）
- #796 record_sse_event 不再就地改写调用方 dict
- #797 FIFO 保留 arguments 有界投影（ref 检测不受影响）
- #799 memory 后端深拷贝语义保持
"""
import json

import pytest

import app.lib.harness.pi_agent_harness as harness_mod
from app.lib.harness.evidence import RefResolutionStatus
from app.lib.harness.pi_agent_harness import PiAgentHarness
from app.lib.harness.ref_resolver import make_session_store_resolver


# ── #794: 评估解析去重 + memo 跳过 ────────────────────────────────────────

class _CountingStore:
    """记录 get/ref_exists/get_ref_descriptor 调用次数的 fake store。"""

    def __init__(self):
        self.get_calls = []
        self.exists_calls = []

    async def get(self, session_id, ref_id):
        self.get_calls.append((session_id, ref_id))
        return {"type": "FeatureCollection", "features": [{"p": "x" * 512}]}

    async def ref_exists(self, session_id, ref_id):
        self.exists_calls.append((session_id, ref_id))
        return True

    async def get_ref_descriptor(self, session_id, ref_id):
        return {
            "ref_id": ref_id,
            "feature_count": 3,
            "point_count": 3,
            "geometry_types": ["Point"],
            "bbox": [0, 0, 1, 1],
            "mvt_capable": True,
            "raster_capable": False,
            "estimated_bytes": 100,
        }


@pytest.mark.asyncio
async def test_evaluate_resolves_distinct_refs_once_and_memo_skips_repeats():
    """#794: 同一评估内同 ref 只解析一次；refs 未变的重复评估零 store 读。"""
    harness = PiAgentHarness(session_id="s794")
    store = _CountingStore()
    harness.ref_resolver = make_session_store_resolver(store)

    for i in range(5):
        harness.record_tool_call(
            tool_call_id=f"c{i}", name="buffer_analysis",
            arguments={"target_layer": "ref:geojson-shared", "radius": 100 + i},
        )
    first = await harness.evaluate_with_evidence()
    assert first is not None
    # 廉价路径：5 个 cursor 共享同一 ref → EXISTS/descriptor 各 1 次，零全量 get
    assert len(store.exists_calls) == 1
    assert store.get_calls == []

    second = await harness.evaluate_with_evidence()
    assert second is not None
    # memo 命中：重复评估不再触碰 store
    assert len(store.exists_calls) == 1


@pytest.mark.asyncio
async def test_resolver_cheap_path_type_mismatch_without_payload_read():
    """#794: descriptor 可判定类型时，typed-prefix 失配直接 TYPE_MISMATCH，
    不做全量 payload get；判定不了（table）回退全量路径。"""
    store = _CountingStore()

    async def raster_descriptor(session_id, ref_id):
        # 静态返回（不得递归调用自身）
        return {
            "ref_id": ref_id,
            "feature_count": 0,
            "point_count": 0,
            "geometry_types": [],
            "bbox": None,
            "mvt_capable": False,
            "raster_capable": True,
            "estimated_bytes": 100,
        }

    store.get_ref_descriptor = raster_descriptor
    resolver = make_session_store_resolver(store)

    bad = await resolver("s", "ref:geojson-x1")
    assert bad.status == RefResolutionStatus.TYPE_MISMATCH
    assert store.get_calls == []  # 未读 payload

    # 判定不了类型（空 FC 形态：无几何、非栅格、0 要素）→ 全量路径兜底；
    # payload 实际是 FeatureCollection → geojson 前缀经全量判定 RESOLVED
    async def inconclusive_descriptor(session_id, ref_id):
        return {
            "ref_id": ref_id, "feature_count": 0, "point_count": 0,
            "geometry_types": [], "bbox": None, "mvt_capable": False,
            "raster_capable": False, "estimated_bytes": 10,
        }

    store.get_ref_descriptor = inconclusive_descriptor
    fallback = await resolver("s", "ref:geojson-f1")
    assert fallback.status == RefResolutionStatus.RESOLVED
    assert len(store.get_calls) == 1  # 回退读了全量 payload


# ── #796: record_sse_event 归属戳写入副本 ─────────────────────────────────

def test_record_sse_event_does_not_mutate_caller_event():
    harness = PiAgentHarness(session_id="s796")
    event = {"type": "token", "data": "x"}
    harness.record_sse_event(event)
    assert "session_id" not in event, "调用方共享 dict 不得被改写"
    assert harness.sse_events[0]["session_id"] == "s796"


# ── #797: FIFO 参数有界保留 ───────────────────────────────────────────────

def test_record_tool_call_retains_bounded_args_projection_but_detects_refs():
    harness = PiAgentHarness(session_id="s797")
    big_fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point",
             "coordinates": [116.0 + i * 0.001, 39.0]},
             "properties": {"pad": "y" * 200}}
            for i in range(3000)
        ],
    }
    harness.record_tool_call(
        tool_call_id="c1", name="kde_heatmap",
        arguments={
            "layer": big_fc,
            # ref 以真实工具形态出现在顶层列表参数（overlay_refs），扫描面覆盖
            "overlay_refs": ["ref:geojson-abc123"],
        },
    )
    stored_args = harness.tool_calls[0]["arguments"]
    encoded = json.dumps(stored_args, ensure_ascii=False, default=str)
    assert len(encoded.encode()) < 8192, "FIFO 保留的 arguments 必须有界"
    # ref 扫描发生在原始参数上 —— 大容器内的 ref 字符串仍被检测
    assert any(rc["ref_cursor"] == "ref:geojson-abc123"
               for rc in harness.ref_cursors)


def test_slim_projection_keeps_scalar_structure():
    args = {"layer_ids": ["l1", "l2"], "radius": 500, "title": "t" * 400}
    slim = harness_mod._slim_args_for_retention(args)
    assert slim["layer_ids"] == ["l1", "l2"]
    assert slim["radius"] == 500
    assert slim["title"].endswith("…") and len(slim["title"]) <= 256


# ── #795/#799: 状态读取隔离语义保持 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_l1_hit_returns_fresh_tree_per_reader():
    """#795: L1 命中重解析原始字段 —— 每个读者拿到独立对象树（#749 隔离
    保持），且缓存条目本身是原始 bytes（不可变共享）。"""
    import fakeredis.aioredis
    from app.services.session_data_redis import RedisSessionStore

    store = RedisSessionStore(
        redis_url="redis://unused",
        redis=fakeredis.aioredis.FakeRedis(decode_responses=False),
    )
    await store.set_map_state("s795", "layers", [{"id": "L1", "paint": {"x": 1}}])
    store.clear_l1_cache()

    first = await store.get_map_state("s795")
    assert ("s795", "map_state") in store._l1
    # 缓存条目是原始字段列表（(name, bytes) 对），不是解析后 dict
    cached = store._l1[("s795", "map_state")][0]
    assert isinstance(cached, list) and isinstance(cached[0], tuple)

    first["layers"][0]["paint"]["x"] = 999  # 就地污染尝试
    second = await store.get_map_state("s795")
    assert second["layers"][0]["paint"]["x"] == 1, "#749 隔离必须保持"


@pytest.mark.asyncio
async def test_memory_backend_get_map_state_isolation_still_holds():
    """#799 对照：to_thread 化不改变 copy 语义。"""
    from app.services.session_data import MemorySessionStore

    store = MemorySessionStore()
    await store.set_map_state("m1", "layers", [{"id": "A"}])
    got = await store.get_map_state("m1")
    got["layers"][0]["id"] = "MUTATED"
    again = await store.get_map_state("m1")
    assert again["layers"][0]["id"] == "A"
