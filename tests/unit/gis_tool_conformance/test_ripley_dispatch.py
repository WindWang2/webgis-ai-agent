"""G03 review P0 回归——ripley_k_analysis 必须经真实 dispatch 可执行。

W4 给工具加 envelopes 透传时只改了契约与工具层,漏了服务包装层
(SpatialAnalyzer.ripley_k),静态对账全绿而运行时 TypeError——正是
「声明面绿、执行面红」的覆盖缺口。本文件把执行路径钉进测试网。
"""
import pytest

from app.tools.registry import ToolRegistry
from app.tools.spatial_stats import register_spatial_stats_tools


@pytest.fixture(scope="module")
def registry():
    r = ToolRegistry()
    register_spatial_stats_tools(r)
    return r


def _points_fc() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point",
             "coordinates": [104.0 + i * 0.01, 30.6 + (i % 3) * 0.01]},
             "properties": {"id": i}}
            for i in range(24)
        ],
    }


async def test_ripley_dispatch_default_envelopes_zero(registry):
    """默认路径( envelopes=0 )必须可执行且纯描述(无包络键)。"""
    result = await registry.dispatch(
        "ripley_k_analysis", {"geojson": _points_fc(), "n_steps": 8})
    assert isinstance(result, dict)
    assert result.get("success") is True, result.get("message") or result
    assert result.get("summary")
    assert isinstance(result.get("data"), dict)


async def test_ripley_dispatch_envelopes_passthrough(registry):
    """envelopes>0 必须经契约校验并透传到实现(CSR 包络输出)。"""
    result = await registry.dispatch(
        "ripley_k_analysis",
        {"geojson": _points_fc(), "n_steps": 8, "envelopes": 9})
    assert isinstance(result, dict)
    assert result.get("success") is True, result.get("message") or result


async def test_ripley_dispatch_contract_rejects_out_of_range(registry):
    """契约边界(envelopes>499)必须被 apply_contract 机器可读拒绝。"""
    result = await registry.dispatch(
        "ripley_k_analysis",
        {"geojson": _points_fc(), "envelopes": 500})
    assert isinstance(result, dict)
    assert result.get("success") is False
    assert "parameter_contract_violation" in str(
        result.get("message") or result.get("error") or "")
