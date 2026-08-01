"""chinese_maps 包结构 + 派发契约回归测试。

不打真实地图 API — 只验证：
- http.py 的 helper（_fallback_order / _has_provider / _speed_mps / with_fallback）行为
- AmapProvider 是可注入 get 的深模块（fake-GET 缝）
- register_chinese_map_tools 跑完后注册了预期工具
- 派发路由：geocode_cn 按 fallback_order 调对应 provider

M2 时代的"amap.py 只能含 _*_amap 自由函数"结构断言已随架构评审 F1
（Amap 深化为 AmapProvider 类）退役 —— 此文件不再断言旧的自由函数布局。
"""
import pytest

from app.tools.chinese_maps import (
    register_chinese_map_tools,
    geocode_cn,
    batch_geocode_cn,
    _AMAP,
)
from app.tools.chinese_maps.http import (
    _has_provider,
    _fallback_order,
    _speed_mps,
    _VALID_PROVIDERS,
    with_fallback,
)
from app.tools.registry import ToolRegistry


def test_fallback_order_preferred_first():
    order = _fallback_order("baidu")
    assert order[0] == "baidu"
    assert set(order) == set(_VALID_PROVIDERS)


def test_fallback_order_exclude():
    order = _fallback_order("amap", exclude={"baidu"})
    assert "baidu" not in order
    assert order[0] == "amap"


def test_has_provider_reads_settings(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "")
    assert _has_provider("amap") is True
    assert _has_provider("baidu") is False
    assert _has_provider("unknown") is False


def test_speed_mps_known_modes():
    assert _speed_mps("walking") > 0
    assert _speed_mps("driving") > _speed_mps("walking")


def test_register_tool_count():
    """register_chinese_map_tools 应注册 16 个 LLM 工具。"""
    r = ToolRegistry()
    register_chinese_map_tools(r)
    names = r.list_tools()
    # 关键工具齐全
    expected = {
        "search_poi", "geocode_cn", "reverse_geocode_cn", "batch_geocode_cn",
        "plan_route", "get_district", "distance_matrix_cn", "isochrone_analysis",
        "search_poi_around", "search_poi_polygon", "input_tips",
        "search_transit_route", "get_traffic_status",
        "get_admin_division", "get_child_districts", "get_sub_districts_polygons",
    }
    missing = expected - set(names)
    assert not missing, f"register 漏了: {missing}"


@pytest.mark.asyncio
async def test_with_fallback_no_key_returns_error(monkeypatch):
    """with_fallback 在没有任何 provider key 时返回 no_key_msg。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "")
    monkeypatch.setattr(settings, "TIANDITU_TOKEN", "")

    called = []

    async def _call(p):
        called.append(p)
        return {"provider": p}

    out = await with_fallback("amap", _call, no_key_msg="custom msg")
    assert out == {"error": "custom msg"}
    # 没有 key → 没有任何 provider 被调用
    assert called == []


@pytest.mark.asyncio
async def test_geocode_cn_routes_to_amap_provider(monkeypatch):
    """geocode_cn 应按 fallback_order 调对应 provider 方法。

    走的是新的注入缝：monkeypatch _AMAP._get（AmapProvider 的 tracked-GET 缝），
    而非旧的 _geocode_amap 自由函数。
    """
    called = []

    async def fake_get(endpoint, params):
        called.append((endpoint, params.get("address")))
        # 模拟 amap geocode 返回结构
        return {
            "geocodes": [{"location": "116.0,39.0", "formatted_address": "北京"}],
        }

    # 模拟有 amap key
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(_AMAP, "_get", fake_get)

    result = await geocode_cn("北京", "", provider="amap")
    assert called == [("/geocode/geo", "北京")]
    assert result["provider"] == "amap"
    # geocode 坐标已从 GCJ-02 归一化到 WGS84
    assert result["results"][0]["location"]  # non-empty


@pytest.mark.asyncio
async def test_batch_geocode_rejects_oversized():
    out = await batch_geocode_cn(["x"] * 101)
    assert "error" in out


@pytest.mark.asyncio
async def test_batch_geocode_rejects_unknown_provider():
    out = await batch_geocode_cn(["x"], provider="bing")
    assert "error" in out
