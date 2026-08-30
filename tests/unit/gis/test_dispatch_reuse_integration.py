"""Dispatch 级分析复用集成测试（V2 P10 Scenario G/H，真实 registry + 真实工具）。

闭环：真实 ToolRegistry 注册真实空间工具 → ToolDispatchService.dispatch →
首次执行 mint ref + 注册 analysis_key → 第二次同参调用命中复用（registry.dispatch
零调用）→ 参数变化 miss（重新执行）。GIS_ANALYSIS_REUSE 环境开关一并验证。
"""
import json
import uuid

import pytest

from app.services.session_data import session_data_manager
from app.services.tool_dispatch_service import ToolDispatchService
from app.tools.registry import ToolRegistry


@pytest.fixture
async def clean_session():
    sid = f"reuse-e2e-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


def _fc(n=6):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point",
                          "coordinates": [104.0 + (i % 3) * 0.01, 30.6 + (i // 3) * 0.01]},
             "properties": {"v": i}}
            for i in range(n)
        ],
    }


def _tc(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": json.dumps(args)}}


async def _dispatch_env(monkeypatch):
    """真实 registry + 计数包装的 ToolDispatchService。"""
    from app.tools.advanced_spatial import register_advanced_spatial_tools
    from app.tools.spatial import register_spatial_tools

    registry = ToolRegistry()
    register_advanced_spatial_tools(registry)
    register_spatial_tools(registry)
    svc = ToolDispatchService(registry=registry)
    calls = {"n": 0}
    inner = registry.dispatch

    async def counting_dispatch(name, args, **kwargs):
        calls["n"] += 1
        return await inner(name, args, **kwargs)

    monkeypatch.setattr(registry, "dispatch", counting_dispatch)
    return svc, calls


async def test_scenario_g_same_analysis_twice_reuses(clean_session, monkeypatch):
    sid = clean_session
    monkeypatch.delenv("GIS_ANALYSIS_REUSE", raising=False)  # 默认开启
    svc, calls = await _dispatch_env(monkeypatch)

    input_ref = await session_data_manager.store(sid, _fc(), prefix="geojson")
    args = {"geojson": input_ref, "resolution": 9, "stat_method": "count"}

    r1 = await svc.dispatch(_tc("c1", "h3_binning", args), sid, set())
    assert r1.status == "ok"
    first_ref = r1.geojson_ref
    assert first_ref and first_ref.startswith("ref:")
    assert calls["n"] == 1  # 首次真实执行

    r2 = await svc.dispatch(_tc("c2", "h3_binning", args), sid, set())
    assert r2.status == "ok"
    assert calls["n"] == 1  # 第二次零执行 —— 复用命中
    assert r2.geojson_ref == first_ref
    assert "复用" in r2.llm_payload and first_ref in r2.llm_payload


async def test_scenario_h_param_change_misses(clean_session, monkeypatch):
    sid = clean_session
    svc, calls = await _dispatch_env(monkeypatch)
    input_ref = await session_data_manager.store(sid, _fc(), prefix="geojson")

    r1 = await svc.dispatch(
        _tc("c1", "h3_binning", {"geojson": input_ref, "resolution": 9}), sid, set())
    r2 = await svc.dispatch(
        _tc("c2", "h3_binning", {"geojson": input_ref, "resolution": 8}), sid, set())
    assert r1.status == "ok" and r2.status == "ok"
    assert calls["n"] == 2  # 参数变化 → 两次真实执行
    assert r2.geojson_ref != r1.geojson_ref


async def test_reuse_env_kill_switch(clean_session, monkeypatch):
    sid = clean_session
    monkeypatch.setenv("GIS_ANALYSIS_REUSE", "0")
    svc, calls = await _dispatch_env(monkeypatch)
    input_ref = await session_data_manager.store(sid, _fc(), prefix="geojson")
    args = {"geojson": input_ref, "resolution": 9}
    await svc.dispatch(_tc("c1", "h3_binning", args), sid, set())
    await svc.dispatch(_tc("c2", "h3_binning", args), sid, set())
    assert calls["n"] == 2  # 关闭复用 → 每次真实执行


async def test_non_analysis_tool_never_reused(clean_session, monkeypatch):
    """非 registry 认定的分析工具（不在 tool_to_capability）不走复用查询：
    同参调用两次 → 两次真实执行（若被复用则是 1 次）。"""
    sid = clean_session
    svc, calls = await _dispatch_env(monkeypatch)
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    assert "query_map_features" not in get_algorithm_registry().tool_to_capability()
    args = {"location": [104.0, 30.6]}
    await svc.dispatch(_tc("c1", "query_map_features", args), sid, set())
    await svc.dispatch(_tc("c2", "query_map_features", args), sid, set())
    assert calls["n"] == 2  # 非分析工具：零复用，每次真实执行


async def test_failure_result_not_reused(clean_session, monkeypatch):
    """失败调用不注册 → 同参重试必须真实执行（不会复用失败产物）。"""
    sid = clean_session
    svc, calls = await _dispatch_env(monkeypatch)
    # 空输入 → h3_binning lib 层失败（Invalid/Empty geojson）→ 不 mint ref
    empty_ref = await session_data_manager.store(
        sid, {"type": "FeatureCollection", "features": []}, prefix="geojson")
    r1 = await svc.dispatch(
        _tc("c1", "h3_binning", {"geojson": empty_ref, "resolution": 9}), sid, set())
    r2 = await svc.dispatch(
        _tc("c2", "h3_binning", {"geojson": empty_ref, "resolution": 9}), sid, set())
    assert r1.status == "error"
    assert r2.status == "error"  # 仍是失败，不是被“成功”谎言拦截
    assert calls["n"] == 2  # 失败不注册 → 两次都真实执行（诚实重试语义）
