"""本地 OSM 主题查询工具（依赖 manage.py osm-ingest 的预处理产物）。

POI 主次策略：pois 主题且未指定 OSM 标签时，先查高德 POI 库（gd_pois，
5100 万点、中文商户名更全），命中即返回；未命中再查 OSM pois 作为补充。
roads/railways/waterways 主题仍走 OSM（gd 库无路网/水系）。
"""
import logging
from typing import Optional

from app.services.local_osm import THEME_SPECS, catalog, query_osm_features
from app.tools.registry import ToolExecutionPolicy, ToolRegistry, tool

logger = logging.getLogger(__name__)

_THEMES_HELP = "/".join(THEME_SPECS)


def _gd_pois_first(bbox, name_like: Optional[str], limit: int, tag: Optional[str] = None):
    """gd_pois 优先：库可用且命中（count>0）返回 FeatureCollection，否则 None。

    带有 OSM 标签（如 amenity=school）时先翻译成高德分类再查 gd 库——
    用户约定 POI 检索以 gd_pois 为主力，OSM 仅在 gd 查不到或标签无法
    翻译（OSM 特有语义，如 amenity=drinking_water）时作补充。
    """
    try:
        from app.services.local_poi import query_gd_poi
    except Exception:  # noqa: BLE001
        return None
    category = subtype = None
    if tag:
        mapped = _OSM_TAG_TO_GD.get(str(tag).strip().lower())
        if mapped is None:
            return None  # 翻译不了的 OSM 特有标签 → 保持 OSM 语义
        category = mapped.get("category")
        subtype = mapped.get("subtype")
    result = query_gd_poi(
        bbox, name_like=name_like, category=category, subtype=subtype, limit=limit,
    )
    if result.get("error") or result.get("count", 0) == 0:
        return None  # 库不可用或无命中 → OSM 补充
    result["source"] = "local_gd_poi"
    result["note"] = "命中高德 POI 库（主力）；OSM 作为补充数据源。"
    return result


# 常见 OSM 标签 → 高德分类映射。只收录语义确定项；未收录的标签保持 OSM 语义。
_OSM_TAG_TO_GD = {
    "amenity=school": {"subtype": "学校"},
    "amenity=university": {"subtype": "高等院校"},
    "amenity=college": {"subtype": "高等院校"},
    "amenity=kindergarten": {"subtype": "幼儿园"},
    "amenity=hospital": {"category": "医疗保健服务"},
    "amenity=clinic": {"category": "医疗保健服务"},
    "amenity=doctors": {"category": "医疗保健服务"},
    "amenity=pharmacy": {"subtype": "药店"},
    "amenity=restaurant": {"category": "餐饮服务"},
    "amenity=fast_food": {"category": "餐饮服务"},
    "amenity=cafe": {"category": "餐饮服务"},
    "amenity=bar": {"category": "餐饮服务"},
    "amenity=bank": {"category": "金融保险服务"},
    "amenity=atm": {"category": "金融保险服务"},
    "amenity=fuel": {"subtype": "加油站"},
    "amenity=parking": {"subtype": "停车场"},
    "amenity=police": {"category": "政府机构及社会团体"},
    "amenity=fire_station": {"category": "政府机构及社会团体"},
    "amenity=post_office": {"category": "生活服务"},
    "amenity=library": {"category": "科教文化服务"},
    "amenity=museum": {"category": "科教文化服务"},
    "amenity=theatre": {"category": "休闲娱乐"},
    "amenity=cinema": {"category": "休闲娱乐"},
    "tourism=hotel": {"category": "住宿服务"},
    "tourism=attraction": {"category": "风景名胜"},
    "leisure=park": {"subtype": "公园"},
    "leisure=sports_centre": {"category": "休闲娱乐"},
    "leisure=stadium": {"category": "休闲娱乐"},
    "shop=supermarket": {"subtype": "超市"},
    "shop=mall": {"category": "购物服务"},
    "shop=convenience": {"category": "购物服务"},
}


def register_local_osm_tools(registry: ToolRegistry):
    @tool(
        registry,
        name="query_local_osm",
        description=(
            f"本地 OSM 数据查询：按主题（{_THEMES_HELP}）在 bbox 范围内查询要素，"
            "支持名称与标签过滤。数据来自本地预处理 GPKG（离线、秒级响应）。"
            "✅ 用于：『这个范围内的主干路/铁路/水系』——roads/railways/waterways "
            "是本工具专属。"
            "\n⚠️ POI 检索的主力是 query_local_poi（高德库，5174 万点、中文商户名全），"
            "不要用它替代。本工具 pois 主题会自动先查高德库（含 amenity=school 等"
            "标签的自动翻译），gd 查不到才回落 OSM。返回坐标为 WGS84。"
        ),
        param_descriptions={
            "theme": f"主题: {', '.join(THEME_SPECS)}",
            "bbox": "WGS84 边界框 [minx,miny,maxx,maxy]（可由行政区边界 total_bounds 获得）",
            "name_like": "可选：名称包含匹配（中英文均可）",
            "tag": "可选：标签过滤，如 'amenity=restaurant' 或 'amenity=school'",
            "amenity": "可选：等价于 tag='amenity=<值>'，如 school / university / hospital",
            "limit": "返回上限（默认 200，最大 2000）",
        },
        tier=1,
        domains=["osm", "dataset"],
        execution_policy=ToolExecutionPolicy.THREAD,
        timeout=120.0,
    )
    def query_local_osm(
        theme: str,
        bbox,
        name_like: Optional[str] = None,
        tag: Optional[str] = None,
        amenity: Optional[str] = None,
        limit: int = 200,
    ) -> dict:
        if not tag and amenity:
            raw = str(amenity).strip()
            tag = raw if "=" in raw else f"amenity={raw}"
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 200
        # gd_pois 主力：pois 主题先查高德库（无标签直接查；OSM 标签翻译成
        # 高德分类后查），未命中或标签无法翻译再走 OSM。
        if theme == "pois":
            gd = _gd_pois_first(bbox, name_like, limit, tag=tag)
            if gd is not None:
                return gd
        return query_osm_features(
            theme, bbox, name_like=name_like, tag=tag, limit=limit
        )

    @tool(
        registry,
        name="get_local_osm_catalog",
        description=(
            "本地 OSM 数据目录：查看已预处理的主题、行数与覆盖说明。"
            "✅ 用于：query_local_osm 之前确认主题可用性。"
        ),
        execution_policy=ToolExecutionPolicy.INLINE,
        tier=1,
        domains=["osm", "meta"],
    )
    def get_local_osm_catalog() -> dict:
        return catalog()
