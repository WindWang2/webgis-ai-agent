"""G03 catalog_conformance 工具接线测试——经真实 registry.dispatch 调用链。

覆盖：正常报告（8 面）、未知工具、空参数、registry 缺席退化、载荷有界、
新工具自身的 conformance 健康度（自反检查）。
"""
import json

import pytest

from app.tools.catalog_discovery_tools import register_catalog_discovery_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_catalog_discovery_tools(r)
    return r


@pytest.fixture
def live_registry(registry, monkeypatch):
    """把一个带样例工具的 registry 挂进 bridge 持有面（经 monkeypatch
    还原，零全局污染）。"""
    registry.register(
        "sample_tool", "sample", func=lambda **_: {"ok": True},
        side_effect="pure", network=False, deterministic=True,
        result_size_policy="bounded", output_semantic_type="object")
    import app.agent_pi_bridge as bridge

    monkeypatch.setattr(bridge, "try_get_tool_registry", lambda: registry)
    return registry


async def test_dispatch_returns_full_report(registry, live_registry):
    result = await registry.dispatch("catalog_conformance", {"tool": "sample_tool"})
    assert isinstance(result, dict) and "error" not in result
    assert result["tool"] == "sample_tool"
    assert result["overall"] == "pass"
    faces = {f["face"]: f for f in result["faces"]}
    assert set(faces) == {
        "registration", "search", "planner", "pi_native",
        "proxy", "dispatch", "result", "science"}
    assert faces["registration"]["status"] == "pass"
    assert faces["pi_native"]["status"] == "not_evaluated"
    assert result["registry_generation"] == "" or isinstance(
        result["registry_generation"], str)
    assert "note" in result and "authoritative" in result["note"]


async def test_dispatch_unknown_tool_error(registry, live_registry):
    result = await registry.dispatch("catalog_conformance", {"tool": "ghost_tool"})
    assert result == {"error": "tool_not_found", "tool": "ghost_tool"}


async def test_dispatch_blank_tool_name(registry, live_registry):
    result = await registry.dispatch("catalog_conformance", {"tool": "   "})
    assert result["error"] == "tool_name_required"


async def test_dispatch_registry_unavailable(registry, monkeypatch):
    import app.agent_pi_bridge as bridge

    monkeypatch.setattr(bridge, "try_get_tool_registry", lambda: None)
    result = await registry.dispatch("catalog_conformance", {"tool": "any"})
    assert result["error"] == "registry_unavailable"


async def test_report_payload_bounded(registry, live_registry):
    """报告载荷有界（≤4KB，对齐 pi_result_abi envelope 预算纪律）。"""
    result = await registry.dispatch("catalog_conformance", {"tool": "sample_tool"})
    size = len(json.dumps(result, ensure_ascii=False))
    assert size < 4096, f"conformance payload {size}B exceeds 4KB budget"


async def test_dispatch_missing_args_rejected_by_schema(registry, live_registry):
    """args_model required 字段缺失 → dispatch 校验层拒绝（真实入向闸）。"""
    result = await registry.dispatch("catalog_conformance", {})
    assert isinstance(result, dict)
    assert result.get("error") or "tool" not in result


def test_catalog_conformance_tool_is_itself_conformant(live_registry):
    """自反检查：新工具自己的 8 面对账不出现 fail。"""
    from app.lib.gis.tool_capability_conformance import (
        STATUS_FAIL,
        ConformanceFacts,
        audit_tool_conformance,
    )

    facts = ConformanceFacts(
        registry=live_registry,
        capability_ids=("plan_workflow_orchestration",),
        algorithms=(),
        parameter_contracts={},
        native_tool_names=(),
        domain_keywords={"meta": ["目录"]},
    )
    report = audit_tool_conformance("catalog_conformance", facts)
    assert report.overall != STATUS_FAIL
    by_face = {v.face: v.status for v in report.faces}
    assert by_face["registration"] == "pass"
    assert by_face["search"] == "pass"  # tier-2 + meta 域有关键词覆盖
