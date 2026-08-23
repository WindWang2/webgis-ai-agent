"""#684: 目录域粘性按用户轮衰减 + 关键词源固定为本 turn 原始用户消息。

flip-red 契约：
- 未修复代码上 select_schemas 无 turn_id 参数 / _select_tools 无
  turn_start_user_message 参数 → TypeError 红；
- 语义红：同一 turn_id 的多次 LLM 轮在未修复代码上按调用衰减，
  第 3 次调用即丢域。
"""

from app.tools.registry import ToolRegistry
from app.tools import init_tools
from app.services.tool_catalog import ToolCatalog
def _prod() -> tuple[ToolRegistry, ToolCatalog]:
    r = ToolRegistry()
    init_tools(r)
    return r, ToolCatalog(r, sticky_ttl=3)
def _names(schemas: list[dict]) -> set[str]:
    return {s["function"]["name"] for s in schemas}
def _domain_tools(r: ToolRegistry, domain: str) -> set[str]:
    return {
        name
        for name, m in r.all_metadata().items()
        if domain in m.get("domains", []) and int(m.get("tier", 1)) == 2
    }
def test_sticky_survives_same_turn_llm_rounds():
    """追问轮（新消息不含旧关键词）第 3+ 个 LLM 轮仍能看到上轮激活的 tier-2 域。"""
    r, cat = _prod()
    raster_tools = _domain_tools(r, "raster")
    assert raster_tools, "raster 域应有 tier-2 工具"

    s1 = _names(cat.select_schemas("这个区域的 NDVI 植被指数怎么样", session_id="s684", turn_id="t1"))
    assert s1 & raster_tools, "首轮关键词应激活 raster 域"

    # 同一用户轮内 5 次 LLM 轮（无关键词消息）——域不得衰减消失
    for i in range(5):
        schemas = cat.select_schemas("继续", session_id="s684", turn_id="t1")
        assert _names(schemas) & raster_tools, (
            f"同 turn 第 {i + 2} 次 LLM 轮丢失 raster 域 —— sticky 必须按用户轮而非 LLM 轮衰减"
        )
def test_sticky_decays_across_user_turns():
    """跨用户轮每轮衰减 1：ttl=3 时第 3 个无关键词用户轮后域消失。"""
    r, cat = _prod()
    raster_tools = _domain_tools(r, "raster")

    cat.select_schemas("NDVI 植被指数分析", session_id="s684b", turn_id="t1")
    # turn 2：还剩 2
    assert _names(cat.select_schemas("继续", session_id="s684b", turn_id="t2")) & raster_tools
    # turn 3：还剩 1
    assert _names(cat.select_schemas("继续", session_id="s684b", turn_id="t3")) & raster_tools
    # turn 4：耗尽（3 轮用户轮后）
    gone = _names(cat.select_schemas("继续", session_id="s684b", turn_id="t4"))
    assert not (gone & raster_tools), "跨 3 个无关键词用户轮后 sticky 应耗尽"
def test_select_tools_keyword_source_is_original_user_message():
    """XML 路径工具结果文本不影响域检测：合成 '[工具执行结果]' 载体（含
    network 域敏感词）不点亮 network；检测跑在本 turn 原始用户消息上。"""
    from app.services.chat.execution_engine import ChatExecutionEngine

    # H-8（#863）：harness 前门工具（webgis_map_intent/product）刻意跨域标注
    # （statistics/report/network/temporal）——它们因 statistics/chinese 激活
    # 而可见是设计内行为，不属于"network 域被合成文本点亮"。本测试守护的
    # 是 network 专属工具不被点亮，故把前门工具从断言集合中排除。
    _HARNESS_FRONTDOOR = {"webgis_map_intent", "webgis_map_product"}
    r, cat = _prod()
    engine = ChatExecutionEngine(tool_registry=r, tool_catalog=cat)
    network_tools = _domain_tools(r, "network") - _HARNESS_FRONTDOOR
    chinese_tools = _domain_tools(r, "chinese")
    assert network_tools and chinese_tools

    messages = [
        {"role": "user", "content": "成都的小学分布情况"},
        {"role": "assistant", "content": "好的"},
        # XML provider 每波工具后追加的合成载体：network 敏感词密集
        {"role": "user", "content": "[工具执行结果]\nsearch_poi 返回 3 条：路径 规划 isochrone 等时圈 路线 导航 通勤"},
    ]
    schemas = engine._select_tools(
        "s684c", messages,
        turn_start_user_message="成都的小学分布情况",
        turn_id="t1",
    )
    names = _names(schemas or [])
    assert names & chinese_tools, "关键词源应为原始用户消息（chinese 域激活）"
    assert not (names & network_tools - chinese_tools), (
        "合成工具结果文本中的 network 敏感词不得点亮 network 域"
    )
def test_select_tools_fallback_skips_synthetic_messages():
    """未显式传原始消息时的回退：跳过 '[工具执行结果]' 载体，取更早的真实
    用户消息做检测。"""
    from app.services.chat.execution_engine import ChatExecutionEngine

    r, cat = _prod()
    engine = ChatExecutionEngine(tool_registry=r, tool_catalog=cat)
    chinese_tools = _domain_tools(r, "chinese")

    messages = [
        {"role": "user", "content": "成都的小学分布情况"},
        {"role": "user", "content": "[工具执行结果]\n执行完成，无敏感词"},
    ]
    schemas = engine._select_tools("s684d", messages)
    names = _names(schemas or [])
    assert names & chinese_tools, "回退扫描应跳过合成载体，命中真实用户消息的域"
