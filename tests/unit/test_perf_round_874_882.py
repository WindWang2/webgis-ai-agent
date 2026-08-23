"""性能专项回归（P-1..P-9 / #874-#882）。

- P-1: ref payload 进程内缓存（TTL/LRU/失效）+ get_shared 零拷贝语义
- P-3: harness 评估 map_actions 单遍分组正确性
- P-4: scenario_compare 并行 gather（wall-clock 断言）
- P-5: 栅格瓦片 ETag/304 响应
- P-8: gdf→FC helper 与 to_json 输出 JSON 等价
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.services.ref_payload_cache import RefPayloadCache
from app.services.session_data import MemorySessionStore


# ─── P-1（#874）: RefPayloadCache 单元 ────────────────────────────────────

def test_p1_payload_cache_put_get_invalidate():
    c = RefPayloadCache(ttl=60)
    fc = {"type": "FeatureCollection", "features": [{"i": 1}]}
    assert c.get("s", "ref:a") is None
    c.put("s", "ref:a", fc, 1000)
    assert c.get("s", "ref:a") is fc  # 共享对象（同一身份，零拷贝）
    c.invalidate("s", "ref:a")
    assert c.get("s", "ref:a") is None


def test_p1_payload_cache_ttl_expiry():
    c = RefPayloadCache(ttl=0.05)
    c.put("s", "ref:a", {"x": 1}, 10)
    assert c.get("s", "ref:a") is not None
    time.sleep(0.08)
    assert c.get("s", "ref:a") is None  # 过期即 miss


def test_p1_payload_cache_byte_cap_eviction():
    c = RefPayloadCache(ttl=60, max_entries=10, max_bytes=1000)
    for i in range(5):
        c.put("s", f"ref:{i}", {"i": i}, 400)  # 每条 400B → 第 3 条起超 1000B
    # LRU 淘汰最旧：ref:0/ref:1 应被逐出
    assert c.get("s", "ref:0") is None
    assert c.get("s", "ref:1") is None
    assert c.get("s", "ref:4") == {"i": 4}
    assert c.total_bytes <= 1000


def test_p1_payload_cache_session_invalidation():
    c = RefPayloadCache(ttl=60)
    c.put("s1", "ref:a", {"x": 1}, 10)
    c.put("s2", "ref:b", {"x": 2}, 10)
    c.invalidate_session("s1")
    assert c.get("s1", "ref:a") is None
    assert c.get("s2", "ref:b") == {"x": 2}  # 其他会话不受影响


@pytest.mark.asyncio
async def test_p1_memory_store_get_shared_zero_copy():
    """内存后端 get_shared 返回存储对象本体（身份不变）；get() 返回拷贝。"""
    store = MemorySessionStore()
    fc = {"type": "FeatureCollection", "features": [{"i": 1}]}
    ref = await store.store("s", fc, prefix="geojson")
    shared1 = await store.get_shared("s", ref)
    shared2 = await store.get_shared("s", ref)
    assert shared1 is fc and shared2 is fc  # 零拷贝
    copied = await store.get("s", ref)
    assert copied == fc and copied is not fc  # get() 保持 deepcopy 契约


@pytest.mark.asyncio
async def test_p1_overwrite_invalidates_shared_view():
    store = MemorySessionStore()
    ref = await store.store("s", {"v": 1}, prefix="geojson")
    assert (await store.get_shared("s", ref)) == {"v": 1}
    await store.overwrite("s", ref, {"v": 2})
    assert (await store.get_shared("s", ref)) == {"v": 2}  # 读到新对象


def test_p1_registry_uses_shared_read():
    """源码锚点：registry 解引用热路径必须走 get_shared（而非每次全量 get）。"""
    src = open("app/tools/registry.py", encoding="utf-8").read()
    assert "session_data_manager.get_shared(session_id, node)" in src


# ─── P-3（#876）: map_actions 单遍分组的正确性 ─────────────────────────────

def test_p3_map_actions_grouped_by_call():
    # 直接构造 evidence 列表并跑评估内同款分组逻辑（复刻 evaluate_with_evidence
    # 的分组段），验证 1000 calls × 1000 actions 的归属正确性。
    class _A:
        __slots__ = ("tool_call_id",)

        def __init__(self, tcid):
            self.tool_call_id = tcid

    actions = [_A(f"tc-{i % 50}") for i in range(1000)]
    actions_by_call: dict = {}
    for a in actions:
        actions_by_call.setdefault(a.tool_call_id, []).append(a)
    assert set(actions_by_call) == {f"tc-{i}" for i in range(50)}
    assert sum(len(v) for v in actions_by_call.values()) == 1000
    # 源码锚点：评估内必须用分组而非嵌套线性过滤
    src = open("app/lib/harness/pi_agent_harness.py", encoding="utf-8").read()
    assert "actions_by_call.get(tcid, [])" in src
    assert "map_actions=[\n                    a for a in self._map_action_evidence" not in src


# ─── P-4（#877）: scenario_compare 并行 ────────────────────────────────────

@pytest.mark.asyncio
async def test_p4_scenario_compare_gathers_in_parallel(monkeypatch):
    """3 个方案 × 每个评估 0.3s —— gather 后总时长必须 << 串行 0.9s。"""
    from app.services.spatial_decision.engine import DecisionEngine
    from app.tools.spatial_decision_tools import register_spatial_decision_tools
    from app.tools.registry import ToolRegistry
    from app.services.spatial_decision.comparison_engine import ScenarioComparisonEngine

    async def _slow_evaluate(self, scenario_text, target_area_text, parameters,
                             baseline_data_ref, session_id):
        await asyncio.sleep(0.3)
        return {"type": "decision", "scenario": scenario_text, "ok": True}

    monkeypatch.setattr(DecisionEngine, "evaluate_decision", _slow_evaluate)

    # compare_scenarios 结果形状按最小契约 stub（本测试只验证并行性）
    async def _fake_compare(self, results, session_id, optimization_goals=None):
        return _CmpResult()

    class _CmpResult:
        def model_dump(self):
            return {"scenarios": []}

    monkeypatch.setattr(ScenarioComparisonEngine, "compare_scenarios", _fake_compare)

    reg = ToolRegistry()
    register_spatial_decision_tools(reg)

    started = time.monotonic()
    res = await reg.dispatch(
        "scenario_compare",
        {"scenarios": [
            {"scenario": "方案A", "target_area": "成都", "parameters": {}},
            {"scenario": "方案B", "target_area": "成都", "parameters": {}},
            {"scenario": "方案C", "target_area": "成都", "parameters": {}},
        ], "session_id": ""},
        session_id="",
    )
    elapsed = time.monotonic() - started
    assert res.get("type") != "error", res
    assert elapsed < 0.75, f"3×0.3s 方案应并行（<0.75s），实际 {elapsed:.2f}s"


# ─── P-5（#878）: 栅格瓦片 ETag/304 ───────────────────────────────────────

def test_p5_png_tile_etag_304():
    from fastapi import Response
    from app.api.routes.layer import _png_tile_response

    body = b"\x89PNG-fake-bytes"
    r1: Response = _png_tile_response(body, None)
    assert r1.status_code == 200
    assert r1.headers["content-type"].startswith("image/png")
    etag = r1.headers["etag"]
    assert etag
    r304: Response = _png_tile_response(body, etag)
    assert r304.status_code == 304
    r304_star: Response = _png_tile_response(body, "*")
    assert r304_star.status_code == 304
    r_miss: Response = _png_tile_response(body, '"deadbeef"')
    assert r_miss.status_code == 200


# ─── P-7（#880）: compact 序列化字节等价 ──────────────────────────────────

@pytest.mark.asyncio
async def test_p7_compact_serializer_byte_equivalence():
    from app.lib.geojson_serializer import serialize_geojson

    doc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1.5, 2.5]},
             "properties": {"名前": "北京", "n": 3}},
        ] + [{"i": i} for i in range(2501)],
    }
    expected = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    got = await serialize_geojson(doc, pretty=False)
    assert got == expected


@pytest.mark.asyncio
async def test_p7_compact_small_documents():
    from app.lib.geojson_serializer import serialize_geojson

    for doc in ({"a": 1}, {}, [], {"features": [], "type": "FeatureCollection"}):
        expected = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        assert await serialize_geojson(doc, pretty=False) == expected
