"""Tool Registry Rationalization（P8）审计测试。

架构立场（Goal → Capability → Algorithm → Tool）：
- 工具数量不是成熟度指标 —— 审计关注分类完备、能力绑定正确、无幽灵引用；
- capability→tool 映射的唯一声明源是 algorithm_registry 的
  tool_candidates —— 任何候选项不在注册表中即能力绑定漂移（错绑/
  改名遗漏），fail-fast；
- 分类是元数据投影：未归类工具必须显式声明归类（新工具落进来即被
  本测试拦下），防止 registry 无声膨胀回"一堆工具"的旧形态。
"""
from app.tools import init_tools
from app.tools.categories import (
    TOOL_CATEGORIES,
    build_tool_category_manifest,
    classify_tool,
)
from app.tools.registry import ToolRegistry


def _registry() -> ToolRegistry:
    r = ToolRegistry()
    init_tools(r)
    return r


def test_every_tool_is_classified():
    """全量工具归类完备（uncategorized 必须为空 —— 新工具必须声明归类）。"""
    manifest = build_tool_category_manifest(_registry())
    uncategorized = manifest["uncategorized"]["tools"]
    assert uncategorized == [], (
        f"unclassified tools (declare them in app/tools/categories.py): {uncategorized}"
    )
    total = sum(len(m["tools"]) for m in manifest.values())
    assert total == len(_registry().list_tools())


def test_all_categories_populated():
    """九类功能各有其员 —— 分类表与实际工具面一致（不是空壳分类）。"""
    manifest = build_tool_category_manifest(_registry())
    for cat in TOOL_CATEGORIES:
        assert manifest[cat]["tools"], f"category {cat} is empty"


def test_planning_surface_is_bounded():
    """planning 面有界且已知（Planner 不应知道具体分析工具实现 ——
    计划族工具只有 intent/product/plan_mode，能力解析归 resolver）。"""
    manifest = build_tool_category_manifest(_registry())
    planning = set(manifest["planning"]["tools"])
    assert planning == {
        "webgis_map_intent",
        "webgis_map_product",
        "propose_plan",
        "execute_plan",
        "get_plan_status",
    }


def test_capability_tool_bindings_resolve():
    """capability-first：algorithm registry 的每个 tool_candidate 都必须是
    已注册工具（错绑/改名遗漏在此 fail-fast，而非运行期静默降级）。"""
    from app.lib.gis.algorithm_registry import get_algorithm_registry

    registry = _registry()
    names = set(registry.list_tools())
    algos = get_algorithm_registry()
    phantom = []
    for algo_id in algos.all_ids:
        algo = algos.get(algo_id)
        for candidate in (algo.tool_candidates or []):
            if candidate not in names:
                phantom.append(f"{algo_id}->{candidate}")
    assert phantom == [], f"algorithm tool_candidates not registered: {phantom}"


def test_classify_tool_deterministic_rules():
    """规则表语义抽查（名字覆盖 > 模块默认 > uncategorized）。"""
    assert classify_tool("heatmap_data", "app.tools.spatial") == "rendering"
    assert classify_tool("buffer_analysis", "app.tools.spatial") == "analysis"
    assert classify_tool("query_osm_poi", "app.tools.osm") == "data_access"
    assert classify_tool("whatever", "app.tools.unknown_module") == "uncategorized"
    # 同名不同模块：名字覆盖优先于模块默认
    assert classify_tool("measure_distance", "app.tools.annotation") == "inspection"
