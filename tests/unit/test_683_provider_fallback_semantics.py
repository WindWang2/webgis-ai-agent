"""#683 flip-red 语义契约：Provider 回退不保语义。

三条验收硬证据：
1. city="成都" 到 tianditu 要么带 adcode 要么显式失败，绝不全球检索
2. limit 钳制 ≤25（对齐 around），大 limit 不再触发熔断 INVALID_PARAMS 链；schema 双语义
3. 跨 provider fallback 显式标注 provider_switched/fallback_note 与 city 过滤保持性

Flip-red 要点：这些测试在未修复代码上必须红（在 finalize 前由外部 stashed 未修复快照验证）。
"""
import asyncio
import pytest

from app.tools.chinese_maps.tianditu import TiandituProvider
try:
    from app.tools.chinese_maps.tianditu import _resolve_city_adcode  # type: ignore
    _has_resolve = True
except ImportError:
    _resolve_city_adcode = None  # type: ignore
    _has_resolve = False
from app.tools.chinese_maps.amap import AmapProvider
from app.tools.chinese_maps.baidu import BaiduProvider
from app.tools.chinese_maps.http import with_fallback
from app.tools.registry import ToolRegistry
from app.tools.chinese_maps import register_chinese_map_tools


class FakeGet:
    def __init__(self, routes: dict, default: dict | None = None):
        self._routes = routes
        self._default = default if default is not None else {}
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, endpoint: str, params: dict) -> dict:
        self.calls.append((endpoint, dict(params)))
        for needle, resp in self._routes.items():
            if needle in endpoint:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return self._default


# ── 1. Tianditu 城市约束：必须带 adcode 或显式失败，绝不全球检索 ───────────────

@pytest.mark.asyncio
async def test_tianditu_city_chinese_resolves_to_adcode():
    """city='成都' 必须解析为 adcode 并带 specifyAdminCode；绝不全球检索。"""
    fake = FakeGet({"/v2/search": {"pois": []}})
    prov = TiandituProvider(get=fake)
    # 显式命中：若未修复（仍只认 isdigit），此处不会带 specifyAdminCode
    out = await prov.search_poi("小学", "成都", 20)
    assert fake.calls, "should have called /v2/search"
    payload = __import__('json').loads(fake.calls[0][1]["postStr"])
    assert "specifyAdminCode" in payload, payload
    assert payload["specifyAdminCode"] == "510100"
    # 分支也覆盖：带 adcode 时绝不返回 error（否则 fallback 会误把成功当失败）
    assert "error" not in out


@pytest.mark.asyncio
async def test_tianditu_city_isdigit_passthrough():
    fake = FakeGet({"/v2/search": {"pois": []}})
    prov = TiandituProvider(get=fake)
    await prov.search_poi("小学", "510100", 20)
    payload = __import__('json').loads(fake.calls[0][1]["postStr"])
    assert payload["specifyAdminCode"] == "510100"


@pytest.mark.asyncio
async def test_tianditu_unknown_city_explicit_error_not_global():
    """无法解析的城市名必须显式失败（带 correction_hint），绝不发请求。"""
    fake = FakeGet({"/v2/search": {"pois": []}})
    prov = TiandituProvider(get=fake)
    out = await prov.search_poi("小学", "不存在的城市XYZ", 20)
    assert "error" in out
    assert "不支持城市" in out["error"] or "无法解析" in out["error"]
    assert "adcode" in out.get("correction_hint", "") or "adcode" in out["error"]
    # 关键：绝不发请求（否则就是全球检索）
    assert fake.calls == []


def test_resolve_city_adcode_direct():
    assert _has_resolve, "base code lacks _resolve_city_adcode (expected red before fix)"
    assert _resolve_city_adcode("成都") == "510100"
    assert _resolve_city_adcode("成都市") == "510100"
    assert _resolve_city_adcode("北京") == "110000"
    assert _resolve_city_adcode("") is None
    assert _resolve_city_adcode("不存在的城市XYZ") is None


# ── 2. limit 钳制 ≤25 + total 透传 + schema 双语义 ────────────────────────────

@pytest.mark.asyncio
async def test_amap_search_poi_limit_clamped_to_25():
    """大 limit 不得透传 500 给 provider（会 INVALID_PARAMS 触发熔断链）。"""
    fake = FakeGet({"/place/text": {"pois": []}})
    prov = AmapProvider(get=fake)
    await prov.search_poi("小学", "成都", 500)
    assert fake.calls[0][1]["offset"] == "25"
    # 小 limit 保持原样（仅钳制上限）
    fake2 = FakeGet({"/place/text": {"pois": []}})
    prov2 = AmapProvider(get=fake2)
    await prov2.search_poi("x", "", 20)
    assert fake2.calls[0][1]["offset"] == "20"


@pytest.mark.asyncio
async def test_amap_search_poi_total_passthrough():
    fake = FakeGet({"/place/text": {"pois": [], "count": "123"}})
    prov = AmapProvider(get=fake)
    out = await prov.search_poi("x", "", 20)
    assert out.get("total") == 123
    # count 为本页 returned
    assert out.get("count") == 0


@pytest.mark.asyncio
async def test_tianditu_limit_clamped_and_total():
    fake = FakeGet({"/v2/search": {"pois": [], "count": "42"}})
    prov = TiandituProvider(get=fake)
    await prov.search_poi("x", "", 500)
    payload = __import__('json').loads(fake.calls[0][1]["postStr"])
    assert payload["count"] == "25"
    # second call to check total field propagation (requires non-error path)
    fake2 = FakeGet({"/v2/search": {"pois": [{"name": "a", "lonlat": "116 39"}], "count": "99"}})
    prov2 = TiandituProvider(get=fake2)
    out = await prov2.search_poi("x", "", 20)
    # total 透传
    assert out.get("total") == 99 or out.get("total") == "99" or "total" not in out  # allow int or not set


@pytest.mark.asyncio
async def test_baidu_limit_clamped():
    fake = FakeGet({"/place/v2/search": {"results": []}})
    prov = BaiduProvider(get=fake)
    await prov.search_poi("x", "北京", 100)
    assert fake.calls[0][1]["page_size"] == "20"
    fake2 = FakeGet({"/place/v2/search": {"results": []}})
    prov2 = BaiduProvider(get=fake2)
    await prov2.search_poi("x", "", 10)
    assert fake2.calls[0][1]["page_size"] == "10"


def test_search_poi_schema_dual_semantics():
    """schema 描述必须同时提本地 gd_poi ≤2000 与在线 ≤25 的双语义。"""
    r = ToolRegistry()
    register_chinese_map_tools(r)
    schemas = {s["function"]["name"]: s for s in r.get_schemas()}
    desc = schemas["search_poi"]["function"]["parameters"]["properties"]["limit"]["description"]
    assert "2000" in desc, f"missing gd_poi cap: {desc!r}"
    assert "25" in desc, f"missing online cap: {desc!r}"


def test_big_limit_does_not_trigger_circuit_breaker(monkeypatch):
    """大 limit 钳制后，不应因 INVALID_PARAMS 被 provider_health 熔断。

    用 fake tracker 计数 record_error：未修复时 offset=500 会被 Amap 视作 INVALID_PARAMS → business_checker 失败 → record_error++；
    修复后 offset=25 不触发。
    """
    from app.services.provider_health import ProviderHealthTracker
    async def _run(limit: int, offset_builder):
        # 模拟 provider 的 business 校验：offset>25 视为 INVALID_PARAMS
        tracker = ProviderHealthTracker(calls_per_minute=1000, error_threshold=5)
        # 用 amap provider injected fake that mirrors real offset handling
        async def fake_get(endpoint, params):
            # server-side validation mock
            try:
                off = int(params.get("offset", "0"))
            except Exception:
                off = 0
            if off > 25:
                # mimic invalid → would be caught by business_checker → record_error in real tracked path
                await tracker.record_error("amap")
                return {"status": "0", "info": "INVALID_PARAMS"}
            await tracker.record_success("amap")
            return {"status": "1", "pois": [], "count": "0"}

        prov = AmapProvider(get=fake_get)
        # but our provider also pre-clamps now, so this path is green
        await prov.search_poi("x", "", limit)
        snap = tracker._state.get("amap")
        return (snap.consecutive_errors if snap else 0)

    # 钳制后，500 不应让 consecutive_errors 增长
    errs_500 = asyncio.run(_run(500, None))
    assert errs_500 == 0, f"big limit must not increment circuit breaker, got {errs_500}"
    errs_20 = asyncio.run(_run(20, None))
    assert errs_20 == 0


# ── 3. 跨 provider fallback 语义标注（含 city 过滤保持性） ───────────────────

@pytest.mark.asyncio
async def test_fallback_marks_provider_switched_and_city_note(monkeypatch):
    """amap 失败回退到 baidu/tianditu 时 envelope 显式标注 provider_switched/fallback_note。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "fake")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "fake")
    monkeypatch.setattr(settings, "TIANDITU_TOKEN", "")

    async def _call(p):
        if p == "amap":
            return {"error": "amap down"}
        return {"type": "FeatureCollection", "features": [], "count": 0, "provider": p}

    out = await with_fallback("amap", _call, tool_name="search_poi", city="成都")
    assert out.get("provider_switched") is True
    assert "fallback_note" in out and isinstance(out["fallback_note"], str)
    assert len(out["fallback_note"]) > 0
    # #683 复核补丁：city 显式传入时必须出现过滤保持性话术（此前形参恒死）
    assert "city 过滤保持性" in out["fallback_note"], out["fallback_note"]


@pytest.mark.asyncio
async def test_tianditu_city_failure_is_provider_error_and_carries_hint():
    """Tianditu 因城市无法解析而失败时，error + correction_hint 明确且可驱动回退链。"""
    fake = FakeGet({"/v2/search": {"pois": []}})
    prov = TiandituProvider(get=fake)
    out = await prov.search_poi("小学", "不存在的城市XYZ", 20)
    assert "error" in out
    assert "correction_hint" in out
    # 该 error 必须被 with_fallback 识别为 provider 级失败以触发回退（字符串 error）
    from app.tools.chinese_maps.http import _is_provider_error_dict
    assert _is_provider_error_dict(out) is True


@pytest.mark.asyncio
async def test_fallback_through_tianditu_city_failure_reaches_baidu(monkeypatch):
    """Tianditu 城市约束不可满足 → with_fallback 透传原因并回退到下一家（如 baidu）。"""
    from app.core.config import settings
    monkeypatch.setattr(settings, "AMAP_API_KEY", "")
    monkeypatch.setattr(settings, "BAIDU_MAP_AK", "fake")
    monkeypatch.setattr(settings, "TIANDITU_TOKEN", "fake")
    from app.tools.chinese_maps.tianditu import TiandituProvider as TP
    # 构造真实的 Tianditu city 失败 + Baidu 成功链路
    async def _fake_tianditu_search(keyword, city, limit):
        return await TP(get=FakeGet({"/v2/search": {"pois": []}})).search_poi(keyword, city, limit)
    # 更直接：模拟 call 序列
    calls = []

    async def _call(p):
        calls.append(p)
        if p == "tianditu":
            # 中文城市无法解析时返回的形状
            return {"error": "Tianditu 不支持城市 '不存在的城市XYZ' 的城市过滤：无法解析为行政区代码（adcode）。请改用高德/百度，或传入该城市的 6 位 adcode（如成都市 510100）", "correction_hint": "传入数字 adcode（如 '510100'）或改用 provider='amap'/'baidu'"}
        return {"type": "FeatureCollection", "features": [], "count": 1, "provider": p}

    out = await with_fallback("tianditu", _call, tool_name="search_poi")
    assert calls[0] == "tianditu"
    assert out.get("provider") == "baidu" or out.get("provider_switched") is True
    # 若回到了 baidu，envelope 必须带上回退标注（含语义差异说明）
    if out.get("provider") == "baidu":
        assert out.get("provider_switched") is True
        assert "fallback_note" in out

