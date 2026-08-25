"""audit4 (2026-08-26) guardrails: tool discoverability, error normalization
fourth family, guidance projection, in-turn folding, schema budget, sticky cap.

Issues: #978 #979 #980 #981 #983 #984.
"""
import json

from app.tools.registry import ToolRegistry
from app.tools import init_tools
from app.services.tool_catalog import ToolCatalog, DOMAIN_KEYWORDS, _MAX_ACTIVE_STICKY_DOMAINS


def _prod_registry():
    r = ToolRegistry()
    init_tools(r)
    return r


# ── #978 / #983: tier-2 工具发现性守护 ─────────────────────────────────────

def test_tier2_domains_keyword_reachable():
    """每个 tier-2 工具的 domains 必须非空且至少一个域在 DOMAIN_KEYWORDS 词表内。

    #978 的根因是 finalize_display 标了词表外的 "cartography"，#983 是
    reproject_coordinates 零 domains —— 两类缺陷都被这条不变量堵死。
    """
    r = _prod_registry()
    kw = set(DOMAIN_KEYWORDS)
    violations = []
    for name, meta in r.all_metadata().items():
        if int(meta.get("tier", 1)) != 2:
            continue
        domains = set(meta.get("domains", []))
        if not domains or not (domains & kw):
            violations.append((name, sorted(domains)))
    assert not violations, (
        f"tier-2 工具存在关键词不可达的域标注: {violations}；"
        f"domains 必须非空且 ⊆ DOMAIN_KEYWORDS 键集 {sorted(kw)}"
    )


def test_finalize_display_reachable_for_mapspec_keywords():
    """#978: 含「图层/隐藏/显示」关键词的消息必须能选到 finalize_display。"""
    r = _prod_registry()
    catalog = ToolCatalog(r, sticky_ttl=0)
    schemas = catalog.select_schemas("把中间的分析图层隐藏，只显示最终结果", session_id="t-fd")
    names = {s["function"]["name"] for s in schemas}
    assert "finalize_display" in names, (
        f"finalize_display 未被选中（domains 失配回归）: detect="
        f"{ToolCatalog.detect_domains('把中间的分析图层隐藏，只显示最终结果')}"
    )


def test_reproject_coordinates_reachable_for_dataset_query():
    """#983: 数据集/上传语境必须能选到 reproject_coordinates。"""
    r = _prod_registry()
    catalog = ToolCatalog(r, sticky_ttl=0)
    schemas = catalog.select_schemas("上传的数据集是 CGCS2000 坐标系，帮我转成 WGS84", session_id="t-rc")
    names = {s["function"]["name"] for s in schemas}
    assert "reproject_coordinates" in names, (
        f"reproject_coordinates 未被选中（零 domains 回归），chinese/dataset 检测: "
        f"{ToolCatalog.detect_domains('上传的数据集是 CGCS2000 坐标系，帮我转成 WGS84')}"
    )


def test_chinese_road_tools_visible_without_network_keywords():
    """#983: 『成都到重庆怎么走』激活 chinese（城市名）但无 network 关键词，
    高德路网族（补标 chinese 域后）必须可见。"""
    r = _prod_registry()
    catalog = ToolCatalog(r, sticky_ttl=0)
    schemas = catalog.select_schemas("成都到重庆怎么走", session_id="t-road")
    names = {s["function"]["name"] for s in schemas}
    assert "plan_route" in names, (
        f"plan_route 不可见（高德路网族缺 chinese 域回归）: {sorted(names & {'plan_route', 'distance_matrix_cn'})}"
    )


# ── #981: schema 硬预算 + 粘性域上限 ───────────────────────────────────────

def test_schema_budget_bounds_tier2_bytes():
    """多域查询的 tier-2 schema 增量字节必须有界（budget 默认 24KB）。"""
    from app.services.tool_catalog import _TIER2_SCHEMA_BUDGET_BYTES

    r = _prod_registry()
    catalog = ToolCatalog(r, sticky_ttl=0)
    schemas = catalog.select_schemas("成都市的医院 NDVI 分布并算下热点", session_id="t-budget")
    all_meta = r.all_metadata()
    tier2_bytes = sum(
        len(json.dumps(s, ensure_ascii=False))
        for s in schemas
        if int(all_meta.get(s["function"]["name"], {}).get("tier", 1)) != 1
    )
    assert tier2_bytes <= _TIER2_SCHEMA_BUDGET_BYTES, (
        f"tier-2 schema 增量 {tier2_bytes} 字节超过硬预算 {_TIER2_SCHEMA_BUDGET_BYTES}"
    )
    # tier-1 必发不截断
    tier1_names = {n for n, m in all_meta.items() if int(m.get("tier", 1)) == 1}
    got_names = {s["function"]["name"] for s in schemas}
    assert tier1_names <= got_names, "tier-1 工具被预算误截"


def test_schema_budget_keeps_fresh_domain_priority():
    """预算截断时，本轮关键词命中的域（如 chinese）优先保留。"""
    r = _prod_registry()
    catalog = ToolCatalog(r, sticky_ttl=0)
    schemas = catalog.select_schemas("成都的小学分布", session_id="t-prio")
    names = {s["function"]["name"] for s in schemas}
    for required in ("search_poi", "geocode_cn"):
        assert required in names, f"预算截断吞掉了 fresh chinese 域核心工具 {required}"


def test_sticky_domains_capped():
    """多主题会话的粘性域数量不得超过 _MAX_ACTIVE_STICKY_DOMAINS。"""
    r = _prod_registry()
    catalog = ToolCatalog(r, sticky_ttl=3)
    sid = "t-sticky"
    # 依次命中 6 个域（每次一个新用户轮）
    catalog.select_schemas("遥感影像分析", session_id=sid, turn_id="u1")
    catalog.select_schemas("路径导航规划", session_id=sid, turn_id="u2")
    catalog.select_schemas("时空演变趋势", session_id=sid, turn_id="u3")
    catalog.select_schemas("聚类密度热点", session_id=sid, turn_id="u4")
    catalog.select_schemas("数据集上传", session_id=sid, turn_id="u5")
    catalog.select_schemas("报告导出", session_id=sid, turn_id="u6")
    active = catalog.active_domains(sid)
    assert len(active) <= _MAX_ACTIVE_STICKY_DOMAINS, (
        f"粘性域 {sorted(active)} 超过上限 {_MAX_ACTIVE_STICKY_DOMAINS}"
    )


# ── #984: 错误归一第四族 + repeated 诚实文案 ───────────────────────────────

def test_is_error_like_result_fourth_family():
    from app.services.llm_result_formatter import is_error_like_result, is_tool_error_result

    assert is_error_like_result({"success": False, "message": "component_id 或 component_type 必须提供其一"})
    assert is_tool_error_result({"success": False, "message": "x"})
    # 不误伤：成功屏蔽与业务键
    assert not is_error_like_result({"success": True, "message": "ok with note"})
    assert not is_error_like_result({"type": "FeatureCollection"})
    assert not is_error_like_result({"status": "template_applied"})
    # success=False 但 message 非 str（数值/嵌套）不归类 —— 保守口径
    assert not is_error_like_result({"success": False, "message": {"code": 1}})


def test_repeat_payload_is_honest():
    """#984: repeated 拦截文案不得声称「已成功/直接汇报」——必须提示结果可能过期。"""
    from app.services import tool_dispatch_service as tds

    text = tds._REPEAT_LLMPAYLOAD
    assert "未重新执行" in text or "未重新调用" in text
    assert "已成功执行" not in text
    assert "变化" in text  # 提示上下文可能已变


# ── #979: guidance 投影在 slim 后保留 ──────────────────────────────────────

def test_slim_tool_result_preserves_guidance():
    from app.services.llm_result_formatter import slim_tool_result

    result = {
        "success": True,
        "summary": "意图:distribution 范围:成都 主体:小学 → 推荐 recipe:heatmap",
        "guidance": [
            "poi_fetch → search_poi（获取成都小学 POI）",
            "density_surface → create_heatmap（核密度制图）",
        ],
        "plan": {"data_requirements": [dict.fromkeys(range(50), "x")]},  # 大对象应被丢弃
    }
    slim = slim_tool_result(result, json.dumps(result), session_geojson_ref=None)
    parsed = json.loads(slim)
    assert parsed.get("guidance"), "guidance 被 summary 分支丢弃（#979 回归）"
    assert "poi_fetch → search_poi" in json.dumps(parsed["guidance"], ensure_ascii=False)
    assert "data_requirements" not in json.dumps(parsed)


# ── #980: 轮内 tool 结果折叠 ───────────────────────────────────────────────

def _make_turn(n_tools: int):
    msgs = [{"role": "system", "content": "sys"}]
    msgs.append({"role": "user", "content": "分析成都的小学分布"})
    for i in range(n_tools):
        cid = f"call_{i}"
        msgs.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": cid, "type": "function",
                            "function": {"name": f"tool_{i}", "arguments": "{}"}}],
        })
        msgs.append({"role": "tool", "tool_call_id": cid,
                     "content": "x" * 2000})
    return msgs


def test_fold_intra_turn_tool_results():
    from app.services.chat.context.history_compression import (
        fold_intra_turn_tool_results,
        _TURN_TOOL_KEEP_RECENT,
        _TURN_TOOL_FOLD_MIN,
    )

    msgs = _make_turn(_TURN_TOOL_FOLD_MIN + 6)
    folded = fold_intra_turn_tool_results(msgs)
    # 原列表不被改动（仅发送视图）
    assert any(len(m.get("content") or "") > 500 for m in msgs if m.get("role") == "tool")
    # 配对保持：消息数不变、tool_call_id 不变
    assert len(folded) == len(msgs)
    assert [m.get("tool_call_id") for m in folded] == [m.get("tool_call_id") for m in msgs]
    tool_msgs = [m for m in folded if m.get("role") == "tool"]
    folded_ones = [m for m in tool_msgs if "已折叠" in (m.get("content") or "")]
    kept_ones = [m for m in tool_msgs if "已折叠" not in (m.get("content") or "")]
    assert len(kept_ones) == _TURN_TOOL_KEEP_RECENT
    assert len(folded_ones) == len(tool_msgs) - _TURN_TOOL_KEEP_RECENT
    # 折叠占位包含工具名
    assert "tool_0" in folded_ones[0]["content"]
    # 小回合不折叠
    small = fold_intra_turn_tool_results(_make_turn(4))
    assert small is not None
    assert sum(1 for m in small if m.get("role") == "tool" and "已折叠" in (m.get("content") or "")) == 0


def test_list_available_tools_unknown_domain_returns_vocab():
    """#983: 拼错域名不再静默 count=0 —— 返回 available_domains 纠错信息。"""
    import asyncio
    from app.tools.registry import ToolRegistry
    from app.tools import init_tools

    r = ToolRegistry()
    init_tools(r)
    res = asyncio.run(r.dispatch("list_available_tools", {"domain": "cartographyy"}))
    assert res.get("count") == 0
    assert "mapspec" in (res.get("available_domains") or []), res
    assert res.get("message")
