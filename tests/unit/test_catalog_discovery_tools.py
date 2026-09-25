"""F07：catalog_discover / catalog_lookup 工具面契约测试。

覆盖：注册元数据（tier/只读面/有界 result_size_policy）、发现工具的有界
payload、参数清洗、错误路径（未知 kind / 空 capabilities）、确定性。
"""
from __future__ import annotations

import asyncio

import pytest

from app.tools import init_tools
from app.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def registry():
    reg = ToolRegistry()
    init_tools(reg)
    return reg


def test_tools_registered_with_readonly_contract(registry):
    for name in ("catalog_discover", "catalog_lookup"):
        assert registry.has(name), f"{name} not registered"
        meta = registry.metadata(name)
        assert meta.get("tier") == 2
        assert meta.get("side_effect") == "pure"
        assert meta.get("deterministic") is True
        assert meta.get("result_size_policy") == "bounded"
        descriptor = registry.descriptor(name)
        assert descriptor.model_visible


@pytest.mark.asyncio
async def test_catalog_discover_happy_path(registry):
    result = await registry.dispatch("catalog_discover", {
        "capabilities": "admin_aggregation", "limit": 3,
    })
    assert result["candidates"], "expected executable candidates"
    assert len(result["candidates"]) <= 3
    first = result["candidates"][0]
    assert {"capability", "algorithm", "tool", "score", "reasons",
            "evidence"} <= set(first.keys())
    assert len(result["catalog_generation_fingerprint"]) == 32


@pytest.mark.asyncio
async def test_catalog_discover_requires_capabilities(registry):
    result = await registry.dispatch("catalog_discover", {"capabilities": ""})
    assert result.get("error") == "capabilities_required"


@pytest.mark.asyncio
async def test_catalog_discover_invalid_args_reported(registry):
    result = await registry.dispatch("catalog_discover", {
        "capabilities": ",".join(f"c{i}" for i in range(20)),
    })
    assert result.get("error") == "invalid_query"


@pytest.mark.asyncio
async def test_catalog_discover_unknown_capability_disclosed(registry):
    result = await registry.dispatch("catalog_discover", {
        "capabilities": "no_such_capability", "limit": 2,
    })
    assert result["candidates"] == []
    assert result["excluded"].get("capability_not_in_catalog") == 1


@pytest.mark.asyncio
async def test_catalog_lookup_roundtrip_and_errors(registry):
    ok = await registry.dispatch("catalog_lookup", {
        "kind": "tool", "entry_id": "spatial_aggregate"})
    assert ok["id"] == "spatial_aggregate"
    assert ok["kind"] == "tool"
    assert "certification" in ok and "fallback_targets" in ok
    missing = await registry.dispatch("catalog_lookup", {
        "kind": "tool", "entry_id": "no_such_tool"})
    assert missing.get("error") == "entry_not_found"
    bad_kind = await registry.dispatch("catalog_lookup", {
        "kind": "magic", "entry_id": "x"})
    assert bad_kind.get("error") == "unknown_kind"


@pytest.mark.asyncio
async def test_catalog_discover_deterministic(registry):
    args = {"capabilities": "admin_aggregation,poi_distribution", "limit": 5}
    a = await registry.dispatch("catalog_discover", args)
    b = await registry.dispatch("catalog_discover", args)
    assert a["candidates"] == b["candidates"]
