"""Independently testable webgis_component_catalog handler and explicit registration."""
from __future__ import annotations
from app.services.session_data import session_data_manager
from app.tools.registry import tool

from app.services.gis_harness.tools import (
    ToolRegistry,
    Any,
    Dict,
    Optional,
)


_variant_catalog_cache: dict = {}


async def webgis_component_catalog(session_id: Optional[str] = None) -> dict:
    if not session_id:
        return {"success": False, "message": "Missing session_id"}
    from app.lib.cartography.component_registry import get_component_registry
    from app.services.mapspec_store import mapspec_store

    spec = await mapspec_store.get_mapspec(session_id) or {}
    raw_components = ((spec.get("layout") or {}).get("components")) or []

    def _summarize(c: Dict[str, Any]) -> Dict[str, Any]:
        opts = c.get("options") if isinstance(c.get("options"), dict) else {}
        summary: Dict[str, Any] = {
            "id": c.get("id"),
            "type": c.get("type"),
            "enabled": c.get("enabled", True),
            "position": c.get("position", "none"),
        }
        if c.get("placement"):
            summary["placement"] = c.get("placement")
        if c.get("variant"):
            summary["variant"] = c.get("variant")
        if "chart" in opts:
            chart = opts.get("chart")
            summary["chart"] = {
                "type": chart.get("type") if isinstance(chart, dict) else None,
                "title": chart.get("title") if isinstance(chart, dict) else None,
                "points": len(chart.get("data") or []) if isinstance(chart, dict) else 0,
                "binding": "inline",
            }
        elif "chartRef" in opts:
            summary["chart"] = {"binding": str(opts.get("chartRef")), "inline": False}
        if isinstance(opts.get("stats"), dict):
            items = opts["stats"].get("items")
            summary["stats"] = {
                "title": opts["stats"].get("title"),
                "items": len(items) if isinstance(items, list) else 0,
            }
        if isinstance(opts.get("text"), str):
            summary["text"] = opts["text"][:60]
        return summary

    # #1076(D-8): 静态目录进程级缓存（描述符载入后才变）—— 此前每次
    # 调用全量排序重建。registry 版本号作为失效键（注册新描述符即变）。
    # v2（§17）：多实例类型目录携带 cardinality —— Agent 由此知道
    # legend/colorbar/chart_panel 可多实例，创建第二个实例必须显式
    # component_id（不许靠数组序/「第一个同型」隐式寻址）。
    component_registry = get_component_registry()
    _reg_version = component_registry.registry_version()
    _cached_catalog = _variant_catalog_cache.get("catalog")
    if _cached_catalog is None or _cached_catalog[0] != _reg_version:
        variant_catalog = []
        for desc in sorted(component_registry.native_descriptors(), key=lambda d: d.id):
            variant_catalog.append({
                "type": desc.type,
                "variants": desc.variants,
                "default_variant": desc.default_variant,
                "default_position": desc.default_position,
                "cardinality": desc.cardinality,
            })
        _variant_catalog_cache["catalog"] = (_reg_version, variant_catalog)
    else:
        variant_catalog = _cached_catalog[1]

    # revision 唯一权威是 map_state._cartographic_mutation_revision
    # （引擎从不在 spec 文档内嵌该字段）。
    state = await session_data_manager.get_map_state(session_id)
    revision = state.get("_cartographic_mutation_revision", 0)
    if not isinstance(revision, int):
        revision = 0

    from app.services.gis_harness.components import _FACTORY_BY_TYPE
    multi_types = [
        entry["type"] for entry in variant_catalog
        if entry["cardinality"] == "multiple"
    ]
    return {
        "success": True,
        "mutation_revision": revision,
        "components": [
            _summarize(c) for c in raw_components if isinstance(c, dict)
        ],
        "available_variants": variant_catalog,
        # 可创建类型从工厂表派生（单一事实源，防目录撒谎）
        "creatable_types": sorted(_FACTORY_BY_TYPE.keys()),
        # 多实例类型：再创建必须带显式 component_id（catalog 可发现的
        # 既有实例 id 就在 components[].id —— 按 id 寻址，不按顺序猜）。
        "multi_instance_types": multi_types,
        "summary": (
            f"{len(raw_components)} 个组件；revision={revision}。"
            "webgis_component_update 传 expected_revision=revision 防覆盖。"
            + (
                f"多实例类型（{', '.join(multi_types[:6])}）新增实例须显式 "
                "component_id（先在 components 里确认未占用）。"
                if multi_types else ""
            )
        ),
    }



def register_component_catalog(registry: ToolRegistry) -> None:
    tool(
        registry,
        tier=2, domains=["report"], name="webgis_component_catalog",
        capabilities=['workspace_state_inspection'],
        description=(
            "读取当前地图的制图组件目录与状态（只读）。一次返回："
            "(1) 当前 MapSpec 的全部组件（id/type/enabled/位置/placement/variant/"
            "图表绑定摘要）；(2) 每种组件类型的可用 variant 清单；"
            "(3) 当前 mutation_revision（供 webgis_component_update 做乐观并发）。"
            "\n『当前有哪些组件？统计图在哪？用什么数据？』『把刚才用户移动过的"
            "统计图恢复到左上角』——先读本工具拿最新状态与 revision，再突变。"
        ),
        side_effect="pure",
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("组件目录", "只读", "revision", "组件状态", "variant 清单"),
        output_semantic_type="list",
        result_size_policy="bounded",
    )(webgis_component_catalog)
