"""ToolCatalog 单元测试 — 分层选择 + 关键词激活 + 会话粘性。"""
import pytest

from app.tools.registry import ToolRegistry
from app.services.tool_catalog import ToolCatalog


@pytest.fixture
def registry():
    """构造一个 mini registry：每个 tier 各放几个工具，覆盖多个 domain。"""
    r = ToolRegistry()
    # tier 1 — 总在
    r.register("buffer_analysis", "buffer", func=lambda **_: {})
    r.register("layer_alias", "alias", func=lambda **_: {})
    # tier 2 — 主题
    r.register("compute_ndvi", "ndvi", func=lambda **_: {}, tier=2, domains=["raster"])
    r.register("fetch_dem", "dem", func=lambda **_: {}, tier=2, domains=["raster"])
    r.register("query_osm_poi", "osm poi", func=lambda **_: {}, tier=2, domains=["osm"])
    r.register("plan_route", "route", func=lambda **_: {}, tier=2, domains=["network"])
    r.register("hotspot_analysis", "hotspot", func=lambda **_: {}, tier=2, domains=["statistics"])
    # tier 3 — 永不自动注入
    r.register("create_new_skill", "meta", func=lambda **_: {}, tier=3, domains=["meta"])
    r.register("what_if_simulate", "what-if", func=lambda **_: {}, tier=3, domains=["what_if"])
    return r


@pytest.fixture
def catalog(registry):
    return ToolCatalog(registry, sticky_ttl=3)


def _names(schemas):
    return {s["function"]["name"] for s in schemas}


# ──────────────────────────────────────────────────────────────


def test_empty_message_only_tier1(catalog):
    """没有任何关键词 → 仅 tier 1。"""
    schemas = catalog.select_schemas("", session_id="s1")
    assert _names(schemas) == {"buffer_analysis", "layer_alias"}


def test_no_session_no_sticky(catalog):
    """没有 session_id 也能工作（无粘性，纯本轮检测）。"""
    schemas = catalog.select_schemas("compute NDVI for Beijing")
    assert "compute_ndvi" in _names(schemas)
    assert "fetch_dem" in _names(schemas)
    assert "buffer_analysis" in _names(schemas)  # tier 1
    assert "create_new_skill" not in _names(schemas)  # tier 3


def test_raster_keyword_zh_loads_raster_domain(catalog):
    schemas = catalog.select_schemas("帮我看下海淀的植被覆盖", session_id="s1")
    names = _names(schemas)
    assert {"compute_ndvi", "fetch_dem"}.issubset(names)
    assert "query_osm_poi" not in names  # osm 域未触发


def test_osm_keyword_loads_osm(catalog):
    schemas = catalog.select_schemas("从 OpenStreetMap 拉一份成都的道路", session_id="s1")
    assert "query_osm_poi" in _names(schemas)


def test_tier3_never_auto_included(catalog):
    """即便用户字面含 'create skill' / 'what if' 关键词，tier 3 也仅匹配 meta/what_if 域。"""
    schemas = catalog.select_schemas("create skill for me", session_id="s1")
    # meta 域被触发，但工具是 tier 3 — 不自动注入
    assert "create_new_skill" not in _names(schemas)


def test_sticky_keeps_domain_for_n_turns(catalog):
    """命中 raster 后，后续 3 轮内即使无关键词，raster 仍载入。"""
    # Turn 1: trigger raster
    s1 = catalog.select_schemas("NDVI 计算", session_id="abc")
    assert "compute_ndvi" in _names(s1)
    # Turn 2: 不再提关键词
    s2 = catalog.select_schemas("把结果导出", session_id="abc")
    assert "compute_ndvi" in _names(s2)  # 仍在粘性内
    # Turn 3
    s3 = catalog.select_schemas("好的", session_id="abc")
    assert "compute_ndvi" in _names(s3)
    # Turn 4: 粘性衰减完
    s4 = catalog.select_schemas("再来一个", session_id="abc")
    assert "compute_ndvi" not in _names(s4)


def test_sticky_refreshes_on_retrigger(catalog):
    """再次命中关键词应把粘性 TTL 重置回满。"""
    catalog.select_schemas("NDVI", session_id="abc")  # ttl=3
    catalog.select_schemas("hello", session_id="abc")  # ttl=2
    catalog.select_schemas("看看 NDVI 再算一次", session_id="abc")  # 命中, ttl 重置=3
    catalog.select_schemas("step", session_id="abc")  # ttl=2
    s = catalog.select_schemas("step", session_id="abc")  # ttl=1, 仍载入
    assert "compute_ndvi" in _names(s)


def test_session_isolation(catalog):
    """不同 session 的粘性互不影响。"""
    catalog.select_schemas("NDVI please", session_id="A")
    s = catalog.select_schemas("hi", session_id="B")
    assert "compute_ndvi" not in _names(s)


def test_multi_domain_message(catalog):
    """一条消息同时触发多个 domain，相应工具都加载。"""
    schemas = catalog.select_schemas(
        "查海淀区的医院 NDVI 分布并算下热点", session_id="s1"
    )
    names = _names(schemas)
    assert "compute_ndvi" in names  # raster
    assert "hotspot_analysis" in names  # statistics


def test_detect_domains_static():
    """静态方法 detect_domains 可单独验证关键词命中。"""
    assert ToolCatalog.detect_domains("NDVI") == {"raster"}
    assert ToolCatalog.detect_domains("OpenStreetMap") == {"osm"}
    assert ToolCatalog.detect_domains("hotspot 热点") == {"statistics"}
    assert ToolCatalog.detect_domains("") == set()
    # 英文关键词加了单词边界，避免 'osm' 被 'cosmos' 误命中
    assert "osm" not in ToolCatalog.detect_domains("cosmos research")


def test_reset_session(catalog):
    catalog.select_schemas("NDVI", session_id="abc")
    assert catalog.active_domains("abc") == {"raster"}
    catalog.reset_session("abc")
    assert catalog.active_domains("abc") == set()


def test_unannotated_tools_default_tier1():
    """未标注 tier 的工具默认 tier 1 (向后兼容)。"""
    r = ToolRegistry()
    r.register("legacy_tool", "no metadata", func=lambda **_: {})
    cat = ToolCatalog(r)
    schemas = cat.select_schemas("anything")
    assert "legacy_tool" in _names(schemas)


def test_declared_domains_activates_tier2_without_keyword(catalog):
    """计划声明的 domain 即使关键词没命中也激活对应 tier 2 工具。"""
    schemas = catalog.select_schemas("随便一句话", session_id="d1",
                                     declared_domains={"raster"})
    names = _names(schemas)
    assert "compute_ndvi" in names   # raster 工具被纳入
    assert "fetch_dem" in names


def test_declared_domains_union_with_keywords(catalog):
    """计划 domain 与关键词检测取并集，关键词仍生效。"""
    schemas = catalog.select_schemas("规划一条驾车路线", session_id="d2",
                                     declared_domains={"raster"})
    names = _names(schemas)
    assert "compute_ndvi" in names   # 来自 declared_domains
    assert "plan_route" in names     # 来自关键词"路线/驾车"


def test_declared_domains_none_preserves_old_behavior(catalog):
    """不传 declared_domains 时行为与旧版一致（纯关键词）。"""
    schemas = catalog.select_schemas("计算 NDVI", session_id="d3")
    assert "compute_ndvi" in _names(schemas)
