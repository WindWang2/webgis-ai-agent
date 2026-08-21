"""#702 provider 缓存 + provenance 时效测试。

覆盖票面验收：
- 同参二次调用命中缓存（provider 只打一次）
- 缓存命中的 provenance.fetched_at == 首次计算时刻（非命中时刻）
- 错误形态不进缓存（重试穿透到 provider）
- get_traffic_status 显式不缓存（无 cache key 写入）但带 provenance
- local-first 命中不产生缓存条目
- gd_poi 本地结果带 generated_at（meta 存在时）
"""
import asyncio
import json

import pytest
from unittest.mock import MagicMock, patch

from app.lib.tool_cache import _reset_redis_client_for_tests


@pytest.fixture()
def _mock_redis():
    storage = {}
    locks = {}

    def fake_set(name, value, nx=False, px=None):
        if nx:
            if name in locks:
                return False
            locks[name] = value
            return True
        storage[name] = value
        return True

    with patch("app.lib.tool_cache._get_redis_client") as mock_client:
        mock_redis = MagicMock()
        mock_redis.set.side_effect = fake_set
        mock_redis.setex.side_effect = lambda k, ttl, v: storage.__setitem__(k, v)
        mock_redis.get.side_effect = lambda k: storage.get(k)
        mock_redis.exists.side_effect = lambda k: 1 if k in locks else 0
        mock_redis.eval.side_effect = lambda script, num, key, token: (
            1 if locks.get(key) == token and locks.pop(key, None) is not None else 0
        )
        mock_client.return_value = mock_redis
        _reset_redis_client_for_tests()
        yield mock_redis, storage, locks
        _reset_redis_client_for_tests()


def _fc(n=2):
    """最小合法 FeatureCollection（模拟 provider 返回）。"""
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0 + i, 30.0]},
             "properties": {"name": f"p{i}"}} for i in range(n)
        ],
        "count": n,
    }


@pytest.fixture()
def fake_amap_poi():
    """替换 _AMAP.search_poi，记录调用次数。"""
    import app.tools.chinese_maps as cm

    calls = {"n": 0}

    async def _fake(keyword, city, limit):
        calls["n"] += 1
        return _fc()

    with patch.object(cm._AMAP, "search_poi", _fake):
        yield calls


@pytest.fixture()
def fake_provider_keys():
    """测试环境无 API Key——让 with_fallback 认为三家都可用。"""
    with patch("app.tools.chinese_maps.http._has_provider", lambda p: True):
        yield


# ── 缓存命中与 provenance 保真 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_online_helper_caches_and_preserves_provenance(_mock_redis, fake_amap_poi, fake_provider_keys):
    """同参二次调用：provider 只打一次；缓存命中的 fetched_at == 首次计算时刻。"""
    from app.tools.chinese_maps import _search_poi_online

    r1 = await _search_poi_online(keyword="咖啡", city="成都", provider="amap", limit=20)
    first_fetched_at = r1["provenance"]["fetched_at"]
    assert fake_amap_poi["n"] == 1

    await asyncio.sleep(1.1)  # 时间戳精度为秒——跨秒后命中仍应保持原时刻
    r2 = await _search_poi_online(keyword="咖啡", city="成都", provider="amap", limit=20)

    assert fake_amap_poi["n"] == 1  # 未再打 provider
    assert r2 == r1  # payload 全等，含原始 fetched_at
    assert r2["provenance"]["fetched_at"] == first_fetched_at
    assert r2["provenance"]["source"] == "amap"


@pytest.mark.asyncio
async def test_geocode_cn_stamps_provenance_and_caches(_mock_redis, fake_provider_keys):
    """geocode_cn（with_fallback 收口盖章）：两次调用一次出网，provenance 在。"""
    import app.tools.chinese_maps as cm

    calls = {"n": 0}

    async def _fake(address, city):
        calls["n"] += 1
        return {"location": [104.06, 30.57], "adcode": "510100"}

    with patch.object(cm._AMAP, "geocode", _fake):
        r1 = await cm.geocode_cn(address="成都市", city="")
        r2 = await cm.geocode_cn(address="成都市", city="")

    assert calls["n"] == 1
    assert r1["provenance"] == r2["provenance"]
    assert r1["provenance"]["source"] == "amap"
    assert "fetched_at" in r1["provenance"]


@pytest.mark.asyncio
async def test_error_shape_not_cached(_mock_redis, fake_amap_poi, fake_provider_keys):
    """错误形态不写缓存：第二次调用穿透到 provider 重试。"""
    from app.tools.chinese_maps import _search_poi_online
    import app.tools.chinese_maps as cm

    async def _fail_then_ok(keyword, city, limit):
        fake_amap_poi["n"] += 1
        if fake_amap_poi["n"] == 1:
            return {"success": False, "code": "PROVIDER_ERROR", "message": "quota"}
        return _fc()

    with patch.object(cm._AMAP, "search_poi", _fail_then_ok):
        r1 = await _search_poi_online(keyword="书店", city="", provider="amap", limit=20)
        assert r1.get("success") is False
        r2 = await _search_poi_online(keyword="书店", city="", provider="amap", limit=20)

    assert fake_amap_poi["n"] == 2  # 错误未滞留缓存，重试穿透
    assert r2.get("type") == "FeatureCollection"


# ── 交通态势：显式不缓存 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_traffic_status_no_cache_but_stamped(_mock_redis):
    """get_traffic_status 不写任何 cache key，但输出带 provenance。"""
    import app.tools.chinese_maps as cm
    _, storage, _ = _mock_redis

    calls = {"n": 0}

    async def _fake(mode, rectangle, center, radius_m, level):
        calls["n"] += 1
        return {"roads": [{"name": "三环", "level": "拥堵"}]}

    with patch.object(cm._AMAP, "traffic", _fake), \
         patch("app.tools.chinese_maps._has_provider", lambda p: True):
        # 直接构造注册函数：register_chinese_map_tools 内部嵌套函数无法直接拿，
        # 走注册表取回。
        from app.tools.registry import ToolRegistry
        reg = ToolRegistry()
        cm.register_chinese_map_tools(reg)
        fn = reg._tools["get_traffic_status"]
        r1 = await fn(mode="rectangle", rectangle=[1, 2, 3, 4])
        r2 = await fn(mode="rectangle", rectangle=[1, 2, 3, 4])

    assert calls["n"] == 2  # 实时语义：每次都出网
    assert not [k for k in storage if k.startswith("tool_cache:")], "交通态势不得写缓存"
    assert r1["provenance"]["source"] == "amap"
    assert "fetched_at" in r1["provenance"]
    assert r2["provenance"]["fetched_at"] >= r1["provenance"]["fetched_at"]


# ── local-first 分支不进缓存 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_local_hit_bypasses_cache(_mock_redis, fake_amap_poi):
    """本地命中：返回本地 payload、不打 provider、不写缓存条目。"""
    _, storage, _ = _mock_redis
    from app.tools.registry import ToolRegistry
    import app.tools.chinese_maps as cm

    local_payload = {
        "type": "FeatureCollection", "features": [], "count": 5,
        "provider": "local_gd_poi", "source": "local_gd_poi",
        "generated_at": "2026-08-01T00:00:00", "bbox": [1, 2, 3, 4],
    }

    async def _run():
        reg = ToolRegistry()
        cm.register_chinese_map_tools(reg)
        fn = reg._tools["search_poi"]
        with patch("app.services.local_first.try_local_search_poi", lambda kw, c, lim: local_payload):
            return await fn(keyword="大学", city="成都", provider="amap", limit=20)

    r1 = await _run()
    r2 = await _run()

    assert r1["source"] == "local_gd_poi"
    assert r2["generated_at"] == "2026-08-01T00:00:00"
    assert fake_amap_poi["n"] == 0  # 本地命中不出网
    assert not [k for k in storage if k.startswith("tool_cache:")], "本地命中不得写缓存"


# ── gd_poi generated_at ──────────────────────────────────────────────────

def test_gd_poi_generated_at_reads_meta(tmp_path, monkeypatch):
    """meta.json 有 generated_at → 读出；缺失 → None（诚实缺位）。"""
    import app.services.local_first as lf
    from app.services import local_poi

    meta_file = tmp_path / "meta.json"
    meta_file.write_text(json.dumps({"generated_at": "2026-07-15T03:00:00"}), encoding="utf-8")
    monkeypatch.setattr(local_poi, "_meta_path", lambda: meta_file)
    assert lf._gd_poi_generated_at() == "2026-07-15T03:00:00"

    monkeypatch.setattr(local_poi, "_meta_path", lambda: tmp_path / "absent.json")
    assert lf._gd_poi_generated_at() is None


# ── TTL 常量契约 ─────────────────────────────────────────────────────────

def test_ttl_family_constants():
    """族常量存在且关系正确（防手滑改错）。"""
    from app.tools.chinese_maps import GEOCODE_CACHE_TTL_S, ROUTE_CACHE_TTL_S
    assert GEOCODE_CACHE_TTL_S == 3600
    assert ROUTE_CACHE_TTL_S == 600
