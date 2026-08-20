"""#678 guardrail: tier-1 bound, chinese domain activation, schema token bound (flip-red)."""
import json

from app.tools.registry import ToolRegistry
from app.tools import init_tools
from app.services.tool_catalog import ToolCatalog


def _prod_registry():
    r = ToolRegistry()
    init_tools(r)
    return r


def test_tier1_count_bounded():
    """护栏：生产注册表 tier-1 数量必须 ≤40。新增工具默认 tier=2，tier-1 需要论证 (#678)。"""
    r = _prod_registry()
    metas = r.all_metadata()
    tier1 = [k for k, v in metas.items() if int(v.get("tier", 1)) == 1]
    assert len(tier1) <= 40, (
        f"tier-1 工具数量 {len(tier1)} 超过上限 40，"
        f"新工具默认 tier=2，tier-1 需要论证 (#678)。tier-1: {sorted(tier1)}"
    )


def test_chinese_domain_has_expected_count():
    """chinese 域应门控 15 工具（11 chinese_maps + 本地行政/乡镇 + transform_coordinates）。"""
    r = _prod_registry()
    metas = r.all_metadata()
    chinese_tools = [k for k, v in metas.items() if "chinese" in v.get("domains", [])]
    assert len(chinese_tools) >= 14, (
        f"chinese 域门控工具数 {len(chinese_tools)} 少于预期 14，实际: {sorted(chinese_tools)}"
    )


def test_chinese_activation_for_city_query():
    """典型中文城市名查询（含‘成都’不含‘市’）必须激活 chinese 域并注入 search_poi 等。"""
    r = _prod_registry()
    catalog = ToolCatalog(r, sticky_ttl=0)
    # 不含“市”的城市名查询，之前关键词表漏命中（仅“市”单字触发）
    schemas = catalog.select_schemas("成都的小学分布", session_id="test-city")
    names = {s["function"]["name"] for s in schemas}
    # 至少应包含 chinese 域的核心 POI/地理编码工具
    for required in ("search_poi", "geocode_cn", "search_poi_around"):
        assert required in names, (
            f"中文查询‘成都的小学分布’未激活 chinese 域工具 {required}，"
            f"当前注入: {sorted(names)}，domains 检测: {ToolCatalog.detect_domains('成都的小学分布')}"
        )


def test_schema_count_and_token_bound_for_typical_query():
    """典型查询一轮注入的 schema 数量与 token 量级有界（tier-1 + 少量 domain）。"""
    r = _prod_registry()
    catalog = ToolCatalog(r, sticky_ttl=0)
    # 空/通用问候：仅 tier-1
    schemas = catalog.select_schemas("你好", session_id="s-typical")
    # 数量有界：tier-1 ≤40 + 粘性 0，所以总数应 ≤40
    assert len(schemas) <= 40, f"通用查询 schema 数 {len(schemas)} 超过 40"
    # token 量级：按 250-350 tokens/工具 估算，40 工具约 10k-14k tokens，设上限 15000
    total_chars = sum(len(json.dumps(s, ensure_ascii=False)) for s in schemas)
    est_tokens = total_chars / 4.0  # ~4 chars per token
    assert est_tokens <= 15000, f"通用查询预估 tokens {est_tokens:.0f} 超过 15000 (chars={total_chars})"
    # 中文+多域查询：chinese+osm+raster+statistics 最多叠加，但仍有界（≤100）
    # tier1 40 + chinese 15 + osm 7 + raster 15 + statistics 18 ≈ 95
    schemas2 = catalog.select_schemas("成都市的医院 NDVI 分布并算下热点", session_id="s-multi")
    assert len(schemas2) <= 100, f"多域查询 schema 数 {len(schemas2)} 超过 100"
    total_chars2 = sum(len(json.dumps(s, ensure_ascii=False)) for s in schemas2)
    est_tokens2 = total_chars2 / 4.0
    assert est_tokens2 <= 35000, f"多域查询预估 tokens {est_tokens2:.0f} 超过 35000"
