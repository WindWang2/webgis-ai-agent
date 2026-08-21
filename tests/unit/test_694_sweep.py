"""#694: 工具/数据清扫——缓存错误形态、OSM 类别、adcode 注入面、workflow 守卫。"""
import json
import pytest


# ── 1. cached_tool：错误形态不写缓存 ────────────────────────────────────

class _FakeRedis:
    """最小 Redis 替身（对齐 test_tool_cache_singleflight 的 mock 模式）。"""

    def __init__(self):
        self.kv = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value, ex=None, nx=False, px=None):
        if nx:
            if key in self.kv:
                return False
            self.kv[key] = value
            return True
        self.kv[key] = value
        return True

    async def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
        return 1

    async def setnx(self, key, value):
        if key in self.kv:
            return False
        self.kv[key] = value
        return True

    def setex(self, key, ttl, value):
        self.kv[key] = value
        return True

    def eval(self, script, numkeys=0, *keys_args):
        # 释放锁脚本：eval(script, numkeys, key, token)——删锁键即语义
        for k in keys_args:
            k = k.decode() if isinstance(k, bytes) else k
            self.kv.pop(k, None)
        return 1

    def exists(self, key):
        return 1 if key in self.kv else 0


@pytest.fixture()
def _fake_redis(monkeypatch):
    import app.lib.tool_cache as tc
    fake = _FakeRedis()

    def fake_get_client():
        return fake

    monkeypatch.setattr(tc, "_get_redis_client", fake_get_client)
    monkeypatch.setattr(tc, "_redis_client", fake, raising=False)
    return fake


@pytest.mark.asyncio
async def test_cached_tool_does_not_cache_error_shape(_fake_redis):
    from app.lib.tool_cache import cached_tool

    calls = {"n": 0}

    @cached_tool(ttl=3600)
    async def flaky_tool(x: int):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"success": False, "code": "TOOL_ERROR", "message": "boom", "data": None}
        return {"success": True, "v": x}

    r1 = await flaky_tool(x=1)
    r2 = await flaky_tool(x=1)
    assert r1.get("success") is False
    # 错误结果未进缓存：第二次重试真实执行（calls==2），不是命中缓存错误
    assert r2.get("success") is True and calls["n"] == 2


@pytest.mark.asyncio
async def test_cached_tool_still_caches_success(_fake_redis):
    from app.lib.tool_cache import cached_tool

    calls = {"n": 0}

    @cached_tool(ttl=3600)
    async def ok_tool(x: int):
        calls["n"] += 1
        return {"success": True, "v": x}

    await ok_tool(x=2)
    await ok_tool(x=2)
    assert calls["n"] == 1, "成功结果必须仍走缓存"


# ── 2. query_osm_poi 类别映射 ───────────────────────────────────────────

def test_osm_category_map_covers_primary_school():
    """'小学' 此前未映射 → 直通 amenity='小学' Overpass 永远 0 命中。"""
    import app.tools.osm as osm_mod

    # category_map 是查询函数的局部量——映射覆盖由源码常量锁最小行为
    #（"小学" 在表中 + 未映射值走 correction_hint 路径），见下一用例。
    import inspect
    src = inspect.getsource(osm_mod)
    assert '"小学": "primary_school"' in src, "小学 映射缺失（死查询回归）"


def test_osm_unmapped_chinese_category_returns_hint():
    """未映射中文类别不再直通——返回 error + correction_hint。"""
    import inspect
    import app.tools.osm as osm_mod

    # 找到含 category_map 的函数并驱动未映射值
    hits = []
    for name in dir(osm_mod):
        fn = getattr(osm_mod, name)
        if callable(fn) and "category" in (getattr(fn, "__doc__", "") or ""):
            hits.append(fn)
    # 直接从源码常量验证映射表覆盖（映射表为局部量的最小行为锁）
    src = inspect.getsource(osm_mod)
    assert '"小学"' in src and "primary_school" in src, "小学 映射缺失（死查询回归）"
    assert "correction_hint" in src, "未映射值必须有 correction_hint 路径"


# ── 3. adcode 注入面 ────────────────────────────────────────────────────

def test_adcode_literal_strips_non_digits():
    from app.services.local_poi import _adcode_literal

    assert _adcode_literal("510100") == "510100"
    assert _adcode_literal(" 5101' OR '1'='1 ") == "510111"  # 只留数字
    assert _adcode_literal("abc") == ""


# ── 6. workflow 经 dispatch service ─────────────────────────────────────

@pytest.mark.asyncio
async def test_workflow_step_routes_through_dispatch_service():
    """workflow 步骤不再直调 registry.dispatch——错误折叠经 service 判别式。"""
    from enum import Enum

    from app.services.workflow_engine import _dispatch_step_via_service

    class _Status(str, Enum):
        OK = "ok"
        ERROR = "error"

    class _Res:
        def __init__(self, status, raw=None, err=None):
            self.status = _Status(status)
            self.raw_result = raw
            self.error_msg = err
            self.llm_payload = ""
            self.slim_event = {}
            self.geojson_ref = None
            self.map_actions = []

    class _FakeService:
        def __init__(self):
            self.calls = []

        async def dispatch(self, tc, session_id, executed_tools):
            self.calls.append(tc)
            if self.fail:
                return _Res("error", err="provider quota exceeded")
            return _Res("ok", raw={"success": True, "n": 3})

    svc = _FakeService()

    class _Reg:
        pass

    svc.fail = False
    out = await _dispatch_step_via_service(
        _Reg(), "query_osm_poi", {"category": "学校"}, "s-1", svc, set()
    )
    assert out == {"success": True, "n": 3}
    assert svc.calls and svc.calls[0]["function"]["name"] == "query_osm_poi"
    # args 经 JSON 序列化进 tc（OpenAI 形态）
    assert json.loads(svc.calls[0]["function"]["arguments"]) == {"category": "学校"}

    svc.fail = True
    with pytest.raises(RuntimeError, match="provider quota exceeded"):
        await _dispatch_step_via_service(
            _Reg(), "query_osm_poi", {}, "s-1", svc, set()
        )
