"""OSM 数据查询工具 - Overpass API (修复版)"""
import json
import logging
import math
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.provider_health import check_nominatim_status, check_overpass_status, tracked_provider_get
from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    """#773: provenance stamp for OSM tool result envelopes."""
    return datetime.now(timezone.utc).isoformat()


# #773: Overpass→Nominatim fallback swaps enumeration semantics (a free-text
# search of named matches is not an amenity-tag census). Mirrors the
# ``_fallback_semantic_note`` discipline of chinese_maps (#683).
_NOMINATIM_FALLBACK_NOTE = (
    "Overpass 未返回结果，已降级为 Nominatim 关键词搜索：结果为命名地点匹配，"
    "并非该类别的全量普查（enumeration），总数可能偏少。"
)




def _sanitize_overpass_value(value: str) -> str:
    """Escape characters that have special meaning in Overpass QL strings."""
    # Remove characters that could break out of a quoted Overpass QL value
    return str(value).replace("\\", "").replace('"', "").replace("]", "").replace(";", "").replace("\n", "").replace("\r", "")


def _overpass_to_geojson(data: str | dict) -> dict:
    """将 Overpass JSON 结果转为 GeoJSON（接受原始字符串或已解析 dict）。"""
    try:
        result = json.loads(data) if isinstance(data, str) else data
    except (json.JSONDecodeError, TypeError):
        return {"type": "FeatureCollection", "features": []}

    features = []
    elements = result.get("elements", [])

    for el in elements:
        props = {k: v for k, v in el.get("tags", {}).items()}
        props["osm_id"] = el.get("id")
        props["osm_type"] = el.get("type")

        geometry = None
        if el.get("type") == "node" and "lat" in el and "lon" in el and el.get("tags"):
            # Skip topology-only nodes (polygon vertices with no meaningful tags)
            geometry = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
        elif el.get("type") == "way" and "geometry" in el:
            coords = [[p["lon"], p["lat"]] for p in el["geometry"]]
            if len(coords) > 3 and coords[0] == coords[-1]:
                geometry = {"type": "Polygon", "coordinates": [coords]}
            else:
                geometry = {"type": "LineString", "coordinates": coords}
        elif el.get("type") == "relation":
            if "center" in el:
                geometry = {"type": "Point", "coordinates": [el["center"]["lon"], el["center"]["lat"]]}

        if geometry:
            features.append({"type": "Feature", "geometry": geometry, "properties": props})

    return {"type": "FeatureCollection", "features": features}


async def _query_overpass(query: str, limit: Optional[int] = None) -> dict:
    """执行 Overpass QL 查询，返回 GeoJSON。

    limit: 可选输出上限 → 追加 ``out body geom <limit>;``，把服务端返回
    限制在契约范围内（大 bbox 的 POI/道路/建筑查询否则会返回数万要素，
    产生几十 MB GeoJSON）。None 时不限制（行政边界等场景）。
    """
    limit_clause = f"out body geom {limit};" if limit is not None else "out body geom;"
    full_query = f"[out:json][timeout:30];{query.rstrip(';')};{limit_clause}"
    logger.info("[OSM] Querying Overpass API...")

    # 经 ProviderHealthTracker 统一执行缝（熔断/限流/SSL/代理/超时），POST 提交查询体。
    result = await tracked_provider_get(
        "overpass",
        settings.OVERPASS_API_URL,
        {},
        method="POST",
        data={"data": full_query},
        timeout=60,
        business_checker=check_overpass_status,
    )
    if "error" in result:
        logger.error(f"[OSM] Overpass error: {result['error']}")
        return {"type": "FeatureCollection", "features": [], "error": result["error"]}

    data = result
    logger.info(f"[OSM] Overpass query successful, data size: {len(json.dumps(data))} bytes")
    return _overpass_to_geojson(data)


async def _geocode_bbox(query: str, expand_km: float = 0) -> Optional[str]:
    """通过 Nominatim 地理编码获取边界框，返回 'south,west,north,east' 格式"""
    params = {
        "q": query,
        "format": "json",
        "limit": 5,
        "accept-language": "zh",
    }
    result = await tracked_provider_get(
        "nominatim",
        settings.NOMINATIM_URL,
        params,
        timeout=30,
        business_checker=check_nominatim_status,
    )
    if "error" in result:
        logger.error(f"Nominatim error: {result['error']}")
        return None
    results = result

    if not results:
        return None

    # 按 importance 降序排序，选择最相关的结果
    results.sort(key=lambda r: float(r.get("importance", 0)), reverse=True)
    best = results[0]

    bb = best.get("boundingbox")
    lat = float(best.get("lat", 0))
    lon = float(best.get("lon", 0))

    if bb and len(bb) == 4:
        south, north, west, east = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
    else:
        south, north = lat - 0.05, lat + 0.05
        west, east = lon - 0.05, lon + 0.05

    if expand_km > 0:
        # #618-14: longitude degrees shrink by cos(lat); latitude stays ~111 km/°.
        lat_delta = expand_km / 111.0
        lon_delta = expand_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
        south -= lat_delta
        north += lat_delta
        west -= lon_delta
        east += lon_delta

    return f"{south},{west},{north},{east}"


async def _nominatim_search_poi(category: str, bbox: str, limit: int) -> dict:
    """通过 Nominatim Search API 查询 POI（Overpass 备选方案）"""
    parts = bbox.split(",")
    if len(parts) != 4:
        return {"type": "FeatureCollection", "features": []}

    south, west, north, east = [float(p) for p in parts]
    # Nominatim viewbox 参数
    params = {
        "q": category,
        "format": "json",
        "limit": limit,
        "accept-language": "zh",
        "viewbox": f"{west},{south},{east},{north}",
        "bounded": "1",
    }
    features = []
    result = await tracked_provider_get(
        "nominatim",
        settings.NOMINATIM_URL,
        params,
        timeout=30,
        business_checker=check_nominatim_status,
    )
    if "error" in result:
        return {"type": "FeatureCollection", "features": []}
    results = result

    for r in results:
        lat = float(r.get("lat", 0))
        lon = float(r.get("lon", 0))
        props = {
            "name": r.get("name", r.get("display_name", "").split(",")[0]),
            "type": r.get("type", ""),
            "class": r.get("class", ""),
            "display_name": r.get("display_name", ""),
        }
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props,
        })

    return {"type": "FeatureCollection", "features": features}


class QueryOsmPoiArgs(BaseModel):
    area: str = Field(..., description="区域名称或地名，如'北京'、'成都天府广场5公里内'")
    category: str = Field("restaurant", description="POI 类别，如 restaurant/school/hospital/park/bank/cafe/bar")
    limit: int = Field(50, ge=1, le=500, description="返回数量上限，范围 1-500")

def register_osm_tools(registry: ToolRegistry):
    """注册 OSM 查询工具"""

    @tool(registry, name="query_osm_poi",
           description=(
               "在区域内查询兴趣点 (POI)，返回 GeoJSON 点要素集。"
               "中国境内优先查本地 OSM GPKG（离线、秒级）；本地未命中再走 Overpass。"
               "\n何时用：用户给的是区域+POI类型 (如『成都的学校』『海淀区的医院』)；"
               "需要全量 / 大批 POI 而不是周边几条结果。"
               "\n何时不用：(1) 用户问『附近 500 米的便利店』 — 用 search_poi_around (按半径搜)；"
               "(2) 已知一个闭合多边形要查内部 POI — 优先 query_local_osm，否则 search_poi_polygon；"
               "(3) 区域明显跨多个国家 — 分批或换 chinese_maps 工具。"
               "\n关键约束：area 必须能被本地行政区或 Nominatim 解析；POI 类别基于 OSM amenity/shop/leisure tag。"
           ),
           tier=2, domains=["osm"],
           args_model=QueryOsmPoiArgs)
    async def query_osm_poi(area: str, category: str = "restaurant", limit: int = 50) -> dict:
        from app.services.local_first import try_local_osm_poi

        local = try_local_osm_poi(area, category, limit)
        if local is not None:
            return local

        # 从 area 中提取距离信息（如 "5公里内"、"3km"）并扩大搜索范围
        import re
        dist_match = re.search(r'(\d+)\s*(公里|千米|km|公里内)', area, re.IGNORECASE)
        # 默认不外扩，或者仅外扩 1km 以防点不在正中心
        expand_km = float(dist_match.group(1)) if dist_match else 1.0 
        
        # 如果关键词包含“区”、“县”、“街道”，通常不需要大幅外扩
        if not dist_match and any(x in area for x in ["区", "县", "街道", "镇", "市"]):
            expand_km = 0

        # 提取纯地名
        clean_area = re.sub(r'\d+\s*(公里|千米|km|公里内|内).*$', '', area, flags=re.IGNORECASE).strip()
        if not clean_area:
            clean_area = area

        # 先地理编码获取 bbox，并扩大范围
        bbox = await _geocode_bbox(clean_area, expand_km=expand_km)
        if not bbox:
            raise ValueError(f"无法地理编码: {clean_area}")

        # 中文 category → 标准 OSM 标签。G-4（#868）：单一事实来源在
        # app/lib/osm_category_map.py（与 local_first 共享；小学/中学走
        # amenity=school + 学段窄化，超市/商场走 shop，车站走 railway——
        # 旧映射的 primary_school/secondary_school 等为非文档化标签，
        # Overpass 召回≈0）。
        from app.lib.osm_category_map import (
            CHINESE_CATEGORY_TAGS,
            NOMINATIM_TERMS,
            OVERPASS_STAGE_NARROW,
        )

        tag_spec = CHINESE_CATEGORY_TAGS.get(category)
        if tag_spec is None and category and any(
            "\u4e00" <= ch <= "\u9fff" for ch in category
        ):
            # #694：未映射的中文值不再直通 Overpass（tag 值必须是英文枚举，
            # 中文直通 = 0 命中死查询）。显式报错并给出可用类别。
            sample = sorted({f"{k}={v}" for k, v in CHINESE_CATEGORY_TAGS.values()})[:8]
            return {
                "error": f"未知的 POI 类别: {category!r}（Overpass tag 需英文枚举值）",
                "correction_hint": (
                    f"请改用已映射类别（如 学校/小学/医院/餐厅/公园/超市 等），"
                    f"或直接用英文 tag（如 {', '.join(sample)}…）"
                ),
            }
        key, value = tag_spec if tag_spec else ("amenity", str(category or ""))

        safe_value = _sanitize_overpass_value(value)
        tag_filter = f'"{key}"="{safe_value}"'

        # G-4：学段窄化——amenity=school + school~primary|elementary；窄化
        # 0 命中时放宽回全量 school（大量学校未标 school 子标签）并披露。
        stage_regex = OVERPASS_STAGE_NARROW.get(category) if (
            key == "amenity" and value == "school"
        ) else None

        def _build_query(use_stage: bool) -> str:
            if use_stage and stage_regex:
                extra = f'["school"~"({stage_regex})"]'
                return (
                    f'node[{key}="{safe_value}"]{extra}({bbox});'
                    f'way[{key}="{safe_value}"]{extra}({bbox});'
                    f'relation[{key}="{safe_value}"]{extra}({bbox});'
                )
            return (
                f'node[{tag_filter}]({bbox});way[{tag_filter}]({bbox});'
                f'relation[{tag_filter}]({bbox});'
            )

        query = _build_query(bool(stage_regex))
        geojson = await _query_overpass(query, limit=limit)

        # #773: provenance — which upstream actually produced the payload.
        source = "overpass"
        fallback_note: Optional[str] = None

        if (
            stage_regex
            and not geojson.get("error")
            and len(geojson.get("features", [])) == 0
        ):
            # 窄化 0 命中：放宽为全量 amenity=school（可能含其他学段），披露。
            fallback_note = (
                f"学段窄化（school~{stage_regex}）0 命中，已放宽为全量 "
                f"{key}={value}（结果可能包含其他学段学校）"
            )
            query = _build_query(False)
            geojson = await _query_overpass(query, limit=limit)

        # Overpass 失败时，fallback 到 Nominatim 搜索
        if geojson.get("error") or len(geojson.get("features", [])) == 0:
            # Overpass 明确报错，抛出异常以触发标准化错误响应
            if geojson.get("error"):
                 raise RuntimeError(geojson["error"])

            # 用中英文关键词搜索（优先使用英文 tag，增加成功率）
            # G-4（#868）：词表来自共享 osm_category_map.NOMINATIM_TERMS，
            # 补齐 supermarket/mall/marketplace/station 等此前缺失词条。
            search_terms = NOMINATIM_TERMS.get(value, [value] if value else [])
            if not search_terms:
                search_terms = [category] if category else ["poi"]
            nom_geojson = {"type": "FeatureCollection", "features": []}
            num_terms = max(1, len(search_terms))
            term_limit = max(1, limit // num_terms)
            for term in search_terms:
                nom_geojson = await _nominatim_search_poi(term, bbox, term_limit)
                if len(nom_geojson.get("features", [])) > 0:
                    break

            # 使用 Nominatim 结果作为 fallback
            if len(nom_geojson.get("features", [])) > 0:
                geojson = nom_geojson
                source = "nominatim"
                fallback_note = _NOMINATIM_FALLBACK_NOTE
            else:
                # 依然没找到数据，抛出异常引导 AI 自愈或向用户解释
                raise ValueError(f"在区域 '{clean_area}' 内找不到类别为 '{category}' 的兴趣点。")

        # limit 契约：服务端已限制 + 防御性截断（Overpass limit 语义差异兜底）
        geojson["features"] = geojson.get("features", [])[:limit]

        # #773: stamp provenance (source + fetched_at) so a stored layer ref
        # keeps its origin auditable; the fallback swap is explicitly marked.
        result = {
            "type": "poi_query",
            "area": area,
            "category": category,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
            "bbox": bbox,
            "source": source,
            "fetched_at": _utcnow_iso(),
        }
        # G-7（#871）：截断披露 —— count==limit 时样本几乎必然不完整
        #（Overpass `out body geom N` 返回任意前 N 条），下游统计（R 比率/
        # Moran/热点）在截断样本上的显著性叙述必须感知这一前提。
        if result["count"] >= limit:
            result["truncated"] = True
            result["note"] = (
                f"结果达到 limit={limit}，样本可能不完整（前 N 条而非空间均匀）；"
                f"分布/密度/统计结论前请提高 limit 或分 bbox 拉取。"
            )
        if fallback_note:
            result["fallback_note"] = fallback_note
        return result

    @tool(registry, name="query_osm_roads",
           description=(
               "OSM 道路网络查询：按区域+道路等级拉取 LineString 路网 GeoJSON。"
               "中国境内优先查本地 roads GPKG；本地未命中再走 Overpass。"
               "\n何时用：路径规划/可达性分析需要路网底图；按等级筛选 (highway/primary 主干道) 做密度统计。"
               "\n何时不用：仅需路径规划终端结果 — 用 plan_route (高德路径) 或 isochrone_analysis (等时圈)；"
               "需要实时路况 — 用 get_traffic_status。"
               "\n关键约束：road_type 是 OSM highway tag 值，常见: motorway/primary/secondary/tertiary/residential/footway。"
           ),
           tier=2, domains=["osm"],
           param_descriptions={
               "area": "区域名称，如 '成都' '海淀区'。会先地理编码取 bbox",
               "road_type": "OSM highway tag 值。常用 primary(主干) / secondary(次干) / residential(支路)",
               "limit": "返回上限，默认 100。大区域 + 低等级路（如 residential）极易超量",
           })
    async def query_osm_roads(area: str, road_type: str = "primary", limit: int = 100) -> dict:
        from app.services.local_first import try_local_osm_roads

        local = try_local_osm_roads(area, road_type, limit)
        if local is not None:
            return local

        bbox = await _geocode_bbox(area)
        if not bbox:
            raise ValueError(f"无法地理编码: {area}")

        query = f'way["highway"="{_sanitize_overpass_value(road_type)}"]({bbox});'
        geojson = await _query_overpass(query, limit=limit)
        if geojson.get("error"):
            raise RuntimeError(geojson["error"])
        if len(geojson.get("features", [])) == 0:
            raise ValueError(f"在区域 '{area}' 内找不到类型为 '{road_type}' 的道路数据。")
        geojson["features"] = geojson.get("features", [])[:limit]

        return {
            "type": "road_query",
            "area": area,
            "road_type": road_type,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
            "bbox": bbox,
            # #773: provenance stamp for the stored layer ref.
            "source": "overpass",
            "fetched_at": _utcnow_iso(),
        }

    @tool(registry, name="query_osm_buildings",
           description=(
               "OSM 建筑物轮廓查询：在指定区域拉取所有带 building=* tag 的多边形 GeoJSON。"
               "\n何时用：用户要看建筑物轮廓底图；做建筑密度/容积率/建筑年代统计；"
               "城市肌理可视化（结合 buffer_analysis）。"
               "\n何时不用：用户问『XX 建筑的信息』(单体查询) — 用 search_poi_around；"
               "需要建筑高度 — OSM 仅部分城市有 building:levels，可能空。"
               "\n关键约束：大城市中心 (如北京三环内) 一次拉可能 10k+ 要素，建议先缩小 area。"
           ),
           tier=2, domains=["osm"],
           param_descriptions={
               "area": "区域名称（街道/小区/POI 级精度更好），如 '成都春熙路'。会被地理编码为 bbox",
               "limit": "返回上限，默认 100。Overpass 服务器对超量请求会拒绝",
           })
    async def query_osm_buildings(area: str, limit: int = 100) -> dict:
        bbox = await _geocode_bbox(area)
        if not bbox:
            raise ValueError(f"无法地理编码: {area}")

        query = f'way["building"]({bbox});'
        geojson = await _query_overpass(query, limit=limit)
        if geojson.get("error"):
            raise RuntimeError(geojson["error"])
        if len(geojson.get("features", [])) == 0:
            raise ValueError(f"在区域 '{area}' 内找不到建筑物数据。")
        geojson["features"] = geojson.get("features", [])[:limit]

        return {
            "type": "building_query",
            "area": area,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
            "bbox": bbox,
            # #773: provenance stamp for the stored layer ref.
            "source": "overpass",
            "fetched_at": _utcnow_iso(),
        }

    @tool(registry, name="query_osm_boundary",
           description=(
               "OSM 行政边界轮廓查询：拉取一个行政区的多边形 GeoJSON。Overpass 失败时自动 fallback 到 Nominatim。"
               "\n何时用：需要国际通用的行政边界（OSM 数据全球覆盖）；用作 clip_layer 的遮罩；做空间统计的母图层。"
               "\n何时不用：中国境内 — 优先 get_local_admin_boundary (本地 SHP，更稳更快)，"
               "或 get_admin_division (天地图官方界线)；要下级单元列表 — 用 get_child_districts。"
               "\n关键约束：admin_level 是 OSM 体系（4=省级/state, 6=市级/prefecture, 8=区/county, 10=街道）；不同国家约定不同。"
           ),
           tier=2, domains=["osm"],
           param_descriptions={
               "name": "行政区名称，需与 OSM 数据一致。如 '海淀区' '成都市' 'California'",
               "admin_level": "OSM admin_level，中国常用 4(省) / 6(市) / 8(区县)。默认 8",
           })
    async def query_osm_boundary(name: str, admin_level: int = 8) -> dict:
        from app.services.local_first import try_local_osm_boundary

        local = try_local_osm_boundary(name, admin_level)
        if local is not None:
            return local

        # 先尝试 Overpass
        query = f'relation["admin_level"="{int(admin_level)}"]["name"="{_sanitize_overpass_value(name)}"]->.searchArea;.searchArea out body geom;'
        geojson = await _query_overpass(query)

        # #773: provenance — which upstream actually produced the polygon.
        source = "overpass"
        fallback_note: Optional[str] = None

        # Overpass 失败时，用 Nominatim 搜索行政边界
        if len(geojson.get("features", [])) == 0:
            params = {
                "q": name,
                "format": "json",
                "limit": 1,
                "accept-language": "zh",
                "polygon_geojson": "1",
            }
            result = await tracked_provider_get(
                "nominatim",
                settings.NOMINATIM_URL,
                params,
                timeout=30,
                business_checker=check_nominatim_status,
            )
            if "error" not in result:
                results = result
                if results:
                        r = results[0]
                        geojson_poly = r.get("geojson")
                        if geojson_poly:
                            geojson = {
                                "type": "FeatureCollection",
                                "features": [{
                                    "type": "Feature",
                                    "geometry": geojson_poly,
                                    "properties": {
                                        "name": r.get("name", name),
                                        "display_name": r.get("display_name", ""),
                                    },
                                }],
                            }
                            # #773: mark the semantics-changing source switch —
                            # a Nominatim best-match polygon is not necessarily
                            # the admin_level relation Overpass was asked for.
                            source = "nominatim"
                            fallback_note = (
                                "Overpass 未返回边界，已降级为 Nominatim 最佳匹配多边形；"
                                "其 admin_level 语义可能与请求不一致。"
                            )

        # #773: provenance stamp (source + fetched_at) on the envelope.
        boundary_result = {
            "type": "boundary_query",
            "name": name,
            "admin_level": admin_level,
            "count": len(geojson.get("features", [])),
            "geojson": geojson,
            "source": source,
            "fetched_at": _utcnow_iso(),
        }
        if fallback_note:
            boundary_result["fallback_note"] = fallback_note
        return boundary_result

