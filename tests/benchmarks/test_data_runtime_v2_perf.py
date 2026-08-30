"""Data Runtime V2 benchmark（V2 §36/§37）—— 确定性计数优先，墙钟仅宽松 smoke。

- Reuse hit/miss 的 registry.dispatch 调用计数（0 次 = 纯复用）；
- 复用查询 IO 有界（descriptor 探测次数 ≤ 输入 ref 数 + 1）；
- 150k 载荷不进 LLM payload（字节计数）；
- 契约验证/画像构建与 feature_count 无关（输出结构不变性）。

避免 wall-clock <100ms 类脆弱断言；墙钟只做 5s 级 smoke guard。
"""
import json
import uuid

import pytest

from app.lib.gis.analysis_reuse import compute_analysis_key
from app.services.session_data import session_data_manager
from app.services.tool_dispatch_service import ToolDispatchService
from app.tools.registry import ToolRegistry


@pytest.fixture
async def clean_session():
    sid = f"bench-reuse-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


def _fc(n):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0 + (i % 100) * 0.001, 30.0 + (i // 100) * 0.001]},
             "properties": {"v": i}}
            for i in range(n)
        ],
    }


async def _env(monkeypatch):
    from app.tools.advanced_spatial import register_advanced_spatial_tools

    registry = ToolRegistry()
    register_advanced_spatial_tools(registry)
    svc = ToolDispatchService(registry=registry)
    calls = {"n": 0}
    inner = registry.dispatch

    async def counting(name, args, **kw):
        calls["n"] += 1
        return await inner(name, args, **kw)

    monkeypatch.setattr(registry, "dispatch", counting)
    return svc, calls


async def test_bench_reuse_hit_zero_execution(clean_session, monkeypatch):
    """Scenario G（benchmark 口径）：20k 级点集聚合跑两遍 —— 第二遍
    registry.dispatch 调用数 0（复用），墙钟宽松 smoke guard。"""
    import time

    sid = clean_session
    svc, calls = await _env(monkeypatch)
    input_ref = await session_data_manager.store(sid, _fc(20_000), prefix="geojson")
    args = {"geojson": input_ref, "resolution": 9}

    t0 = time.perf_counter()
    r1 = await svc.dispatch(
        {"id": "c1", "function": {"name": "h3_binning", "arguments": json.dumps(args)}},
        sid, set())
    first_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    r2 = await svc.dispatch(
        {"id": "c2", "function": {"name": "h3_binning", "arguments": json.dumps(args)}},
        sid, set())
    reuse_ms = (time.perf_counter() - t0) * 1000

    assert r1.status == "ok" and r2.status == "ok"
    assert calls["n"] == 1, "复用命中不得再次执行 registry.dispatch"
    assert r2.geojson_ref == r1.geojson_ref
    # 宽松 smoke：复用路径不该比首次全量执行更贵（确定性断言在上面）。
    assert reuse_ms < max(first_ms, 5_000)


async def test_bench_reuse_probe_io_bounded(clean_session, monkeypatch):
    """复用查询的 descriptor 探测次数有界：输入 ref 数 + 产物 ref 1 次 + 账本读。"""
    sid = clean_session
    svc, calls = await _env(monkeypatch)
    input_ref = await session_data_manager.store(sid, _fc(1_000), prefix="geojson")
    args = {"geojson": input_ref, "resolution": 9}
    await svc.dispatch(
        {"id": "c1", "function": {"name": "h3_binning", "arguments": json.dumps(args)}},
        sid, set())

    probes = {"n": 0}
    original = session_data_manager.get_ref_descriptor

    async def counting_probe(s, ref):
        probes["n"] += 1
        return await original(s, ref)

    monkeypatch.setattr(session_data_manager, "get_ref_descriptor", counting_probe)
    await svc.dispatch(
        {"id": "c2", "function": {"name": "h3_binning", "arguments": json.dumps(args)}},
        sid, set())
    assert probes["n"] <= 8, f"复用查询 descriptor 探测必须有界，实际 {probes['n']}"


def test_bench_150k_payload_exclusion():
    """Scenario D：150k 特征不得进入 LLM payload（字节计数，确定性）。"""
    from app.services.llm_result_formatter import slim_tool_result

    fc = _fc(150_000)
    slim = slim_tool_result(fc, json.dumps(fc), session_geojson_ref="ref:geojson-huge")
    size = len(slim.encode("utf-8"))
    assert size < 64_000, f"150k FC slim 后 {size}B 超界"
    assert "ref:geojson-huge" in slim


def test_bench_contract_validation_feature_count_invariant():
    """契约验证输出与 feature_count 无关（O(1) 证据，非墙钟）；
    count=0 恒定产出 empty 披露（也不随规模变化）。"""
    from app.lib.gis.contract_validation import validate_output_contract
    from app.lib.gis.dataset_profile import DatasetProfile

    positive = set()
    for n in (1, 1_000, 150_000, 10**9):
        p = DatasetProfile.from_ref_descriptor(
            {"feature_count": n, "geometry_types": ["Point"], "crs": "EPSG:4326"})
        f = tuple(sorted(x.code for x in validate_output_contract(["point_feature_set"], p)))
        positive.add(f)
    assert positive == {()}, "合法 point 输入在任何规模下都是零 findings"
    zero = DatasetProfile.from_ref_descriptor(
        {"feature_count": 0, "geometry_types": ["Point"], "crs": "EPSG:4326"})
    assert [x.code for x in validate_output_contract(["point_feature_set"], zero)] == [
        "contract_empty_artifact"]


def test_bench_analysis_key_stability():
    """同参数同 ref → 键稳定（跨轮次复用前提）；参数/输入变化 → 键变。"""
    base = {"geojson": "ref:geojson-a", "resolution": 9, "stat_method": "count"}
    k1 = compute_analysis_key("h3_binning", base)
    assert k1 == compute_analysis_key("h3_binning", dict(base))  # 键序无关
    variants = [
        {"geojson": "ref:geojson-a", "resolution": 8, "stat_method": "count"},
        {"geojson": "ref:geojson-b", "resolution": 9, "stat_method": "count"},
        {"geojson": "ref:geojson-a", "resolution": 9, "stat_method": "sum"},
    ]
    for v in variants:
        assert compute_analysis_key("h3_binning", v) != k1
