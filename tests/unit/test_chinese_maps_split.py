"""chinese_maps 包结构 + 派发契约回归测试。

不打真实地图 API — 只验证：
- http.py 的 helper（_fallback_order / _has_provider / _speed_mps / with_fallback）行为
- AmapProvider 是可注入 get 的深模块（fake-GET 缝）
- register_chinese_map_tools 跑完后注册了预期工具
- 派发路由：geocode_cn 按 fallback_order 调对应 provider

M2 时代的"amap.py 只能含 _*_amap 自由函数"结构断言已随架构评审 F1
（Amap 深化为 AmapProvider 类）退役 —— 此文件不再断言旧的自由函数布局。
"""
import asyncio

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


@pytest.mark.asyncio
async def test_with_fallback_error_dict_triggers_next_provider(monkeypatch):
    """provider 级失败（WAF 418/配额/业务错误）返回 error dict，也必须回退。

    tracked_provider_get 对非 200/JSON 失败不抛异常而是返回 {"error": ...}——
    天地图被 WAF 频控时若不回退，整项能力直接不可用。
    """
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "")
    monkeypatch.setattr(settings, "TIANDITU_TOKEN", "fake")

    calls = []

    async def _call(p):
        calls.append(p)
        if p == "amap":
            return {"error": "Amap API HTTP 418"}
        return {"provider": p}

    out = await with_fallback("amap", _call, no_key_msg="全部失败")
    assert out == {"provider": "tianditu"}
    assert calls == ["amap", "tianditu"]


@pytest.mark.asyncio
async def test_with_fallback_all_fail_reports_first_detail(monkeypatch):
    """全部失败时返回 no_key_msg 并带上第一个 provider 的失败详情。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "")
    monkeypatch.setattr(settings, "TIANDITU_TOKEN", "fake")

    async def _call(p):
        return {"error": f"{p} down"}

    out = await with_fallback("amap", _call, no_key_msg="地图服务不可用")
    assert out["error"].startswith("地图服务不可用（amap down")


# ── Issue #432: provider 总超时必须触发回退 ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_exc", [asyncio.TimeoutError, TimeoutError])
async def test_with_fallback_timeout_error_triggers_next_provider(monkeypatch, timeout_exc):
    """provider 总超时（非 ClientError 家族）必须回退到下一家（issue #432）。

    tracked_provider_get 的 total timeout 让 aiohttp 抛 asyncio.TimeoutError
    （py3.11+ 即内建 TimeoutError，OSError 子类），旧 _FALLBACK_ERRORS 只认
    ClientError 家族 → 异常直接穿透 with_fallback，备选 provider 从未被尝试。
    """
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "fake")
    monkeypatch.setattr(settings, "TIANDITU_TOKEN", "")

    calls = []

    async def _call(p):
        calls.append(p)
        if p == "amap":
            raise timeout_exc()
        return {"provider": p}

    out = await with_fallback("amap", _call, no_key_msg="全部失败")
    assert out == {"provider": "baidu"}
    assert calls == ["amap", "baidu"]


@pytest.mark.asyncio
async def test_geocode_cn_amap_timeout_falls_back_to_baidu(monkeypatch):
    """整工具链路：首选 provider 黑洞超时 → 自动降级到百度并返回其结果。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "fake")
    monkeypatch.setattr(settings, "TIANDITU_TOKEN", "")

    async def amap_get(endpoint, params):
        raise TimeoutError("total timeout")

    async def baidu_get(endpoint, params):
        return {"status": 0, "result": {"location": {"lng": 116.40, "lat": 39.90},
                                        "level": "ROAD"}}

    monkeypatch.setattr(_AMAP, "_get", amap_get)
    from app.tools.chinese_maps import _BAIDU
    monkeypatch.setattr(_BAIDU, "_get", baidu_get)

    result = await geocode_cn("北京市海淀区", "", provider="amap")
    assert result["provider"] == "baidu"
    assert result["count"] == 1


# ── Issue #479: fallback 触发条件收敛（不掩盖自身代码 bug）─────────────────


@pytest.mark.asyncio
async def test_with_fallback_error_none_business_dict_does_not_fall_back(monkeypatch):
    """业务 payload 里带 error=None 不是 provider 失败，不得触发回退（#479）。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "fake")
    monkeypatch.setattr(settings, "TIANDITU_TOKEN", "")

    calls = []

    async def _call(p):
        calls.append(p)
        if p == "amap":
            # 业务字段恰好叫 error、值为 None —— 旧条件 `"error" in result` 会误判
            return {"provider": "amap", "results": [], "error": None}
        return {"provider": p}

    out = await with_fallback("amap", _call)
    assert out == {"provider": "amap", "results": [], "error": None}
    assert calls == ["amap"], "baidu must not be tried for a non-error payload"


@pytest.mark.asyncio
@pytest.mark.parametrize("bug_exc", [KeyError, ValueError, TypeError])
async def test_with_fallback_own_code_bugs_propagate(monkeypatch, bug_exc):
    """KeyError/ValueError/TypeError 是自身代码/schema bug —— 必须穿透为代码
    错误，不得被伪装成 provider 失败触发回退（#479）。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "fake")
    monkeypatch.setattr(settings, "TIANDITU_TOKEN", "")

    calls = []

    async def _call(p):
        calls.append(p)
        if p == "amap":
            raise bug_exc("schema drift in our unwrap code")
        return {"provider": p}

    with pytest.raises(bug_exc):
        await with_fallback("amap", _call)
    assert calls == ["amap"], "fallback must not fire for own-code bugs"


@pytest.mark.asyncio
async def test_geocode_cn_schema_bug_surfaces_as_code_error(monkeypatch):
    """整工具链路：per-provider 调用抛 KeyError（schema bug）→ 直接暴露，
    不降级到 baidu、不产出误导性的『provider 失败』错误。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "fake")
    monkeypatch.setattr(settings, "TIANDITU_TOKEN", "fake")

    async def buggy_amap_geocode(address, city):
        raise KeyError("amap response schema drifted")

    baidu_called = []

    async def ok_baidu_geocode(address, city):
        baidu_called.append(address)
        return {"results": [], "count": 0, "provider": "baidu"}

    monkeypatch.setattr(_AMAP, "geocode", buggy_amap_geocode)
    from app.tools.chinese_maps import _BAIDU
    monkeypatch.setattr(_BAIDU, "geocode", ok_baidu_geocode)

    with pytest.raises(KeyError):
        await geocode_cn("北京", "", provider="amap")
    assert baidu_called == []


@pytest.mark.asyncio
async def test_geocode_cn_business_error_dict_still_falls_back(monkeypatch):
    """f545d7c 行为保留（全工具链路 + 真实 payload 形状）：amap 业务失败
    返回 {"error": "<str>"}（business_checker / HTTP 非 200）→ 回退到 baidu。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "fake")
    monkeypatch.setattr(settings, "TIANDITU_TOKEN", "")

    async def amap_get(endpoint, params):
        # tracked_provider_get business_checker 失败时的真实返回形状
        return {"error": "Amap: INVALID_USER_KEY", "status": "0", "infocode": "10001"}

    async def baidu_get(endpoint, params):
        # 百度 geocoding/v3 真实成功形状
        return {"status": 0, "result": {"location": {"lng": 116.40, "lat": 39.90}, "level": "ROAD"}}

    monkeypatch.setattr(_AMAP, "_get", amap_get)
    from app.tools.chinese_maps import _BAIDU
    monkeypatch.setattr(_BAIDU, "_get", baidu_get)

    result = await geocode_cn("北京市海淀区", "", provider="amap")
    assert result["provider"] == "baidu"
    assert result["count"] == 1
