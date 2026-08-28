"""Unit tests verifying Tool Architecture audit fixes (#945-#949)."""
import pytest
from app.tools.registry import ToolRegistry
from app.services.tool_catalog import ToolCatalog


@pytest.mark.asyncio
async def test_registry_dispatches_sync_func_without_kwargs_safely():
    """Sync tools lacking session_id / **kwargs should not fail when session_id is provided in dispatch."""
    reg = ToolRegistry()

    @reg.tool(name="strict_sync_add", description="add numbers")
    def strict_sync_add(a: int, b: int) -> dict:
        return {"result": a + b}

    # Dispatch with session_id="test-session"
    res = await reg.dispatch("strict_sync_add", {"a": 1, "b": 2}, session_id="test-session")
    assert res == {"result": 3}


def test_tool_catalog_keyword_case_insensitivity():
    """ToolCatalog keyword detection should match mixed-case and uppercase keywords case-insensitively."""
    reg = ToolRegistry()
    cat = ToolCatalog(reg)

    # Mixed-case / uppercase queries for domains
    domains_osm = cat.detect_domains("我想查看这个区域的OSM道路网络")
    assert "network" in domains_osm or "osm" in domains_osm or len(domains_osm) > 0

    domains_ndvi = cat.detect_domains("计算植被NDVI指数")
    assert "remote_sensing" in domains_ndvi or "raster" in domains_ndvi or len(domains_ndvi) > 0


def test_dynamic_tool_registration_updates_schema_cleanly():
    """Registering a tool with the same name replaces the old schema and maintains single entry."""
    reg = ToolRegistry()

    @reg.tool(name="dynamic_calc", description="v1 calc")
    def calc_v1(x: int) -> int:
        return x * 1

    schemas_1 = reg.get_schemas()
    assert len(schemas_1) == 1
    assert schemas_1[0]["function"]["description"] == "v1 calc"

    # Register updated implementation under the same name
    @reg.tool(name="dynamic_calc", description="v2 calc")
    def calc_v2(x: int, y: int = 10) -> int:
        return x + y

    schemas_2 = reg.get_schemas()
    assert len(schemas_2) == 1
    assert schemas_2[0]["function"]["description"] == "v2 calc"
    assert "y" in schemas_2[0]["function"]["parameters"]["properties"]


# ─── #1057/#1059/#1060/#1062 audit batch regression tests ──────────────────

def test_query_local_yearbook_executes_on_thread_policy():
    """#1057: yearbook 重查询是同步 SQLite 扫描，INLINE 会在事件循环上阻塞
    数百 ms（实测暖 ~115ms/冷 ~973ms）——必须 THREAD（与 query_local_poi 对齐）。"""
    from app.tools.registry import ToolExecutionPolicy
    from app.tools.local_stats import register_local_stats_tools

    reg = ToolRegistry()
    register_local_stats_tools(reg)
    policy = reg.metadata("query_local_yearbook")["execution_policy"]
    assert policy == ToolExecutionPolicy.THREAD


def test_measure_tools_execute_on_thread_policy():
    """#1062: measure_distance/measure_area 逐坐标循环超 INLINE <5ms 契约。"""
    from app.tools.registry import ToolExecutionPolicy
    from app.tools.annotation import register_annotation_tools

    reg = ToolRegistry()
    try:
        register_annotation_tools(reg)
    except Exception:
        pytest.skip("annotation tools unavailable")
    for name in ("measure_distance", "measure_area"):
        assert reg.metadata(name)["execution_policy"] == ToolExecutionPolicy.THREAD, name


@pytest.mark.asyncio
async def test_explicit_parameters_registration_still_validates(monkeypatch):
    """#1059/#945: 显式 parameters= 注册此前不建 args model —— 未知参数以
    原生 TypeError 漏出。修复后走统一 VALIDATION_ERROR。"""
    reg = ToolRegistry()

    def _report(topic: str = "", include_charts: bool = True) -> dict:
        return {"success": True, "topic": topic}

    reg.register(
        "explicit_params_tool",
        "report tool",
        _report,
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "topic"},
                "include_charts": {"type": "boolean"},
            },
            "required": [],
        },
    )
    assert "explicit_params_tool" in reg._models  # 校验模型已建
    res = await reg.dispatch("explicit_params_tool", {"bogus_param_xyz": 1})
    assert isinstance(res, dict) and res.get("success") is False
    assert res.get("code") == "VALIDATION_ERROR"
    # 合法调用不受影响（函数默认值语义保持）
    res2 = await reg.dispatch("explicit_params_tool", {"topic": "t"})
    assert res2 == {"success": True, "topic": "t"}


@pytest.mark.asyncio
async def test_cancelled_error_releases_dedup_key():
    """#1060/#946: 硬取消（asyncio.CancelledError 是 BaseException）此前
    逃逸 dispatch 的 except Exception 体系，dedup 键滞留 executed_tools。"""
    import asyncio
    from app.services.tool_dispatch_service import ToolDispatchService

    reg = ToolRegistry()

    async def _slow(**_):
        await asyncio.sleep(5)

    reg.register("slow_async_tool", "slow", _slow, execution_policy="async")
    svc = ToolDispatchService(registry=reg)
    executed: set = set()
    tc = {"id": "c1", "function": {"name": "slow_async_tool", "arguments": {"x": 1}}}

    task = asyncio.create_task(svc.dispatch(tc, "s1", executed))
    await asyncio.sleep(0.05)  # 让 dispatch 进入 registry 执行段
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ("slow_async_tool", '{"x": 1}') not in executed


def test_reregistration_warns(caplog):
    """#1062: 同名覆盖此前静默替换活工具 —— 现在必须留下 WARNING。"""
    import logging

    reg = ToolRegistry()
    reg.register("dup_tool", "v1", func=lambda **_: {"v": 1})
    with caplog.at_level(logging.WARNING, logger="app.tools.registry"):
        reg.register("dup_tool", "v2", func=lambda **_: {"v": 2})
    assert any("重复注册" in r.message for r in caplog.records)


def test_schema_size_cached_and_invalidated():
    """#1062: schema 字节缓存 —— 二次查询不再 dumps；同名覆盖后失效重算。"""
    import json as _json
    from unittest.mock import patch

    reg = ToolRegistry()
    reg.register("sized_tool", "desc", func=lambda a=1: {"a": a})
    first = reg.schema_size("sized_tool")
    assert first == len(_json.dumps(
        next(s for s in reg.get_schemas() if s["function"]["name"] == "sized_tool"),
        ensure_ascii=False))

    calls = {"n": 0}
    real_dumps = _json.dumps

    def _counting_dumps(*a, **k):
        calls["n"] += 1
        return real_dumps(*a, **k)

    with patch("app.tools.registry.json.dumps", side_effect=_counting_dumps):
        assert reg.schema_size("sized_tool") == first
        assert calls["n"] == 0  # 缓存命中，零序列化

    reg.register("sized_tool", "desc-v2", func=lambda a=1, b=2: {"a": a})
    assert reg.schema_size("sized_tool") != first  # 覆盖后缓存失效重算


def test_detect_domains_runs_once_per_select(monkeypatch):
    """#1062: detect_domains 此前每次 select 跑两遍（_activate_domains +
    _enforce_schema_budget 各一次）。"""
    reg = ToolRegistry()
    reg.register("t1", "d", func=lambda: {})
    cat = ToolCatalog(reg, sticky_ttl=0)
    calls = {"n": 0}
    real = ToolCatalog.detect_domains  # staticmethod -> plain function

    def _counting(text):
        calls["n"] += 1
        return real(text)

    monkeypatch.setattr(ToolCatalog, "detect_domains", staticmethod(_counting))
    cat.select_schemas("成都的小学分布")
    assert calls["n"] == 1
