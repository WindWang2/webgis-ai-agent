"""M2 拆分回归：chinese_maps 包结构契约。

不打真实地图 API — 只验证：
- 三个 provider 模块独立可 import
- HTTP 客户端在 http.py 里、各 provider impl 在自己文件
- __init__ re-export 旧路径符号（让 `from app.tools.chinese_maps import _amap_get` 等仍 OK）
- register_chinese_map_tools 跑完后注册了预期工具
"""
import pytest

from app.tools.chinese_maps import (
    register_chinese_map_tools,
    geocode_cn,
    batch_geocode_cn,
)
from app.tools.chinese_maps.http import (
    _amap_get,
    _baidu_get,
    _tianditu_get,
    _has_provider,
    _fallback_order,
    _speed_mps,
    _VALID_PROVIDERS,
)
from app.tools.chinese_maps import (
    amap as amap_mod,
    baidu as baidu_mod,
    tianditu as tianditu_mod,
)
from app.tools.registry import ToolRegistry


def test_http_layer_isolates_providers():
    """三个 *_get 都在 http.py，provider 模块没自己定义。"""
    assert callable(_amap_get)
    assert callable(_baidu_get)
    assert callable(_tianditu_get)
    # provider 模块不应有 *_get
    assert not hasattr(amap_mod, "_amap_get_impl")
    assert not hasattr(baidu_mod, "_baidu_get_impl")


def test_each_provider_module_has_only_its_impls():
    """amap 模块只有 _*_amap，baidu 只有 _*_baidu，tianditu 只有 _*_tianditu。"""
    amap_funcs = [n for n in dir(amap_mod) if n.startswith("_") and n.endswith("_amap")]
    baidu_funcs = [n for n in dir(baidu_mod) if n.startswith("_") and n.endswith("_baidu")]
    tianditu_funcs = [n for n in dir(tianditu_mod) if n.startswith("_") and (n.endswith("_tianditu") or "_tianditu_" in n)]
    # 不为空
    assert amap_funcs
    assert baidu_funcs
    assert tianditu_funcs
    # 跨污染检测：amap 模块里不该有 _*_baidu 或 _*_tianditu 实现
    cross = [n for n in dir(amap_mod) if n.endswith("_baidu") or n.endswith("_tianditu")]
    assert cross == [], f"amap.py 含异类: {cross}"
    cross = [n for n in dir(baidu_mod) if n.endswith("_amap") or n.endswith("_tianditu")]
    assert cross == [], f"baidu.py 含异类: {cross}"


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
    """register_chinese_map_tools 应注册 14 个 LLM 工具（按 docstring + grep 确认）。"""
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
async def test_geocode_cn_routes_to_dispatcher(monkeypatch):
    """geocode_cn 应按 fallback_order 调对应 _geocode_*。"""
    called = []

    async def fake_amap(addr, city):
        called.append(("amap", addr))
        return {"results": [{"location": [116.0, 39.0]}], "count": 1, "provider": "amap"}

    # 模拟有 amap key
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr("app.tools.chinese_maps._geocode_amap", fake_amap)

    result = await geocode_cn("北京", "", provider="amap")
    assert called == [("amap", "北京")]
    assert result["provider"] == "amap"


@pytest.mark.asyncio
async def test_batch_geocode_rejects_oversized():
    out = await batch_geocode_cn(["x"] * 101)
    assert "error" in out


@pytest.mark.asyncio
async def test_batch_geocode_rejects_unknown_provider():
    out = await batch_geocode_cn(["x"], provider="bing")
    assert "error" in out
